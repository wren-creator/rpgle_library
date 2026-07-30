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
from typing import Optional

from ._helpers import col

_INZ_RE = re.compile(r"INZ\(([^)]*)\)", re.IGNORECASE)


@dataclass
class FileSpec:
    """The single WORKSTN file an RPGLE program can declare."""

    name: Optional[str] = None
    type: Optional[str] = None
    designation: Optional[str] = None
    format: Optional[str] = None
    device: Optional[str] = None


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


@dataclass
class Program:
    """A fully-tokenized RPGLE source file."""

    file: FileSpec = field(default_factory=FileSpec)
    dspecs: list[DSpec] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)


def parse_rpgle(source: str) -> Program:
    """Tokenizes fixed-form RPG IV source into F-spec/D-spec/C-spec
    records, in source order.
    """
    prog = Program()

    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6:
            continue
        spec_type = line[5]  # column 6
        if spec_type == "*":
            continue  # comment line

        if spec_type == "F":
            prog.file.name = col(line, 7, 16)
            prog.file.type = col(line, 17, 17)
            prog.file.designation = col(line, 18, 18)
            prog.file.format = col(line, 22, 22)
            prog.file.device = col(line, 36, 42)
            continue

        if spec_type == "D":
            name = col(line, 7, 21)
            if not name:
                continue
            deftype = col(line, 24, 25)
            length_s = col(line, 33, 39)
            dec_s = col(line, 41, 42)
            keywords = line[43:].strip() if len(line) >= 44 else ""
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
                )
            )
            continue
        # H-spec and other spec types: not needed for v1, ignored.

    return prog
