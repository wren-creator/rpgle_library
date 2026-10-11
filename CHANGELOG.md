# Changelog

Versions follow [`docs/PUBLISHING.md`](./docs/PUBLISHING.md). Everything below is in the **Python port** unless it says otherwise; the JS and Go ports are unchanged since 1.0.0 (their parity work is listed under "Coming to the JS and Go ports" in the README). See "Status by language" and "Port parity" in the [README](./README.md).

## Unreleased (Python)

A patch release (1.1.1) once published.

### Fixed

- A key or record value for a `DISK` file follows the same rule as typing into a number field. Text that isn't a number (`'abc'`) used to escape as a raw `ValueError` instead of a clean error, and a blank number key crashed the same way; a blank now reads as zero. A number used as a character key reads the way RPG shows it, so `CHAIN 20` finds the key `20`, not `20.0`.

## Unreleased (JS and Go)

Behavior fixes, brought over from the Python port so all three agree, and then `DISK` files. Together that is new API with nothing broken, so the next release of each is a minor one (1.1.0). Each is covered by tests that mirror the Python ones, and `tools/parity/check.py` (new, and run in CI) checks the three ports against the same 119 cases.

### Fixed

- Character values compare as if blank-padded: `name = 'BOB'` is now true for a 20-character field holding `BOB`. Before, JS and Go compared exactly.
- `+` joins two texts. Text mixed with a number, `-` on text, and negating text are errors. Before, JS gave `"a1"` or `NaN` and Go gave `NaN` for every one of them, so Go could not join text at all.
- A number with decimals is shown with its decimal point (`0000048.75`) instead of being rounded to a whole number (`0000049`), so what is on screen survives being typed back in.
- `EXFMT` finds its record format ignoring case, and reports the DDS's own spelling.
- A `DIM` keyword, an O-spec, and a non-numeric length or decimals column are errors naming the field. They were silently skipped, so a program could run and quietly do the wrong thing. (A text literal that merely mentions `DIM(` is not an array.)
- Letters typed into a number field are an error naming the field. JS showed `00NaN` and Go quietly turned `abc`, and even `12x`, into 0. A blank number field reads as zero. Only plain numbers are numbers: `1_000`, `0x10`, `inf` and `1,5` are not.
- Dividing by zero, in an expression or with `DIV`, is an error instead of Infinity.
- Text is counted in characters. Go's `%LEN` and `%SUBST` counted bytes (`%LEN('café')` was 5, and `%SUBST` could split a character), and JS counted UTF-16 units (an emoji was 2). Go's `%SUBST` with one argument indexed past its slice and would have panicked, and JS's `%SUBST` mishandled a start before the first character.
- Only plain numbers are numbers in arithmetic and ordering as well: JS read `'0x10' * 1` as 16, and Go read `'1_000' * 1` as 1000.
- Numbers become text as every digit when whole and the shortest round-trip digits otherwise, never in exponent form, in all three ports. Go printed whole numbers past 9.2e18 incorrectly (it went through a 64-bit integer).
- A stray `ELSE`, `ENDIF`, `ENDDO`, `LEAVE`, `WHEN`, `OTHER` or `ENDSL` is a clean error (it crashed JS with a `TypeError`), and a `DOW`, `IF` or `SELECT` left open is a compile error naming it (both ports compiled it and crashed later).
- JS no longer crashes when resumed with nothing (`gen.next()`).
- Conditioning indicators (columns 9 to 11) are honored. Both ports read them and then ignored them, so `C   71   EVAL ...` ran whether or not indicator 71 was on. One on a block-structure op (`IF`, `DOW`, `SELECT`, ...) is an error, since skipping it would break the jumps, and one that isn't `nn`, `Nnn` or `LR` is an error too.
- A file name that isn't an object name (`../x`, `a/b`) is an error, as in the Python port, so a file name can never be a path.

### Added

