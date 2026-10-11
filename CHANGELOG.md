# Changelog

Versions follow [`docs/PUBLISHING.md`](./docs/PUBLISHING.md). Everything below is in the **Python port** unless it says otherwise; the JS and Go ports are unchanged since 1.0.0. See "Status by language" and "Port parity" in the [README](./README.md).

## Unreleased

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
- `parse_rpgle()` takes `path`, `include_dirs` and `defines`; `run_program()` takes `db`, `pfs`, `loader` and `spool`; `build_vars()` takes `pfs` and `prtfs`. All are optional, so existing callers are unaffected.

### Fixed

Found by reviewing the new code before release, and by feeding the parsers and interpreter more than two million malformed inputs. A small, deterministic version of that fuzzing now runs in the test suite (`tests/test_fuzz.py`).

- **Path traversal through file names.** A `dcl-f ../outside/leak printer;` (or the fixed-form equivalent) read a `.prtf`, `.pf` or `.dspf` file from outside the program's folder. File names must now be IBM i object names (letters, digits, `_ $ # @`), checked where they enter the parser and again where they become a path. Includes and program calls were already restricted; they now have tests for symlinks, absolute paths and `..`.
- **Typing letters into a number field crashed the program** with a Python traceback. The interpreter now says `'abc' is not a number, so it can't be put in the numeric field custno`; a blank numeric field reads as zero; and the `rpgle` command refuses letters in a numeric field as you type and asks again. `ScreenData` gained an optional `numeric_fields` for front ends that want to do the same.
- Division by zero (`1 / 0`, fixed-form `DIV`) is an error, not a `ZeroDivisionError`.
- A `DOW`, `DOU`, `FOR`, `IF` or `SELECT` with no matching end is an error at compile time, not a crash on a missing jump address.
- Malformed numbers in a fixed-form D-spec, display DDS, physical DDS or printer DDS length, decimals, line or position column are errors naming the field, not a raw `ValueError`. So are an empty `dcl-f`, negating text (`-'a'`), and a control character in a `/COPY` name.

### Not supported (each is an error, not a silent skip)

O-specs and program-described files; `extproc` and service programs; `MONITOR`, `%ERROR` and the `(E)` extender; date, time and pointer types; arrays inside data structures; `DESCEND` keys and joined logical files; overprinting and `DATE`/`TIME` print fields; `/IF` conditions other than `DEFINED`. The full list is in [`docs/LANGUAGE.md`](./docs/LANGUAGE.md) under "What's Explicitly Out of Scope".

### Documentation

New or rewritten: the free-form, data structure, procedure, array, `/COPY`, program-call, printer-file and DISK-file sections of `docs/LANGUAGE.md`; physical-file and printer-file DDS in `docs/DDS_REFERENCE.md`; a dozen new glossary terms; the Python API reference and module map; the README's "What you can run today", "Status by language" and "Port parity".

## 1.0.0

The original release: a zero-dependency RPG IV and DDS parser and interpreter for JS, Go and Python, with fixed-form parsing, `EXFMT` as a generator (or goroutine, in Go), the core opcodes, and display-file DDS.
