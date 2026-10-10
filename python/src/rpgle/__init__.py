"""
rpgle
─────────────────────────────────────────────────────────────────
A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter.

Quick start:
    from rpgle import parse_dds, parse_rpgle, run_program, Resume

    dds = parse_dds(dspf_source)
    prog = parse_rpgle(rpgle_source)
    gen = run_program(prog, dds)
    screen = next(gen)
    while True:
        print(screen.fields)
        try:
            screen = gen.send(Resume(key="ENTER", values={...}))
        except StopIteration:
            break
─────────────────────────────────────────────────────────────────
"""

from .dds import DDS, Field, Format, IndicatorCond, parse_dds
from .errors import RpgleError
from .expr import Context, Var, as_bool, as_float, as_string, eval_expr, split_assignment
from .interpreter import (
    Compiled,
    Cursor,
    Instr,
    RenderedField,
    RenderResult,
    Resume,
    ScreenData,
    build_vars,
    compile_program,
    render_format,
    run_program,
)
from .pf import PfField, PhysicalFile, parse_pf
from .rpgle import DSpec, FileSpec, Program, Statement, parse_rpgle

__all__ = [
    # dds.py
    "DDS",
    "Format",
    "Field",
    "IndicatorCond",
    "parse_dds",
    # rpgle.py
    "Program",
    "FileSpec",
    "DSpec",
    "Statement",
    "parse_rpgle",
    # pf.py
    "PhysicalFile",
    "PfField",
    "parse_pf",
    # expr.py
    "Context",
    "Var",
    "eval_expr",
    "split_assignment",
    "as_bool",
    "as_float",
    "as_string",
    # interpreter.py
    "build_vars",
    "compile_program",
    "render_format",
    "run_program",
    "Compiled",
    "Instr",
    "RenderedField",
    "Cursor",
    "RenderResult",
    "ScreenData",
    "Resume",
    # errors.py
    "RpgleError",
]
