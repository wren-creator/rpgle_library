"""
pf.py
─────────────────────────────────────────────────────────────────
Minimal DDS parser for DISK (physical/logical) files: the record
format, its fields, and the key. This is what makes a DISK file
"externally described": the RPG program declares only the file name
(F-spec, column 22 = E) and every field here becomes a program
variable, the same way a display file's fields do.

Column layout is the DDS-for-physical-files one (same as display
files up to the keywords):
  6      Form type ('A')
  7      Comment marker ('*')
  17     'R' starts the record format, 'K' is a key field line
  19-28  Name
  30-34  Length
  35     Data type (blank = character, or zoned if decimals given;
         P packed, S zoned, B binary, F float are all numeric here)
  36-37  Decimal positions
  45-80  Keywords (UNIQUE before the R line makes the key unique;
         DESCEND on a key line is parsed but rejected, see below)

Not supported: DESCEND keys, join and select/omit logical files,
ALIAS, TEXT/COLHDG-driven behavior (ignored), field references
(REF/REFFLD). A K line naming a field not in the format is an error.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ._helpers import col
from .errors import RpgleError


@dataclass
class PfField:
    name: str  # upper-case, as written in the DDS
    length: int
    dtype: str  # "A" (character) or "N" (numeric)
    dec: int = 0


@dataclass
class PhysicalFile:
    """One DISK file's layout: file name, its single record format,
    fields in order, key field names in order (empty = arrival
    sequence), and whether the key must be unique.
    """

    name: str
    format: str = ""
    fields: list[PfField] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    unique: bool = False

    def field_named(self, name: str) -> Optional[PfField]:
        up = name.upper()
        for f in self.fields:
            if f.name == up:
                return f
        return None


def parse_pf(file_name: str, source: str) -> PhysicalFile:
    """Parses physical-file DDS source for the file called file_name."""
    pf = PhysicalFile(name=file_name.upper())

    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6 or line[5] != "A":
            continue
        if len(line) > 6 and line[6] == "*":
            continue

        kind = col(line, 17, 17)
        name = col(line, 19, 28).upper()
        keywords = (line[44:] if len(line) >= 45 else "").upper()

        if kind == "R":
            if pf.format:
                raise RpgleError(f"{pf.name}: only one record format per DISK file is supported")
            pf.format = name
            continue

        if kind == "K":
            if "DESCEND" in keywords:
                raise RpgleError(f"{pf.name}: DESCEND keys are not supported")
            pf.keys.append(name)
            continue

        if not pf.format:
            # File-level keywords before the R line.
            if "UNIQUE" in keywords.split():
                pf.unique = True
            continue

        if not name:
            continue

        length_s = col(line, 30, 34)
        dtype = col(line, 35, 35).upper()
        dec_s = col(line, 36, 37)
        numeric = dtype in ("P", "S", "B", "F") or bool(dec_s)
        pf.fields.append(
            PfField(
                name=name,
                length=int(length_s) if length_s else 0,
                dtype="N" if numeric else "A",
                dec=int(dec_s) if dec_s else 0,
            )
        )

    if not pf.format:
        raise RpgleError(f"{pf.name}: no record format (R line) found in DDS")
    for k in pf.keys:
        if pf.field_named(k) is None:
            raise RpgleError(f"{pf.name}: key field {k} is not in record format {pf.format}")
    return pf
