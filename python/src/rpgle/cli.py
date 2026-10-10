"""
cli.py
─────────────────────────────────────────────────────────────────
The `rpgle` command: run an RPG IV program from a terminal.

    rpgle run prog.rpgle [--dspf screen.dspf] [--db data.db] [--pf-dir DIR]
                         [-I includedir ...] [-D NAME ...]
    rpgle load custmast.pf customers.csv --db data.db

The library itself never touches a terminal; it yields plain screen
data at every EXFMT. This module is the smallest useful front end on
top of that: it draws each screen as a 24x80 grid, asks for whatever
the format's input fields need, and sends the answer back. No 5250
datastream, no curses, just stdin and stdout, so it works over SSH,
in CI, and in a pipe.

Typing a function key (F3, F12, ...) at any prompt sends that key
instead of Enter, as long as the current format declares it.

DISK files live in a SQLite database (--db). Each one is described by
a physical-file DDS named after the file, e.g. CUSTMAST is read from
custmast.pf in --pf-dir (default: next to the program). `rpgle load`
fills a file from a CSV whose header row names the fields.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import re
import sys
from typing import Optional, TextIO

from .database import Database
from .dds import DDS, parse_dds
from .errors import RpgleError
from .interpreter import Resume, ScreenData, run_program
from .pf import PhysicalFile, parse_pf
from .rpgle import Program, parse_rpgle

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


def drive(
    prog,
    dds: DDS,
    stdin: TextIO,
    stdout: TextIO,
    db: Optional[Database] = None,
    pfs: Optional[dict[str, PhysicalFile]] = None,
) -> int:
    """Runs prog to completion against the given streams. Returns a
    process exit code: 0 when the program ends or input runs out.
    """

    def out(s: str = "") -> None:
        print(s, file=stdout)

    gen = run_program(prog, dds, db, pfs)
    try:
        return _drive_loop(gen, stdin, stdout, out)
    finally:
        # Close the program (which commits its file changes) while the
        # caller still has the database open, not at garbage collection.
        gen.close()


def _drive_loop(gen, stdin: TextIO, stdout: TextIO, out) -> int:
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


def _find_pf(name: str, directory: pathlib.Path) -> pathlib.Path:
    for candidate in (name.lower(), name.upper(), name):
        p = directory / f"{candidate}.pf"
        if p.exists():
            return p
    raise RpgleError(f"No physical file DDS for {name}: expected {directory / (name.lower() + '.pf')}")


def _load_pfs(prog: Program, directory: pathlib.Path) -> dict[str, PhysicalFile]:
    pfs = {}
    for fs in prog.files:
        if (fs.device or "").upper() == "DISK":
            pfs[fs.name.upper()] = parse_pf(fs.name, _find_pf(fs.name, directory).read_text())
    return pfs


def load_csv(pf: PhysicalFile, db: Database, csv_path: pathlib.Path) -> int:
    """Appends every row of the CSV to the file. The header row must
    name every field of the record format (any order, any case).
    Returns the number of records written.
    """
    handle = db.open_file(pf)
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        header = {h.strip().upper(): h for h in (reader.fieldnames or [])}
        missing = [f.name for f in pf.fields if f.name not in header]
        if missing:
            raise RpgleError(f"{csv_path.name} is missing columns {missing} for {pf.name}")
        n = 0
        for row in reader:
            record = {}
            for f in pf.fields:
                raw = row[header[f.name]]
                try:
                    record[f.name] = float(raw) if f.dtype == "N" else raw
                except ValueError:
                    raise RpgleError(f"{csv_path.name} row {n + 2}: {f.name} is not a number: {raw!r}") from None
            handle.write(record)
            n += 1
    db.commit()
    return n


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="rpgle", description="Run RPG IV programs without an IBM i.")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a fixed-form RPG IV source file")
    run.add_argument("source", help="path to the .rpgle file")
    run.add_argument("--dspf", help="display file DDS (default: same name with .dspf, if it exists)")
    run.add_argument("--db", help="SQLite database holding the program's DISK files (required if it has any)")
    run.add_argument("--pf-dir", help="folder of <file>.pf DDS files (default: next to the program)")
    run.add_argument("-I", dest="include_dirs", action="append", default=[], metavar="DIR", help="folder to search for /COPY members (repeatable)")
    run.add_argument("-D", dest="defines", action="append", default=[], metavar="NAME", help="define NAME for /IF DEFINED(NAME) (repeatable)")
    load = sub.add_parser("load", help="append a CSV file's rows to a DISK file")
    load.add_argument("pf", help="the file's physical-file DDS, e.g. custmast.pf (file name = its stem)")
    load.add_argument("csv", help="CSV with a header row naming the fields")
    load.add_argument("--db", required=True, help="SQLite database to load into")
    args = ap.parse_args(argv)

    try:
        if args.command == "load":
            pf_path = pathlib.Path(args.pf)
            pf = parse_pf(pf_path.stem, pf_path.read_text())
            db = Database(args.db)
            try:
                n = load_csv(pf, db, pathlib.Path(args.csv))
            finally:
                db.close()
            print(f"Loaded {n} records into {pf.name}")
            return 0

        rpg_path = pathlib.Path(args.source)
        prog = parse_rpgle(
            rpg_path.read_text(),
            path=rpg_path,
            include_dirs=[pathlib.Path(d) for d in args.include_dirs],
            defines=args.defines,
        )
        dds = _load_dds(rpg_path, args.dspf)
        has_disk = any((f.device or "").upper() == "DISK" for f in prog.files)
        if has_disk and not args.db:
            raise RpgleError("This program uses DISK files. Pass --db <database file> to say where they live.")
        db = Database(args.db) if has_disk else None
        try:
            pfs = _load_pfs(prog, pathlib.Path(args.pf_dir) if args.pf_dir else rpg_path.parent) if has_disk else None
            return drive(prog, dds, sys.stdin, sys.stdout, db, pfs)
        finally:
            if db is not None:
                db.close()
    except KeyboardInterrupt:
        print("\n(interrupted)", file=sys.stderr)
        return 130
    except (OSError, RpgleError) as e:
        print(f"rpgle: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
