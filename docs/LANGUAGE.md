# RPGLE Language Reference

This is a narrative walkthrough of the RPG IV (RPGLE) subset `rpgle_library` actually parses and runs. See [`GLOSSARY.md`](./GLOSSARY.md) if a term here is unfamiliar, and the mission section in the root [`README.md`](../README.md) for why this exists.

## Overview: The Four Spec Types

Fixed-form RPG source is organized into "specs," identified by a letter in column 6. This library only reads three of them.

### H-spec — not supported

Header specs set compile-time/program-level options (date format, debug flags, etc.). Nothing this library does depends on them, so they're skipped entirely rather than parsed.

### F-spec — WORKSTN-only subset

File specs describe the files a program uses. This library only reads enough of an F-spec to identify a single `WORKSTN` (display) file: its name, type, designation, record format, and device. Multi-file programs, database files (`DISK`), and keyed-file details aren't parsed — a program here has exactly one file, its screen.

### D-spec — standalone fields only

Definition specs declare variables. Supported: standalone fields (`deftype` `S`) with a length, decimal positions, and an optional `INZ(...)` initial value. **Not supported:** data structures (`DS`) and their subfields, `LIKE`/`LIKEDS`, arrays (`DIM`), or prototypes (`PR`/`PI`). Most of a real program's variables don't even need a D-spec here — see "Externally-Described Files" below.

### C-spec — fixed-form only

Calculation specs are the executable statements. Only fixed-form C-specs are parsed — free-form RPG (available from V5R1 onward) isn't, which is also why the example program's own header comment calls itself "V4R3-safe." A C-spec line captures indicators, factor 1, the opcode, and everything from column 36 onward as both a free-form expression (for `EVAL`/`IF`/`DOW`/`WHEN`) and fixed classic factor-2/result columns (for `DIV`/`MVR`-style ops) — see `src/rpgle.js`'s header comment for the exact column map.

## Externally-Described Files

A WORKSTN file's DDS record formats aren't just screen layout — every named field across every format automatically becomes a program variable, with no D-spec needed (`buildVars()` in `src/interpreter.js`). This is real ILE RPG behavior, not a shortcut this library takes: it's the whole point of "externally described" files. If the same field name appears in more than one record format, the first definition in source order wins.

## Compile Phases

Running a program happens in three phases, each its own module:

### Phase 1: Parse (`rpgle.js`)

Turns source lines into a flat list of spec records, in source order. No control-flow resolution happens here — `IF`/`ENDIF` are just two statements next to each other in the list, with no structural relationship yet.

### Phase 2: Compile (`interpreter.js`'s `compile()`; `compile_program()` in the Python port, renamed to avoid shadowing Python's `compile` builtin)

Walks the flat statement list once and produces a jump-resolved instruction array, using the classic backpatch technique: an open block (`IF`, `DOW`, `SELECT`) pushes a frame onto a stack; its closing keyword (`ENDIF`, `ENDDO`, `ENDSL`) pops the frame and fills in the jump target(s) it owns.

Worked example — `IF`/`ELSE`/`ENDIF`:

```
0: IF   cond           falseTarget: ?  →  patched to 3 when ELSE is seen
1: EVAL a = 1
2: JMP  ?               (ELSE's own jump, over the else-branch)  →  patched to 4 when ENDIF is seen
3: EVAL a = 2
4: NOP                  (ENDIF's marker — both branches land here)
```

`DOW`/`ENDDO` works the same way but also patches every `LEAVE` inside the loop to jump past the loop's own back-edge. `SELECT`/`WHEN`/`OTHER`/`ENDSL` chains each `WHEN`'s false-branch to the next arm, and every arm's fall-through to a shared `JMP` past `ENDSL`.

### Phase 3: Run (the generator/goroutine execution loop)

A simple program counter walks the compiled instruction array. Every opcode is a `case` in a big switch — see the table below. The interesting part is `EXFMT`.

## The EXFMT Yield/Resume Contract

Real RPG's `EXFMT` is "write a format to the screen, then read it back" in one instruction — the program blocks until the operator responds. This library models that with:

- **JS**: `runProgram()` is a generator. Hitting `EXFMT` `yield`s `{ formatName, fields, cursor, keys, inputMap }` and blocks until the caller calls `.next({ key, values })`.
- **Python**: `run_program()` is likewise a generator (Python has them natively, same as JS) — hitting `EXFMT` `yield`s a `ScreenData` and blocks until the caller calls `.send(Resume(key, values))`.
- **Go**: `Run()` starts the program in a goroutine and returns a `*Runner`. Hitting `EXFMT` blocks on a channel send; the caller drives it with `Next(resume)`, which has the same call shape as the JS/Python generator loop.

Either way, the shape is the same plain data — no terminal protocol is involved. `fields` is what to display; `inputMap` says which screen rows are typeable and what variable each one feeds; `keys` maps recognized function keys (`F3`, etc.) to the response indicator that key turns on, straight from the format's own `CFnn`/`CAnn` DDS keywords.

## Supported Opcodes

