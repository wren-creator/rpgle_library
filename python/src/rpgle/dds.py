"""
dds.py
─────────────────────────────────────────────────────────────────
Minimal DDS parser for WORKSTN display files: record formats,
constants, and named input/output fields, plus record-level
CFnn/CAnn (command key -> response indicator) keywords and
field-level indicator conditioning.

Column positions are IBM's documented DDS-for-display-files layout
(verified against the DDS Reference: Display Files manual):
  6      Form type ('A')
  7      Comment marker ('*') -- rest of line is comment text
  7-16   Indicator conditioning (this parser reads one N?nn pair)
  17     'R' starts a record format; blank = field/constant line
  19-28  Name (record format name, field name, or blank = constant)
  30-34  Length
  35     Data type
  36-37  Decimal positions
  38     Usage (O=output, I=input, B=both)
  39-41  Line (screen row, 1-based)
  42-44  Position (screen column, 1-based)
  45-80  Keywords, or the quoted literal for a constant

Not supported (see docs/DDS_REFERENCE.md for the full list and why):
OR'd/multi-line ANDed indicator conditions, subfiles, window/keyword
continuation lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ._helpers import col, digits

_INDICATOR_RE = re.compile(r"(N?)(\d{2})")
_KEYWORD_RE = re.compile(r"([A-Z0-9]+)(\(([^)]*)\))?")
_CF_CA_RE = re.compile(r"^C[AF](\d{2})$")
_QUOTED_RE = re.compile(r"'([^']*)'")


@dataclass(frozen=True)
class IndicatorCond:
    not_: bool
    num: str


@dataclass
class Field:
    """One DDS field or constant line within a record format."""

    kind: str  # "field" | "const"
    row: int
    col: int
    name: str = ""
    length: int = 0
    dtype: str = ""
    # None = character field (blank decimal-positions column);
    # an int = numeric with that many decimals. Must stay None rather
    # than collapse to 0, or a real "0 decimals" numeric field becomes
    # indistinguishable from a character field.
    dec: Optional[int] = None
    usage: str = ""
    text: str = ""  # constant literal text, for kind == "const"
    indicators: list[IndicatorCond] = field(default_factory=list)


@dataclass
class Format:
    """One DDS record format."""

    name: str
    fields: list[Field] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)  # "F3" -> "03"


@dataclass
class DDS:
    """The result of parsing a display-file DDS source file."""

    formats: dict[str, Format]
    # format_order preserves source order, since dict iteration in
    # Python 3.7+ already preserves insertion order -- kept as an
    # explicit field anyway so build_vars' "first definition wins" rule
    # reads the same way it does in the Go port, rather than relying on
    # an implementation detail of dict ordering.
    format_order: list[str]


def _parse_indicators(line: str) -> list[IndicatorCond]:
    raw = line[6:16]  # columns 7-16
    m = _INDICATOR_RE.search(raw)
    if not m:
        return []
    return [IndicatorCond(not_=m.group(1) == "N", num=m.group(2))]


def _parse_keywords(text: str) -> list[tuple[str, list[str]]]:
    """Parses "CF03(03) DSPATR(HI)" into [("CF03", ["03"]), ...]."""
    out = []
    for m in _KEYWORD_RE.finditer(text):
        params = m.group(3).split() if m.group(3) else []
        out.append((m.group(1), params))
    return out


def parse_dds(source: str) -> DDS:
    """Parses DDS display-file source into its record formats."""
    formats: dict[str, Format] = {}
    order: list[str] = []
    current: Optional[Format] = None

    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6:
            continue
        if line[5] != "A":  # column 6
            continue
        if len(line) > 6 and line[6] == "*":  # column 7: comment
            continue

        is_record = col(line, 17, 17) == "R"
        name = col(line, 19, 28)
        keyword_area = line[44:] if len(line) >= 45 else ""

        if is_record:
            current = Format(name=name)
            if name not in formats:
                order.append(name)
            formats[name] = current
            for kw_name, params in _parse_keywords(keyword_area):
                m = _CF_CA_RE.match(kw_name)
                if m and params and params[0]:
                    current.keys[f"F{int(m.group(1))}"] = params[0]  # group(1) is two digits by the regex
            continue

        if current is None:
            continue  # stray field line before any R spec

        line_no = col(line, 39, 41)
        pos_no = col(line, 42, 44)
        if not line_no or not pos_no:
            continue  # not a locatable field/constant line

        row = digits(line_no, "line", name or "a constant") - 1  # convert to 0-indexed, matching screen renderers
        dcol = digits(pos_no, "position", name or "a constant") - 1
        indicators = _parse_indicators(line)

        if not name:
            m = _QUOTED_RE.search(keyword_area)
            current.fields.append(
                Field(kind="const", row=row, col=dcol, text=m.group(1) if m else "", indicators=indicators)
            )
            continue

        length_s = col(line, 30, 34)
        dtype = col(line, 35, 35)
        dec_s = col(line, 36, 37)
        usage = col(line, 38, 38)
        current.fields.append(
            Field(
                kind="field",
                name=name,
                row=row,
                col=dcol,
                length=digits(length_s, "length", name),
                dtype=dtype or ("N" if dec_s else "A"),
                dec=digits(dec_s, "decimal positions", name) if dec_s else None,
                usage=usage or "O",  # O=output, I=input, B=both
                indicators=indicators,
            )
        )

    return DDS(formats=formats, format_order=order)
