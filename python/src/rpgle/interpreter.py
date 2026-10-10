"""
interpreter.py
─────────────────────────────────────────────────────────────────
Runs a parsed RPGLE program (see rpgle.py) against its WORKSTN
display file (see dds.py). Real RPG's EXFMT ("write a format, then
read it") maps onto a Python generator: it yields the format to render
as a ScreenData -- and resumes via .send(Resume(...)) once the caller
has collected the next AID key and input field values, however the
caller happens to be presenting the screen (5250 terminal, plain text,
a test harness, anything). The library has no opinion about
presentation; it only produces and consumes plain data.

Python has native generators exactly like the original JS port did, so
run_program() below is a near 1:1 translation of src/interpreter.js's
function* runProgram -- unlike the Go port, which had to reach for a
goroutine + channel pair specifically because Go has no generators.

Supported opcode subset (see docs/LANGUAGE.md for the full RPGLE
subset this library implements, and what's explicitly out of scope):
EVAL, IF/ELSE/ENDIF, DOW/ENDDO, LEAVE, SELECT/WHEN/OTHER/ENDSL,
EXSR/BEGSR/ENDSR, EXFMT, DIV/MVR, RETURN, and the DISK file opcodes
(CHAIN, SETLL, SETGT, READ, READE, READP, READPE, WRITE, UPDATE,
DELETE, plus KLIST/KFLD) which live in fileops.py. Expression evaluation
(arithmetic, comparisons, %TRIM/%LEN/%SUBST) lives in expr.py.

DIV/MVR (not EVAL-expression division) is the classic RPG idiom for
modulo: RPG has no %REM until well past V4R3, and relying on
intermediate-expression truncation inside a compound EVAL expression is
not unambiguous real RPG behavior. DIV followed immediately by MVR is
the classic, unambiguous way to get a remainder, real since RPG's
earliest versions.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from .dds import DDS, Format
from .errors import RpgleError
from .fileops import FILE_OPS, FileRuntime
from .database import Database
from .pf import PhysicalFile
from .expr import Context, Var, as_bool, as_float, as_string, eval_expr, split_assignment
from .rpgle import DSpec, DsSpec, Program
from ._helpers import pad_end_trunc, pad_start_zero

__all__ = [
    "Var",
    "Instr",
    "Compiled",
    "RenderedField",
    "Cursor",
    "RenderResult",
    "ScreenData",
    "Resume",
    "build_vars",
    "compile_program",
    "render_format",
    "run_program",
]


def _dspec_from_pf_field(f) -> DSpec:
    return DSpec(name=f.name, deftype="S", length=f.length, dec=f.dec if f.dtype == "N" else None)


def _ds_layout(prog: Program, ds: DsSpec, dds: DDS, pfs: Optional[dict[str, PhysicalFile]], seen=()) -> list[DSpec]:
    """The subfields a data structure ends up with, wherever they come from."""
    if ds.likeds:
        other = next((d for d in prog.dss if d.name.lower() == ds.likeds.lower()), None)
        if other is None or other.name.lower() in seen or other.name.lower() == ds.name.lower():
            raise RpgleError(f"{ds.name}: LIKEDS({ds.likeds}) must name a data structure declared earlier")
        return _ds_layout(prog, other, dds, pfs, seen + (ds.name.lower(),))
    if ds.likerec:
        want = ds.likerec.upper()
        for pf in (pfs or {}).values():
            if pf.format.upper() == want:
                return [_dspec_from_pf_field(f) for f in pf.fields]
        for fmt in dds.formats.values():
            if fmt.name.upper() == want:
                return [
                    DSpec(name=f.name, deftype="S", length=f.length, dec=f.dec if f.dtype != "A" else None)
                    for f in fmt.fields
                    if f.kind == "field"
                ]
        raise RpgleError(f"{ds.name}: LIKEREC({ds.likerec}) is not a record format of any file in this program")
    if ds.extname:
        pf = (pfs or {}).get(ds.extname.upper())
        if pf is None:
            raise RpgleError(f"{ds.name}: EXTNAME({ds.extname}) is not a file in this program")
        return [_dspec_from_pf_field(f) for f in pf.fields]
    return ds.subfields


def expand_ds(
    prog: Program, dds: DDS, pfs: Optional[dict[str, PhysicalFile]] = None
) -> dict[str, list[tuple[str, DSpec]]]:
    """Every data structure's variables: {ds name (lower): [(variable
    name (lower), the subfield's DSpec), ...]}. A qualified DS's
    variables are named `ds.sub`; an unqualified one's are just `sub`.
    """
    out: dict[str, list[tuple[str, DSpec]]] = {}
    for ds in prog.dss:
        prefix = ds.name.lower() + "." if ds.qualified else ""
        out[ds.name.lower()] = [(prefix + sub.name.lower(), sub) for sub in _ds_layout(prog, ds, dds, pfs)]
    return out


def _new_var(d: DSpec) -> Var:
    if d.dec is None:
        init = d.init.strip("'") if d.init is not None else ""
        value = init[: d.length] if d.kind == "varchar" else pad_end_trunc(init, d.length)
        return Var(type="A", length=d.length, dec=0, value=value, kind=d.kind)
    return Var(type="N", length=d.length, dec=d.dec, value=float(d.init) if d.init is not None else 0.0)


def build_vars(prog: Program, dds: DDS, pfs: Optional[dict[str, PhysicalFile]] = None) -> dict[str, Var]:
    """Builds the program's variable table: every named DDS field
    across every record format of the externally-described WORKSTN
    file becomes a program variable (first definition in source order
    wins on a name collision), then every field of each DISK file's
    record format (pfs, keyed by upper-case file name), plus D-spec
    standalone fields (definition type 'S' only).
    """
    variables: dict[str, Var] = {}

    for fmt_name in dds.format_order:
        fmt = dds.formats[fmt_name]
        for f in fmt.fields:
            if f.kind != "field":
                continue
            key = f.name.lower()
            if key in variables:
                continue
            if f.dtype == "A":
                variables[key] = Var(type="A", length=f.length, dec=0, value=" " * f.length)
            else:
                variables[key] = Var(type="N", length=f.length, dec=f.dec or 0, value=0.0)

    for fs in prog.files:
        pf = (pfs or {}).get((fs.name or "").upper())
        if pf is None or (fs.device or "").upper() != "DISK":
            continue
        for pff in pf.fields:
            key = pff.name.lower()
            if key in variables:
                continue
            if pff.dtype == "A":
                variables[key] = Var(type="A", length=pff.length, dec=0, value=" " * pff.length)
            else:
                variables[key] = Var(type="N", length=pff.length, dec=pff.dec, value=0.0)

    for d in prog.dspecs:
        if d.deftype != "S":
            continue
        variables[d.name.lower()] = _new_var(d)

    for members in expand_ds(prog, dds, pfs).values():
        for key, sub in members:
            variables.setdefault(key, _new_var(sub))

    return variables


@dataclass
class Instr:
    """One compiled, jump-resolved instruction. Fields are deliberately
    overloaded per-opcode (mirroring the JS port's loosely typed
    instruction objects) rather than split into a class per opcode --
    there are few enough opcodes that a flat shape reads more directly
    against the JS source it's ported from.
    """

    op: str
    expr: Optional[str] = None  # EVAL/IF/DOW/WHEN expression source
    false_target: Optional[int] = None  # IF/DOW/WHEN false-branch target
    target: Optional[int] = None  # JMP/LEAVE/ENDDO jump target
    sub_target: Optional[str] = None  # EXSR: subroutine name to call
    format: Optional[str] = None  # EXFMT: record format name
    factor1: Optional[str] = None  # DIV: factor1
    factor2: Optional[str] = None  # DIV: factor2
    result: Optional[str] = None  # DIV/MVR: result field name
    ind_hi: Optional[str] = None  # file ops: resulting indicators, cols 71-76
    ind_lo: Optional[str] = None
    ind_eq: Optional[str] = None
    # Conditioning indicator, cols 9-11: (negated, "71"). The
    # instruction runs only when it holds.
    cond: Optional[tuple[bool, str]] = None
    limit: Optional[str] = None  # FORINIT: the TO/DOWNTO limit
    step: Optional[str] = None  # FORINIT: the BY increment
    down: bool = False  # FORINIT/FORTEST: DOWNTO
    ds: Optional[str] = None  # file ops: data structure to read into / write from


@dataclass
class Compiled:
    """A jump-resolved instruction array plus subroutine entry points."""

    instrs: list[Instr]
    subroutines: dict[str, int]
    klists: dict[str, list[str]] = field(default_factory=dict)  # KLIST name -> KFLD field names


def compile_program(statements) -> Compiled:
    """Compiles a flat C-spec statement list into a jump-resolved
    instruction array (classic backpatch compilation: IF/DOW/SELECT
    frames on a stack, each closing keyword patches the jump targets it
    owns). See docs/LANGUAGE.md's "Compile Phases" section for a worked
    example.

    Named compile_program, not compile, to avoid shadowing the compile
    builtin -- the one deliberate naming deviation from the JS/Go ports'
    compile()/Compile().
    """
    instrs: list[Instr] = []
    subroutines: dict[str, int] = {}
    if_stack: list[dict] = []
    dow_stack: list[dict] = []
    sel_stack: list[dict] = []
    klists: dict[str, list[str]] = {}
    current_klist: Optional[str] = None

    for stmt in statements:
        op = stmt.op
        start = len(instrs)
        cond = _parse_cond(stmt.indic)
        if cond is not None and op in _NO_COND_OPS:
            raise RpgleError(f"Conditioning indicators on {op} are not supported")

        if op == "EVAL":
            instrs.append(Instr(op="EVAL", expr=stmt.expr))

        elif op == "IF":
            idx = len(instrs)
            instrs.append(Instr(op="IF", expr=stmt.expr, false_target=None))
            if_stack.append({"if_index": idx, "else_jmp_index": None})

        elif op == "ELSE":
            if not if_stack:
                raise RpgleError("ELSE with no open IF")
            frame = if_stack[-1]
            jmp_idx = len(instrs)
            instrs.append(Instr(op="JMP", target=None))
            frame["else_jmp_index"] = jmp_idx
            instrs[frame["if_index"]].false_target = jmp_idx + 1

        elif op == "ELSEIF":
            # ELSE + IF sharing one ENDIF: the nested IF's frame is marked
            # "chained", so ENDIF closes it and every frame it hangs off.
            if not if_stack:
                raise RpgleError("ELSEIF with no open IF")
            frame = if_stack[-1]
            jmp_idx = len(instrs)
            instrs.append(Instr(op="JMP", target=None))
            frame["else_jmp_index"] = jmp_idx
            instrs[frame["if_index"]].false_target = jmp_idx + 1
            idx = len(instrs)
            instrs.append(Instr(op="IF", expr=stmt.expr, false_target=None))
            if_stack.append({"if_index": idx, "else_jmp_index": None, "chained": True})

        elif op == "ENDIF":
            if not if_stack:
                raise RpgleError("ENDIF with no open IF")
            idx = len(instrs)
            while True:
                frame = if_stack.pop()
                if frame["else_jmp_index"] is None:
                    instrs[frame["if_index"]].false_target = idx
                else:
                    instrs[frame["else_jmp_index"]].target = idx
                if not frame.get("chained"):
                    break
            instrs.append(Instr(op="NOP"))

        elif op == "DOW":
            idx = len(instrs)
            instrs.append(Instr(op="DOW", expr=stmt.expr, false_target=None))
            dow_stack.append({"kind": "DOW", "start": idx, "leaves": [], "iters": []})

        elif op == "DOU":
            idx = len(instrs)
            instrs.append(Instr(op="NOP"))  # loop top; the test is at ENDDO
            dow_stack.append({"kind": "DOU", "start": idx, "expr": stmt.expr, "leaves": [], "iters": []})

        elif op == "FOR":
            if stmt.limit is None:
                raise RpgleError("FOR is only supported in free-form source (**FREE)")
            init_idx = len(instrs)
            instrs.append(
                Instr(op="FORINIT", factor1=stmt.factor1, expr=stmt.expr, limit=stmt.limit, step=stmt.step, down=stmt.down)
            )
            test_idx = len(instrs)
            instrs.append(Instr(op="FORTEST", factor1=stmt.factor1, false_target=None, down=stmt.down))
            dow_stack.append(
                {"kind": "FOR", "start": test_idx, "var": stmt.factor1, "leaves": [], "iters": [], "init": init_idx}
            )

        elif op in ("LEAVE", "ITER"):
            if not dow_stack:
                raise RpgleError(f"{op} with no open loop")
            frame = dow_stack[-1]
            if op == "ITER" and frame["kind"] == "DOW":
                instrs.append(Instr(op="LEAVE", target=frame["start"]))  # jump back to the test
            else:
                frame["leaves" if op == "LEAVE" else "iters"].append(len(instrs))
                instrs.append(Instr(op="LEAVE", target=None))

        elif op in ("ENDDO", "ENDFOR"):
            if not dow_stack:
                raise RpgleError(f"{op} with no open loop")
            frame = dow_stack.pop()
            kind = frame["kind"]
            if (op == "ENDFOR") != (kind == "FOR"):
                raise RpgleError(f"{op} closes a {kind} loop")
            idx = len(instrs)
            if kind == "DOW":
                instrs[frame["start"]].false_target = idx + 1
                instrs.append(Instr(op="ENDDO", target=frame["start"]))
                continue_at = frame["start"]
            elif kind == "DOU":
                instrs.append(Instr(op="DOUTEST", expr=frame["expr"], target=frame["start"]))
                continue_at = idx
            else:
                instrs[frame["start"]].false_target = idx + 1
                instrs.append(Instr(op="FORSTEP", factor1=frame["var"], target=frame["start"], down=instrs[frame["init"]].down))
                continue_at = idx
            for li in frame["leaves"]:
                instrs[li].target = idx + 1
            for ii in frame["iters"]:
                instrs[ii].target = continue_at

        elif op == "SELECT":
            sel_stack.append({"arms": [], "pending_jumps": []})
            instrs.append(Instr(op="NOP"))

        elif op in ("WHEN", "OTHER"):
            if not sel_stack:
                raise RpgleError(f"{op} with no open SELECT")
            sframe = sel_stack[-1]
            if sframe["arms"]:
                sframe["pending_jumps"].append(len(instrs))
                instrs.append(Instr(op="JMP", target=None))
            idx = len(instrs)
            if op == "WHEN":
                instrs.append(Instr(op="WHEN", expr=stmt.expr, false_target=None))
            else:
                instrs.append(Instr(op="OTHER"))
            sframe["arms"].append({"index": idx, "is_other": op == "OTHER"})

        elif op == "ENDSL":
            if not sel_stack:
                raise RpgleError("ENDSL with no open SELECT")
            sframe = sel_stack.pop()
            sframe["pending_jumps"].append(len(instrs))
            instrs.append(Instr(op="JMP", target=None))
            endsl_idx = len(instrs)
            instrs.append(Instr(op="NOP"))
            arms = sframe["arms"]
            for i, arm in enumerate(arms):
                if arm["is_other"]:
                    continue
                next_idx = arms[i + 1]["index"] if i + 1 < len(arms) else endsl_idx
                instrs[arm["index"]].false_target = next_idx
            for j in sframe["pending_jumps"]:
                instrs[j].target = endsl_idx

        elif op == "BEGSR":
            subroutines[stmt.factor1] = len(instrs)

        elif op == "ENDSR":
            instrs.append(Instr(op="ENDSR"))

        elif op == "EXSR":
            instrs.append(Instr(op="EXSR", sub_target=stmt.expr))

        elif op == "EXFMT":
            instrs.append(Instr(op="EXFMT", format=stmt.expr))

        elif op == "DIV":
            instrs.append(Instr(op="DIV", factor1=stmt.factor1, factor2=stmt.factor2, result=stmt.result))

        elif op == "MVR":
            instrs.append(Instr(op="MVR", result=stmt.result))

        elif op == "RETURN":
            instrs.append(Instr(op="RETURN"))

        elif op in FILE_OPS:
            instrs.append(
                Instr(
                    op=op,
                    factor1=stmt.factor1,
                    factor2=stmt.factor2,
                    ind_hi=stmt.ind_hi,
                    ind_lo=stmt.ind_lo,
                    ind_eq=stmt.ind_eq,
                    ds=stmt.ds,
                )
            )

        elif op == "CLEAR":
            instrs.append(Instr(op="CLEAR", expr=stmt.expr))

        elif op == "KLIST":
            current_klist = (stmt.factor1 or "").upper()
            klists[current_klist] = []

        elif op == "KFLD":
            if current_klist is None:
                raise RpgleError("KFLD with no KLIST before it")
            klists[current_klist].append(stmt.result)

        else:
            raise RpgleError(f"Unsupported RPG opcode: {op}")

        for ins in instrs[start:]:
            ins.cond = cond

    return Compiled(instrs=instrs, subroutines=subroutines, klists=klists)


# Ops that expand into jumps, so skipping one on a condition would
# desynchronize the jump targets. Real RPG allows conditioning these;
# here it's rejected loudly rather than run wrong.
_NO_COND_OPS = frozenset(
    {"IF", "ELSE", "ELSEIF", "ENDIF", "DOW", "DOU", "FOR", "ENDDO", "ENDFOR", "SELECT", "WHEN", "OTHER", "ENDSL", "BEGSR", "ENDSR", "KLIST", "KFLD"}
)
_COND_RE = re.compile(r"^(N?)(\d\d|LR)$", re.IGNORECASE)


def _parse_cond(raw: Optional[str]) -> Optional[tuple[bool, str]]:
    if not raw:
        return None
    m = _COND_RE.match(raw)
    if not m:
        raise RpgleError(f"Unsupported conditioning indicator: {raw!r}")
    return (m.group(1).upper() == "N", m.group(2).upper())


_NUM_LIT_RE = re.compile(r"^-?\d+(\.\d+)?$")
_ASSIGN_IND_RE = re.compile(r"^\*IN(\d\d)$", re.IGNORECASE)
_ASSIGN_INLR_RE = re.compile(r"^\*INLR$", re.IGNORECASE)


def _read_value(variables: dict[str, Var], name: str) -> Any:
    key = name.strip().lower()
    if key not in variables:
        raise RpgleError(f"Unknown RPG variable: {name}")
    return variables[key].value


def _numeric_operand(variables: dict[str, Var], operand: str) -> float:
    if _NUM_LIT_RE.match(operand):
        return float(operand)
    return as_float(_read_value(variables, operand))


def _assign(variables: dict[str, Var], indicators: dict[str, bool], lvalue: str, rvalue: Any) -> None:
    m = _ASSIGN_IND_RE.match(lvalue)
    if m:
        indicators[m.group(1)] = as_bool(rvalue)
        return
    if _ASSIGN_INLR_RE.match(lvalue):
        indicators["LR"] = as_bool(rvalue)
        return
    key = lvalue.strip().lower()
    v = variables.get(key)
    if v is None:
        raise RpgleError(f"Unknown RPG variable on assignment: {lvalue}")
    if v.kind == "ind":
        v.value = "1" if as_bool(rvalue) else "0"
    elif v.kind == "varchar":
        v.value = as_string(rvalue)[: v.length]  # no padding: its length is its content
    elif v.type == "A":
        v.value = pad_end_trunc(as_string(rvalue), v.length)
    else:
        # Truncate-on-assign (no rounding) -- the real RPG default, and
        # what DIV/MVR-based modulo relies on for boundedness.
        factor = 10**v.dec
        v.value = math.trunc(as_float(rvalue) * factor) / factor


def _clear(target: str, variables: dict[str, Var], ds_members: dict[str, list[tuple[str, DSpec]]]) -> None:
    """CLEAR: character fields to blanks, numbers to zero, indicators to
    off. Naming a data structure clears every subfield.
    """
    name = target.strip().lower()
    keys = [k for k, _ in ds_members[name]] if name in ds_members else [name]
    for key in keys:
        v = variables.get(key)
        if v is None:
            raise RpgleError(f"CLEAR: unknown variable {target}")
        if v.kind == "ind":
            v.value = "0"
        elif v.type == "A":
            v.value = "" if v.kind == "varchar" else " " * v.length
        else:
            v.value = 0.0


def _format_field_text(v: Var) -> str:
    """Display text for a field. Numeric fields are zero-filled to
    their length. With decimals, the decimal point is written out
    (so the field is one column wider than a real 5250 would draw it,
    which shows bare digits with an implied point): bare digits would
    not survive being typed back in, since "000004875" parses as 4875,
    not 48.75.
    """
    if v.type == "A":
        return v.value
    n = v.value
    if v.dec > 0:
        body = f"{abs(n):0{v.length + 1}.{v.dec}f}"
        return "-" + body[1:] if n < 0 else body
    if n < 0:
        width = max(1, v.length - 1)
        return "-" + pad_start_zero(str(round(-n)), width)
    return pad_start_zero(str(round(n)), v.length)


@dataclass
class RenderedField:
    """One field or constant, positioned and formatted, ready to
    display -- protocol-agnostic (no 5250/3270 datastream concerns
    here; that's a presentation-layer concern for the caller).
    """

    row: int
    col: int
    text: str
    input: bool
    length: Optional[int] = None  # only meaningful when input is True


@dataclass(frozen=True)
class Cursor:
    row: int
    col: int


@dataclass
class RenderResult:
    """A DDS record format rendered against the current
    variable/indicator state.
    """

    fields: list[RenderedField]
    input_map: dict[int, str]  # screen row -> field name
    cursor: Cursor


def render_format(fmt: Format, variables: dict[str, Var], indicators: dict[str, bool]) -> RenderResult:
    """Renders a DDS record format against the current
    variable/indicator state. input_map maps each input field's screen
    row to its field name, so the caller can read input back after the
    next AID key using whatever row/field convention its own renderer
    already uses.
    """
    fields: list[RenderedField] = []
    input_map: dict[int, str] = {}
    cursor: Optional[Cursor] = None

    def condition_ok(conds) -> bool:
        for c in conds:
            on = bool(indicators.get(c.num, False))
            if c.not_ if on else not on:
                return False
        return True

    for f in fmt.fields:
        if not condition_ok(f.indicators):
            continue
        if f.kind == "const":
            fields.append(RenderedField(row=f.row, col=f.col, text=f.text, input=False))
            continue
        v = variables[f.name.lower()]
        text = _format_field_text(v)
        is_input = f.usage in ("I", "B")
        fields.append(
            RenderedField(row=f.row, col=f.col, text=text, input=is_input, length=f.length if is_input else None)
        )
        if is_input:
            input_map[f.row] = f.name.lower()
            if cursor is None:
                cursor = Cursor(row=f.row, col=f.col)

    return RenderResult(fields=fields, input_map=input_map, cursor=cursor or Cursor(row=0, col=0))


@dataclass
class ScreenData:
    """What the interpreter sends when the program hits EXFMT --
    equivalent to the JS generator's yielded value.
    """

    format_name: str
    fields: list[RenderedField]
    cursor: Cursor
    keys: dict[str, str]
    input_map: dict[int, str]


@dataclass
class Resume:
    """What the caller sends back to unblock the interpreter after
    EXFMT -- equivalent to the argument passed to the JS generator's
    .next(resume).
    """

    key: str
    values: dict[str, str] = field(default_factory=dict)


def run_program(
    prog: Program,
    dds: DDS,
    db: Optional[Database] = None,
    pfs: Optional[dict[str, PhysicalFile]] = None,
) -> Generator[ScreenData, Optional[Resume], None]:
    """Runs the program. Yields a ScreenData on every EXFMT; resume
    with .send(Resume(key, values)) where key is whatever function key
    the caller recognizes (matched against the format's own
    CFnn/CAnn-declared keys) and values is {field_name: typed_text} for
    every input field on the format just shown.

    A program with DISK files needs db (a Database) and pfs, a dict of
    upper-case file name -> PhysicalFile (see pf.parse_pf). Changes are
    committed when the program ends or the generator is closed.
    """
    try:
        yield from _run_program(prog, dds, db, pfs)
    finally:
        if db is not None:
            db.commit()


@dataclass
class _Frame:
    """One running body of code: the main line, later each procedure
    call. Everything that must not leak between calls lives here, so a
    recursive procedure gets a fresh copy of each on every call.
    """

    instrs: list[Instr]
    subroutines: dict[str, int]
    variables: Any  # dict[str, Var], or a ChainMap(locals, globals) inside a procedure
    ctx: Context
    ds_members: dict[str, list[tuple[str, DSpec]]]
    call_stack: list[int] = field(default_factory=list)  # EXSR return addresses
    for_state: dict[int, tuple[float, float]] = field(default_factory=dict)  # FORTEST index -> (limit, by)


class _Runtime:
    """State shared by every frame of one program run."""

    def __init__(self, dds: DDS, indicators: dict[str, bool], files: FileRuntime):
        self.dds = dds
        self.indicators = indicators  # global, as in RPG: *INnn and *INLR are shared by every procedure
        self.files = files

    def exec(self, frame: _Frame) -> Generator[ScreenData, Optional[Resume], Any]:
        """Runs one frame to its RETURN (or its end). Yields a ScreenData
        at every EXFMT. The generator's return value is the RETURN value.
        """
        dds, indicators, files = self.dds, self.indicators, self.files
        instrs, subroutines = frame.instrs, frame.subroutines
        variables, ctx, ds_members = frame.variables, frame.ctx, frame.ds_members
        call_stack, for_state = frame.call_stack, frame.for_state
        pc = 0
        last_div_remainder = 0.0

        while pc < len(instrs):
            instr = instrs[pc]
            op = instr.op

            if instr.cond is not None:
                negated, num = instr.cond
                if indicators.get(num, False) == negated:  # condition not met
                    pc += 1
                    continue

            if op == "NOP":
                pc += 1

            elif op == "ENDSR":
                if not call_stack:
                    raise RpgleError("ENDSR with no active EXSR call")
                pc = call_stack.pop()

            elif op == "EVAL":
                lvalue, rvalue_src = split_assignment(instr.expr)
                rvalue = eval_expr(rvalue_src, ctx)
                _assign(variables, indicators, lvalue, rvalue)
                pc += 1

            elif op in ("IF", "WHEN"):
                cond = eval_expr(instr.expr, ctx)
                pc = pc + 1 if as_bool(cond) else instr.false_target

            elif op == "OTHER":
                pc += 1

            elif op == "JMP":
                pc = instr.target

            elif op == "DOW":
                cond = eval_expr(instr.expr, ctx)
                pc = pc + 1 if as_bool(cond) else instr.false_target

            elif op == "ENDDO":
                pc = instr.target

            elif op == "LEAVE":
                pc = instr.target

            elif op == "DOUTEST":
                pc = pc + 1 if as_bool(eval_expr(instr.expr, ctx)) else instr.target

            elif op == "FORINIT":
                _assign(variables, indicators, instr.factor1, eval_expr(instr.expr, ctx))
                limit = as_float(eval_expr(instr.limit, ctx))
                by = as_float(eval_expr(instr.step, ctx)) if instr.step else 1.0
                if by <= 0:
                    raise RpgleError(f"FOR {instr.factor1}: BY must be positive (use DOWNTO to count down)")
                for_state[pc + 1] = (limit, by)
                pc += 1

            elif op == "FORTEST":
                limit, _ = for_state[pc]
                value = as_float(_read_value(variables, instr.factor1))
                keep_going = value >= limit if instr.down else value <= limit
                pc = pc + 1 if keep_going else instr.false_target

            elif op == "FORSTEP":
                _, by = for_state[instr.target]
                value = as_float(_read_value(variables, instr.factor1))
                _assign(variables, indicators, instr.factor1, value - by if instr.down else value + by)
                pc = instr.target

            elif op == "EXSR":
                target = subroutines.get(instr.sub_target)
                if target is None:
                    raise RpgleError(f"Unknown RPG subroutine: {instr.sub_target}")
                call_stack.append(pc + 1)
                pc = target

            elif op == "DIV":
                f1 = _numeric_operand(variables, instr.factor1)
                f2 = _numeric_operand(variables, instr.factor2)
                quotient = math.trunc(f1 / f2)
                last_div_remainder = f1 - quotient * f2
                _assign(variables, indicators, instr.result, float(quotient))
                pc += 1

            elif op == "MVR":
                _assign(variables, indicators, instr.result, last_div_remainder)
                pc += 1

            elif op == "EXFMT":
                fmt = next((f for n, f in dds.formats.items() if n.upper() == instr.format.upper()), None)
                if fmt is None:
                    raise RpgleError(f"Unknown DDS record format: {instr.format}")
                rendered = render_format(fmt, variables, indicators)

                resume = yield ScreenData(
                    format_name=fmt.name,
                    fields=rendered.fields,
                    cursor=rendered.cursor,
                    keys=fmt.keys,
                    input_map=rendered.input_map,
                )

                # Response indicators reset before applying this read's key,
                # scoped to only the indicators this format's own CF/CA
                # keywords own (real RPG behavior -- other formats' key
                # indicators are untouched).
                for num in fmt.keys.values():
                    indicators[num] = False
                ind_for_key = fmt.keys.get(resume.key) if resume else None
                if ind_for_key:
                    indicators[ind_for_key] = True

                for field_name in rendered.input_map.values():
                    if resume and resume.values and field_name in resume.values:
                        _assign(variables, indicators, field_name, resume.values[field_name])
                pc += 1

            elif op == "CLEAR":
                _clear(instr.expr, variables, ds_members)
                pc += 1

            elif op in FILE_OPS:
                files.exec(
                    instr,
                    variables,
                    lambda name, value: _assign(variables, indicators, name, value),
                    lambda num, on: indicators.__setitem__(num, on),
                    lambda src: eval_expr(src, ctx),
                )
                pc += 1

            elif op == "RETURN":
                return None

            else:
                raise RpgleError(f"Unimplemented RPG instruction: {op}")
        return None


def _run_program(
    prog: Program, dds: DDS, db: Optional[Database], pfs: Optional[dict[str, PhysicalFile]]
) -> Generator[ScreenData, Optional[Resume], None]:
    variables = build_vars(prog, dds, pfs)
    indicators: dict[str, bool] = {}
    compiled = compile_program(prog.statements)
    files = FileRuntime(prog, db, pfs, compiled.klists)
    ctx = Context(vars=variables, indicators=indicators, file_status=files.status)
    main = _Frame(
        instrs=compiled.instrs,
        subroutines=compiled.subroutines,
        variables=variables,
        ctx=ctx,
        ds_members=expand_ds(prog, dds, pfs),
    )
    yield from _Runtime(dds, indicators, files).exec(main)
