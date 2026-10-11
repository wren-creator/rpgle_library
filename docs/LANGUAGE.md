# RPGLE Language Reference

This is a narrative walkthrough of the RPG IV (RPGLE) subset `rpgle_library` actually parses and runs. See [`GLOSSARY.md`](./GLOSSARY.md) if a term here is unfamiliar, and the mission section in the root [`README.md`](../README.md) for why this exists.

## Overview: Spec Types, and Free-Form

Fixed-form RPG source is organized into "specs," identified by a letter in column 6. This library reads F, D and C specs (and refuses O specs, below). Free-form source (`**FREE`, Python port) has no columns and is covered in "Free-Form RPG" further down; it produces the same records, so everything after the parsing stage is shared.

### H-spec — not supported

Header specs set compile-time/program-level options (date format, debug flags, etc.). Nothing this library does depends on them, so they're skipped entirely rather than parsed. (Free-form `ctl-opt` is handled the same way.)

### F-spec: WORKSTN and DISK files (fixed-form; free-form uses `dcl-f`)

File specs describe the files a program uses. Every F-spec is collected in `Program.files`, and `Program.file` is still the program's `WORKSTN` (display) file. `DISK` files are **Python port only so far**, and must be externally described (column 22 = `E`): their layout comes from a physical-file DDS (see "DISK Files" below and [`DDS_REFERENCE.md`](./DDS_REFERENCE.md)). The key is whatever the DDS says; the `K` in column 34 is read but the DDS decides.

### D-spec — standalone fields only

Definition specs declare variables. Supported: standalone fields (`deftype` `S`) with a length, decimal positions, and an optional `INZ(...)` initial value. **Not supported in fixed-form:** data structures (`DS`) and their subfields, `LIKE`/`LIKEDS`, prototypes (`PR`/`PI`), and arrays: a `DIM` keyword is an error, not silently ignored. All of those exist in free-form (Python port). Most of a real program's variables don't even need a D-spec here, see "Externally-Described Files" below.

### C-spec: fixed-form (free-form in the Python port)

Calculation specs are the executable statements. In the JS and Go ports only fixed-form C-specs are parsed, which is why the original example program's header comment calls itself "V4R3-safe." The Python port also reads free-form source. Mixed source (`/free ... /end-free` blocks inside fixed-form) is not supported: a file is one or the other. A C-spec line captures indicators, factor 1, the opcode, and everything from column 36 onward as both a free-form expression (for `EVAL`/`IF`/`DOW`/`WHEN`) and fixed classic factor-2/result columns (for `DIV`/`MVR`-style ops), see `src/rpgle.js`'s header comment for the exact column map.

### O-spec: refused

Output specifications are how classic RPG describes printed output without a DDS. They are **an error**, not ignored: a program that quietly printed nothing would be worse than one that says so. Print with an externally described printer file instead (see "Printer Files").

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
| `RETURN` | Ends the program (or the procedure, in free-form) | `RETURN` |
| `ELSEIF` / `DOU` / `FOR` / `ITER` / `ENDFOR` | Free-form control flow, also reachable as fixed-form opcodes except `FOR`, which is free-form only | `FOR i = 1 TO 10` |
| `CHAIN` `SETLL` `SETGT` `READ` `READE` `READP` `READPE` `WRITE` `UPDATE` `DELETE` | DISK file access and printing (Python port), see "DISK Files" and "Printer Files" | `custno CHAIN CUSTMAST 71` |
| `KLIST` / `KFLD` | Names a composite key; `KFLD`'s field goes in the result column | `ORDK KLIST` |
| `CALL` / `PARM` / `PLIST` | Calls another program, see "Calling Other Programs" | `CALL 'CUSTLOOKUP'` |
| `CALLP` | Free-form procedure and program calls | `callp total(a : b)` |
| `CLEAR` / `SORTA` | Blank or zero a variable, array or data structure; sort an array | `clear cust` |

Anything else throws `Unsupported RPG opcode` at compile time, or `Unimplemented RPG instruction` at run time if it somehow got past compile. **Not supported at all:** `CALLB`, `MOVE`/`MOVEL`, `Z-ADD`, `ADD`/`SUB`/`MULT` and the other classic arithmetic opcodes (use `EVAL`), `DSPLY`, `MONITOR`, `OPEN`/`CLOSE`, `TEST*`, `LOOKUP`, `XFOOT`, `SCAN`/`SUBST`/`CAT` (use the built-in functions).

