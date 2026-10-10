# rpgle_library

A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter, for JS, Go, and Python.

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
| Fixed-form RPG IV | Yes | Free-form (V5R1+) is not |
| `EVAL`, `IF`/`ELSE`/`ENDIF`, `DOW`/`ENDDO`/`LEAVE`, `SELECT`/`WHEN`/`OTHER`/`ENDSL` | Yes | |
| `EXSR`/`BEGSR`/`ENDSR`, `EXFMT`, `DIV`/`MVR`, `RETURN` | Yes | |
| Standalone D-spec fields (`S`), `INZ` | Yes | |
| Data structures, `LIKE`, arrays, prototypes | No | |
| File I/O (`READ`/`WRITE`/`CHAIN`/`UPDATE`/`DELETE`), `CALL` | No | |
| `%TRIM`, `%LEN`, `%SUBST` | Yes | Other BIFs (`%CHAR`, `%DEC`, `%EDITC`, ...) are not |

Full detail: [`docs/LANGUAGE.md`](./docs/LANGUAGE.md).

## Supported DDS Subset

| Feature | Supported | Notes |
|---|---|---|
| Record formats, named fields, constants | Yes | WORKSTN display files only |
| `CFnn`/`CAnn` | Yes | |
| Single `N?nn` indicator conditioning per line | Yes | |
| OR'd / multi-line ANDed indicators | No | |
| Subfiles, windows | No | |
| Other display keywords (`DSPATR`, `EDTCDE`, `COLOR`, ...) | No | |

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
- [ ] **2. `DISK` file I/O on SQLite.** Multi-file F-specs, `CHAIN`, `READ`, `READE`, `SETLL`, `WRITE`, `UPDATE`, `DELETE`, `%EOF`, `%FOUND`, `%EQUAL`
- [ ] **3. Free-form RPG.** `**FREE`, `DCL-S`, `DCL-DS`, `DCL-PR`/`DCL-PI`, `DCL-PROC`, `DCL-F`, built-in functions
- [ ] **4. Calls and structures.** Data structures, arrays, `CALL`/`CALLP`, `/COPY`, `PRINTER` files

This will not be a drop-in replacement for the IBM compiler. System APIs, CL, data areas, job and spool behavior, and ILE binding are out of scope. The pitch is run, test and port RPG logic anywhere, not replace IBM i.

## Publishing

See [`docs/PUBLISHING.md`](./docs/PUBLISHING.md).

## Documentation

- [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) — the RPGLE language subset, compile phases, opcodes, indicators, expression grammar
- [`docs/DDS_REFERENCE.md`](./docs/DDS_REFERENCE.md) — the DDS subset, column layout, keyword support
- [`docs/GLOSSARY.md`](./docs/GLOSSARY.md) — RPG/DDS terminology and background
- [`docs/PUBLISHING.md`](./docs/PUBLISHING.md) — release process

## License

GPL-3.0-or-later
