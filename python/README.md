# rpgle (Python)

A zero-dependency RPG IV (RPGLE) and DDS interpreter for Python, with a command line. It runs fixed-form and free-form RPG programs with screens, DISK files (on SQLite), `/COPY` includes, calls between programs and printed reports, on any machine, with no IBM i.

```bash
pip install rpgle_library
```

> **Why this library exists:** RPG IV is a mature language powering thousands of IBM i installations. The programmers who wrote it are retiring. This library is both a working implementation and a documented reference — see [`../docs/`](../docs/) for the companion glossary and language/DDS reference.

---

## Quick Start

```python
from rpgle import Resume, parse_dds, parse_rpgle, run_program

dds_source = open("programs/adventure.dspf").read()
rpg_source = open("programs/adventure.rpgle").read()

dds = parse_dds(dds_source)
prog = parse_rpgle(rpg_source)

gen = run_program(prog, dds)
screen = next(gen)          # runs until the first EXFMT
print(screen.format_name)   # "MAIN"

screen = gen.send(Resume(key="ENTER", values={"cmd": "M"}))
print(screen.format_name)   # still "MAIN" after a move
```

Run it yourself: `python examples/quick_math.py` (minimal, no display file at all), `python examples/adventure_cli.py` (a full plain-text playthrough), or `python examples/sales_report.py` (a single-screen business report, computed live and rendered as a 24x80 grid).

---

## Running a program: the `rpgle` command

Installing the package gives you an `rpgle` command. From a checkout, install it into a virtualenv and activate that venv, or the command won't be on your PATH:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e python
```

Without installing, `PYTHONPATH=python/src python3 -m rpgle ...` runs the same thing.

```bash
rpgle run programs/sales-report.rpgle                  # picks up sales-report.dspf next to it
rpgle run programs/adventure.rpgle --dspf my.dspf      # or name the display file
```

```bash
rpgle load programs/custmast.pf programs/custmast.csv --db shop.db     # fill a DISK file from a CSV
rpgle run programs/custinq.rpgle --db shop.db                          # customer inquiry: CHAIN by number
```

`/COPY` members and CALLed programs are found next to the program and in any `-I folder`, and `-D NAME` defines a name for `/IF DEFINED(NAME)`.

A program that prints shows its output after it ends (or `--spool-dir DIR` writes one `.txt` per printer file); `--page-length` and `--overflow-line` set the page (66 and 60 by default). Printer layouts are `<file>.prtf` DDS files beside the program.

Programs with `DISK` files need `--db`, and each file's layout comes from `<filename>.pf` (physical-file DDS) next to the program, or in `--pf-dir`. `custinq.rpgle` is the worked example: a screen, a `CHAIN`, and the not-found indicator.

Each `EXFMT` is drawn as a 24x80 grid, then it prompts for each input field (press Enter to keep the value shown). Type a function key like `F3` at any prompt to send that key, if the current format declares it. It reads plain stdin and writes plain stdout, so it works over SSH, in a pipe, and in CI. The program ends when the RPG does, or when input runs out.

---

## Why Generators, Not Goroutines

The JS port's `runProgram()` is a generator: it `yield`s at every `EXFMT` and resumes via `.next(resume)`. The Go port had to reach for a goroutine + channel pair instead, specifically *because* Go has no generators. Python doesn't have that problem — it has native generators just like JS — so `run_program()` here is a near 1:1 translation of the original JS generator, resumed with `.send(resume)` instead of `.next(resume)`. No thread or channel plumbing anywhere in this port.

---

## API Reference

### `parse_dds(source: str) -> DDS`

Parses DDS display-file source. `DDS.formats` is `dict[str, Format]`; `DDS.format_order` preserves source order, which `build_vars`' "first definition wins" rule depends on.

### `parse_rpgle(source: str, path: Path | None = None, include_dirs=None, defines=()) -> Program`

Parses RPG IV source into `Program(file, files, dspecs, dss, procs, protos, main_params, statements)`. Source starting with `**FREE` is free-form; anything else is fixed-form. `/COPY` and `/INCLUDE` are expanded first: `path` is the file the source came from (where includes are looked for first), `include_dirs` are further folders to search, and `defines` pre-defines names for `/IF DEFINED`. Errors inside a copy member name the member and its line.

### `build_vars(prog: Program, dds: DDS, pfs=None, prtfs=None) -> dict[str, Var]`

Builds the program's variable table: fields of the display file, of each DISK file (`pfs`) and printer file (`prtfs`), data structure subfields, arrays, and D-spec / `dcl-s` fields. `Var` is one value with a length and decimals; `ArrayVar` is a list of them (or, for an array of data structures, of `{subfield: Var}` dicts).

### `compile_program(statements) -> Compiled`

Compiles a flat statement list into a jump-resolved instruction array. Most callers won't call this directly — `run_program` does it internally. Named `compile_program`, not `compile`, to avoid shadowing Python's `compile` builtin — the one deliberate naming deviation from the JS/Go ports' `compile()`/`Compile()`.

### `run_program(prog, dds, db=None, pfs=None, loader=None, spool=None) -> Generator[ScreenData, Resume, None]`

Runs the program. Yields a `ScreenData` at every `EXFMT`, resumes with `.send(resume)`. What else you pass depends on what the program uses:

| The program uses | Pass | Notes |
|---|---|---|
| `DISK` files | `db` (a `Database`) and `pfs` (upper-case file name to `PhysicalFile`; `load_pfs(prog, [folder])` builds it from `<file>.pf`) | Changes are committed when the program ends |
| calls to other programs | `loader` (a `ProgramLoader`) | Finds each called program by name in its folders |
| printer files | `spool` (a `Spool`) | Collects the printed output; read it with `spool.outputs()` |

Everything the program is allowed to do is documented in [`../docs/LANGUAGE.md`](../docs/LANGUAGE.md).

| `ScreenData` field | Type | Meaning |
|---|---|---|
| `format_name` | `str` | The DDS record format just written |
| `fields` | `list[RenderedField]` | Everything to display |
| `cursor` | `Cursor` | Where to place the cursor |
| `keys` | `dict[str, str]` | Function key → response indicator, from the format's own `CFnn`/`CAnn` |
| `input_map` | `dict[int, str]` | Screen row → field name, for every typeable field |

| `Resume` field | Type | Meaning |
|---|---|---|
| `key` | `str` | Whichever function key the caller recognized |
| `values` | `dict[str, str]` | Typed text for the input fields just shown |

```python
gen = run_program(prog, dds)
screen = next(gen)
while True:
    # ...render screen.fields, read a key + field values from the user...
    try:
        screen = gen.send(Resume(key="ENTER", values={"cmd": "M"}))
    except StopIteration:
        break
