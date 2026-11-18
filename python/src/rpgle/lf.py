"""
lf.py
─────────────────────────────────────────────────────────────────
Minimal DDS parser for a logical file over a single physical file: a
different key order over the same records, built from the physical
file's own fields and nothing else. The DDS layout is the same as a
physical file's (see pf.py), plus one file-level keyword:

  PFILE(name)   the physical file this logical file is built over,
                specified before the R line (same position as
                UNIQUE in pf.py)

A logical file's own record format and key fields are written the
same way a physical file's are. Every field of the physical file
shows through, in the physical file's own order; a logical file
exists here only to offer a different key, so at least one K line
is required (a keyless one would just duplicate the physical file).

Not supported: join logical files (more than one PFILE), select/omit
(S/O conditioning), choosing or renaming individual fields, DESCEND
keys (same as pf.py), multiple record formats.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._helpers import col
from .errors import RpgleError
from .pf import PfField, PhysicalFile


@dataclass
class LogicalFile:
    """One logical file's layout: its own name, the physical file it is
    built over, its record format name, the physical file's fields (in
    the physical file's own order), key field names in order, and
    whether the key must be unique.
    """

    name: str
    based_on: str = ""
    format: str = ""
    fields: list[PfField] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    unique: bool = False


def _pfile_name(keywords: str) -> str:
    """Pulls the name out of 'PFILE(NAME)' among a line's keywords, or '' if not present."""
    i = keywords.find("PFILE(")
    if i < 0:
        return ""
    j = keywords.find(")", i)
    if j < 0:
        return ""
    return keywords[i + 6:j].strip()


def find_pfile(source: str) -> str:
    """The physical file name from a logical file's PFILE keyword, read without needing
    that physical file's own layout. '' if no PFILE keyword is present. For a host to
    locate and parse the physical file before calling parse_lf with it."""
    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6 or line[5] != "A":
            continue
        if len(line) > 6 and line[6] == "*":
            continue
        if col(line, 17, 17) == "R":
            break  # PFILE is a file-level keyword, written before the R line
        keywords = (line[44:] if len(line) >= 45 else "").upper()
        name = _pfile_name(keywords)
        if name:
            return name
    return ""


def parse_lf(file_name: str, source: str, pf: PhysicalFile) -> LogicalFile:
    """Parses logical-file DDS source for the file called file_name, built over the
    already-parsed physical file pf. Raises if the DDS's PFILE does not name pf, a key
    line names a field pf does not have, no key is given at all, or a field line tries
    to choose or rename a field (not supported yet)."""
    lf = LogicalFile(name=file_name.upper())

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
            if lf.format:
                raise RpgleError(f"{lf.name}: only one record format per logical file is supported")
            lf.format = name
            continue

        if kind == "K":
            if "DESCEND" in keywords:
                raise RpgleError(f"{lf.name}: DESCEND keys are not supported")
            lf.keys.append(name)
            continue

        if not lf.format:
            # File-level keywords before the R line.
            if not lf.based_on:
                lf.based_on = _pfile_name(keywords)
            if "UNIQUE" in keywords.split():
                lf.unique = True
            continue

        if name:
            raise RpgleError(
                f"{lf.name}: a logical file choosing or renaming individual fields is not supported yet "
                f"(omit field lines to show every field of {pf.name})"
            )

    if not lf.format:
        raise RpgleError(f"{lf.name}: no record format (R line) found in DDS")
    if not lf.based_on:
        raise RpgleError(f"{lf.name}: no PFILE keyword found in DDS")
    if lf.based_on != pf.name:
        raise RpgleError(f"{lf.name}: PFILE({lf.based_on}) does not match physical file {pf.name}")
    if not lf.keys:
        raise RpgleError(f"{lf.name}: a logical file needs at least one K line (a keyless one would just "
                         f"duplicate {pf.name})")
    for k in lf.keys:
        if pf.field_named(k) is None:
            raise RpgleError(f"{lf.name}: key field {k} is not in physical file {pf.name}")

    lf.fields = list(pf.fields)
    return lf
