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
EXSR/BEGSR/ENDSR, EXFMT, DIV/MVR, RETURN. Expression evaluation
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
from .expr import Context, Var, as_bool, as_float, as_string, eval_expr, split_assignment
from .rpgle import Program
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


def build_vars(prog: Program, dds: DDS) -> dict[str, Var]:
    """Builds the program's variable table: every named DDS field
    across every record format of the externally-described WORKSTN
    file becomes a program variable (first definition in source order
    wins on a name collision), plus D-spec standalone fields
    (definition type 'S' only).
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

    for d in prog.dspecs:
        if d.deftype != "S":
            continue
        key = d.name.lower()
        if d.dec is None:
            init = d.init.strip("'") if d.init is not None else ""
            variables[key] = Var(type="A", length=d.length, dec=0, value=pad_end_trunc(init, d.length))
        else:
            variables[key] = Var(type="N", length=d.length, dec=d.dec, value=float(d.init) if d.init is not None else 0.0)

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


@dataclass
class Compiled:
    """A jump-resolved instruction array plus subroutine entry points."""

    instrs: list[Instr]
    subroutines: dict[str, int]


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

    for stmt in statements:
        op = stmt.op

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

        elif op == "ENDIF":
            if not if_stack:
                raise RpgleError("ENDIF with no open IF")
            frame = if_stack.pop()
            idx = len(instrs)
            if frame["else_jmp_index"] is None:
                instrs[frame["if_index"]].false_target = idx
            else:
                instrs[frame["else_jmp_index"]].target = idx
            instrs.append(Instr(op="NOP"))

        elif op == "DOW":
            idx = len(instrs)
            instrs.append(Instr(op="DOW", expr=stmt.expr, false_target=None))
            dow_stack.append({"dow_index": idx, "leaves": []})

        elif op == "LEAVE":
            if not dow_stack:
                raise RpgleError("LEAVE with no open DOW")
            frame = dow_stack[-1]
            frame["leaves"].append(len(instrs))
            instrs.append(Instr(op="LEAVE", target=None))

        elif op == "ENDDO":
            if not dow_stack:
                raise RpgleError("ENDDO with no open DOW")
            frame = dow_stack.pop()
            idx = len(instrs)
            instrs[frame["dow_index"]].false_target = idx + 1
            for li in frame["leaves"]:
                instrs[li].target = idx + 1
            instrs.append(Instr(op="ENDDO", target=frame["dow_index"]))

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

        else:
            raise RpgleError(f"Unsupported RPG opcode: {op}")

    return Compiled(instrs=instrs, subroutines=subroutines)


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
    if v.type == "A":
        v.value = pad_end_trunc(as_string(rvalue), v.length)
    else:
        # Truncate-on-assign (no rounding) -- the real RPG default, and
        # what DIV/MVR-based modulo relies on for boundedness.
        factor = 10**v.dec
        v.value = math.trunc(as_float(rvalue) * factor) / factor


def _format_field_text(v: Var) -> str:
    if v.type == "A":
        return v.value
    n = v.value
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


def run_program(prog: Program, dds: DDS) -> Generator[ScreenData, Optional[Resume], None]:
    """Runs the program. Yields a ScreenData on every EXFMT; resume
    with .send(Resume(key, values)) where key is whatever function key
    the caller recognizes (matched against the format's own
    CFnn/CAnn-declared keys) and values is {field_name: typed_text} for
    every input field on the format just shown.
    """
    variables = build_vars(prog, dds)
    indicators: dict[str, bool] = {}
    compiled = compile_program(prog.statements)
    instrs = compiled.instrs
    subroutines = compiled.subroutines
    call_stack: list[int] = []
    pc = 0
    last_div_remainder = 0.0

    while pc < len(instrs):
        instr = instrs[pc]
        op = instr.op

        if op == "NOP":
            pc += 1

        elif op == "ENDSR":
            if not call_stack:
                raise RpgleError("ENDSR with no active EXSR call")
            pc = call_stack.pop()

        elif op == "EVAL":
            lvalue, rvalue_src = split_assignment(instr.expr)
            rvalue = eval_expr(rvalue_src, Context(vars=variables, indicators=indicators))
            _assign(variables, indicators, lvalue, rvalue)
            pc += 1

        elif op in ("IF", "WHEN"):
            cond = eval_expr(instr.expr, Context(vars=variables, indicators=indicators))
            pc = pc + 1 if as_bool(cond) else instr.false_target

        elif op == "OTHER":
            pc += 1

        elif op == "JMP":
            pc = instr.target

        elif op == "DOW":
            cond = eval_expr(instr.expr, Context(vars=variables, indicators=indicators))
            pc = pc + 1 if as_bool(cond) else instr.false_target

        elif op == "ENDDO":
            pc = instr.target

        elif op == "LEAVE":
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
            fmt = dds.formats.get(instr.format)
            if fmt is None:
                raise RpgleError(f"Unknown DDS record format: {instr.format}")
            rendered = render_format(fmt, variables, indicators)

            resume = yield ScreenData(
                format_name=instr.format,
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

        elif op == "RETURN":
            return

        else:
            raise RpgleError(f"Unimplemented RPG instruction: {op}")