- **`DISK` files on SQLite**, with the Python port's keyed-access model and its tests: `CHAIN`, `SETLL`, `SETGT`, `READ`, `READE`, `READP`, `READPE`, `WRITE`, `UPDATE`, `DELETE`, `KLIST`/`KFLD`, `%EOF`, `%FOUND` and `%EQUAL`, with resulting indicators in columns 71 to 76. Physical-file DDS is parsed by `parsePf` (JS) and `ParsePF` (Go), and every F-spec is now collected (`files`/`Files`; `file` is still the first `WORKSTN` one). SQLite stays out of the core: JS loads `better-sqlite3` (an optional peer dependency, Node 20 or newer) the first time a `Database` is created, and Go keeps `modernc.org/sqlite` v1.34.5, pure Go, in the separate `sqlitedb` package. New entry points: `runProgram(rpg, dds, { db, pfs })` (JS), `RunWith(prog, dds, RunOptions{DB, PFs})` and `BuildVarsWith` (Go). Examples: `examples/customer-inquiry.js` and `go/examples/customer-inquiry`. 98 parity cases run whole programs against seeded tables and compare every table afterward.
- **The built-in functions.** JS and Go had three (`%TRIM`, `%LEN`, `%SUBST`); they now have all twenty core functions the Python port has: `%TRIML`, `%TRIMR`, `%UPPER`, `%LOWER`, `%SCAN`, `%REPLACE`, `%CHAR`, `%DEC`, `%DECH`, `%INT`, `%INTH`, `%ABS`, `%SQRT`, `%REM`, `%DIV`, `%MIN`, `%MAX`. Also the `NOT` operator and the `*BLANKS` and `*ZEROS` constants. Go's are in the new `bifs.go`. `tools/parity/check.py` runs about 160 cases of them through all three ports, and mutation testing confirms it catches a break in any one function in any one port.
- `numericFields` (JS) / `NumericFields` (Go) on the screen data: the input fields that hold numbers, so a front end can refuse letters as they are typed.

## 1.1.0 (Python port only), 2026-10-10

The JS and Go ports stay at 1.0.0. **Requires Python 3.12 or newer; tested on 3.12, 3.13 and 3.14** (CI runs all three).

The Python port grew from a screen-and-calculation interpreter into a runtime that runs real RPG programs with files, reports and calls between programs, with no IBM i. It was built in milestones; the roadmap in the README tracks them.

### Added

**A command line, `rpgle`** (milestone 1)
- `rpgle run prog.rpgle` draws each `EXFMT` screen as a 24x80 grid, takes typed input and function keys, and works over SSH, in pipes and in CI. Ctrl-C exits quietly with code 130.
- `rpgle load file.pf data.csv --db shop.db` fills a DISK file from a CSV.
- Flags: `--dspf`, `--db`, `--pf-dir`, `-I` (include and program folders), `-D` (define a name), `--spool-dir`, `--page-length`, `--overflow-line`.

**DISK files on SQLite** (milestone 2)
- `CHAIN`, `SETLL`, `SETGT`, `READ`, `READE`, `READP`, `READPE`, `WRITE`, `UPDATE`, `DELETE`, `KLIST`/`KFLD`, `%EOF`, `%FOUND`, `%EQUAL`, with resulting indicators in columns 71-76.
- Physical-file DDS (`.pf`): record format, fields, `K` keys, `UNIQUE`. Partial keys, arrival-sequence files, `*LOVAL`/`*HIVAL`.
- A file's position is stored as a key, not a database cursor, so deleting or updating inside a read loop is safe.
- Conditioning indicators in columns 9-11 (`C  71`, `C N71`), which were parsed before but ignored.

**Free-form RPG** (milestone 3)
- `**FREE` source: `dcl-s`, `dcl-c`, `dcl-f`, `dcl-ds`, `dcl-proc`/`dcl-pi`/`dcl-pr`, `if`/`elseif`, `dow`, `dou`, `for`, `select`, `iter`, `leave`, `+=`, `NOT`, `ind`, `varchar`.
- Procedures: by-reference, `const` and `value` parameters, `options(*nopass)`, return values, recursion, `callp` and calls inside expressions; fresh locals per call.
- Data structures: `qualified`, `likerec`, `likeds`, `extname`; reading a record into one (`chain key file ds`); `clear`.
- Built-in functions: `%CHAR`, `%DEC`/`%DECH`, `%INT`/`%INTH`, `%ABS`, `%SQRT`, `%REM`, `%DIV`, `%MIN`/`%MAX`, `%SCAN`, `%REPLACE`, `%UPPER`/`%LOWER`, `%TRIML`/`%TRIMR`, `%ELEM`, `%LOOKUP`.
- Arrays: `dim(n)` on `dcl-s` and `dcl-ds`, `sorta`, whole-array assignment, array parameters.

**Includes, calls and printing** (milestone 4)
- `/COPY` and `/INCLUDE` with `/DEFINE`, `/UNDEFINE`, `/IF [NOT] DEFINED`, `/ELSEIF`, `/ELSE`, `/ENDIF`, `/EOF`; errors name the copybook and its line; includes can't leave the program's folder or an `-I` folder.
- Calls between programs, found by name in the caller's folder then `-I` folders: free-form `dcl-pr ... extpgm` and the program's own `dcl-pi`, and fixed-form `CALL`/`PARM`/`PLIST`/`*ENTRY`. A program that ends with `*INLR` off stays active and keeps its state; a program can't call itself.
- Printer files: `.prtf` DDS, `WRITE` of a record format, page layout (`SKIPB`, `SKIPA`, `SPACEB`, `SPACEA`, absolute/relative/same-line positions), overflow (`OFLIND`, `*INOA`-`*INOG`, `*INOV`), `PAGNBR`, edit codes `1 2 3 4 A B C D J K L M Z`, text spool output.

