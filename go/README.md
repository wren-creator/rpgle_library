# rpgle (Go)

An RPG IV (RPGLE) and DDS parser/interpreter for Go. The core has no dependencies; `DISK` files come from the separate `sqlitedb` package, which uses `modernc.org/sqlite` (pure Go, no cgo).

> **Scope:** this port has fixed-form parsing, `EXFMT`, the core opcodes, display-file DDS, the built-in functions and keyed `DISK` files on SQLite. The Python port has grown into a full runtime (free-form, calls, printing, arrays). See the root README's "Status by language" and "Port parity" for exactly what differs.

```bash
go get github.com/wren-creator/rpgle_library/go
```

> **Why this library exists:** RPG IV is a mature language powering thousands of IBM i installations. The programmers who wrote it are retiring. This library is both a working implementation and a documented reference — see [`../docs/`](../docs/) for the companion glossary and language/DDS reference.

---

## Quick Start

```go
package main

import (
	"fmt"
	"os"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

func main() {
	ddsSource, _ := os.ReadFile("programs/adventure.dspf")
	rpgSource, _ := os.ReadFile("programs/adventure.rpgle")

	dds, _ := rpgle.ParseDDS(string(ddsSource))
	prog, _ := rpgle.ParseRPGLE(string(rpgSource))

	r := rpgle.Run(prog, dds)
	screen, done, _ := r.Next(nil) // runs until the first EXFMT
	fmt.Println(screen.FormatName) // "MAIN"

	screen, done, _ = r.Next(&rpgle.Resume{
		Key:    "ENTER",
		Values: map[string]string{"cmd": "M"},
	})
	fmt.Println(screen.FormatName, done) // "MAIN" false
}
```

Run it yourself (`cd` into the example's own directory first — each one reads its DDS/RPGLE source via a path relative to itself, not to wherever `go run` was invoked from):

```bash
cd examples/quick-math && go run .        # minimal, no display file at all
cd examples/adventure-cli && go run .     # a full plain-text playthrough
cd examples/sales-report && go run .      # a single-screen business report, computed live
```

---

## Why Go, Not JS Generators

The JS port's `runProgram()` is a generator: it `yield`s at every `EXFMT` and resumes via `.next(resume)`. Go has no generators, so this port uses the same idiom `tn3270_library`'s Go port already established for asynchronous events — a goroutine and channels — but shaped for a **pull-based, bidirectional** handshake rather than a one-way event stream, since each `EXFMT` yield must receive a specific resume value before the program may continue:

```go
func Run(prog *Program, dds *DDS) *Runner
func (r *Runner) Next(resume *Resume) (screen *ScreenData, done bool, err error)
```

`Run` starts the program in its own goroutine. Hitting `EXFMT` blocks on a channel send until `Next()` reads it; `Next()` in turn blocks on a second channel until the caller passes the next `Resume`. The result is a Go call shape that matches the JS `.next()`/`.next(resume)` loop almost exactly, just with an explicit `done bool` instead of a `{value, done}` struct.

---

## API Reference

### `ParseDDS(source string) (*DDS, error)`

Parses DDS display-file source. `DDS.Formats` is `map[string]*Format`; `DDS.FormatOrder` preserves source order (Go map iteration is randomized, and `BuildVars`' "first definition wins" rule depends on source order — same reasoning the JS port gets for free from `Object.keys()`).

### `ParseRPGLE(source string) (*Program, error)`

Tokenizes fixed-form RPG IV source into `Program{File, Files, DSpecs, Statements}`. `Files` is every F-spec; `File` is the first `WORKSTN` one.

### `BuildVars(prog *Program, dds *DDS) map[string]*Var`

Builds the program's variable table from DDS-bound fields and D-spec standalone fields.

### `BuildVarsWith(prog *Program, dds *DDS, pfs map[string]*PhysicalFile) map[string]*Var`

The same, plus the fields of each `DISK` file's record format (`pfs` is keyed by upper-case file name).

### `Compile(statements []Statement) (*Compiled, error)`

Compiles a flat statement list into a jump-resolved instruction array. Most callers won't call this directly — `Run` does it internally.

### `Run(prog *Program, dds *DDS) *Runner`

Starts the program. See "Why Go, Not JS Generators" above.

| `ScreenData` field | Type | Meaning |
|---|---|---|
| `FormatName` | `string` | The DDS record format just written |
| `Fields` | `[]RenderedField` | Everything to display |
| `Cursor` | `Cursor` | Where to place the cursor |
| `Keys` | `map[string]string` | Function key → response indicator, from the format's own `CFnn`/`CAnn` |
| `InputMap` | `map[int]string` | Screen row → field name, for every typeable field |

| `Resume` field | Type | Meaning |
|---|---|---|
| `Key` | `string` | Whichever function key the caller recognized |
| `Values` | `map[string]string` | Typed text for the input fields just shown |

### `RunWith(prog *Program, dds *DDS, opts RunOptions) *Runner`

`Run` for a program with `DISK` files. `RunOptions{DB, PFs}` takes a `Database` and the physical-file definitions (from `ParsePF`) keyed by upper-case file name.

### `ParsePF(name, source string) (*PhysicalFile, error)`

Parses physical-file DDS: the record format, its fields, the `K` key lines and `UNIQUE`. `DESCEND` keys are an error.

### The `sqlitedb` package

`sqlitedb.Open(path)` returns a `*DB` (`":memory:"` for a private one); `db.OpenFile(pf)` creates the file's table if it is new, checks the columns match if it isn't, and returns an `rpgle.FileHandle` (`Chain`, `Setll`, `Setgt`, `Read`, `Readp`, `Reade`, `Readpe`, `Write`, `Update`, `Delete`, and `Found`, `EOF`, `Equal`). The interfaces live in the core package, so you can supply another database. Each statement commits as it runs, so `Close` is all the cleanup there is. See `examples/customer-inquiry` for a whole program.

### `EvalExpr(src string, ctx *Context) (Value, error)` / `SplitAssignment(src string) (lvalue, rvalueSrc string, err error)`

The expression evaluator and `EVAL`-statement splitter. `Value` is a small tagged union (`Kind` is `'N'`/`'S'`/`'B'`) with `AsFloat()`/`AsString()`/`AsBool()` coercion methods — see `expr.go`.

---

## Testing

```bash
go test ./...
```

Single `package rpgle_test` (black-box only) — unlike the sibling `tn3270_library` Go port's white-box/black-box split, this package is almost entirely pure, already-exported functions, so there's little unexported internal machinery worth reaching around the public API to test directly.

---

## Publishing

See [`../docs/PUBLISHING.md`](../docs/PUBLISHING.md) — Go modules publish via git tags, no separate step required.

## See Also

- [`../README.md`](../README.md) — the JS API and full project overview
- [`../docs/LANGUAGE.md`](../docs/LANGUAGE.md) — the RPGLE language subset
- [`../docs/DDS_REFERENCE.md`](../docs/DDS_REFERENCE.md) — the DDS subset

## License

GPL-3.0-or-later
