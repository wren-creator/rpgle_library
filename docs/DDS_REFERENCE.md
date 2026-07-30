# DDS Reference (Display Files)

`src/dds.js` / `go/dds.go` / `python/src/rpgle/dds.py` parse DDS source for **WORKSTN display files only** — the subset needed to describe a screen made of record formats, constant text, and named input/output fields. This document covers exactly what's parsed, and is explicit about what isn't.

## Column Layout

DDS is fixed-column source, same punch-card-derived layout as RPG's own specs. Every line the parser looks at is checked against these column ranges:

| Columns | Meaning |
|---|---|
| 6 | Form type — must be `A` or the line is skipped |
| 7 | Comment marker — `*` means the rest of the line is a comment, skipped |
| 7–16 | Indicator conditioning — one `N?nn` pair (see below) |
| 17 | `R` starts a new record format; blank means this line is a field or constant inside the current format |
| 19–28 | Name — record format name, field name, or blank (blank + a quoted literal later on the line means a constant) |
| 30–34 | Length |
| 35 | Data type |
| 36–37 | Decimal positions |
| 38 | Usage: `O` (output), `I` (input), `B` (both) |
| 39–41 | Line — screen row, 1-based in the source, converted to 0-based internally |
| 42–44 | Position — screen column, 1-based in the source, converted to 0-based internally |
| 45–80 | Keywords, or the quoted literal for a constant line |

## Record Format Lines

A line with `R` in column 17 starts a new record format. Everything from that line's keyword area (columns 45+) is scanned for `CFnn`/`CAnn` keywords (see below); every subsequent field/constant line belongs to this format until the next `R` line.

## Field Lines

Two kinds of lines can follow a record format:

- **Named fields** — column 19–28 has a name. Parsed into `{ kind: 'field', name, row, col, length, dtype, dec, usage, indicators }`. `dtype` defaults to `'N'` if a decimal count is present, `'A'` otherwise. `dec` is deliberately `null` (not `0`) when the decimal-positions column is blank — a blank means "character field," a real `0` means "numeric with zero decimals," and the interpreter needs to tell those two apart.
- **Constant/literal lines** — column 19–28 is blank, and the keyword area holds a quoted string. Parsed into `{ kind: 'const', row, col, text, indicators }`.

## Indicator Conditioning

Columns 7–16 can hold one `N?nn` conditioning pair: an optional `N` (negate) followed by a two-digit indicator number. A field or constant line with `90` in that range is shown only when indicator 90 is on; with `N90`, only when it's off.

```
     A 90         MSGTXT        40A  O  7  3
```

**Only one indicator per line is supported.** Real DDS allows OR'd conditions (multiple indicator groups on one line) and AND'd conditions continued across multiple lines — see "Not Supported" below.

## Keywords

### Supported: `CFnn` / `CAnn`

Record-level command-key mapping. `CF03(03)` means "when function key 3 is pressed while this format is on the screen, turn on response indicator 03." The parser stores this as `format.keys.F3 = '03'`; the interpreter uses it to map an incoming AID key to the indicator the RPG program tests.

### Explicitly NOT Supported

| Feature | Why not (yet) |
|---|---|
| OR'd indicator conditions (multiple groups on one line) | Not needed by the example content; the single-`N?nn`-per-line parser is simpler and covers everything shipped so far. |
| Multi-line ANDed indicator conditions | Same reasoning — no shipped content needs it. |
| Subfiles (`SFL`/`SFLCTL`) | A genuinely large feature (separate record formats, page/relative-record-number keywords, `SFLDSP`/`SFLCLR`/`SFLEND` protocol) — out of scope until something actually needs list-style screens. |
| Windows (`WINDOW` keyword) | Out of scope — no shipped content uses overlapping windows. |
| Keyword continuation lines (`+`/`-` continuation) | Out of scope — all shipped keyword areas fit on one line. |
| Other display keywords (`DSPATR`, `EDTCDE`, `COLOR`, etc.) | Not parsed at all currently — a field's presentation attributes beyond position/length/type aren't tracked. |

## Field Usage Codes

- `O` — output only (the program writes it, the operator can't type into it)
- `I` — input only
- `B` — both (the operator can type into it; the program can also set an initial value)

Only `I`/`B` fields end up in a rendered screen's `inputMap` — see `renderFormat()` in `src/interpreter.js`.

## Worked Example

`programs/adventure.dspf`'s `MAIN` format:

```
     A          R MAIN                      CF03(03)
     A                                  1 30'ADVENTURE'
     A                                  3  3'Location:'
     A            LOCTXT        30A  O  3 13
     A                                  5  3'HP:'
     A            PLRHP          3  0O  5 13
     A 90         MSGTXT        40A  O  7  3
     A                                 22  3'Command (M=Move, F3=Exit):'
     A            CMD            1A  B 22 31
```

- `R MAIN ... CF03(03)`: starts format `MAIN`; pressing F3 sets indicator 03.
- `1 30'ADVENTURE'`: a constant, row 0 / col 29 (0-based), always shown.
- `LOCTXT 30A O 3 13`: a 30-character alpha output field at row 2 / col 12.
- `PLRHP 3 0O 5 13`: a 3-digit, 0-decimal numeric output field.
- `90 MSGTXT 40A O 7 3`: only shown when indicator 90 is on — this is how the game conditionally shows a status message.
- `CMD 1A B 22 31`: a 1-character input/output field — the player's typed command.

## See Also

- [`LANGUAGE.md`](./LANGUAGE.md) — how the RPGLE side of a program reads and writes these fields
- [`GLOSSARY.md`](./GLOSSARY.md) — DDS/RPG terminology