## Free-Form RPG

*Python port only so far.* Source whose first line is `**FREE` is free-form. `parse_rpgle()` detects that and hands it to `freeform.py`, which produces the same `Program`/`FileSpec`/`DSpec`/`Statement` records the fixed-form parser does. The compiler, interpreter and file runtime are shared, so a free-form `chain key custmast;` is exactly a fixed-form `CHAIN` with no result indicators. `programs/custinq-free.rpgle` is the free-form twin of `custinq.rpgle`.

A statement is everything up to a semicolon: it can span lines, and several can share one. `//` comments and `/* */` blocks are stripped. Keywords are case-insensitive, as are variable and file names. Errors name the source line.

| Area | Supported |
|---|---|
| Declarations | `ctl-opt` (ignored), `dcl-s`, `dcl-c`, `dcl-f`, `dcl-ds` (see "Data structures"), `dcl-proc`/`dcl-pi` (see "Procedures"), `dcl-pr` (parsed and skipped) |
| Types | `char`, `varchar` (no padding, `%LEN` is its content length, truncated at its declared size), `ind`, `int`, `uns`, `packed`, `zoned`, `bindec`, `float`, `like(x)` of an earlier `dcl-s`; `inz` with a value, `*blank`, `*zero`, `*on`, `*off` |
| Control flow | `if`/`elseif`/`else`/`endif`, `dow`, `dou`, `enddo`, `for ... to\|downto ... by`/`endfor`, `select`/`when`/`other`/`endsl`, `leave`, `iter`, `return` |
| Subroutines | `begsr`, `endsr`, `exsr` |
| Calls | `callp name(args);` or just `name(args);`, and `name(args)` inside any expression |
| Arrays | `dim(n)` on `dcl-s` and `dcl-ds`, `%elem`, `%lookup`, `sorta`, `clear`, whole-array assignment (see "Arrays") |
| Other | `clear` a variable, an array, or a whole data structure |
| Assignment | `x = expr;`  `x += expr;` (also `-=` `*=` `/=`)  `eval x = expr;`  `*in03 = *on;` |
| Files | `chain` `setll` `setgt` `read` `reade` `readp` `readpe` `write` `update` `delete` `exfmt`; keys can be an expression or a list: `chain (cust : ord) orders;` |
| Operators | the expression language below, now including `NOT` (looser than comparison, tighter than `AND`) |

**`FOR`** evaluates its start, limit and `BY` once, on entry, as RPG does. `BY` must be positive; use `downto` to count down. The index is left one step past the limit when the loop finishes.

**`DOU`** tests at the bottom, so the body always runs at least once. `ITER` jumps to the next test (`DOW`, `DOU`) or the next step (`FOR`); `LEAVE` exits the innermost loop.

**`ind`** variables hold `'1'` or `'0'`, as in real RPG, but read and assign as booleans: `ok = *on; if not ok; ...`.

**Character comparison** ignores trailing blanks (`name = 'BOB'` is true for a 20-character `name` holding `BOB` and 17 blanks), as RPG does. This applies to fixed-form too.

**Opcode extenders** `(n)`, `(p)`, `(h)` are accepted and ignored. `(e)` raises an error, since it needs `%ERROR`.

Not supported yet, and each raises an error naming the line rather than being skipped: arrays inside data structures, external procedures (`extproc`), date/time types, `monitor`, `reset`, `/copy`, `dcl-f` keywords other than the device, `keyed` and `usage`. Mixed source (`/free ... /end-free` blocks inside fixed-form) is not supported either: the whole file is one or the other.

### Data structures

```
dcl-ds cust qualified likerec(custrec);     // layout borrowed from a file's record format
chain custno custmast cust;                 // reads INTO cust, leaving the program's own fields alone
if %found(custmast);
  total += cust.bal;
endif;
cust.name = 'ERIN';
write custrec cust;                         // writes FROM cust
```

