# Glossary

**Reading order:** if you're new to RPG, read the "RPG Background" section first — the alphabetical reference below assumes you know roughly what RPG is and why its source looks the way it does.

---

## RPG Background

RPG (Report Program Generator) dates to 1959, originally a report-writing language for punched-card IBM systems, not a general-purpose language in the modern sense. Its earliest versions were "cycle-based": the runtime itself looped over input records and called your code at fixed points in that cycle, rather than your code containing an explicit main loop. That cycle model is why classic RPG code reads so differently from most languages — and why fixed-form RPG still has "H/F/D/C spec" columns baked into every line: they're a direct descendant of which columns meant what on an 80-column punch card.

RPG II and RPG III kept the cycle model and column layout. **RPG IV** (also called ILE RPG, introduced with the AS/400 in the mid-1990s) is a real break: structured control flow (`IF`/`DOW`/`SELECT`, not just cycle indicators), free-form expressions in calculations, and eventually (V5R1 and later) fully free-form source that drops the column discipline entirely. This library implements **RPG IV**: the column-based fixed-form dialect in all three ports, and V5R1+ free-form in the Python port. It does not implement RPG II/III's cycle model. See [`LANGUAGE.md`](./LANGUAGE.md) for exactly which subset is supported.

The platform itself has been renamed several times without the underlying OS/400 or RPG runtime changing in any way that matters here: **AS/400 → iSeries → System i → IBM i.** You'll see all four names used interchangeably in older documentation and in the wild.

---

## Terms

### Array (DIM)
A list of values of one type, declared with `dim(n)`, numbered from 1: `price(3)`. An array of data structures is read as `line(i).qty`. RPG has no bounds-checking wraparound: a subscript outside 1 to `dim` is a runtime error, and so it is here. See "Arrays" in [`LANGUAGE.md`](./LANGUAGE.md).

### BIF (Built-In Function)
A function whose name starts with `%` (`%TRIM`, `%LEN`, `%SUBST`, `%EOF`, ...), built into the compiler rather than something you write. All three ports implement the same twenty core functions (`%TRIM`, `%LEN`, `%SUBST`, `%CHAR`, `%DEC`, `%SCAN`, `%REPLACE` and so on); the Python port adds five that belong to files and arrays (`%EOF`, `%FOUND`, `%EQUAL`, `%ELEM`, `%LOOKUP`). See [`LANGUAGE.md`](./LANGUAGE.md).

### CALL / PARM / PLIST
The classic fixed-form way for one program to call another. `CALL 'PGM'` names the program and the `PARM` lines under it name the fields passed; a `PLIST` is a named, reusable list of `PARM`s, and `*ENTRY PLIST` is the called program's own parameter list. Parameters are passed by reference: the called program works on the caller's variables. The free-form equivalent is a prototype with `extpgm` plus `dcl-pi` in the called program.

### CHAIN / SETLL / READ / READE
Record-level file access, the heart of RPG as a database language. `CHAIN` reads the record with a given key. `SETLL` positions the file before the first record at or after a key, without reading anything, and `READ` / `READE` then read forward (`READE` stops when the key changes). `%FOUND` and `%EOF` report what happened. See "DISK Files" in [`LANGUAGE.md`](./LANGUAGE.md).

### Copybook (/COPY)
A source file other source files include, usually shared declarations or prototypes. `/COPY QRPGLESRC,CUSTDEFS` splices in member `CUSTDEFS` of source file `QRPGLESRC` before the compiler sees the program. Copybooks guard themselves against being included twice with `/IF DEFINED`, `/DEFINE` and `/EOF`. See "Source Includes" in [`LANGUAGE.md`](./LANGUAGE.md).

### C-spec (Calculation Specification)
The executable-statement lines of fixed-form RPG, identified by `C` in column 6. Indicators, factor 1, opcode, and factor 2/extended-factor-2 each occupy their own fixed columns.

### CFnn / CAnn (Command Function / Command Attention)
DDS record-format keywords mapping a function key to a response indicator — `CF03(03)` means "F3 turns on indicator 03." `CF` keys are meant to be checked by the program before further processing; `CA` keys were historically meant to interrupt immediately, though this library treats both identically.