**Examples:** `custinq` (fixed and free-form), `custinq-copy`, `custinq-call` with `custlookup`, `custreport` with `custrpt.prtf`, and a `qrpglesrc/` copybook.

### Changed (behavior corrections, Python only)

These are fixes. JS and Go still have the old behavior (see "Port parity" in the README).

- Character comparison ignores trailing blanks, as RPG does: `name = 'BOB'` is now true for a 20-character field holding `BOB`.
- `+` joins two texts; text mixed with a number, or `-` on text, is an error that says to use `%CHAR` or `%DEC`, instead of producing `NaN`.
- A numeric field with decimals is shown with its decimal point (`0000048.75`), not rounded to a whole number.
- `EXFMT` format names, file names and variable names are case-insensitive.
- A fixed-form `DIM` keyword and any O-spec are now errors. They were silently ignored, so a program could run and quietly print nothing.
- Built-in functions now reject a position, length or count that is not a number (it was a raw `ValueError`), limit `%DEC`'s decimals to 0 to 63, and print numbers in plain decimal, never exponent form.
- Numeric text is strict: only an optional sign, ASCII digits, an optional decimal point and an optional exponent are numbers. Python's own `float()` also accepts `1_000` and Arabic-Indic digits, which JS and Go reject, so Python was tightened to match.
- `requires-python` is now `>=3.12` (it said `>=3.9`, which was never tested). Nothing in the code needs anything newer than 3.9, so older versions may well work, but they are unsupported until someone runs the suite on them.
- `parse_rpgle()` takes `path`, `include_dirs` and `defines`; `run_program()` takes `db`, `pfs`, `loader` and `spool`; `build_vars()` takes `pfs` and `prtfs`. All are optional, so existing callers are unaffected.

### Fixed

Found by reviewing the new code before release, and by feeding the parsers and interpreter more than two million malformed inputs. A small, deterministic version of that fuzzing now runs in the test suite (`tests/test_fuzz.py`).

- **Path traversal through file names.** A `dcl-f ../outside/leak printer;` (or the fixed-form equivalent) read a `.prtf`, `.pf` or `.dspf` file from outside the program's folder. File names must now be IBM i object names (letters, digits, `_ $ # @`), checked where they enter the parser and again where they become a path. Includes and program calls were already restricted; they now have tests for symlinks, absolute paths and `..`.
- **Typing letters into a number field crashed the program** with a Python traceback. The interpreter now says `'abc' is not a number, so it can't be put in the numeric field custno`; a blank numeric field reads as zero; and the `rpgle` command refuses letters in a numeric field as you type and asks again. `ScreenData` gained an optional `numeric_fields` for front ends that want to do the same.
- Division by zero (`1 / 0`, fixed-form `DIV`) is an error, not a `ZeroDivisionError`.
- A `DOW`, `DOU`, `FOR`, `IF` or `SELECT` with no matching end is an error at compile time, not a crash on a missing jump address.
- Malformed numbers in a fixed-form D-spec, display DDS, physical DDS or printer DDS length, decimals, line or position column are errors naming the field, not a raw `ValueError`. So are an empty `dcl-f`, negating text (`-'a'`), a control character in a `/COPY` name, and a name too long for the filesystem (which raised `OSError` on Python 3.13 but not 3.14, so it was caught only by running the fuzz test on the version CI uses).

### Not supported (each is an error, not a silent skip)

O-specs and program-described files; `extproc` and service programs; `MONITOR`, `%ERROR` and the `(E)` extender; date, time and pointer types; arrays inside data structures; `DESCEND` keys and joined logical files; overprinting and `DATE`/`TIME` print fields; `/IF` conditions other than `DEFINED`. The full list is in [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) under "What's Explicitly Out of Scope".

### Documentation

New or rewritten: the free-form, data structure, procedure, array, `/COPY`, program-call, printer-file and DISK-file sections of `docs/LANGUAGE.md`; physical-file and printer-file DDS in `docs/DDS_REFERENCE.md`; a dozen new glossary terms; the Python API reference and module map; the README's "What you can run today", "Status by language" and "Port parity".

## 1.0.0

The original release: a zero-dependency RPG IV and DDS parser and interpreter for JS, Go and Python, with fixed-form parsing, `EXFMT` as a generator (or goroutine, in Go), the core opcodes, and display-file DDS.
