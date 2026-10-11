"""
rpgle.py
─────────────────────────────────────────────────────────────────
Fixed-form RPG IV tokenizer, scoped to the F/D/C-spec subset this
library's interpreter supports (see interpreter.py's header for the
exact opcode list). This module only tokenizes columns into spec
records in source order -- control-flow resolution (matching
IF/ELSE/ENDIF, DOW/ENDDO, SELECT/WHEN/OTHER/ENDSL, BEGSR/ENDSR)
happens in interpreter.py's compile_program step.

Column positions are IBM's documented fixed-form layout (verified
against the ILE RPG Language Reference, SC09-2508):
  F-spec: 7-16 name, 17 type, 18 designation, 19 EOF, 20 addition,
          21 sequence, 22 format, 23-27 record length, 36-42 device,
          44-80 keywords.
  D-spec: 7-21 name, 22 external desc, 23 DS type, 24-25 definition
          type, 26-32 from position, 33-39 to position/length,
          40 internal data type, 41-42 decimal positions,
          44-80 keywords.
  C-spec: 7-8 control level, 9-11 indicators, 12-25 factor 1,
          26-35 opcode, 36-80 factor 2 / extended factor 2 (this
          parser always reads 36-80 as one field -- every opcode
          this interpreter supports either leaves it blank, holds
          a bare name (EXSR/EXFMT/BEGSR target), or holds an
          extended-factor-2 expression).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ._helpers import col
from .errors import RpgleError

_INZ_RE = re.compile(r"INZ\(([^)]*)\)", re.IGNORECASE)


@dataclass
class FileSpec:
    """One F-spec: a WORKSTN display file or a DISK file."""

    name: Optional[str] = None
    type: Optional[str] = None  # I=input, O=output, U=update, C=combined
    designation: Optional[str] = None
    format: Optional[str] = None  # E = externally described
    device: Optional[str] = None
    addition: Optional[str] = None  # 'A' = records may be added
    keyed: bool = False  # K in column 34 (the PF's DDS key is what actually orders access)
    oflind: Optional[str] = None  # a printer file's overflow indicator: *INOF, *IN90, or an ind variable


@dataclass
class DSpec:
    """One standalone (definition type 'S') D-spec field."""

    name: str
    deftype: str
    length: int = 0
    # None for a character field (blank decimal-positions column);
    # an int for a numeric field. Same reasoning as Field.dec in
    # dds.py: a real "0" and "blank" are different things.
    dec: Optional[int] = None
    init: Optional[str] = None
    kind: str = ""  # "" (plain), or a free-form type with its own rules, e.g. "ind"
    dim: Optional[int] = None  # number of elements, for an array (free-form only)


@dataclass
class DsSpec:
    """A free-form data structure. Each subfield becomes its own program
    variable, named `ds.sub` when the DS is qualified and plain `sub`
    when it isn't. The layout is either the explicit subfields, or
    borrowed: likeds (another DS), likerec (a file or display record
    format), extname (a whole file).
    """

    name: str
    qualified: bool = False
    subfields: list[DSpec] = field(default_factory=list)
    likeds: Optional[str] = None
    likerec: Optional[str] = None
    extname: Optional[str] = None
    dim: Optional[int] = None  # an array of data structures (must be qualified)


@dataclass
class ParamSpec:
    """One procedure parameter. mode is how the caller's argument is
    passed: "ref" (the default: the procedure sees and can change the
    caller's variable), "const" (read-only, so an expression is fine),
    or "value" (a private copy).
    """

    spec: DSpec
    mode: str = "ref"
    nopass: bool = False  # options(*nopass): the caller may leave it off


@dataclass
class ProtoSpec:
    """A free-form prototype (dcl-pr). One with extpgm('NAME') describes
    another program that can be called by name; with extproc it describes
    an external procedure, which isn't supported. A prototype for a
    procedure defined in the same source is redundant and ignored.
    """

    name: str
    extpgm: Optional[str] = None  # the program's name (upper-case), if it's an external program
    extproc: bool = False
    params: list[ParamSpec] = field(default_factory=list)
    returns: Optional[DSpec] = None


@dataclass
class ProcSpec:
    """A free-form procedure (dcl-proc ... end-proc). Its statements are
    kept apart from the main line, and its dcl-s/dcl-ds declarations are
    local to it.
    """

    name: str
    export: bool = False
    params: list[ParamSpec] = field(default_factory=list)
    returns: Optional[DSpec] = None
    dspecs: list[DSpec] = field(default_factory=list)
    dss: list[DsSpec] = field(default_factory=list)
    statements: list["Statement"] = field(default_factory=list)


@dataclass
class Statement:
    """One C-spec line, tokenized but not yet control-flow resolved
    (that happens in interpreter.compile_program).
    """

    op: str
    indic: Optional[str] = None
    factor1: Optional[str] = None
    expr: Optional[str] = None  # extended factor 2 (EVAL/IF/DOW/WHEN): free-form, cols 36-80
    factor2: Optional[str] = None  # classic factor 2 (DIV/MVR-style ops): cols 36-49
    result: Optional[str] = None  # classic result (DIV/MVR-style ops): cols 50-63
    # Resulting indicators, cols 71-72 / 73-74 / 75-76 (two digits each).
    # What they mean depends on the opcode: CHAIN uses hi as not-found,
    # READ uses eq as end-of-file. See interpreter.py's file ops.
    ind_hi: Optional[str] = None
    ind_lo: Optional[str] = None
    ind_eq: Optional[str] = None
    # FOR loops (free-form only): factor1 is the index variable, expr the
    # start value, then these. `down` is DOWNTO instead of TO.
    limit: Optional[str] = None
    step: Optional[str] = None
    down: bool = False
    ds: Optional[str] = None  # file ops: read into / write from this data structure
    line: int = 0  # source line, for error messages (free-form only)


@dataclass
class Program:
    """A fully-tokenized RPGLE source file."""

    file: FileSpec = field(default_factory=FileSpec)  # the WORKSTN file
    files: list[FileSpec] = field(default_factory=list)  # every F-spec, source order
    dspecs: list[DSpec] = field(default_factory=list)
    dss: list[DsSpec] = field(default_factory=list)  # free-form data structures
    procs: list[ProcSpec] = field(default_factory=list)  # free-form procedures
    protos: dict[str, ProtoSpec] = field(default_factory=dict)  # free-form prototypes, by lower-case name
    main_params: list[ParamSpec] = field(default_factory=list)  # the program's own dcl-pi: what a caller passes it
    statements: list[Statement] = field(default_factory=list)


def parse_rpgle(
    source: str,
    path: Optional[Path] = None,
    include_dirs: Optional[Iterable[Path]] = None,
    defines: Iterable[str] = (),
) -> Program:
    """Tokenizes RPG IV source into F-spec/D-spec/C-spec records, in
    source order. Source starting with **FREE is free-form and goes to
    freeform.parse_free, which produces the same records.

    /COPY, /INCLUDE and the /IF /DEFINE family are expanded first (see
    include.py). path is where the source came from, which is where
    includes are looked for before include_dirs; defines pre-defines
    names for /IF DEFINED. Errors inside a copy member name the member.
    """
    from .include import expand_includes, has_directives, remap_error

    if not has_directives(source):
        return _parse(source)
    text, origin = expand_includes(source, path, include_dirs, defines)
    try:
        return _parse(text)
    except RpgleError as e:
        raise RpgleError(remap_error(str(e), origin)) from None


def _oflind(keywords: str) -> Optional[str]:
    m = re.search(r"\bOFLIND\s*\(\s*([^)\s]+)\s*\)", keywords, re.IGNORECASE)
    return m.group(1) if m else None


def _parse(source: str) -> Program:
    from .freeform import is_free, parse_free  # local: freeform imports this module

    if is_free(source):
        return parse_free(source)

    prog = Program()

    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6:
            continue
        spec_type = line[5]  # column 6
        if spec_type == "*":
            continue  # comment line

        if spec_type == "O":
            raise RpgleError(
                "output specifications (O-specs) are not supported: print with an externally described "
                "printer file (an F-spec with E in column 22, a .prtf DDS, and WRITE)"
            )

        if spec_type == "F":
            fs = FileSpec(
                name=col(line, 7, 16),
                type=col(line, 17, 17),
                designation=col(line, 18, 18),
                format=col(line, 22, 22),
                device=col(line, 36, 42),
                addition=col(line, 20, 20) or None,
                keyed=col(line, 34, 34).upper() == "K",
                oflind=_oflind(line[43:] if len(line) > 43 else ""),
            )
            prog.files.append(fs)
            # prog.file stays "the WORKSTN file" (first one wins) so
            # single-screen programs and existing callers are unchanged.
            if fs.device.upper() == "WORKSTN" and not prog.file.name:
                prog.file = fs
            continue

        if spec_type == "D":
            name = col(line, 7, 21)
            if not name:
                continue
            deftype = col(line, 24, 25)
            length_s = col(line, 33, 39)
            dec_s = col(line, 41, 42)
            keywords = line[43:].strip() if len(line) >= 44 else ""
            if re.search(r"\bDIM\s*\(", keywords, re.IGNORECASE):
                raise RpgleError(f"{name}: arrays in fixed-form D-specs are not supported (use free-form dcl-s ... dim())")
            init = None
            m = _INZ_RE.search(keywords)
            if m:
                init = m.group(1).strip()
            prog.dspecs.append(
                DSpec(
                    name=name,
                    deftype=deftype,
                    length=int(length_s) if length_s else 0,
                    dec=int(dec_s) if dec_s else None,
                    init=init,
                )
            )
            continue

        if spec_type == "C":
            indic = col(line, 9, 11)
            factor1 = col(line, 12, 25)
            opcode = col(line, 26, 35).upper()
            if not opcode:
                continue
            # Extended factor 2 (EVAL/IF/DOW/WHEN): free-form expression, 36-80.
            expr = line[35:].strip() if len(line) >= 36 else ""
            # Classic factor 2 / result (DIV/MVR and any other
            # traditional fixed-column op): 36-49 and 50-63, read
            # separately since they aren't whitespace-delimited from
            # each other in a reliable way.
            factor2 = col(line, 36, 49)
            result = col(line, 50, 63)
            prog.statements.append(
                Statement(
                    op=opcode,
                    indic=indic or None,
                    factor1=factor1 or None,
                    expr=expr or None,
                    factor2=factor2 or None,
                    result=result or None,
                    ind_hi=col(line, 71, 72) or None,
                    ind_lo=col(line, 73, 74) or None,
                    ind_eq=col(line, 75, 76) or None,
                )
            )
            continue
        # H-spec and other spec types: not needed for v1, ignored.

    return prog