A data structure's layout comes from one of: its own subfield lines (`dcl-ds ... end-ds`), `likerec(format)` (a record format of any DISK file, or of the display file), `likeds(other)` (a copy of an earlier data structure, always qualified), or `extname('FILE')` (a whole file). The borrowed forms need no subfields, and the `end-ds` after them is optional.

Each subfield is its own program variable. `qualified` structures name them `cust.name`; unqualified ones are just `name`, which is how a plain `dcl-ds` with subfields has always worked. `chain`, `read`, `reade`, `write` and `update` take a data structure as a trailing operand, and the subfield names must match the record's field names. `clear cust` blanks every character subfield and zeroes every number.

The limit worth knowing: a data structure is a *group of variables*, not a block of memory. There is no `pos()`, `overlay()` or `samepos()` (they raise an error), no `%size(ds)`, no assigning one data structure to another (`a = b`), no `eval-corr`, and no data structure as a subfield of another. A data structure's name on its own isn't a variable, so `chain k file cust` works but `msg = cust` doesn't. Arrays of data structures (`dim`) arrive with arrays.

### Procedures

```
dcl-s total packed(9:2);
total = addtax(100);              // in an expression
report(total);                    // as a statement (callp is optional)
*inlr = *on;
return;                           // end of the main line

dcl-proc addtax;
  dcl-pi *n packed(9:2);          // *n, then the return type
    amount packed(9:2) const;
  end-pi;
  dcl-s rate packed(5:4) inz(0.0825);   // local to this procedure
  return amount + amount * rate;
end-proc;
```

Statements outside any `dcl-proc` are the main line and run first, in the order written. Procedures can be declared above or below it. A procedure called but not defined in the same source (an external program or service program) is an error: there's nothing to link to.

**Parameters** follow RPG's three modes. The default is by **reference**: the procedure works on the caller's own variable, so changes show through, and the argument must be a variable. `const` is read-only and accepts a variable, a literal or an expression. `value` gets a private copy. `options(*nopass)` lets the caller leave the argument off, and the parameter then holds its default (blank or zero). `*omit` and `*varsize` are not supported. For by-reference and const-aliased parameters the kind (text or number) is checked but not the length.

**Scope.** Variables declared in a procedure (`dcl-s`, `dcl-c`, `dcl-ds`) are local, start fresh on every call, and shadow a global of the same name. A procedure can read and change globals. Subroutines (`begsr`) declared in a procedure are local to it, and the main line's subroutines are not visible inside procedures. Indicators (`*INnn`, `*INLR`) are global, as in RPG, so a procedure can set `*in90` for the main line to see. `dcl-f` must be at the top of the program, not inside a procedure; files and their `%FOUND`/`%EOF` state are shared.

**Return values.** `return value;` converts to the declared return type, so a `packed(5)` function returning `7 / 2` gives 3. A procedure with a return type must `return` a value or it's an error. One with no return type may be called either way and its value is ignored.

**Recursion** works, with a fresh set of locals per call. The depth is bounded by Python's stack: roughly 75 levels deep when the recursion goes through expressions (`return 1 + depth(n - 1)`) and 300 or more when it goes through `callp` statements. Past that you get an error, not a crash.

**Screens.** A procedure called with `callp` or as a bare statement can `exfmt`, and the screen reaches whoever is driving `run_program`, same as the main line's. A procedure called from inside an *expression* cannot, because an expression can't pause for the user, and you get an error saying to use `callp`.

Not supported: procedures taking data structures as parameters, `*omit`, `export`/`import` linkage (`export` is parsed and ignored), `static` locals, main-program parameters (`dcl-pi` outside a procedure), `%PARMS`, `%ADDR`.

### Arrays

```
dcl-s price packed(7:2) dim(50);               // 50 elements, numbered from 1
dcl-ds line qualified dim(10);                 // an array of data structures
  sku char(6);
  qty packed(5);
end-ds;

for i = 1 to %elem(price);
  price(i) = i * 1.5;
endfor;
line(3).qty += 10;
n = %lookup('A100' : skus);                    // 0 if not found
sorta(d) price;                                // descending
```

Elements are numbered from 1 and the subscript can be any expression, including another array element. A subscript outside 1 to `dim` is an error, never a wraparound. `dim` takes a whole number or a numeric `dcl-c`. `inz` applies to every element.