### Data Structure (DS)
A named group of fields. In free-form, `dcl-ds` declares one, with its own subfields or borrowing a layout from a file record format (`likerec`) or another data structure (`likeds`). `qualified` data structures are accessed as `cust.name`. A common pattern is to `chain` a whole record into one. See "Data structures" in [`LANGUAGE.md`](./LANGUAGE.md).

### D-spec (Definition Specification)
The variable-declaration lines of fixed-form RPG, identified by `D` in column 6. In fixed-form this library supports only standalone fields (definition type `S`); data structures, arrays and `LIKE` exist only in free-form (`dcl-s`, `dcl-ds`), in the Python port. See [`LANGUAGE.md`](./LANGUAGE.md).

### DDS (Data Description Specifications)
IBM's fixed-column language for describing files externally to the programs that use them — display files (screens), database files, printer files. This library parses only the display-file dialect. See [`DDS_REFERENCE.md`](./DDS_REFERENCE.md).

### Edit Code (EDTCDE)
A one-character code in printer DDS that says how a number is printed: whether it gets commas, a decimal point, a minus sign or `CR` for negatives, and whether a zero prints as `0.00` or as blanks. `EDTCDE(J)` is the common "commas, decimal point, trailing minus" form. A number printed with no edit code is just zero-filled digits.

### EXFMT
The RPG opcode that writes a display-file record format to the screen and reads the operator's response, in one instruction. The closest real-world analog is a synchronous network round-trip — which is exactly why this library models it as a generator yield (JS, Python) / channel handshake (Go) rather than a plain function call: the program's execution genuinely pauses until the "response" arrives.

### EXSR / BEGSR / ENDSR (Subroutine)
`BEGSR name`...`ENDSR` declares a subroutine; `EXSR name` calls it. RPG subroutines have no parameters or return values of their own — they operate on shared program variables, and returning is unconditional (no early-return opcode; a subroutine always runs to its `ENDSR`).

### Externally-Described File
A file (in this library, always the WORKSTN display file) whose field definitions live in its DDS source, not repeated in the RPG program's D-specs. Every field from every record format automatically becomes a program variable. See "Externally-Described Files" in [`LANGUAGE.md`](./LANGUAGE.md).

### Extended Factor 2
The free-form expression area (columns 36–80) of a C-spec line, used by `EVAL`, `IF`, `DOW`, and `WHEN`. Contrasted with "classic" factor 2, which is a single fixed-column operand (see Factor 1 / Factor 2).

### F-spec (File Specification)
The file-declaration lines of fixed-form RPG, identified by `F` in column 6. All three ports read every F-spec: display and DISK files in all of them, and PRINTER files (including the `OFLIND` keyword) in the Python port. In JS and Go the program's one display file is the first WORKSTN F-spec. Free-form uses `dcl-f` instead.

### Factor 1 / Factor 2
Classic (pre-extended-factor-2) RPG's fixed-column operand terminology, still used by "traditional" opcodes like `DIV`/`MVR`: factor 1 is the left operand, factor 2 the right, result is where the answer goes — three separate fixed-column fields on one C-spec line, not a free-form expression.

### Fixed-form vs. Free-form RPG
Fixed-form: every statement's meaning depends on which column each token starts in (the punch-card legacy). Free-form (V5R1 and later): ordinary statement syntax, no column discipline. The JS and Go ports implement fixed-form only; the Python port implements both, and a file is one or the other (source starting with `**FREE` is free-form).

### H-spec (Header Specification)
Program-level compile-time options, identified by `H` in column 6. Not parsed by this library at all — nothing here depends on header options.

### ILE (Integrated Language Environment)
IBM i's modern compilation/runtime model (module → program/service-program, shared runtime across RPG/COBOL/C/CL). "ILE RPG" is another name for RPG IV.

