# Glossary

**Reading order:** if you're new to RPG, read the "RPG Background" section first — the alphabetical reference below assumes you know roughly what RPG is and why its source looks the way it does.

---

## RPG Background

RPG (Report Program Generator) dates to 1959, originally a report-writing language for punched-card IBM systems, not a general-purpose language in the modern sense. Its earliest versions were "cycle-based": the runtime itself looped over input records and called your code at fixed points in that cycle, rather than your code containing an explicit main loop. That cycle model is why classic RPG code reads so differently from most languages — and why fixed-form RPG still has "H/F/D/C spec" columns baked into every line: they're a direct descendant of which columns meant what on an 80-column punch card.

RPG II and RPG III kept the cycle model and column layout. **RPG IV** (also called ILE RPG, introduced with the AS/400 in the mid-1990s) is a real break: structured control flow (`IF`/`DOW`/`SELECT`, not just cycle indicators), free-form expressions in calculations, and eventually (V5R1 and later) fully free-form source that drops the column discipline entirely. This library implements **fixed-form RPG IV** specifically — the column-based dialect, not RPG II/III's cycle model and not V5R1+ free-form. See [`LANGUAGE.md`](./LANGUAGE.md) for exactly which fixed-form subset is supported.

The platform itself has been renamed several times without the underlying OS/400 or RPG runtime changing in any way that matters here: **AS/400 → iSeries → System i → IBM i.** You'll see all four names used interchangeably in older documentation and in the wild.

---

## Terms

### BIF (Built-In Function)
A function whose name starts with `%` (`%TRIM`, `%LEN`, `%SUBST`, `%EOF`, ...), built into the compiler rather than something you write. This library implements exactly three: `%TRIM`, `%LEN`, `%SUBST` — see [`LANGUAGE.md`](./LANGUAGE.md).

### C-spec (Calculation Specification)
The executable-statement lines of fixed-form RPG, identified by `C` in column 6. Indicators, factor 1, opcode, and factor 2/extended-factor-2 each occupy their own fixed columns.

### CFnn / CAnn (Command Function / Command Attention)
DDS record-format keywords mapping a function key to a response indicator — `CF03(03)` means "F3 turns on indicator 03." `CF` keys are meant to be checked by the program before further processing; `CA` keys were historically meant to interrupt immediately, though this library treats both identically.

### D-spec (Definition Specification)
The variable-declaration lines of fixed-form RPG, identified by `D` in column 6. This library supports only standalone fields (definition type `S`) — see [`LANGUAGE.md`](./LANGUAGE.md) for what's not supported (data structures, arrays, `LIKE`).

### DDS (Data Description Specifications)
IBM's fixed-column language for describing files externally to the programs that use them — display files (screens), database files, printer files. This library parses only the display-file dialect. See [`DDS_REFERENCE.md`](./DDS_REFERENCE.md).

### EXFMT
The RPG opcode that writes a display-file record format to the screen and reads the operator's response, in one instruction. The closest real-world analog is a synchronous network round-trip — which is exactly why this library models it as a generator yield (JS, Python) / channel handshake (Go) rather than a plain function call: the program's execution genuinely pauses until the "response" arrives.

### EXSR / BEGSR / ENDSR (Subroutine)
`BEGSR name`...`ENDSR` declares a subroutine; `EXSR name` calls it. RPG subroutines have no parameters or return values of their own — they operate on shared program variables, and returning is unconditional (no early-return opcode; a subroutine always runs to its `ENDSR`).

### Externally-Described File
A file (in this library, always the WORKSTN display file) whose field definitions live in its DDS source, not repeated in the RPG program's D-specs. Every field from every record format automatically becomes a program variable. See "Externally-Described Files" in [`LANGUAGE.md`](./LANGUAGE.md).

### Extended Factor 2
The free-form expression area (columns 36–80) of a C-spec line, used by `EVAL`, `IF`, `DOW`, and `WHEN`. Contrasted with "classic" factor 2, which is a single fixed-column operand (see Factor 1 / Factor 2).

### F-spec (File Specification)
The file-declaration lines of fixed-form RPG, identified by `F` in column 6. This library reads only enough of an F-spec to identify a single WORKSTN display file.

### Factor 1 / Factor 2
Classic (pre-extended-factor-2) RPG's fixed-column operand terminology, still used by "traditional" opcodes like `DIV`/`MVR`: factor 1 is the left operand, factor 2 the right, result is where the answer goes — three separate fixed-column fields on one C-spec line, not a free-form expression.

### Fixed-form vs. Free-form RPG
Fixed-form: every statement's meaning depends on which column each token starts in (the punch-card legacy). Free-form (V5R1 and later): ordinary statement syntax, no column discipline. This library implements fixed-form only.

### H-spec (Header Specification)
Program-level compile-time options, identified by `H` in column 6. Not parsed by this library at all — nothing here depends on header options.

### ILE (Integrated Language Environment)
IBM i's modern compilation/runtime model (module → program/service-program, shared runtime across RPG/COBOL/C/CL). "ILE RPG" is another name for RPG IV.

### Indicator
A named boolean flag, distinct from an ordinary variable — either a two-digit numbered indicator (`*IN90`) or one of the named ones (`*INLR`, `*INRT`). Indicators condition DDS field visibility, drive control flow, and carry function-key state back from `EXFMT`. See "Indicators" in [`LANGUAGE.md`](./LANGUAGE.md).

### *INLR (Last Record)
The indicator that signals normal end-of-program — conventionally set on right before a program's final `RETURN`. Named for RPG's cycle-based ancestry, where it meant "this is the last input record."

### *INRT (Return)
An indicator related to RPG's cycle-based return-processing feature. Tracked by this library like any other indicator, though no shipped example content uses it.

### MDT (Modified Data Tag)
In the DDS/5250 sense (distinct from the 3270 field-attribute-byte sense some readers may know from `tn3270_library`'s glossary): a per-field flag meaning "the operator changed this field's contents since it was last written," used by the terminal to decide which fields to actually send back on the next read. This library's `renderFormat()`/screen data doesn't model MDT explicitly — every input field's current value is simply included in `values` on resume.

### V4R3 / V5R1 (as version-gating context)
IBM i release numbers used in this library's own comments/example content to mean "before/after free-form RPG existed." V5R1 (2001) introduced free-form C-specs; `programs/adventure.rpgle` is deliberately written to be valid on V4R3 (pre-free-form) as a demonstration that this library's fixed-form-only support isn't an arbitrary restriction — it's what real, still-running production RPG looks like on plenty of systems.

### WORKSTN File
RPG's device type for an interactive display (a 5250 terminal, in the physical world). This library's F-spec support exists specifically to recognize a single WORKSTN file — the only kind of file a program here can have.

---

## See Also

- [`LANGUAGE.md`](./LANGUAGE.md) — the RPGLE language subset this library implements
- [`DDS_REFERENCE.md`](./DDS_REFERENCE.md) — the DDS subset this library implements
