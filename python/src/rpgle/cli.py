"""
cli.py
─────────────────────────────────────────────────────────────────
The `rpgle` command: run an RPG IV program from a terminal.

    rpgle run prog.rpgle [--dspf screen.dspf]

The library itself never touches a terminal; it yields plain screen
data at every EXFMT. This module is the smallest useful front end on
top of that: it draws each screen as a 24x80 grid, asks for whatever
the format's input fields need, and sends the answer back. No 5250
datastream, no curses, just stdin and stdout, so it works over SSH,
in CI, and in a pipe.

Typing a function key (F3, F12, ...) at any prompt sends that key
instead of Enter, as long as the current format declares it.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Optional, TextIO

from .dds import DDS, parse_dds
from .errors import RpgleError
from .interpreter import Resume, ScreenData, run_program
from .rpgle import parse_rpgle

ROWS, COLS = 24, 80
_FKEY = re.compile(r"^F(\d{1,2})$", re.IGNORECASE)


def render_grid(screen: ScreenData) -> str:
    """Draws the screen as a boxed ROWS x COLS grid, using the same
    row/col positions a real terminal would. The DDS parser already
    converts to 0-based, so they index the grid directly.
    """
    grid = [[" "] * COLS for _ in range(ROWS)]
    for f in screen.fields:
        for i, ch in enumerate(f.text):
            r, c = f.row, f.col + i
            if 0 <= r < ROWS and 0 <= c < COLS:
                grid[r][c] = ch
    border = "+" + "-" * (COLS + 2) + "+"
    body = ["| " + "".join(row) + " |" for row in grid]
    return "\n".join([border, *body, border])


def _key_hint(screen: ScreenData) -> str:
    if not screen.keys:
        return ""
    return "Keys: Enter, " + ", ".join(sorted(screen.keys, key=_key_order))


def _key_order(k: str) -> int:
    m = _FKEY.match(k)
    return int(m.group(1)) if m else 99


def _input_fields(screen: ScreenData) -> list[tuple[str, str]]:
    """(field name, current text) for every input field, in screen order."""
    out = []
    for f in screen.fields:
        if f.input and f.row in screen.input_map:
            out.append((screen.input_map[f.row], f.text.rstrip()))
    return out


def _as_function_key(line: str, screen: ScreenData) -> Optional[str]:
    m = _FKEY.match(line.strip())
    if not m:
        return None
    key = "F" + str(int(m.group(1)))
    return key if key in screen.keys else None


def drive(prog, dds: DDS, stdin: TextIO, stdout: TextIO) -> int:
    """Runs prog to completion against the given streams. Returns a
    process exit code: 0 when the program ends or input runs out.
    """

    def out(s: str = "") -> None:
        print(s, file=stdout)

    gen = run_program(prog, dds)
    try:
        screen = next(gen)
    except StopIteration:
        out("(program ended)")
        return 0

    while True:
        out(render_grid(screen))
        hint = _key_hint(screen)
        if hint:
            out(hint)

        key = "ENTER"
        values: dict[str, str] = {}
        fields = _input_fields(screen)

        prompts = fields or [("", "")]
        for name, current in prompts:
            label = f"{name} [{current}]" if name else "Enter"
            stdout.write(f"{label}> ")
            stdout.flush()
            line = stdin.readline()
            if line == "":
                out("\n(end of input)")
                return 0
            line = line.rstrip("\n")
            fkey = _as_function_key(line, screen)
            if fkey:
                key = fkey
                break
            if name:
                values[name] = line.strip() if line.strip() else current

        try:
            screen = gen.send(Resume(key=key, values=values))
        except StopIteration:
            out("(program ended)")
            return 0


def _load_dds(rpg_path: pathlib.Path, dspf_arg: Optional[str]) -> DDS:
    if dspf_arg:
        return parse_dds(pathlib.Path(dspf_arg).read_text())
    sibling = rpg_path.with_suffix(".dspf")
    if sibling.exists():
        return parse_dds(sibling.read_text())
    return parse_dds("")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rpgle", description="Run RPG IV programs without an IBM i.")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a fixed-form RPG IV source file")
    run.add_argument("source", help="path to the .rpgle file")
    run.add_argument("--dspf", help="display file DDS (default: same name with .dspf, if it exists)")
    args = ap.parse_args(argv)

    rpg_path = pathlib.Path(args.source)
    try:
        prog = parse_rpgle(rpg_path.read_text())
        dds = _load_dds(rpg_path, args.dspf)
        return drive(prog, dds, sys.stdin, sys.stdout)
    except (OSError, RpgleError) as e:
        print(f"rpgle: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
