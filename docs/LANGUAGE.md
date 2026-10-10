# RPGLE Language Reference

This is a narrative walkthrough of the RPG IV (RPGLE) subset `rpgle_library` actually parses and runs. See [`GLOSSARY.md`](./GLOSSARY.md) if a term here is unfamiliar, and the mission section in the root [`README.md`](../README.md) for why this exists.

## Overview: The Four Spec Types

Fixed-form RPG source is organized into "specs," identified by a letter in column 6. This library only reads three of them.

### H-spec — not supported

Header specs set compile-time/program-level options (date format, debug flags, etc.). Nothing this library does depends on them, so they're skipped entirely rather than parsed.

### F-spec — WORKSTN and DISK files (fixed-form; free-form uses `dcl-f`)

File specs describe the files a program uses. Every F-spec is collected in `Program.files`, and `Program.file` is still the program's `WORKSTN` (display) file. `DISK` files are **Python port only so far**, and must be externally described (column 22 = `E`): their layout comes from a physical-file DDS (see "DISK Files" below and [`DDS_REFERENCE.md`](./DDS_REFERENCE.md)). The key is whatever the DDS says; the `K` in column 34 is read but the DDS decides.

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

| `CHAIN` `SETLL` `SETGT` `READ` `READE` `READP` `READPE` `WRITE` `UPDATE` `DELETE` | DISK file access (Python port) — see "DISK Files" below | `custno CHAIN CUSTMAST 71` |
| `KLIST` / `KFLD` | Names a composite key; `KFLD`'s field goes in the result column | `ORDK KLIST` |

Anything else throws `Unsupported RPG opcode` at compile time, or `Unimplemented RPG instruction` at run time if it somehow got past compile. **Not supported at all:** `CALL`, arrays.

## Free-Form RPG

*Python port only so far.* Source whose first line is `**FREE` is free-form. `parse_rpgle()` detects that and hands it to `freeform.py`, which produces the same `Program`/`FileSpec`/`DSpec`/`Statement` records the fixed-form parser does. The compiler, interpreter and file runtime are shared, so a free-form `chain key custmast;` is exactly a fixed-form `CHAIN` with no result indicators. `programs/custinq-free.rpgle` is the free-form twin of `custinq.rpgle`.

A statement is everything up to a semicolon: it can span lines, and several can share one. `//` comments and `/* */` blocks are stripped. Keywords are case-insensitive, as are variable and file names. Errors name the source line.

| Area | Supported |
|---|---|
| Declarations | `ctl-opt` (ignored), `dcl-s`, `dcl-c`, `dcl-f` |
| Types | `char`, `varchar` (treated as `char`), `ind`, `int`, `uns`, `packed`, `zoned`, `bindec`, `float`, `like(x)` of an earlier `dcl-s`; `inz` with a value, `*blank`, `*zero`, `*on`, `*off` |
| Control flow | `if`/`elseif`/`else`/`endif`, `dow`, `dou`, `enddo`, `for ... to\|downto ... by`/`endfor`, `select`/`when`/`other`/`endsl`, `leave`, `iter`, `return` |
| Subroutines | `begsr`, `endsr`, `exsr` |
| Assignment | `x = expr;`  `x += expr;` (also `-=` `*=` `/=`)  `eval x = expr;`  `*in03 = *on;` |
| Files | `chain` `setll` `setgt` `read` `reade` `readp` `readpe` `write` `update` `delete` `exfmt`; keys can be an expression or a list: `chain (cust : ord) orders;` |
| Operators | the expression language below, now including `NOT` (looser than comparison, tighter than `AND`) |

**`FOR`** evaluates its start, limit and `BY` once, on entry, as RPG does. `BY` must be positive; use `downto` to count down. The index is left one step past the limit when the loop finishes.

**`DOU`** tests at the bottom, so the body always runs at least once. `ITER` jumps to the next test (`DOW`, `DOU`) or the next step (`FOR`); `LEAVE` exits the innermost loop.

**`ind`** variables hold `'1'` or `'0'`, as in real RPG, but read and assign as booleans: `ok = *on; if not ok; ...`.

**Character comparison** ignores trailing blanks (`name = 'BOB'` is true for a 20-character `name` holding `BOB` and 17 blanks), as RPG does. This applies to fixed-form too.

**Opcode extenders** `(n)`, `(p)`, `(h)` are accepted and ignored. `(e)` raises an error, since it needs `%ERROR`.

Not supported yet, and each raises an error naming the line rather than being skipped: `dcl-ds`, `dcl-proc`/`dcl-pi`/`dcl-pr`, `callp`, arrays (`dim`), date/time types, `monitor`, `clear`/`reset`, `/copy`, `dcl-f` keywords other than the device, `keyed` and `usage`. Mixed source (`/free ... /end-free` blocks inside fixed-form) is not supported either: the whole file is one or the other.

## DISK Files

*Python port only so far.* A program with `DISK` F-specs runs against a `Database` (SQLite) and a dict of physical-file DDS, one per file:

```python
from rpgle import Database, parse_pf, parse_rpgle, parse_dds, run_program

db = Database("shop.db")                      # or Database() for in-memory
pfs = {"CUSTMAST": parse_pf("CUSTMAST", open("custmast.pf").read())}
for screen in run_program(prog, dds, db, pfs): ...
```