Each element is its own variable, so it keeps the array's length and decimals: `%char(m(1))` for a `packed(9:2)` array shows `1.50`. For an array of data structures each element is a set of subfields, read and written as `line(i).qty`; the data structure must be `qualified`, and can borrow its layout with `likerec` or `likeds`, as in "Data structures".

| Operation | Notes |
|---|---|
| `%elem(arr)` | the number of elements |
| `%lookup(value : arr [: start [: count]])` | 1-based index of the first element equal to `value`, comparing text without trailing blanks, or 0. Works on number, text and indicator arrays. The `%LOOKUPLT`/`LE`/`GE`/`GT` forms are not supported |
| `sorta arr;` `sorta(d) arr;` | sort ascending or descending, in place. Text sorts by its characters, not EBCDIC order |
| `clear arr;` `clear arr(i);` | blank, zero or off. `clear line(2)` clears one element's subfields |
| `arr = other;` | copies element by element; both must have the same `dim` |
| `arr = 0;` `arr = *blanks;` `arr = *zeros;` | sets every element |

Procedures take arrays as parameters declared with `dim(n)`, which must match the argument's `dim` exactly. By reference and `const` share the caller's array; `value` copies it. A single value where an array is expected, or an array where a single value is, is an error naming the parameter.

An array name on its own (`x = arr;`) is an error that tells you to subscript it, rather than quietly doing something odd. `*BLANKS` and `*ZEROS` also work in ordinary assignments now, e.g. `name = *blanks`.

Not supported: arrays inside data structures (a `dim` on a subfield), arrays of data structures as `chain`/`write` targets, `%subarr`, `%xfoot`, `sorta` on a data structure array, `*in(n)` indicator arrays, compile-time and pre-runtime arrays (`**CTDATA`), and fixed-form `DIM` (which is now an error rather than being silently ignored, as it was before).

## Source Includes (`/COPY`)

*Python port only so far.* `/COPY` and `/INCLUDE` splice another file into the source before it is parsed, in fixed-form and free-form alike. The preprocessor lives in `python/src/rpgle/include.py`.

```
/copy qrpglesrc,custfiles        // shared declarations
```

On IBM i a member is named `library/file,member`. Here that maps to paths on disk, searched in the including file's folder and then each `-I` folder:

| Directive | Looks for (in each folder, in this order) |
|---|---|
| `/copy defs` | `defs` |
| `/copy qrpglesrc,defs` | `qrpglesrc/defs`, then `defs` |
| `/copy lib/qrpglesrc,defs` | `lib/qrpglesrc/defs`, `qrpglesrc/defs`, then `defs` |
| `/copy 'inc/defs.rpgleinc'` | exactly that path |

Each name is tried as written, in lower case and in upper case, with no extension and then `.rpgleinc` `.rpgle` `.copy` `.inc` `.rpg` `.sqlrpgle`. Includes nest up to 32 deep; a file that includes itself, directly or not, is an error.

**Guards.** Real copybooks protect themselves from being included twice, so the conditional directives are supported:

```
/if defined(CUSTFILES_INCLUDED)
/eof
/endif
/define CUSTFILES_INCLUDED
```

`/DEFINE name`, `/UNDEFINE name`, `/IF DEFINED(name)`, `/IF NOT DEFINED(name)`, `/ELSEIF`, `/ELSE`, `/ENDIF` and `/EOF` (stop reading this member, even inside an open `/IF`, which is what makes the guard above work). Names are case-insensitive. A branch that isn't taken is skipped whole: its `/COPY`s aren't opened and its `/DEFINE`s don't happen. Conditions must balance within each member. Other conditions (`*VERSION`, `*CRTBNDRPG`) are an error, not a silent false. `-D NAME` on the command line pre-defines a name.

**Errors name the real file.** The parser sees one merged text, but a mistake in a copybook is reported as `custfiles.rpgleinc:7: ...`, not a line number in text nobody wrote.

**Safety.** An include can only resolve inside the program's own folder or an `-I` folder. `/copy '/etc/passwd'` and `/copy '../../elsewhere'` are refused, so running someone else's source can't make it read arbitrary files. Add an `-I` folder to allow a shared library of copybooks.

