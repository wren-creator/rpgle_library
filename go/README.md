# rpgle (Go)

A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter for Go.

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

Run it yourself: `go run ./examples/quick-math` (minimal, no display file at all) or `go run ./examples/adventure-cli` (a full plain-text playthrough).

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

Tokenizes fixed-form RPG IV source into `Program{File, DSpecs, Statements}`.

### `BuildVars(prog *Program, dds *DDS) map[string]*Var`

Builds the program's variable table from DDS-bound fields and D-spec standalone fields.

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