Every field of the file's record format becomes a program variable. `CHAIN`, `READ` and friends copy a record into those variables; `WRITE` and `UPDATE` copy them back out. `WRITE`/`UPDATE` name the record format (`CUSTREC`) and the others name the file (`CUSTMAST`); either works anywhere here. Changes are committed when the program ends.

### Resulting indicators (columns 71-76)

Which column means what depends on the opcode:

| Opcode | hi (71-72) | lo (73-74) | eq (75-76) |
|---|---|---|---|
| `CHAIN` | not found | error | |
| `SETLL` | not found | error | equal |
| `SETGT` | not found | error | |
| `READ` `READE` `READP` `READPE` | | error | end of file |
| `WRITE` `UPDATE` | | error | |
| `DELETE` | not found | error | |

Each indicator named on the line is set on or off every time, so a stale "on" can't carry over. An error with no error indicator named halts the program, as in real RPG with no `*PSSR`. A duplicate key on `WRITE` to a `UNIQUE` file is an error.

### Positioning

A file always has a position that the next `READ` moves from. `SETLL key` puts it just before the first record with a key `>=` the one given; `SETGT key` just after the last record `<=` it. `*LOVAL` and `*HIVAL` mean the very start and very end. `CHAIN` positions on the record it finds. A key with fewer values than the file has key fields (a partial `KLIST`) compares on the leading fields only, which is how `READE` walks all of one customer's orders.

The position is held as a key, not a database cursor, so deleting or updating records in the middle of a read loop is safe.

### `%EOF`, `%FOUND`, `%EQUAL`

`%EOF(file)`, `%FOUND(file)` and `%EQUAL(file)` read the state of the last operation on that file; with no argument they mean the last file used. `%EOF` is set by the `READ` family; `%FOUND` by `CHAIN`, `SETLL`, `SETGT` and keyed `DELETE`; `%EQUAL` by `SETLL`.

### Conditioning indicators (columns 9-11)

`C   71   EVAL ...` runs only when indicator 71 is on, `N71` when it's off. Supported on every opcode except the block-structure ones (`IF`, `ELSE`, `ENDIF`, `DOW`, `ENDDO`, `SELECT`, `WHEN`, `OTHER`, `ENDSL`, `BEGSR`, `ENDSR`), which raise an error instead of running wrong. `LR` works as an indicator name too.

### Not supported

`DESCEND` keys, join and select/omit logical files, more than one record format per file, record locking, `READC`, `CLOSE`/`OPEN` and `USROPN`, `%ERROR`, `PREFIX`/`RENAME`, program-described files, `*PSSR`. The F-spec type (`I`/`U`/`O`) is read but not enforced. Decimal fields are shown with an explicit decimal point (one column wider than a real 5250 would draw them) so a value survives being typed back in.

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

`%TRIM(str)`, `%LEN(str)`, `%SUBST(str : start [: len])` (1-based start, matching real RPG), and the file-status trio `%EOF`, `%FOUND`, `%EQUAL` (Python port, see "DISK Files"). Anything else throws `Unsupported RPG built-in`.

### Figurative constants

`*ON`, `*OFF`, `*INnn`, `*INLR`, `*INRT`. Any other `*XXX` throws `Unsupported RPG figurative constant`.

### Why AND/OR always evaluate both operands

JS's native `&&`/`||` short-circuit — they skip evaluating the right side once the left side already determines the result. That's wrong here, because "evaluating" an operand in this parser also means *consuming its tokens*. If the parser short-circuited, it would leave unconsumed tokens on the stream and throw a spurious "trailing tokens" error whenever the left side of an `AND`/`OR` already settled the result. `parseAnd()`/`parseOr()` deliberately always call `parseCompare()`/`parseAnd()` on the right side regardless of the left side's value, then combine with `Boolean(v) && Boolean(rhs)` / `Boolean(v) || Boolean(rhs)` — full evaluation, JS-boolean combination.

## DIV/MVR as Modulo

RPG has no `%REM` built-in until well past V4R3. The classic, unambiguous way to get a remainder is `DIV` immediately followed by a bare `MVR` — `DIV` computes and stores the truncated quotient *and* remembers the remainder internally; the following `MVR` (no factor 1/2 of its own) writes that remembered remainder to its result field. `programs/adventure.rpgle` uses this idiom repeatedly as a simple LCG-style pseudo-random generator (`ROLLRNG`): `seed = seed * 31 + 7`, then `DIV 9973`/`MVR` to fold it back into range.

## What's Explicitly Out of Scope

Arrays, data structures (`DS`), `CALL`/prototypes, free-form RPG and DISK file I/O in the JS and Go ports, subfiles and windows in DDS, DDS keyword continuation lines, OR'd/multi-line indicator conditions. None of this is an oversight — it's the boundary of what the shipped example content needs, documented here instead of silently missing.

## See Also

- [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) — the display-file side of a program
- [`GLOSSARY.md`](./GLOSSARY.md) — term definitions
- [`PUBLISHING.md`](./PUBLISHING.md) — release process