A `**FREE` line at the top of a member is dropped: a member can't change the source's form, and a fixed-form program can't include a free-form member or the reverse. Other slash lines (`/FREE`, `/END-FREE`, `/EJECT`, `/SPACE`, `/TITLE`) pass through and are ignored.

```bash
rpgle run prog.rpgle -I ~/rpg/copybooks -D DEBUG
```

## Calling Other Programs

*Python port only so far.* One program can call another by name. The called program is found by **lookup**, not registration: `rpgle` looks for `custlookup.rpgle` (then `.rpg`, `.sqlrpgle`) in the calling program's folder, then in the main program's folder, then in each `-I` folder. A library prefix (`'MYLIB/CUSTLOOKUP'`) is accepted and ignored, since there are folders here, not libraries. A program name is an IBM i object name, never a path, so a call can't reach outside the folders it searches. File names (`dcl-f`, F-specs) follow the same rule, because each becomes a file on disk (`custmast.pf`, `custrpt.prtf`): letters, digits and `_ $ # @`, no dots or slashes.

```
// the caller                              // custlookup.rpgle, the called program
dcl-pr custlookup extpgm;                  **free
  id    packed(5) const;                   dcl-pi *n;
  nm    char(20);                            id    packed(5) const;
end-pr;                                      nm    char(20);
                                           end-pi;
custlookup(custno : who);                  ...
                                           *inlr = *on;
```