| Opcode | Phase semantics | Example |
|---|---|---|
| `EVAL` | Splits on the first `=`, evaluates the right-hand side via `expr.js`, assigns | `EVAL plrhp = plrhp - dmg` |
| `IF` / `ELSE` / `ENDIF` | Standard conditional | `IF loc = 1` |
| `DOW` / `ENDDO` / `LEAVE` | Do-while loop; `LEAVE` exits early | `DOW *in03 = *off` |
| `SELECT` / `WHEN` / `OTHER` / `ENDSL` | Multi-arm conditional, first matching `WHEN` wins, `OTHER` is the default | `WHEN cmd = 'M'` |
| `BEGSR` / `ENDSR` / `EXSR` | Subroutine definition, return, call (its own tiny call stack) | `EXSR BUILDLOC` |
| `EXFMT` | Write-then-read a DDS record format — see above | `EXFMT MAIN` |
| `DIV` / `MVR` | Classic fixed-column integer divide, then remainder-of-last-DIV | `seed DIV 9973 quot` then `MVR seed` |
| `RETURN` | Ends the program immediately | `RETURN` |

Anything else throws `Unsupported RPG opcode` at compile time, or `Unimplemented RPG instruction` at run time if it somehow got past compile. **Not supported at all:** file I/O opcodes (`READ`/`WRITE`/`CHAIN`/`UPDATE`/`DELETE`), `CALL`, arrays, `%EOF`/`%FOUND`, free-form RPG.

## Indicators

RPG indicators are named boolean flags, tracked separately from ordinary variables.

### Numbered indicators (`*INnn`)

Two-digit indicators (`*IN90`, `*IN03`, ...) — set with `EVAL *in90 = *on`/`*off`, tested in any expression, and used by DDS to conditionally show/hide fields (see [`DDS_REFERENCE.md`](./DDS_REFERENCE.md)).

### `*INLR` / `*INRT`

`*INLR` ("last record") signals normal end-of-program when set on — real RPG uses it to tell the runtime not to keep the program active for the next call. This library's interpreter doesn't currently act on it directly (a program still ends via `RETURN`), but it's tracked and testable like any other indicator, since example content sets it before returning, matching real RPG convention. `*INRT` ("return") is tracked the same way but isn't used by any shipped content.

### Response indicators (`CFnn`/`CAnn`) and the reset-on-read rule

When `EXFMT` resumes, the interpreter first resets every indicator the *current format's own* `CFnn`/`CAnn` keywords own to off, then turns on the one matching whatever key was pressed. This matches real RPG behavior: response indicators from a different format's key mappings are untouched, since they belong to a different screen.

## Expression Language

`expr.js`/`expr.go` evaluate `EVAL` right-hand sides and `IF`/`DOW`/`WHEN` conditions.

### Grammar

```
expr       := orExpr
orExpr     := andExpr (OR andExpr)*
andExpr    := compare (AND compare)*
compare    := additive ((= | <> | <= | >= | < | >) additive)?
additive   := term ((+ | -) term)*
term       := unary ((* | /) unary)*
unary      := '-' unary | primary
primary    := NUMBER | STRING | '*ON' | '*OFF'
            | '*IN' DIGIT DIGIT | '*INLR'
            | '%' NAME '(' expr (':' expr)* ')'
            | NAME | '(' expr ')'
```

### Built-in functions

Only three: `%TRIM(str)`, `%LEN(str)`, `%SUBST(str : start [: len])` (1-based start, matching real RPG). Anything else throws `Unsupported RPG built-in`.

### Figurative constants

`*ON`, `*OFF`, `*INnn`, `*INLR`, `*INRT`. Any other `*XXX` throws `Unsupported RPG figurative constant`.

### Why AND/OR always evaluate both operands

JS's native `&&`/`||` short-circuit — they skip evaluating the right side once the left side already determines the result. That's wrong here, because "evaluating" an operand in this parser also means *consuming its tokens*. If the parser short-circuited, it would leave unconsumed tokens on the stream and throw a spurious "trailing tokens" error whenever the left side of an `AND`/`OR` already settled the result. `parseAnd()`/`parseOr()` deliberately always call `parseCompare()`/`parseAnd()` on the right side regardless of the left side's value, then combine with `Boolean(v) && Boolean(rhs)` / `Boolean(v) || Boolean(rhs)` — full evaluation, JS-boolean combination.

## DIV/MVR as Modulo

RPG has no `%REM` built-in until well past V4R3. The classic, unambiguous way to get a remainder is `DIV` immediately followed by a bare `MVR` — `DIV` computes and stores the truncated quotient *and* remembers the remainder internally; the following `MVR` (no factor 1/2 of its own) writes that remembered remainder to its result field. `programs/adventure.rpgle` uses this idiom repeatedly as a simple LCG-style pseudo-random generator (`ROLLRNG`): `seed = seed * 31 + 7`, then `DIV 9973`/`MVR` to fold it back into range.

## What's Explicitly Out of Scope

Free-form RPG, arrays, data structures (`DS`), file I/O opcodes (`READ`/`WRITE`/`CHAIN`/`UPDATE`/`DELETE`), `CALL`/prototypes, `%EOF`/`%FOUND`, subfiles and windows in DDS, DDS keyword continuation lines, OR'd/multi-line indicator conditions. None of this is an oversight — it's the boundary of what the shipped example content needs, documented here instead of silently missing.

## See Also

- [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) — the display-file side of a program
- [`GLOSSARY.md`](./GLOSSARY.md) — term definitions
- [`PUBLISHING.md`](./PUBLISHING.md) — release process
