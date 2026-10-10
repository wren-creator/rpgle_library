# rpgle (Python)

A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter for Python.

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

Installing the package gives you an `rpgle` command (or use `python -m rpgle`):

```bash
rpgle run programs/sales-report.rpgle                  # picks up sales-report.dspf next to it
rpgle run programs/adventure.rpgle --dspf my.dspf      # or name the display file
```

Each `EXFMT` is drawn as a 24x80 grid, then it prompts for each input field (press Enter to keep the value shown). Type a function key like `F3` at any prompt to send that key, if the current format declares it. It reads plain stdin and writes plain stdout, so it works over SSH, in a pipe, and in CI. The program ends when the RPG does, or when input runs out.

---

## Why Generators, Not Goroutines

The JS port's `runProgram()` is a generator: it `yield`s at every `EXFMT` and resumes via `.next(resume)`. The Go port had to reach for a goroutine + channel pair instead, specifically *because* Go has no generators. Python doesn't have that problem — it has native generators just like JS — so `run_program()` here is a near 1:1 translation of the original JS generator, resumed with `.send(resume)` instead of `.next(resume)`. No thread or channel plumbing anywhere in this port.

---

## API Reference

### `parse_dds(source: str) -> DDS`

Parses DDS display-file source. `DDS.formats` is `dict[str, Format]`; `DDS.format_order` preserves source order, which `build_vars`' "first definition wins" rule depends on.

### `parse_rpgle(source: str) -> Program`

Tokenizes fixed-form RPG IV source into `Program(file, dspecs, statements)`.

### `build_vars(prog: Program, dds: DDS) -> dict[str, Var]`

Builds the program's variable table from DDS-bound fields and D-spec standalone fields.

### `compile_program(statements) -> Compiled`

Compiles a flat statement list into a jump-resolved instruction array. Most callers won't call this directly — `run_program` does it internally. Named `compile_program`, not `compile`, to avoid shadowing Python's `compile` builtin — the one deliberate naming deviation from the JS/Go ports' `compile()`/`Compile()`.

### `run_program(prog: Program, dds: DDS) -> Generator[ScreenData, Resume, None]`

Runs the program. Yields a `ScreenData` at every `EXFMT`, resumes with `.send(resume)`.

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

---

## Testing

```bash
python -m unittest discover -s tests -v
```

Same reasoning as the Go port's black-box-only test package: this package is almost entirely pure, already-exported functions, so there's little internal machinery worth reaching around the public API to test directly.

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
