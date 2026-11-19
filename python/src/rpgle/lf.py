"""
lf.py
─────────────────────────────────────────────────────────────────
DDS parser for a logical file: a different key order over a physical file's own fields
(see pf.py), plus select/omit (filtering which records show) and join logical files
(combining more than one physical file). The DDS layout is the same as a physical
file's, plus file-level keywords before the R line:

  PFILE(name)            the one physical file a plain logical file is built over
  JFILE(primary second...)  every physical file a join logical file is built over,
                         primary first

Every field of every named physical file shows through, in file order (primary's
fields, then each secondary's); a logical file exists here only to offer a different
key (or a join, or a filter), so at least one K line is required (a keyless one would
just duplicate the physical file) and choosing or renaming individual fields is not
supported.

Select/omit: an S or O line names a field (columns 19-28, the same slot a key line's
name uses) and one comparison keyword: COMP(op value), RANGE(lo hi) or VALUES(v1 v2
...), op one of EQ, NE, GT, LT, GE, LE. A record must satisfy every S line and must not
match any O line. IBM's own AND/OR between select/omit lines (continued by a blank line
with no S or O of its own) is not modeled; every line here is its own condition,
combined that way rather than by whatever AND/OR the DDS itself asked for.

Join logical files: a J line (one per secondary file, in JFILE order) is followed by
one or more JFLD(primary-field secondary-field) lines (continuation lines: no kind, no
name, just more keywords) giving its join condition, one field pair per JFLD; JOIN(1)
is required on the J line (joining straight to the primary; a secondary joined to
another secondary is not supported). Built as an inner join, IBM's default: every
secondary must have a matching row or the primary's row does not show either. A field
name that appears in more than one joined file is an error, unless it is one of the
JFLD fields (which are equal by definition, so either file's copy would do). A join
logical file is read-only, the same as a real one.

Not supported: *LIST joins (JDUPSEQ, more than one matching row per secondary), a
non-match-required (left) join, a secondary joined to another secondary rather than
the primary, DESCEND keys (same as pf.py), choosing or renaming individual fields,
multiple record formats.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ._helpers import col
from .errors import RpgleError
from .pf import PfField, PhysicalFile


@dataclass
class JoinedFile:
    """One secondary file of a join logical file: its name and the (primary field,
    this file's field) pairs its JFLD lines gave."""

    name: str
    on: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Condition:
    """One select or omit line: field, whether it is an omit (excludes a match) or a
    select (requires one), the comparison keyword, and its raw DDS arguments (coerced
    against the field's type where the condition is used)."""

    field: str
    exclude: bool
    op: str                       # "COMP", "RANGE" or "VALUES"
    args: tuple[str, ...]
    comp: str = ""                 # for COMP: EQ, NE, GT, LT, GE or LE


@dataclass
class LogicalFile:
    """One logical file's layout: its own name, the physical file(s) it is built over
    (`based_on` the primary; `joined`, empty unless this is a join), its record format
    name, every field of every file it is built over (in file order), key field names
    in order, whether the key must be unique, and any select/omit conditions.
    """

    name: str
    based_on: str = ""
    format: str = ""
    fields: list[PfField] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    unique: bool = False
    joined: list[JoinedFile] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)

    @property
    def is_join(self) -> bool:
        return bool(self.joined)


def _names_in(keywords: str, kw: str) -> list[str]:
    """Every space-separated name inside kw(...) among a line's keywords, or [] if kw is not there."""
    i = keywords.find(kw + "(")
    if i < 0:
        return []
    j = keywords.find(")", i)
    if j < 0:
        return []
    return keywords[i + len(kw) + 1:j].split()


def _pfile_name(keywords: str) -> str:
    names = _names_in(keywords, "PFILE")
    return names[0] if names else ""


