# rpgle_library

A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter, for JS, Go, and Python. **The Python port is a working runtime**: it runs RPG programs, with files, reports, free-form source and calls between programs, on any machine with no IBM i. The JS and Go ports are the original core (screens and calculations). See [Status by language](#status-by-language).

```
npm install rpgle_library
go get github.com/wren-creator/rpgle_library/go
pip install rpgle_library
```

---

## Why this library exists

RPG IV is a mature, stable language powering thousands of IBM i installations worldwide. The practitioners who wrote and maintain that RPG are retiring, and the knowledge lives mostly in IBM manuals and the heads of programmers who've been doing it for 30 years.

This library aims to be a living, documented reference implementation — not just working code, but code that explains *why* at every step, so the knowledge survives.

See [`docs/`](./docs/) for the companion glossary and language/DDS reference.

---

## What you can run today

The Python port runs fixed-form and free-form RPG IV programs from the command line against a local SQLite database:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e python
rpgle load programs/custmast.pf programs/custmast.csv --db shop.db      # fill a DISK file from a CSV
rpgle run programs/custinq.rpgle --db shop.db                           # a 5250-style screen, a CHAIN, a not-found indicator
rpgle run programs/custreport.rpgle --db shop.db                        # a printed report with page breaks and totals
```

| Example program | What it shows | Run it with |
|---|---|---|
| `adventure.rpgle` + `.dspf` | a text game: screens, subroutines, `DIV`/`MVR` | `rpgle run programs/adventure.rpgle` |
| `sales-report.rpgle` + `.dspf` | calculations and one screen | `rpgle run programs/sales-report.rpgle` |
| `custinq.rpgle` + `custmast.pf` | fixed-form: a keyed file, `CHAIN`, indicators | `rpgle run programs/custinq.rpgle --db shop.db` |
| `custinq-free.rpgle` | the same, free-form | add `--dspf programs/custinq.dspf` |
| `custinq-copy.rpgle` | file declarations moved into a copybook (`/COPY`) | add `--dspf programs/custinq.dspf` |
| `custinq-call.rpgle` + `custlookup.rpgle` | one program calling another by name | add `--dspf programs/custinq.dspf` |
| `custreport.rpgle` + `custrpt.prtf` | a printer file: headings, page breaks, edit codes | `--page-length 8 --overflow-line 6` shows the paging |

The honest pitch: run, test and port RPG logic anywhere, not replace IBM i. System APIs, CL, data areas, job and spool behavior, and ILE binding are out of scope.

## Status by language

| | JS | Go | Python |
|---|---|---|---|
| Fixed-form parser, `EXFMT`, core opcodes, DDS display files | yes | yes | yes |
| `rpgle` command line | no | no | yes |
| Free-form (`**FREE`): declarations, loops, procedures, data structures, arrays | no | no | yes |
| `DISK` files on SQLite: `CHAIN`, `READ`, `SETLL`, `WRITE`, `UPDATE`, `DELETE` | no | no | yes |
| `/COPY` and `/INCLUDE` | no | no | yes |
| Calls between programs (`CALL`/`PARM`, `extpgm`) | no | no | yes |
| Printer files and edit codes | no | no | yes |
| Built-in functions beyond `%TRIM`, `%LEN`, `%SUBST` | no | no | yes |

### Port parity: known differences

`docs/PUBLISHING.md` says the three ports should behave identically for any input all three support. That is **no longer fully true**. While building the Python runtime, these behaviors were corrected in Python only, and the JS and Go ports still have the old behavior:

| Behavior | Python | JS and Go |
|---|---|---|
| Comparing character values | ignores trailing blanks, as RPG does (`name = 'BOB'` is true for `BOB` plus padding) | compares exactly, so a padded field never equals a literal |
| `+` and `-` on text | `+` joins two texts; text with a number, or `-` on text, is an error that says to use `%CHAR` or `%DEC` | JS: `+` joins, and `'a' + 1` is `"a1"`; `'a' - 'b'` is `NaN`. Go: any of these is `NaN`, even `'a' + 'b'` |
| Showing a number with decimals on a screen | with a decimal point (`0000048.75`) so it survives being typed back in | rounded to a whole number |
| `EXFMT` format name | case-insensitive | case-sensitive |
| Unsupported source (`DIM` in a fixed-form D-spec, O-specs) | an error | ignored silently |

I checked each row against the JS and Go ports directly (not from memory). Porting these fixes to JS and Go is on the roadmap below. Until then, treat the Python port as the reference for these behaviors.

---

## Quick Start

```javascript
const { parseDds, parseRpgle, runProgram } = require('rpgle_library');
const fs = require('fs');

const dds = parseDds(fs.readFileSync('programs/adventure.dspf', 'utf8'));
const rpg = parseRpgle(fs.readFileSync('programs/adventure.rpgle', 'utf8'));

const gen = runProgram(rpg, dds);
let step = gen.next();                     // runs until the first EXFMT
console.log(step.value.formatName);        // 'MAIN'
console.log(step.value.fields);            // rendered screen fields

step = gen.next({ key: 'ENTER', values: { cmd: 'M' } });  // respond, resume
console.log(step.value.formatName);        // still 'MAIN' after a move
```

Run it yourself: `node examples/quick-math.js` (minimal, no display file at all) or `node examples/adventure-cli.js` (a full plain-text playthrough).

---

## API Reference

### `parseDds(source) -> { formats }`

Parses DDS display-file source into a map of record-format name → `{ name, fields, keys }`. See [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md) for the supported subset.

```javascript
const dds = parseDds(dspfSource);
console.log(Object.keys(dds.formats));   // ['MAIN', 'COMBAT', 'ENDGAME']
```

### `parseRpgle(source) -> { file, dspecs, statements }`

Tokenizes fixed-form RPG IV source into F-spec/D-spec/C-spec records, in source order. See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) for the supported subset.

```javascript
const rpg = parseRpgle(rpgleSource);
console.log(rpg.file.device);   // 'WORKSTN'
```

### `buildVars(rpgAst, ddsFormats) -> vars`

Builds the program's variable table: every named DDS field across every record format, plus D-spec standalone fields. See "Externally-Described Files" in [`docs/LANGUAGE.md`](./docs/LANGUAGE.md).

### `compile(statements) -> { instrs, subroutines }`

Compiles a flat C-spec statement list into a jump-resolved instruction array. Most callers won't call this directly — `runProgram()` does it internally — but it's exported for anyone who wants to inspect the compiled form.

### `runProgram(rpgAst, ddsFormats) -> Generator`

Runs the program. Yields at every `EXFMT`, resumes with `.next(resume)`.

| Yielded value | Shape |
|---|---|
| `formatName` | The DDS record format just written |
| `fields` | `[{ row, col, text, input, length? }]` — everything to display |
| `cursor` | `{ row, col }` — where to place the cursor |
| `keys` | `{ 'F3': '03', ... }` — function key → response indicator, from the format's own `CFnn`/`CAnn` |
| `inputMap` | `{ row: fieldName }` for every typeable field on this format |

| Resume argument | Shape |
|---|---|
| `key` | Whichever function key the caller recognized (`'ENTER'`, `'F3'`, ...) |
| `values` | `{ fieldName: typedText }` for the input fields just shown |

```javascript
const gen = runProgram(rpg, dds);
let step = gen.next();
while (!step.done) {
  // ...render step.value.fields, read a key + field values from the user...
  step = gen.next({ key: 'ENTER', values: { cmd: 'M' } });
}
```

### `evalExpr(src, ctx)` / `splitAssignment(src)`

The expression evaluator and `EVAL`-statement splitter, exported for anyone building their own interpreter loop on top of the parser output. `ctx` is `{ vars, indicators }`. See "Expression Language" in [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) for the grammar.

```javascript
evalExpr('1 < 2 and 3 < 4', { vars: {}, indicators: {} });   // true
evalExpr("%TRIM('  hi  ')", { vars: {}, indicators: {} });   // 'hi'
```

---

## Supported RPGLE Subset

| Feature | Supported | Notes |
|---|---|---|
| Fixed-form RPG IV | Yes | |
| Free-form RPG IV (`**FREE`): `dcl-s`/`dcl-c`/`dcl-f`, `if`/`elseif`, `dow`/`dou`/`for`, `select`, `iter`/`leave`, `+=`, `NOT`, `ind`, `dcl-ds`, `clear`, `dcl-proc`/`dcl-pi`, `callp`, arrays | Python only | See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) |
| `EVAL`, `IF`/`ELSE`/`ENDIF`, `DOW`/`ENDDO`/`LEAVE`, `SELECT`/`WHEN`/`OTHER`/`ENDSL` | Yes | |
| `EXSR`/`BEGSR`/`ENDSR`, `EXFMT`, `DIV`/`MVR`, `RETURN` | Yes | |
| Standalone D-spec fields (`S`), `INZ` | Yes | |
| Data structures, `LIKE`, prototypes | Free-form, Python only | |
| Arrays (`dim`), `%elem`, `%lookup`, `sorta`, arrays of data structures | Free-form, Python only | Fixed-form `DIM` raises an error |
| DISK file I/O: `CHAIN`, `SETLL`, `SETGT`, `READ`, `READE`, `READP`, `READPE`, `WRITE`, `UPDATE`, `DELETE`, `KLIST`/`KFLD`, `%EOF`/`%FOUND`/`%EQUAL` | Python only | Backed by SQLite. See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) |
| `/COPY`, `/INCLUDE`, `/IF DEFINED`/`/DEFINE`/`/EOF` | Python only | See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) |
| Printer files: `dcl-f ... printer`, `WRITE` a record format, `OFLIND`, `EDTCDE`, `PAGNBR`, `SKIPB`/`SPACEA`... (not O-specs) | Python only | Output goes to stdout or `--spool-dir`. See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) |
| `CALL`/`PARM`/`PLIST`/`*ENTRY` and `callp` to `dcl-pr ... extpgm`: one program calling another by name | Python only | Looked up in the caller's folder, then `-I` folders. See [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) |
| `%TRIM`, `%LEN`, `%SUBST` | Yes | |
| `%TRIML`/`%TRIMR`, `%UPPER`/`%LOWER`, `%SCAN`, `%REPLACE`, `%CHAR`, `%DEC`/`%DECH`, `%INT`/`%INTH`, `%ABS`, `%SQRT`, `%REM`, `%DIV`, `%MIN`/`%MAX`, `%EOF`/`%FOUND`/`%EQUAL` | Python only | Not yet: `%EDITC`, `%EDITW`, `%XLATE`, `%CHECK`, date and time functions |

Full detail: [`docs/LANGUAGE.md`](./docs/LANGUAGE.md).

## Supported DDS Subset

| Feature | Supported | Notes |
|---|---|---|
| Record formats, named fields, constants | Yes | WORKSTN display files |
| Physical files (record format, fields, `K` key lines, `UNIQUE`) | Python only | `DESCEND` keys are an error. See [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md) |
| Printer files (`SKIPB`/`SKIPA`/`SPACEB`/`SPACEA`, line and position, `EDTCDE`, `PAGNBR`) | Python only | See [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md) |
| `CFnn`/`CAnn` | Yes | |
| Single `N?nn` indicator conditioning per line | Yes | |
| OR'd / multi-line ANDed indicators | No | |
| Subfiles, windows | No | |
| Other display keywords (`DSPATR`, `COLOR`, ...) | No | `EDTCDE` works on printer files |

Full detail: [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md).

---

## Go Usage

```go
import "github.com/wren-creator/rpgle_library/go"

dds, _ := rpgle.ParseDDS(dspfSource)
prog, _ := rpgle.ParseRPGLE(rpgleSource)

r := rpgle.Run(prog, dds)
screen, done, _ := r.Next(nil)
fmt.Println(screen.FormatName)

screen, done, _ = r.Next(&rpgle.Resume{Key: "ENTER", Values: map[string]string{"cmd": "M"}})
```

Full API and the JS-generator-to-Go-channel design: [`go/README.md`](./go/README.md).

---

## Python Usage

```python
from rpgle import Resume, parse_dds, parse_rpgle, run_program

dds = parse_dds(dspf_source)
prog = parse_rpgle(rpgle_source)

gen = run_program(prog, dds)
screen = next(gen)
print(screen.format_name)

screen = gen.send(Resume(key="ENTER", values={"cmd": "M"}))
```

Python has native generators, just like JS, so this port didn't need Go's goroutine/channel workaround — `run_program()` is a near 1:1 translation of the original JS generator. Full API: [`python/README.md`](./python/README.md).

---

## Roadmap

Goal: run and test RPG IV logic on Linux, macOS or Windows, with no IBM i license. The Python port leads; JS and Go follow once a feature settles.

- [x] **1. A real runner.** `rpgle run prog.rpgle` draws each screen on a terminal (Python)
- [x] **2. `DISK` file I/O on SQLite.** Multi-file F-specs, `CHAIN`, `READ`, `READE`, `SETLL`, `WRITE`, `UPDATE`, `DELETE`, `%EOF`, `%FOUND`, `%EQUAL` (Python; JS and Go to follow). Try it (after `pip install -e python` in an active venv, see [`python/README.md`](./python/README.md)): `rpgle load programs/custmast.pf programs/custmast.csv --db shop.db`, then `rpgle run programs/custinq.rpgle --db shop.db`
- [x] **3. Free-form RPG.**
  - [x] 3a. `**FREE` parser, `dcl-s`/`dcl-c`/`dcl-f`, `elseif`, `dou`, `for`, `iter`, `NOT`, `+=`, `ind`
  - [x] 3b. Built-in functions (`%char`, `%dec`, `%scan`, `%replace`, `%upper`, `%rem`, ...), string `+`, real `varchar`
  - [x] 3c. `dcl-ds` (`qualified`, `likerec`, `likeds`, `extname`), file ops into and out of a data structure, `clear`
  - [x] 3d. `dcl-proc`, `dcl-pi` (reference, `const`, `value`, `*nopass`), return values, recursion, `callp` and calls inside expressions
- [x] **4. Calls and structures.** (Data structures and `CALLP` shipped with 3c and 3d.)
  - [x] 4a. Arrays: `dim`, `%elem`, `%lookup`, `sorta`, arrays of data structures, array parameters
  - [x] 4b. `/COPY` and `/INCLUDE`, with `/IF DEFINED`, `/DEFINE`, `/EOF` guards (`-I`, `-D`)
  - [x] 4c. `CALL` between programs: same-folder lookup, `dcl-pr extpgm` and `CALL`/`PARM`/`PLIST`/`*ENTRY`, retained state when `*INLR` is off
  - [x] 4d. `PRINTER` files: externally described, page layout, overflow, edit codes, spool output

- [ ] **5. Port parity.** Bring the behavior fixes above to the JS and Go ports, then decide which of the Python runtime features are worth porting

This will not be a drop-in replacement for the IBM compiler. System APIs, CL, data areas, job and spool behavior, and ILE binding are out of scope. The pitch is run, test and port RPG logic anywhere, not replace IBM i.

## Publishing

See [`docs/PUBLISHING.md`](./docs/PUBLISHING.md).

## Documentation

- [`CHANGELOG.md`](./CHANGELOG.md): everything that changed since 1.0.0, including behavior corrections
- [`python/README.md`](./python/README.md): the Python API, the `rpgle` command and the module map
- [`docs/LANGUAGE.md`](./docs/LANGUAGE.md): the RPGLE language subset, compile phases, opcodes, indicators, expression grammar
- [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md): the DDS subset, column layout, keyword support
- [`docs/GLOSSARY.md`](./docs/GLOSSARY.md): RPG/DDS terminology and background
- [`docs/PUBLISHING.md`](./docs/PUBLISHING.md): release process

## License

GPL-3.0-or-later
