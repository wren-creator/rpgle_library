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
EVAL, IF/ELSEIF/ELSE/ENDIF, DOW, DOU, FOR, ITER, LEAVE,
SELECT/WHEN/OTHER/ENDSL, EXSR/BEGSR/ENDSR, EXFMT, DIV/MVR, RETURN,
CALLP and procedures, CALL/PARM/PLIST between programs, CLEAR, SORTA,
and the file opcodes (CHAIN, SETLL, SETGT, READ, READE, READP, READPE,
WRITE, UPDATE, DELETE, plus KLIST/KFLD) which live in fileops.py and
cover DISK and PRINTER files. Expression evaluation, the built-in
functions and arrays live in expr.py.

The run loop is _Runtime.exec(frame): one generator per running body of
code (the main line, each procedure call, each called program), so
recursion gets fresh locals and a screen shown deep in a call still
reaches whoever is driving run_program.

DIV/MVR (not EVAL-expression division) is the classic RPG idiom for
modulo: RPG has no %REM until well past V4R3, and relying on
intermediate-expression truncation inside a compound EVAL expression is
not unambiguous real RPG behavior. DIV followed immediately by MVR is
the classic, unambiguous way to get a remainder, real since RPG's
earliest versions.
"""

from __future__ import annotations

import copy
import math
import re
from collections import ChainMap
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from .dds import DDS, Format
from .errors import RpgleError
from .fileops import FILE_OPS, FileRuntime
from .database import Database
from .pf import PhysicalFile
from .prtf import PrintFile
from .spool import Spool
from .expr import ArrayVar, Context, Var, as_bool, as_float, as_string, eval_call, eval_expr, resolve_target, split_assignment
from .loader import CallError, LoadedProgram, ProgramLoader, program_key
from .rpgle import DSpec, DsSpec, ParamSpec, ProcSpec, Program, ProtoSpec
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


def _ds_layout(dss: list[DsSpec], ds: DsSpec, dds: DDS, pfs: Optional[dict[str, PhysicalFile]], seen=()) -> list[DSpec]:
    """The subfields a data structure ends up with, wherever they come from."""
    if ds.likeds:
        other = next((d for d in dss if d.name.lower() == ds.likeds.lower()), None)
        if other is None or other.name.lower() in seen or other.name.lower() == ds.name.lower():
            raise RpgleError(f"{ds.name}: LIKEDS({ds.likeds}) must name a data structure declared earlier")
        return _ds_layout(dss, other, dds, pfs, seen + (ds.name.lower(),))
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
    prog: Program,
    dds: DDS,
    pfs: Optional[dict[str, PhysicalFile]] = None,
    local: Optional[list[DsSpec]] = None,
) -> dict[str, list[tuple[str, DSpec]]]:
    """Every data structure's variables: {ds name (lower): [(variable
    name (lower), the subfield's DSpec), ...]}. A qualified DS's
    variables are named `ds.sub`; an unqualified one's are just `sub`.
    With `local` (a procedure's own data structures), returns just
    those, which may borrow layouts from the program's.
    """
    out: dict[str, list[tuple[str, DSpec]]] = {}
    visible = [*prog.dss, *(local or [])]
    for ds in local if local is not None else prog.dss:
        if ds.dim:
            continue  # an array of data structures is one ArrayVar, see _new_ds_array
        prefix = ds.name.lower() + "." if ds.qualified else ""
        out[ds.name.lower()] = [(prefix + sub.name.lower(), sub) for sub in _ds_layout(visible, ds, dds, pfs)]
    return out


def _new_ds_array(prog: Program, ds: DsSpec, dds: DDS, pfs, visible: list[DsSpec]) -> ArrayVar:
    """An array of data structures: one {subfield: Var} per element."""
    layout = _ds_layout(visible, ds, dds, pfs)
    return ArrayVar([{sub.name.lower(): _new_var(sub) for sub in layout} for _ in range(ds.dim)])


def _new_var(d: DSpec) -> Any:
    """A fresh variable for a declaration: a Var, or an ArrayVar when it has dim()."""
    if d.dim:
        scalar = DSpec(name=d.name, deftype=d.deftype, length=d.length, dec=d.dec, init=d.init, kind=d.kind)
        return ArrayVar([_new_var(scalar) for _ in range(d.dim)])
    if d.dec is None:
        init = d.init.strip("'") if d.init is not None else ""
        value = init[: d.length] if d.kind == "varchar" else pad_end_trunc(init, d.length)
        return Var(type="A", length=d.length, dec=0, value=value, kind=d.kind)
    return Var(type="N", length=d.length, dec=d.dec, value=float(d.init) if d.init is not None else 0.0)


def build_vars(
    prog: Program,
    dds: DDS,
    pfs: Optional[dict[str, PhysicalFile]] = None,
    prtfs: Optional[dict[str, PrintFile]] = None,
) -> dict[str, Var]:
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

    for fs in prog.files:
        defs = (prtfs or {}).get((fs.name or "").upper())
        if defs is None or (fs.device or "").upper() != "PRINTER":
            continue
        for fmt in defs.formats.values():
            for f in fmt.fields:
                if f.kind != "field" or f.name.lower() in variables:
                    continue
                variables[f.name.lower()] = (
                    Var(type="A", length=f.length, dec=0, value=" " * f.length)
                    if f.dtype == "A"
                    else Var(type="N", length=f.length, dec=f.dec, value=0.0)
                )

    for d in prog.dspecs:
        if d.deftype != "S":
            continue
        variables[d.name.lower()] = _new_var(d)

    for members in expand_ds(prog, dds, pfs).values():
        for key, sub in members:
            variables.setdefault(key, _new_var(sub))
    for ds in prog.dss:
        if ds.dim:
            variables.setdefault(ds.name.lower(), _new_ds_array(prog, ds, dds, pfs, prog.dss))

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
    params: list[str] = field(default_factory=list)  # CALL: the PARM fields that followed it


@dataclass
class Compiled:
    """A jump-resolved instruction array plus subroutine entry points."""

    instrs: list[Instr]
    subroutines: dict[str, int]
    klists: dict[str, list[str]] = field(default_factory=dict)  # KLIST name -> KFLD field names
    entry_params: list[str] = field(default_factory=list)  # fixed-form *ENTRY PLIST: the parameters a caller passes
    plists: dict[str, list[str]] = field(default_factory=dict)  # PLIST name -> PARM field names


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
    plists: dict[str, list[str]] = {}
    entry_params: list[str] = []
    current_plist: Optional[list[str]] = None  # between a PLIST and its PARMs
    last_call: Optional[Instr] = None  # a CALL whose PARMs may follow

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
            instrs.append(Instr(op="RETURN", expr=stmt.expr))

        elif op == "CALLP":
            instrs.append(Instr(op="CALLP", expr=stmt.expr))

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

        elif op == "SORTA":
            instrs.append(Instr(op="SORTA", expr=stmt.expr, down=stmt.down))

        elif op == "KLIST":
            current_klist = (stmt.factor1 or "").upper()
            klists[current_klist] = []

        elif op == "KFLD":
            if current_klist is None:
                raise RpgleError("KFLD with no KLIST before it")
            klists[current_klist].append(stmt.result)

        elif op == "PLIST":
            name = (stmt.factor1 or "").upper()
            if not name:
                raise RpgleError("PLIST needs a name in factor 1 (*ENTRY for the program's own parameters)")
            current_plist = entry_params if name == "*ENTRY" else plists.setdefault(name, [])

        elif op == "PARM":
            if stmt.factor1 or stmt.factor2:
                raise RpgleError("PARM with a factor 1 or factor 2 is not supported; name the field in the result column")
            if not stmt.result:
                raise RpgleError("PARM needs a field name in the result column")
            target = last_call.params if last_call is not None else current_plist
            if target is None:
                raise RpgleError("PARM with no CALL or PLIST before it")
            target.append(stmt.result)

        elif op == "CALL":
            if not stmt.factor2:
                raise RpgleError("CALL needs a program name in factor 2: CALL 'PROGRAM'")
            instrs.append(
                Instr(op="CALL", factor2=stmt.factor2, result=stmt.result, ind_lo=stmt.ind_lo, ind_eq=stmt.ind_eq)
            )

        else:
            raise RpgleError(f"Unsupported RPG opcode: {op}")

        for ins in instrs[start:]:
            ins.cond = cond
        if op != "PARM":  # PARMs belong to the CALL or PLIST just before them, nothing further back
            last_call = instrs[-1] if op == "CALL" else None
            if op != "PLIST":
                current_plist = None

    # A block left open has jump targets that were never filled in; running it
    # would crash on a missing address, so say so now.
    if if_stack:
        raise RpgleError("IF with no ENDIF")
    if dow_stack:
        kind = dow_stack[-1]["kind"]
        raise RpgleError(f"{kind} loop with no {'ENDFOR' if kind == 'FOR' else 'ENDDO'}")
    if sel_stack:
        raise RpgleError("SELECT with no ENDSL")
    return Compiled(
        instrs=instrs, subroutines=subroutines, klists=klists, entry_params=entry_params, plists=plists
    )


# Ops that expand into jumps, so skipping one on a condition would
# desynchronize the jump targets. Real RPG allows conditioning these;
# here it's rejected loudly rather than run wrong.
_NO_COND_OPS = frozenset(
    {"IF", "ELSE", "ELSEIF", "ENDIF", "DOW", "DOU", "FOR", "ENDDO", "ENDFOR", "SELECT", "WHEN", "OTHER", "ENDSL", "BEGSR", "ENDSR", "KLIST", "KFLD", "PLIST", "PARM"}
)
_COND_RE = re.compile(r"^(N?)(\d\d|LR|O[A-GV])$", re.IGNORECASE)


def _parse_cond(raw: Optional[str]) -> Optional[tuple[bool, str]]:
    if not raw:
        return None
    m = _COND_RE.match(raw)
    if not m:
        raise RpgleError(f"Unsupported conditioning indicator: {raw!r}")
    return (m.group(1).upper() == "N", m.group(2).upper())


_NUM_LIT_RE = re.compile(r"^-?\d+(\.\d+)?$")
_ASSIGN_IND_RE = re.compile(r"^\*IN(\d\d|O[A-GV])$", re.IGNORECASE)
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
        indicators[m.group(1).upper()] = as_bool(rvalue)
        return
    if _ASSIGN_INLR_RE.match(lvalue):
        indicators["LR"] = as_bool(rvalue)
        return
    key = lvalue.strip().lower()
    v = variables.get(key)
    if v is None:
        raise RpgleError(f"Unknown RPG variable on assignment: {lvalue}")
    _store(v, rvalue, lvalue.strip())


def _store(v: Var, rvalue: Any, name: str = "") -> None:
    """Puts a value into a variable the way RPG assignment does: padded
    or truncated to a character field's length, truncated (not rounded)
    to a number's decimals.
    """
    if v.kind == "ind":
        v.value = "1" if as_bool(rvalue) else "0"
    elif v.kind == "varchar":
        v.value = as_string(rvalue)[: v.length]  # no padding: its length is its content
    elif v.type == "A":
        v.value = pad_end_trunc(as_string(rvalue), v.length)
    else:
        # Truncate-on-assign (no rounding) -- the real RPG default, and
        # what DIV/MVR-based modulo relies on for boundedness.
        number = as_float(rvalue)
        if math.isnan(number) or math.isinf(number):
            what = f"{rvalue!r}" if isinstance(rvalue, str) else "that value"
            raise RpgleError(f"{what} is not a number, so it can't be put in the numeric field{' ' + name if name else ''}")
        factor = 10**v.dec
        v.value = math.trunc(number * factor) / factor


def _eval_assign(lvalue: str, rvalue_src: str, ctx: Context, variables: Any, indicators: dict[str, bool]) -> None:
    """EVAL / assignment. Handles plain variables and indicators, array
    elements and subfields (arr(i), items(i).qty), and whole arrays.
    """
    target = None
    if "(" in lvalue:
        target = resolve_target(lvalue, ctx)
    else:
        v = variables.get(lvalue.strip().lower())
        if isinstance(v, ArrayVar):
            target = v
    if target is None:
        _assign(variables, indicators, lvalue, eval_expr(rvalue_src, ctx))
    elif isinstance(target, ArrayVar):
        _assign_array(target, lvalue, rvalue_src, ctx)
    else:
        _store(target, eval_expr(rvalue_src, ctx))


def _assign_array(target: ArrayVar, lvalue: str, rvalue_src: str, ctx: Context) -> None:
    """arr = other (same size), or arr = scalar / *BLANKS / *ZEROS (every element)."""
    if any(isinstance(e, dict) for e in target.elems):
        raise RpgleError(f"{lvalue}: assigning a whole array of data structures is not supported")
    other = ctx.vars.get(rvalue_src.strip().lower())
    if isinstance(other, ArrayVar):
        if len(other.elems) != len(target.elems):
            raise RpgleError(f"{lvalue} has {len(target.elems)} elements but {rvalue_src.strip()} has {len(other.elems)}")
        for dst, src in zip(target.elems, other.elems):
            _store(dst, src.value == "1" if src.kind == "ind" else src.value)
        return
    value = eval_expr(rvalue_src, ctx)
    for e in target.elems:
        _store(e, value)


def _clear_var(v: Var) -> None:
    if v.kind == "ind":
        v.value = "0"
    elif v.type == "A":
        v.value = "" if v.kind == "varchar" else " " * v.length
    else:
        v.value = 0.0


def _clear(
    target: str, variables: Any, ds_members: dict[str, list[tuple[str, DSpec]]], ctx: Context
) -> None:
    """CLEAR: character fields to blanks, numbers to zero, indicators to
    off. Naming a data structure or an array clears everything in it.
    """
    name = target.strip().lower()
    if "(" in name:
        found = resolve_target(target, ctx, whole_ds=True)
        for v in found.values() if isinstance(found, dict) else [found]:
            _clear_var(v)
        return
    keys = [k for k, _ in ds_members[name]] if name in ds_members else [name]
    for key in keys:
        v = variables.get(key)
        if v is None:
            raise RpgleError(f"CLEAR: unknown variable {target}")
        if isinstance(v, ArrayVar):
            for e in v.elems:
                for item in (e.values() if isinstance(e, dict) else [e]):
                    _clear_var(item)
        else:
            _clear_var(v)


def _sort_array(arr: Any, name: str, descending: bool) -> None:
    if not isinstance(arr, ArrayVar):
        raise RpgleError(f"SORTA: {name} is not an array")
    if any(isinstance(e, dict) for e in arr.elems):
        raise RpgleError("SORTA: sorting an array of data structures is not supported yet")
    values = sorted(
        (e.value for e in arr.elems),
        key=lambda v: v.rstrip() if isinstance(v, str) else v,
        reverse=descending,
    )
    for e, v in zip(arr.elems, values):
        e.value = v


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
    # Names of the input fields that hold numbers, so a front end can refuse
    # letters as they are typed (a 5250 never lets you type them).
    numeric_fields: frozenset = frozenset()


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
    loader: Optional[ProgramLoader] = None,
    spool: Optional[Spool] = None,
) -> Generator[ScreenData, Optional[Resume], None]:
    """Runs the program. Yields a ScreenData on every EXFMT; resume
    with .send(Resume(key, values)) where key is whatever function key
    the caller recognizes (matched against the format's own
    CFnn/CAnn-declared keys) and values is {field_name: typed_text} for
    every input field on the format just shown.

    A program with DISK files needs db (a Database) and pfs, a dict of
    upper-case file name -> PhysicalFile (see pf.parse_pf). Changes are
    committed when the program ends or the generator is closed.

    A program that calls other programs needs a loader
    (loader.ProgramLoader), which finds them by name in its folders. A
    program that prints needs a spool (spool.Spool), which collects the
    output of every printer file; read it with spool.outputs().
    """
    try:
        yield from _run_program(prog, dds, db, pfs, loader, spool)
    finally:
        if db is not None:
            db.commit()


def _py_value(v: Any) -> Any:
    """A Var's value as the expression evaluator would see it."""
    return v.value == "1" if getattr(v, "kind", "") == "ind" else getattr(v, "value", None)


def _bind_args(
    who: str, params: list[ParamSpec], args: list, error: type = RpgleError, lenient_ref: bool = False
) -> dict[str, Any]:
    """Binds a call's arguments to the callee's parameters, for both
    procedures and programs. Each argument is (value, Var or None); the
    Var is there when the caller passed a plain variable. Returns
    {parameter name (lower): the Var or ArrayVar the callee will use}.

    By reference (the default) aliases the caller's own variable, so the
    callee's changes show through. const aliases it too, or holds a
    temporary when given an expression; value takes a private copy.
    lenient_ref lets a by-reference parameter take an expression too (the
    callee just works on a temporary), for old fixed-form programs whose
    parameter lists can't say const.
    """
    if len(args) > len(params):
        raise error(f"{who} takes {len(params)} parameter(s), got {len(args)}")
    bound: dict[str, Any] = {}
    for i, p in enumerate(params):
        key = p.spec.name.lower()
        if i >= len(args):
            if not p.nopass:
                raise error(f"{who}: missing argument for parameter {p.spec.name}")
            bound[key] = _new_var(p.spec)
            continue
        value, var = args[i]
        want = "A" if p.spec.dec is None else "N"
        if p.spec.dim:
            if not isinstance(var, ArrayVar):
                raise error(f"{who}: parameter {p.spec.name} is an array: pass a whole array")
            if len(var.elems) != p.spec.dim:
                raise error(
                    f"{who}: parameter {p.spec.name} is dim({p.spec.dim}) but the array passed has {len(var.elems)} elements"
                )
            bound[key] = copy.deepcopy(var) if p.mode == "value" else var
            continue
        if isinstance(var, ArrayVar):
            raise error(f"{who}: parameter {p.spec.name} takes a single value, not an array")
        if p.mode == "value" or (p.mode == "const" and var is None) or (lenient_ref and var is None):
            bound[key] = _new_var(p.spec)  # a private copy (or a temporary for an expression)
            _store(bound[key], value)
        elif var is None:
            raise error(
                f"{who}: parameter {p.spec.name} is passed by reference, so the argument must be a "
                "variable (declare the parameter const to pass an expression)"
            )
        elif var.type != want:
            raise error(f"{who}: parameter {p.spec.name} expects {'text' if want == 'A' else 'a number'}")
        else:
            bound[key] = var  # the caller's own variable: changes show through
    return bound


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
    ds_prefix: dict[str, str] = field(default_factory=dict)  # data structure -> "name." if qualified
    plists: dict[str, list[str]] = field(default_factory=dict)  # fixed-form PLISTs a CALL can name
    call_stack: list[int] = field(default_factory=list)  # EXSR return addresses
    for_state: dict[int, tuple[float, float]] = field(default_factory=dict)  # FORTEST index -> (limit, by)


class _Calls:
    """State shared by a program and every program it calls, directly or not."""

    def __init__(self, loader: Optional[ProgramLoader], db: Optional[Database], spool: Optional[Spool] = None):
        self.loader = loader
        self.db = db
        self.spool = spool
        # Programs currently running, outermost first. RPG programs can't
        # call themselves, directly or through another program.
        self.active: list[str] = [loader.main_name] if loader is not None and loader.main_name else []
        # Programs that ended with *INLR off. Like the real thing they stay
        # active: variables, file positions and indicators survive to the next call.
        self.retained: dict[str, "_Instance"] = {}


@dataclass
class _Instance:
    rt: "_Runtime"
    compiled: Compiled
    loaded: LoadedProgram

    @property
    def globals(self) -> dict[str, Any]:
        return self.rt.globals

    @property
    def indicators(self) -> dict[str, bool]:
        return self.rt.indicators


class _Runtime:
    """State shared by every frame of one program run."""

    def __init__(
        self,
        prog: Program,
        dds: DDS,
        pfs: Optional[dict[str, PhysicalFile]],
        globals_: dict[str, Var],
        indicators: dict[str, bool],
        files: FileRuntime,
        procs: dict[str, tuple[ProcSpec, Compiled]],
        calls: Optional[_Calls] = None,
        directory: Optional[Path] = None,
    ):
        self.calls = calls
        self.directory = directory  # this program's folder: where its CALLs look first
        self.protos = prog.protos
        self.prog = prog
        self.dds = dds
        self.pfs = pfs
        self.globals = globals_
        self.indicators = indicators  # global, as in RPG: *INnn and *INLR are shared by every procedure
        self.files = files
        self.procs = procs
        self.global_ds = expand_ds(prog, dds, pfs)
        self.global_prefix = {d.name.lower(): (d.name.lower() + "." if d.qualified else "") for d in prog.dss if not d.dim}

    # ── procedures ───────────────────────────────────────────────

    def context(self, variables: Any) -> Context:
        return Context(
            vars=variables,
            indicators=self.indicators,
            file_status=self.files.status,
            has_proc=lambda name: name.lower() in self.procs or name.lower() in self.protos,
            call_proc=self.call_value,
        )

    def main_frame(self, compiled: Compiled) -> _Frame:
        return _Frame(
            instrs=compiled.instrs,
            subroutines=compiled.subroutines,
            variables=self.globals,
            ctx=self.context(self.globals),
            ds_members=self.global_ds,
            ds_prefix=self.global_prefix,
            plists=compiled.plists,
        )

    def _proc_frame(self, name: str, args: list) -> tuple[ProcSpec, _Frame]:
        entry = self.procs.get(name.lower())
        if entry is None:
            raise RpgleError(f"Unknown procedure: {name}")
        proc, compiled = entry
        local = _bind_args(proc.name, proc.params, args)

        for d in proc.dspecs:
            local[d.name.lower()] = _new_var(d)
        local_ds = expand_ds(self.prog, self.dds, self.pfs, local=proc.dss)
        for members in local_ds.values():
            for key, sub in members:
                local[key] = _new_var(sub)
        for d in proc.dss:
            if d.dim:
                local[d.name.lower()] = _new_ds_array(self.prog, d, self.dds, self.pfs, [*self.prog.dss, *proc.dss])

        variables = ChainMap(local, self.globals)  # locals shadow globals
        return proc, _Frame(
            instrs=compiled.instrs,
            subroutines=compiled.subroutines,
            variables=variables,
            ctx=self.context(variables),
            ds_members={**self.global_ds, **local_ds},
            ds_prefix={
                **self.global_prefix,
                **{d.name.lower(): (d.name.lower() + "." if d.qualified else "") for d in proc.dss if not d.dim},
            },
        )

    @staticmethod
    def _result(proc: ProcSpec, value: Any) -> Any:
        """The RETURN value, converted to the procedure's declared type."""
        if proc.returns is None:
            return None
        if value is None:
            raise RpgleError(f"{proc.name} must RETURN a value")
        v = _new_var(proc.returns)
        _store(v, value)
        return v.value == "1" if v.kind == "ind" else v.value

    def call_value(self, name: str, args: list) -> Any:
        """Calls a procedure from inside an expression. Expressions can't
        pause for the screen, so a procedure called this way can't EXFMT.
        """
        if name.lower() not in self.procs:
            raise RpgleError(f"{name} is another program, which can't be called from inside an expression; use CALLP")
        proc, frame = self._proc_frame(name, args)
        gen = self.exec(frame)
        try:
            next(gen)
        except StopIteration as done:
            return self._result(proc, done.value)
        except RecursionError:
            raise RpgleError(f"{proc.name}: calls nested too deeply") from None
        gen.close()
        raise RpgleError(
            f"{proc.name} shows a screen (EXFMT), so it can't be called from inside an expression; "
            "call it with CALLP instead"
        )

    def call_program(self, target: str, args: list, lenient_ref: bool = False) -> Generator[ScreenData, Optional[Resume], bool]:
        """Calls another program by name and returns whether it ended with
        *INLR on. Raises CallError if the call can't be made (the CALL's
        error indicator catches that); anything raised while the called
        program runs is the program's own, and propagates.
        """
        calls = self.calls
        if calls is None or calls.loader is None:
            raise CallError("CALL needs a program loader: pass loader=ProgramLoader(...) to run_program, or use the rpgle command")
        key = program_key(target)
        if key in calls.active:
            raise CallError(f"{key} is already running: an RPG program can't call itself, directly or through another program")
        loaded = calls.loader.load(key, self.directory)
        inst = calls.retained.get(key)
        if inst is None:
            inst = _new_instance(loaded, calls)

        if loaded.prog.main_params:
            params = loaded.prog.main_params
        else:  # fixed-form: *ENTRY PLIST names variables the program declared itself
            params = [_param_for(n, inst.globals) for n in inst.compiled.entry_params]
        try:
            bound = _bind_args(key, params, args, error=CallError, lenient_ref=lenient_ref or not loaded.prog.main_params)
        except CallError:
            raise
        inst.globals.update(bound)

        calls.active.append(key)
        try:
            yield from inst.rt.exec(inst.rt.main_frame(inst.compiled))
        except BaseException:
            calls.retained.pop(key, None)  # whatever state it was in is no longer trustworthy
            raise
        finally:
            calls.active.pop()
        last_record = bool(inst.indicators.get("LR", False))
        if last_record:
            calls.retained.pop(key, None)  # closed: the next call starts it afresh
        else:
            calls.retained[key] = inst
        return last_record

    def call_stmt(self, name: str, args: list) -> Generator[ScreenData, Optional[Resume], Any]:
        """Calls a procedure as a statement (CALLP). Screens it shows
        reach the caller of run_program, same as the main line's.
        """
        proc, frame = self._proc_frame(name, args)
        try:
            value = yield from self.exec(frame)
        except RecursionError:
            raise RpgleError(f"{proc.name}: calls nested too deeply") from None
        return self._result(proc, value) if proc.returns is not None and value is not None else None

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
                _eval_assign(lvalue, rvalue_src, ctx, variables, indicators)
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
                if f2 == 0:
                    raise RpgleError("DIV: division by zero")
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
                    numeric_fields=frozenset(
                        n for n in rendered.input_map.values() if getattr(variables.get(n), "type", "") == "N"
                    ),
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
                        typed = resume.values[field_name]
                        if variables[field_name].type == "N" and not str(typed).strip():
                            typed = "0"  # a blank numeric field reads as zero
                        _assign(variables, indicators, field_name, typed)
                pc += 1

            elif op == "SORTA":
                _sort_array(variables.get(instr.expr.lower()), instr.expr, instr.down)
                pc += 1

            elif op == "CLEAR":
                _clear(instr.expr, variables, ds_members, ctx)
                pc += 1

            elif op in FILE_OPS:
                files.exec(
                    instr,
                    variables,
                    lambda name, value: _assign(variables, indicators, name, value),
                    lambda num, on: indicators.__setitem__(num, on),
                    lambda src: eval_expr(src, ctx),
                    frame.ds_prefix,
                    lambda num: indicators.get(num, False),
                )
                pc += 1

            elif op == "CALL":
                target = instr.factor2.strip()
                if target.startswith("'"):
                    program = target.strip("'")
                else:  # CALL PGMVAR: the name is in a variable
                    program = as_string(eval_expr(target, ctx)).strip()
                names = instr.params
                if instr.result and not names:
                    names = frame.plists.get(instr.result.upper())
                    if names is None:
                        raise RpgleError(f"CALL names PLIST {instr.result}, which is not defined")
                args = []
                for n in names:
                    v = variables.get(n.lower())
                    if v is None:
                        raise RpgleError(f"PARM field {n} is not declared")
                    args.append((_py_value(v), v))
                try:
                    last_record = yield from self.call_program(program, args, lenient_ref=True)
                    failed = False
                except CallError:
                    if not instr.ind_lo:
                        raise
                    last_record, failed = False, True
                for ind, on in ((instr.ind_lo, failed), (instr.ind_eq, last_record)):
                    if ind:
                        indicators[ind] = on
                pc += 1

            elif op == "CALLP":
                name, args = eval_call(instr.expr, ctx)
                low = name.lower()
                if low in self.procs:
                    yield from self.call_stmt(name, args)
                elif low in self.protos:
                    proto = self.protos[low]
                    if proto.extpgm is None:
                        raise RpgleError(f"{name}: external procedures (extproc) are not supported, only programs (extpgm)")
                    yield from self.call_program(proto.extpgm, args)
                else:
                    raise RpgleError(f"Unknown procedure: {name} (declare it with dcl-proc, or dcl-pr ... extpgm for another program)")
                pc += 1

            elif op == "RETURN":
                return eval_expr(instr.expr, ctx) if instr.expr else None

            else:
                raise RpgleError(f"Unimplemented RPG instruction: {op}")
        return None


def _param_for(name: str, variables: dict[str, Any]) -> ParamSpec:
    """A fixed-form program's *ENTRY PLIST names variables it declares; the
    parameter's type and length are whatever that variable is.
    """
    v = variables.get(name.lower())
    if not isinstance(v, Var):
        raise RpgleError(f"PARM field {name} is not declared as a standalone field (D-spec)")
    spec = DSpec(name=name, deftype="S", length=v.length, dec=None if v.type == "A" else v.dec, kind=v.kind)
    return ParamSpec(spec=spec, mode="ref", nopass=True)


def _new_instance(loaded: LoadedProgram, calls: _Calls) -> _Instance:
    """Builds a program ready to run: its variables, files and procedures."""
    prog = loaded.prog
    try:
        prtfs = calls.spool.load_defs(prog, [loaded.path.parent]) if calls.spool is not None else {}
        variables = build_vars(prog, loaded.dds, loaded.pfs, prtfs)
        indicators: dict[str, bool] = {}
        compiled = compile_program(prog.statements)
        files = FileRuntime(prog, calls.db, loaded.pfs, compiled.klists, spool=calls.spool, prtfs=prtfs)
        rt = _Runtime(
            prog, loaded.dds, loaded.pfs, variables, indicators, files, compile_procs(prog.procs),
            calls=calls, directory=loaded.path.parent,
        )
    except RpgleError as e:
        raise RpgleError(f"{loaded.key}: {e}") from None
    return _Instance(rt=rt, compiled=compiled, loaded=loaded)


def compile_procs(procs: list[ProcSpec]) -> dict[str, tuple[ProcSpec, Compiled]]:
    """Compiles each procedure body separately: its own instruction
    array and subroutine table, so subroutine names are local to it.
    """
    out: dict[str, tuple[ProcSpec, Compiled]] = {}
    for p in procs:
        out[p.name.lower()] = (p, compile_program(p.statements))
    return out


def _run_program(
    prog: Program,
    dds: DDS,
    db: Optional[Database],
    pfs: Optional[dict[str, PhysicalFile]],
    loader: Optional[ProgramLoader] = None,
    spool: Optional[Spool] = None,
) -> Generator[ScreenData, Optional[Resume], None]:
    prtfs = spool.load_defs(prog) if spool is not None else {}
    variables = build_vars(prog, dds, pfs, prtfs)
    indicators: dict[str, bool] = {}
    compiled = compile_program(prog.statements)
    files = FileRuntime(prog, db, pfs, compiled.klists, spool=spool, prtfs=prtfs)
    rt = _Runtime(
        prog, dds, pfs, variables, indicators, files, compile_procs(prog.procs),
        calls=_Calls(loader, db, spool), directory=loader.dirs[0] if loader is not None and loader.dirs else None,
    )
    yield from rt.exec(rt.main_frame(compiled))