def find_pfiles(source: str) -> list[str]:
    """Every physical file name a logical file's DDS needs, from PFILE (one) or JFILE (one or
    more, primary first), read without needing any of their own layouts. [] if neither keyword
    is present. For a host to locate and parse each one before calling parse_lf."""
    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6 or line[5] != "A":
            continue
        if len(line) > 6 and line[6] == "*":
            continue
        if col(line, 17, 17) == "R":
            break  # PFILE/JFILE are file-level keywords, written before the R line
        keywords = (line[44:] if len(line) >= 45 else "").upper()
        names = _names_in(keywords, "JFILE") or _names_in(keywords, "PFILE")
        if names:
            return names
    return []


def find_pfile(source: str) -> str:
    """The one physical file name from a plain logical file's PFILE keyword. '' if there is
    none, or if the DDS is a join logical file (see find_pfiles)."""
    names = find_pfiles(source)
    return names[0] if len(names) == 1 else ""


_COMPS = {"EQ": "=", "NE": "<>", "GT": ">", "LT": "<", "GE": ">=", "LE": "<="}


def _args_of(keywords: str, kw: str) -> tuple[str, ...]:
    """The raw text inside kw(...), split on spaces but keeping a quoted value together."""
    i = keywords.find(kw + "(")
    if i < 0:
        return ()
    j = keywords.find(")", i)
    if j < 0:
        return ()
    inner = keywords[i + len(kw) + 1:j]
    out, cur, quoted = [], "", False
    for ch in inner:
        if ch == "'":
            quoted = not quoted
            cur += ch
        elif ch == " " and not quoted:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return tuple(out)