```

### `eval_expr(src: str, ctx: Context)` / `split_assignment(src: str)`

The expression evaluator and `EVAL`-statement splitter. Unlike the Go port, there's no tagged `Value` union here — Python is dynamically typed just like JS, so expression results are plain `float`/`str`/`bool`. `as_bool()`/`as_float()`/`as_string()` are exported coercion helpers for anyone evaluating those results the way the interpreter itself does.

```python
from rpgle import Context, eval_expr

ctx = Context(vars={}, indicators={})
eval_expr("1 < 2 and 3 < 4", ctx)   # True
eval_expr("%TRIM('  hi  ')", ctx)   # 'hi'
```

### Files, calls and printing from Python

```python
from pathlib import Path
from rpgle import Database, ProgramLoader, Spool, load_pfs, parse_dds, parse_rpgle, run_program

path = Path("programs/custreport.rpgle")
prog = parse_rpgle(path.read_text(), path=path)

db = Database("shop.db")                              # SQLite; Database() is in-memory
pfs = load_pfs(prog, [path.parent])                   # custmast.pf -> the CUSTMAST file's layout
loader = ProgramLoader([path.parent], main=path)      # for CALLs
spool = Spool(directories=[path.parent])              # for printer files (66-line pages, overflow at 60)

for screen in run_program(prog, parse_dds(""), db, pfs, loader, spool):
    ...                                               # one ScreenData per EXFMT; none for this report
for name, text in spool.outputs():                    # [("custrpt", "...text with \f between pages...")]
    print(name, text)
db.close()
```

The pieces, each in its own module so you can use one without the others:

| Module | What it is |
|---|---|
| `rpgle.py`, `freeform.py` | the fixed-form and free-form parsers; both produce the same records |
| `include.py` | `/COPY`, `/INCLUDE` and `/IF DEFINED` expansion (`expand_includes`) |
| `interpreter.py` | the compiler (`compile_program`) and the run loop, one frame per call |
| `expr.py` | the expression evaluator and built-in functions; `Var` and `ArrayVar` |
| `dds.py`, `pf.py`, `prtf.py` | DDS parsers for display, physical and printer files |
| `database.py`, `fileops.py` | `Database` (SQLite) with RPG's keyed access, and the opcodes that drive it |
| `loader.py` | `ProgramLoader`: finding, parsing and caching called programs |
| `spool.py` | `Spool` and the printer page layout |
| `cli.py` | the `rpgle` command |

---

## Trust model

Running an RPG program is running code someone wrote: it can read and write the database you point it at, write spool files where you tell it, and read files in its own folder. Only run source you trust, as you would any script.

What the library does guard against is a program reaching *outside* its folder through the names it uses. File and program names must be IBM i object names (letters, digits and `_ $ # @`, up to 100 characters; no `/`, no `..`, no dots), and a `/COPY` or `CALL` can only resolve inside the program's own folder or an `-I` folder you named (a symlink out is refused). The `rpgle` command opens only the `--db` file you give it. Malformed source gets an error that names the problem; it was fuzzed with more than two million malformed inputs during development (a smaller deterministic run is `tests/test_fuzz.py`) so that nothing but an `RpgleError` comes out, and a Python traceback from bad input is a bug worth reporting.

---

## Testing

```bash
python -m unittest discover -s tests -v
```

The suite (a few hundred tests) is mostly black-box: it drives the public API, building fixed-form lines by column (`tests/_cols.py`) so a misplaced space can't hide in a hand-counted string, and it runs the shipped example programs through the real command line. CI runs it on Python 3.12 and 3.13.

---

## Publishing

See [`../docs/PUBLISHING.md`](../docs/PUBLISHING.md).

## See Also

- [`../README.md`](../README.md) — the JS API and full project overview
- [`../go/README.md`](../go/README.md) — the Go port
- [`../docs/LANGUAGE.md`](../docs/LANGUAGE.md) — the RPGLE language subset
- [`../docs/DDS_REFERENCE.md`](../docs/DDS_REFERENCE.md) — the DDS subset

## License

GPL-3.0-or-later