### Indicator
A named boolean flag, distinct from an ordinary variable — either a two-digit numbered indicator (`*IN90`) or one of the named ones (`*INLR`, `*INRT`). Indicators condition DDS field visibility, drive control flow, and carry function-key state back from `EXFMT`. See "Indicators" in [`LANGUAGE.md`](./LANGUAGE.md).

### *INLR (Last Record)
The indicator that says "this program is finished", conventionally set on right before a program's final `RETURN`. Named for RPG's cycle-based ancestry, where it meant "this is the last input record." For a *called* program it has a real effect: set on, the program is closed and starts afresh next call; left off, it stays active and keeps its variables and file positions. See "Calling Other Programs" in [`LANGUAGE.md`](./LANGUAGE.md).

### *INRT (Return)
An indicator related to RPG's cycle-based return-processing feature. Tracked by this library like any other indicator, though no shipped example content uses it.

### KLIST / KFLD
A fixed-form way to name a composite key: `KLIST` names it and each `KFLD` under it names one field. `CHAIN`, `SETLL` and `READE` then take the `KLIST` name as their key. A key list with fewer fields than the file's key is a partial key, which is how `READE` walks all of one customer's orders.

### MDT (Modified Data Tag)
In the DDS/5250 sense (distinct from the 3270 field-attribute-byte sense some readers may know from `tn3270_library`'s glossary): a per-field flag meaning "the operator changed this field's contents since it was last written," used by the terminal to decide which fields to actually send back on the next read. This library's `renderFormat()`/screen data doesn't model MDT explicitly — every input field's current value is simply included in `values` on resume.

### Overflow Indicator (OFLIND)
An indicator (`*INOF`, usually) that a printer file turns on when a record reaches the overflow line, to tell the program the page is nearly full. It's up to the program to react, typically by printing its headings again and turning the indicator off. See "Printer Files" in [`LANGUAGE.md`](./LANGUAGE.md).

### Physical File / Logical File
A physical file stores records; its DDS defines the fields and the key. A logical file is a view over one or more physical files, with a different key or a subset of records. This library supports physical files (as SQLite tables); joined and select/omit logical files are not supported. See "Physical Files" in [`DDS_REFERENCE.md`](./DDS_REFERENCE.md).

### Printer File (PRTF)
A file a program writes printed output to. Like a display file it is externally described: its DDS lists record formats made of fields and constants placed on lines and columns, plus spacing keywords (`SKIPB`, `SPACEA`) that say where on the page each record goes. The program prints by `WRITE`-ing a record format. See [`DDS_REFERENCE.md`](./DDS_REFERENCE.md).

### Procedure / Prototype
In free-form RPG a procedure (`dcl-proc`) is a function: it has parameters, local variables and a return value, and can call itself. A prototype (`dcl-pr`) describes a procedure or program so it can be called from elsewhere. Procedures in the same source need no prototype. See "Procedures" in [`LANGUAGE.md`](./LANGUAGE.md).

### Record Format
The layout of one kind of record: a named list of fields. A display file has one per screen, a printer file one per kind of printed line, a physical file one for its records. `EXFMT`, `WRITE` and `UPDATE` take a record format name.

### Spool File
Output a program has written to a printer file, held until it is printed or viewed. Here each printer file a program opens becomes one text file (`rpgle run --spool-dir DIR`), with a form feed between pages. See "Printer Files" in [`LANGUAGE.md`](./LANGUAGE.md).

### V4R3 / V5R1 (as version-gating context)
IBM i release numbers used in this library's own comments/example content to mean "before/after free-form RPG existed." V5R1 (2001) introduced free-form C-specs; `programs/adventure.rpgle` is deliberately written to be valid on V4R3 (pre-free-form) as a demonstration that this library's fixed-form-only support isn't an arbitrary restriction — it's what real, still-running production RPG looks like on plenty of systems.

### WORKSTN File
RPG's device type for an interactive display (a 5250 terminal, in the physical world). A program talks to one with `EXFMT`. The other device types are DISK (database files) and PRINTER (printed output); the Python port supports all three.

---

## See Also

- [`LANGUAGE.md`](./LANGUAGE.md) — the RPGLE language subset this library implements
- [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) — the DDS subset this library implements