def parse_lf(file_name: str, source: str, pf: PhysicalFile,
            secondaries: Optional[dict[str, PhysicalFile]] = None) -> LogicalFile:
    """Parses logical-file DDS source for the file called file_name, built over the already-
    parsed primary physical file pf, and, for a join logical file, every secondary file named
    by JFILE (already parsed, keyed by name in `secondaries`). Raises if the DDS's PFILE/JFILE
    does not match what was given, a key or select/omit line names a field none of the files
    have, no key is given at all, a field line tries to choose or rename a field, or a join is
    shaped in a way that is not supported (see the module docstring)."""
    lf = LogicalFile(name=file_name.upper())
    secondaries = secondaries or {}
    jfile_names: list[str] = []
    current_join: Optional[JoinedFile] = None
    join_idx = 0

    def _all_fields() -> list[PfField]:
        out = list(pf.fields)
        for jf in lf.joined:
            out += secondaries[jf.name].fields
        return out

    def _field_named(name: str) -> Optional[PfField]:
        for f in _all_fields():
            if f.name == name:
                return f
        return None

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
            if jfile_names:
                lf.based_on = jfile_names[0]
                for sname in jfile_names[1:]:
                    lf.joined.append(JoinedFile(name=sname))
                current_join = None
            continue

        if kind == "J":
            if not lf.is_join:
                raise RpgleError(f"{lf.name}: a J line needs JFILE with more than one file")
            seq = _names_in(keywords, "JOIN")
            if not seq or seq[0] != "1":
                raise RpgleError(f"{lf.name}: JOIN must name 1 (a secondary joined to another "
                                 "secondary is not supported)")
            if "JDUPSEQ" in keywords:
                raise RpgleError(f"{lf.name}: JDUPSEQ (more than one matching row per secondary) "
                                 "is not supported")
            if join_idx >= len(lf.joined):
                raise RpgleError(f"{lf.name}: more J lines than secondary files in JFILE")
            current_join = lf.joined[join_idx]
            join_idx += 1
            args = _args_of(keywords, "JFLD")
            if args:
                if len(args) != 2:
                    raise RpgleError(f"{lf.name}: JFLD needs exactly two field names")
                current_join.on.append((args[0], args[1]))
            continue

        if kind == "K":
            if "DESCEND" in keywords:
                raise RpgleError(f"{lf.name}: DESCEND keys are not supported")
            lf.keys.append(name)
            current_join = None
            continue

        if kind in ("S", "O"):
            if "COMP" in keywords:
                args = _args_of(keywords, "COMP")
                if len(args) != 2 or args[0] not in _COMPS:
                    raise RpgleError(f"{lf.name}: COMP needs an operator (EQ, NE, GT, LT, GE or LE) "
                                     "and a value")
                lf.conditions.append(Condition(name, kind == "O", "COMP", (args[1],), comp=args[0]))
            elif "RANGE" in keywords:
                args = _args_of(keywords, "RANGE")
                if len(args) != 2:
                    raise RpgleError(f"{lf.name}: RANGE needs a low and a high value")
                lf.conditions.append(Condition(name, kind == "O", "RANGE", args))
            elif "VALUES" in keywords:
                args = _args_of(keywords, "VALUES")
                if not args:
                    raise RpgleError(f"{lf.name}: VALUES needs at least one value")
                lf.conditions.append(Condition(name, kind == "O", "VALUES", args))
            else:
                raise RpgleError(f"{lf.name}: a select/omit line needs COMP, RANGE or VALUES")
            continue

        if not lf.format:
            # File-level keywords before the R line.
            if not jfile_names:
                jfile_names = _names_in(keywords, "JFILE")
                if not jfile_names:
                    pfile = _pfile_name(keywords)
                    if pfile:
                        jfile_names = [pfile]
            if "UNIQUE" in keywords.split():
                lf.unique = True
            continue

        if current_join is not None:
            # A JFLD continuation line: no kind, no name, just more keywords for the current J.
            args = _args_of(keywords, "JFLD")
            if args:
                if len(args) != 2:
                    raise RpgleError(f"{lf.name}: JFLD needs exactly two field names")
                current_join.on.append((args[0], args[1]))
            continue

        if name:
            raise RpgleError(
                f"{lf.name}: a logical file choosing or renaming individual fields is not supported yet "
                f"(omit field lines to show every field of {pf.name})"
            )

    if not lf.format:
        raise RpgleError(f"{lf.name}: no record format (R line) found in DDS")
    if not lf.based_on:
        raise RpgleError(f"{lf.name}: no PFILE or JFILE keyword found in DDS")
    if lf.based_on != pf.name:
        raise RpgleError(f"{lf.name}: PFILE/JFILE({lf.based_on}) does not match physical file {pf.name}")
    for jf in lf.joined:
        if jf.name not in secondaries:
            raise RpgleError(f"{lf.name}: JFILE names {jf.name}, which was not given to parse_lf")
        if not jf.on:
            raise RpgleError(f"{lf.name}: {jf.name} has no JFLD join condition")
    if not lf.keys:
        raise RpgleError(f"{lf.name}: a logical file needs at least one K line (a keyless one would just "
                         f"duplicate {pf.name})")
    for k in lf.keys:
        if _field_named(k) is None:
            raise RpgleError(f"{lf.name}: key field {k} is not in {pf.name}" +
                             (" or its joined files" if lf.joined else ""))
    for c in lf.conditions:
        if _field_named(c.field) is None:
            raise RpgleError(f"{lf.name}: select/omit field {c.field} is not in {pf.name}" +
                             (" or its joined files" if lf.joined else ""))
    for jf in lf.joined:
        for pfield, sfield in jf.on:
            if pf.field_named(pfield) is None:
                raise RpgleError(f"{lf.name}: JFLD field {pfield} is not in {pf.name}")
            if secondaries[jf.name].field_named(sfield) is None:
                raise RpgleError(f"{lf.name}: JFLD field {sfield} is not in {jf.name}")

    seen: dict[str, str] = {}
    lf.fields = []
    for owner_name, fields in [(pf.name, pf.fields), *((jf.name, secondaries[jf.name].fields) for jf in lf.joined)]:
        join_fields = {sfield for jf in lf.joined if jf.name == owner_name for _, sfield in jf.on}
        for f in fields:
            if f.name in seen and f.name not in join_fields:
                raise RpgleError(f"{lf.name}: field {f.name} is in both {seen[f.name]} and {owner_name}; "
                                 "choosing or renaming individual fields is not supported yet")
            if f.name not in seen:
                lf.fields.append(f)
                seen[f.name] = owner_name

    return lf
