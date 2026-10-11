"""
rpgle
─────────────────────────────────────────────────────────────────
A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter, with a
runtime that needs no IBM i: fixed-form and free-form source, DISK files
on SQLite, /COPY includes, calls between programs and printer files.
From a terminal, `rpgle run program.rpgle` does it all (see the README).

The smallest use, a program with screens and no files:
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

from .database import Database, FileHandle
from .dds import DDS, Field, Format, IndicatorCond, parse_dds
from .errors import RpgleError
from .expr import ArrayVar, Context, Var, as_bool, as_float, as_string, eval_expr, split_assignment
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
from .include import expand_includes
from .loader import CallError, LoadedProgram, ProgramLoader, load_pfs
from .pf import PfField, PhysicalFile, parse_pf
from .prtf import PrintFile, parse_prtf
from .spool import Spool
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
    # database.py
    "Database",
    "FileHandle",
    # include.py
    "expand_includes",
    # loader.py
    "ProgramLoader",
    "LoadedProgram",
    "CallError",
    "load_pfs",
    # prtf.py / spool.py
    "PrintFile",
    "parse_prtf",
    "Spool",
    # pf.py
    "PhysicalFile",
    "PfField",
    "parse_pf",
    # expr.py
    "Context",
    "Var",
    "ArrayVar",
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