`dcl-pr ... extpgm` names the program (`extpgm('OTHERNAME')` if the call name differs from the program's). The called program's own `dcl-pi` is its parameter list. It follows the same rules as a procedure's: by reference by default, `const`, `value`, `options(*nopass)`, arrays with a matching `dim`. Call it with `callp name(args);` or just `name(args);`. A program can't be called from inside an expression, since it has no return value.

**Each program is its own world.** It has its own variables, indicators (`*IN90` in the callee is not `*IN90` in the caller) and files, with its own position in each. They share the database. A called program finds its own display file (`<workstn file>.dspf`, then `<program>.dspf`) and its physical-file DDS (`<file>.pf`) the same way the main program does, and a screen it shows (`exfmt`) reaches whoever is driving the main program.

**`*INLR` decides whether it stays.** A called program that ends with `*INLR` **on** is finished: the next call starts it from scratch. One that ends with `*INLR` **off** stays active, as in real RPG. Its variables, indicators and file positions survive to the next call, which re-enters its main line from the top with the new parameters. That's how a subprogram keeps a running total or reads a file one record per call. (A program that fails with an error is not kept.)

**Fixed-form** programs call and are called the classic way, and the two forms mix freely:

```
     C     *ENTRY        PLIST                       the called program's parameters
     C                   PARM                    ID
     C                   PARM                    NM
                                                       the caller
     C                   CALL      'CUSTLOOKUP'                       72  <- error
     C                   PARM                    WANT                     (lo, 73-74)
     C                   PARM                    WHO
```

`PARM` names its field in the result column (factor 1 and 2 aren't supported and are an error, not ignored). A callee's `*ENTRY` fields must be declared as standalone fields (D-spec `S`) in the callee; their types decide what a caller may pass. A caller can write its parameters under `CALL` or name a shared `PLIST` in the result column: `CALL 'PGM' MYPLIST`. `CALL PGMVAR` (no quotes) takes the program name from a variable. Fewer `PARM`s than the callee declares is allowed, as in RPG, and a fixed-form callee can be passed an expression by a free-form caller (it works on a temporary).

`CALL`'s resulting indicators: **lo (73-74)** is set when the call can't be made (program not found, parameters of the wrong kind, a program calling itself), and **eq (75-76)** is set when the called program ended with `*INLR` on. With no lo indicator, a failed call raises instead.

**A program can't call itself**, directly or through another program. That's a runtime error in real RPG (use a procedure for recursion), and here too. Calling the main program by its own name counts.

**Errors.** A call that can't be made (program not found, parameters that don't fit, a program calling itself) is a `CallError`. An error raised *while the called program runs* is that program's, and propagates with its file name: `broken.rpgle:3: unrecognized statement`.

From Python, pass a `ProgramLoader`: `run_program(prog, dds, db, pfs, loader=ProgramLoader([folder], main=path))`. The `rpgle` command builds one for you. A called program that uses files needs `--db` even if the main program doesn't: `programs/custinq-call.rpgle` is a screen with no files that calls `programs/custlookup.rpgle`, which owns the customer file.

Not supported yet: `extproc` and service programs, `CALLB`, `extpgm` with a variable name, `*OMIT`, `%PARMS`, activation groups, and the called program's `return` value.

## Printer Files

*Python port only so far.* A program prints by declaring a printer file and writing record formats to it:

```
dcl-f custrpt printer oflind(*inof);          // fixed-form: FCUSTRPT O E PRINTER OFLIND(*INOF)

write heading;
dow not %eof(custmast);
  if *inof;                                   // the overflow indicator came on...
    write heading;                            // ...so start a new page (SKIPB(1) does that)
    *inof = *off;
  endif;
  write detail;
  read custmast;
enddo;
write total;
```

The layout comes from the file's DDS, `custrpt.prtf`, found beside the program (or in `--pf-dir` or an `-I` folder). Its fields become program variables, like any externally described file's. `WRITE` takes a record format; it's the only operation a printer file allows. `programs/custreport.rpgle` and `programs/custrpt.prtf` are a complete worked example. See [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) for the DDS.

**Where output goes.** Each printer file a program opens becomes one text "spool file". `rpgle run` shows them on stdout after the program ends, under a heading, with `----- page break -----` between pages; `--spool-dir DIR` writes `DIR/<file>.txt` instead, with a form feed (`\f`) on a line of its own between pages. A printer file that printed nothing produces no output. If a program and a program it calls open the same printer file, each gets its own spool file (`custrpt.txt`, `custrpt-2.txt`), as in real life.

**The page model.** Each open printer file remembers the last line printed on the current page. To write a record:

1. **Before.** `SKIPB(n)` goes to line n, starting a new page first if line n has been reached already. `SPACEB(n)` goes n lines down from the last line. With neither, the record starts on the next line.
2. **Body.** Each field's line is absolute (`5` is page line 5), relative (`+1` is one below the previous field, or below the last line printed for the first field) or blank (the same line as the previous field). An **absolute line that has been reached already starts a new page**: print a record at line 5 twice and the second one lands on the next page, which is the classic printer-DDS surprise.
3. **After.** `SKIPA(n)` or `SPACEA(n)` position the next record. The default is the next line.

A record that doesn't fit on the rest of the page starts a new one; one taller than a whole page is an error. Fields written to the same place overlay each other.

**Overflow.** When the last line printed in a record is at or past the overflow line, the file's `OFLIND` indicator turns on. It stays on until the program turns it off, which is why the loop above resets it right after printing its headings. `OFLIND` can name an indicator (`*INOF`, `*INOA` to `*INOG`, `*INOV`, `*IN90`) or an `ind` variable. Defaults match `CRTPRTF`: **66 lines to a page, overflow at line 60**; `--page-length` and `--overflow-line` change them, and the API takes the same two numbers (`Spool(page_length=..., overflow_line=...)`).

**Numbers.** A numeric field with no edit code prints as zero-filled digits with no decimal point and no sign, as a real printer file would, and a negative value is therefore an error rather than a silently lost sign. Add `EDTCDE` for readable amounts:

| Code | Commas | Sign | Zero prints as |
|---|---|---|---|
| `1` / `2` | yes | none | `0.00` / blanks |
| `3` / `4` | no | none | `0.00` / blanks |
| `A` / `B` | yes | `CR` after a negative | `0.00` / blanks |
| `C` / `D` | no | `CR` after a negative | `0.00` / blanks |
| `J` / `K` | yes | `-` after a negative | `0.00` / blanks |
| `L` / `M` | no | `-` after a negative | `0.00` / blanks |
| `Z` | no (and no decimal point) | none | blanks |

An edited field is always the same width however big the value is, so columns line up. The width is the field's digits, plus commas, the decimal point, and room for the sign.

`PAGNBR` on a numeric field makes it the page number, counting from 1 for each printer file opened.

Not supported, and each is an error, never a silent blank: O-specs and program-described printer files (the old `FQPRINT O F 132 PRINTER` with output specifications), overprinting (`SPACEA(0)`), `DATE`, `TIME`, `USER` and `SYSNAME` fields, `EDTWRD`, edit codes `X`, `Y` and the others not in the table, an edit code's fill character, barcodes, and overlays. Keywords that only change appearance (`TEXT`, `COLHDG`, `DSPATR`, `FONT` and the like) are ignored. A program must be run with a spool: `rpgle run` provides one, and from Python pass `spool=Spool(directories=[...])` to `run_program`.

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

Two-digit indicators (`*IN90`, `*IN03`, ...), set with `EVAL *in90 = *on`/`*off`, tested in any expression, and used by DDS to conditionally show/hide fields (see [`DDS_REFERENCE.md`](./DDS_REFERENCE.md)). In fixed-form, columns 9-11 of a C-spec condition it on an indicator (`71`, `N71`, `LR`, or an overflow indicator), and columns 71-76 receive an opcode's resulting indicators.

### Overflow indicators (`*INOA`-`*INOG`, `*INOV`)

The indicators a printer file's `OFLIND` names, usually `*INOF`. They are set on when a record reaches the overflow line, and the program turns them off. See "Printer Files". (Python port.) Each program, including each program it calls, has its own set of indicators.

### `*INLR` / `*INRT`

`*INLR` ("last record") means "this program is finished": set on, the runtime closes the program, and the next call starts it afresh. Left off, a called program stays active and keeps its variables and file positions for its next call. For the main program, which just ends, it makes no difference. For a program that is *called* (Python port), it decides whether the program is kept: see "Calling Other Programs". It's an ordinary indicator otherwise, set with `*inlr = *on`. `*INRT` ("return") is tracked the same way but isn't used by any shipped content.

### Response indicators (`CFnn`/`CAnn`) and the reset-on-read rule

When `EXFMT` resumes, the interpreter first resets every indicator the *current format's own* `CFnn`/`CAnn` keywords own to off, then turns on the one matching whatever key was pressed. This matches real RPG behavior: response indicators from a different format's key mappings are untouched, since they belong to a different screen.

## Expression Language

`expr.js`/`expr.go` evaluate `EVAL` right-hand sides and `IF`/`DOW`/`WHEN` conditions.

### Grammar

```
expr       := orExpr
orExpr     := andExpr (OR andExpr)*
andExpr    := notExpr (AND notExpr)*
notExpr    := NOT notExpr | compare            (Python port; looser than comparison, tighter than AND)
compare    := additive ((= | <> | <= | >= | < | >) additive)?
additive   := term ((+ | -) term)*
term       := unary ((* | /) unary)*
unary      := '-' unary | primary
primary    := NUMBER | STRING | '*ON' | '*OFF'
            | '*IN' DIGIT DIGIT | '*INLR' | '*INOA'..'*INOG' | '*INOV'
            | '*BLANKS' | '*ZEROS'
            | '%' NAME '(' expr (':' expr)* ')'
            | NAME                              a variable, or ds.subfield
            | NAME '(' expr ')' ('.' NAME)?     an array element, or arr(i).subfield
            | NAME '(' expr (':' expr)* ')'     a procedure call
            | '(' expr ')'
```

The JS and Go grammars stop at the `*IN` forms and the `%` functions: no `NOT`, no figurative constants beyond `*ON`/`*OFF`, and no subscripts or calls, since those ports have no arrays, data structures or procedures.

### Built-in functions

The JS and Go ports have `%TRIM(str)`, `%LEN(str)` and `%SUBST(str : start [: len])` (1-based start, matching real RPG). The Python port has these and more:

| Function | Result |
|---|---|
| `%TRIM` `%TRIML` `%TRIMR` | text with blanks removed from both ends, the left, the right |
| `%UPPER` `%LOWER` | text in that case |
| `%LEN(x)` | length: the declared size of a `char`, the current length of a `varchar` |
| `%SUBST(s : start [: len])` | part of a string |
| `%SCAN(find : in [: start])` | 1-based position, or 0 if absent |
| `%REPLACE(new : in [: start [: len]])` | replaces `len` characters at `start`; `len` of 0 inserts |
| `%CHAR(x)` | number or indicator as text. A numeric variable keeps its declared decimals: `%CHAR(money)` for a `packed(9:2)` holding 5 is `5.00`, but `%CHAR(money + 1)` is `6` |
| `%DEC(x : len : dec)` `%DECH` | number with `dec` decimals, truncated (`%DECH` rounds half up) |
| `%INT(x)` `%INTH` | whole number, truncated (`%INTH` rounds) |
| `%ABS` `%SQRT` `%REM(a : b)` `%DIV(a : b)` | what they say; `%REM`/`%DIV` by zero is an error |
| `%MIN(a : b ...)` `%MAX(a : b ...)` | smallest or largest, numbers or text |
| `%EOF` `%FOUND` `%EQUAL` | file status, see "DISK Files" |

Text and numbers don't mix: `'a' + 1` is an error, not a silent NaN. Convert with `%CHAR` or `%DEC`. `+` between two character values joins them, padding included, so `%TRIM` the pieces you don't want padded. Anything not listed throws `Unsupported RPG built-in`.

### Figurative constants

`*ON`, `*OFF`, `*INnn`, `*INLR`, `*INRT` in all three ports. The Python port adds `*BLANK`/`*BLANKS` (empty text, which pads to the field's length on assignment), `*ZERO`/`*ZEROS`, `*LOVAL`/`*HIVAL` as file keys, and the overflow indicators. Any other `*XXX` throws `Unsupported RPG figurative constant`.

### Why AND/OR always evaluate both operands

JS's native `&&`/`||` short-circuit — they skip evaluating the right side once the left side already determines the result. That's wrong here, because "evaluating" an operand in this parser also means *consuming its tokens*. If the parser short-circuited, it would leave unconsumed tokens on the stream and throw a spurious "trailing tokens" error whenever the left side of an `AND`/`OR` already settled the result. `parseAnd()`/`parseOr()` deliberately always call `parseCompare()`/`parseAnd()` on the right side regardless of the left side's value, then combine with `Boolean(v) && Boolean(rhs)` / `Boolean(v) || Boolean(rhs)` — full evaluation, JS-boolean combination.

## DIV/MVR as Modulo

RPG has no `%REM` built-in until well past V4R3. The classic, unambiguous way to get a remainder is `DIV` immediately followed by a bare `MVR` — `DIV` computes and stores the truncated quotient *and* remembers the remainder internally; the following `MVR` (no factor 1/2 of its own) writes that remembered remainder to its result field. `programs/adventure.rpgle` uses this idiom repeatedly as a simple LCG-style pseudo-random generator (`ROLLRNG`): `seed = seed * 31 + 7`, then `DIV 9973`/`MVR` to fold it back into range.

## What's Explicitly Out of Scope

Nothing here is an oversight: each is documented where it matters, and each is an **error rather than a silent skip** wherever a program could otherwise run and give a wrong answer.

- **Whole areas:** system APIs, CL, data areas and data queues, job and spool-queue behavior, ILE binding and service programs (`extproc`), activation groups, commitment control, SQL (`EXEC SQL`), and record locking.
- **Source forms:** O-specs and program-described files, mixed fixed-and-free source, `/IF` conditions other than `DEFINED`, `**CTDATA` compile-time arrays.
- **Language:** `MONITOR`/`ON-ERROR` and `%ERROR`, the `(E)` opcode extender, `DSPLY`, date, time and timestamp types, pointers, `%EDITC`/`%EDITW`/`%XLATE`, `*OMIT`, `%PARMS`, procedures taking data structures as parameters, arrays inside data structures.
- **Files:** subfiles and windows in display DDS, DDS keyword continuation lines, OR'd and multi-line indicator conditions, `DESCEND` keys, joined and select/omit logical files, `READC`, user-controlled open/close.
- **Printing:** overprinting (`SPACEA(0)`), `DATE`/`TIME`/`USER`/`SYSNAME` fields, `EDTWRD`, edit codes outside the supported set, barcodes.
- **The JS and Go ports** have none of the Python runtime (free-form, files, includes, calls, printing, the built-in functions beyond three). See the root README's "Status by language".

## See Also

- [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) — the display-file side of a program
- [`GLOSSARY.md`](./GLOSSARY.md) — term definitions
- [`PUBLISHING.md`](./PUBLISHING.md) — release process
