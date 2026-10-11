"""
prtf.py
─────────────────────────────────────────────────────────────────
Printer file DDS (a PRTF): record formats made of constants and fields
positioned on lines and columns, plus the spacing keywords that say where
on the page a record goes. Same column layout as display-file DDS (see
dds.py), with the printer's rules for the line column:

  39-41  Line: blank = the same line as the previous field in the
         record, 12 = page line 12, +2 = two lines below the previous
         field (or below the last line printed, for the first field)
  42-44  Position: the column the field starts in (1-based)

Record keywords: SKIPB(n) SKIPA(n) SPACEB(n) SPACEA(n).
Field keywords: EDTCDE(c), PAGNBR (this field is the page number).

Edit codes supported: 1 2 3 4 A B C D J K L M Z (see edit_number).
Everything harmless to layout is ignored (TEXT, COLHDG, DSPATR, FONT ...);
anything that would change what is printed and isn't supported (DATE,
TIME, EDTWRD, BARCODE, ...) is an error, never a silent blank.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ._helpers import col
from .dds import IndicatorCond, _parse_indicators, _parse_keywords
from .errors import RpgleError

_QUOTED = re.compile(r"'((?:[^']|'')*)'")
_LINE_REL = re.compile(r"^\+\s*(\d+)$")
_IGNORED = {
    "TEXT", "COLHDG", "ALIAS", "REF", "REFFLD", "INDARA", "DSPATR", "COLOR", "FONT", "CHRSIZ", "CPI", "LPI",
    "HIGHLIGHT", "UNDERLINE", "FORMLEN", "FORMTYPE", "DRAWER", "PRTQLTY", "DUPLEX", "PAGRTT", "FNTCHRSET",
}
_EDIT_CODES = set("1234ABCDJKLMZ")


@dataclass
class PrintField:
    kind: str  # "const" | "field"
    line_mode: str  # "abs" | "rel" | "same"
    line: int  # the page line for "abs", the offset for "rel"
    col: int  # 0-based
    name: str = ""
    length: int = 0
    dtype: str = "A"  # "A" or "N"
    dec: int = 0
    text: str = ""
    edtcde: Optional[str] = None
    pagnbr: bool = False
    indicators: list[IndicatorCond] = field(default_factory=list)


@dataclass
class PrintFormat:
    name: str
    fields: list[PrintField] = field(default_factory=list)
    skipb: Optional[int] = None
    skipa: Optional[int] = None
    spaceb: Optional[int] = None
    spacea: Optional[int] = None


@dataclass
class PrintFile:
    name: str
    formats: dict[str, PrintFormat]  # by upper-case format name, in source order


def _num_param(fmt: str, kw: str, params: list[str]) -> int:
    try:
        n = int(params[0])
    except (IndexError, ValueError):
        raise RpgleError(f"{fmt}: {kw} needs a whole number, as in {kw}(2)") from None
    if n < 0 or n > 255:
        raise RpgleError(f"{fmt}: {kw}({n}) is out of range")
    return n


def parse_prtf(file_name: str, source: str) -> PrintFile:
    formats: dict[str, PrintFormat] = {}
    current: Optional[PrintFormat] = None

    for raw in source.split("\n"):
        line = raw.rstrip()
        if len(line) < 6 or line[5] != "A":
            continue
        if len(line) > 6 and line[6] == "*":
            continue

        name = col(line, 19, 28)
        keyword_area = line[44:] if len(line) >= 45 else ""
        keywords = _parse_keywords(keyword_area.replace("'", " ")) if not _QUOTED.search(keyword_area) else _parse_keywords(
            _QUOTED.sub(" ", keyword_area)
        )

        if col(line, 17, 17) == "R":
            current = PrintFormat(name=name.upper())
            if current.name in formats:
                raise RpgleError(f"{file_name}: record format {name} is defined twice")
            formats[current.name] = current
            for kw, params in keywords:
                if kw in ("SKIPB", "SKIPA", "SPACEB", "SPACEA"):
                    n = _num_param(current.name, kw, params)
                    if kw.startswith("SPACE") and n == 0:
                        raise RpgleError(f"{current.name}: {kw}(0) overprints the last line, which is not supported")
                    if kw.startswith("SKIP") and n == 0:
                        raise RpgleError(f"{current.name}: {kw}(0) is not a line number")
                    setattr(current, kw.lower(), n)
                    continue
                _check_keyword(current.name, kw, record=True)
            _both = (current.skipb and current.spaceb) or (current.skipa and current.spacea)
            if _both:
                raise RpgleError(f"{current.name}: SKIPB with SPACEB, or SKIPA with SPACEA, is ambiguous")
            continue

        if current is None:
            continue

        pos_s = col(line, 42, 44)
        line_s = col(line, 39, 41).replace(" ", "")
        if not pos_s:
            if not name and not line_s:
                continue  # a keyword-only continuation line
            raise RpgleError(f"{current.name}: {name or 'a constant'} needs a position (columns 42-44)")
        try:
            pos = int(pos_s) - 1
        except ValueError:
            raise RpgleError(f"{current.name}: position {pos_s!r} is not a number") from None
        if pos < 0:
            raise RpgleError(f"{current.name}: position must be 1 or more")

        if not line_s:
            mode, ln = "same", 0
        elif _LINE_REL.match(line_s):
            mode, ln = "rel", int(_LINE_REL.match(line_s).group(1))
        elif line_s.isdigit() and int(line_s) > 0:
            mode, ln = "abs", int(line_s)
        else:
            raise RpgleError(f"{current.name}: line {line_s!r} is not a line number or +offset")

        indicators = _parse_indicators(line)
        edtcde, pagnbr = None, False
        for kw, params in keywords:
            if kw == "EDTCDE":
                code = (params[0] if params else "").upper()
                if code not in _EDIT_CODES:
                    raise RpgleError(
                        f"{current.name}: EDTCDE({code or '?'}) is not supported (supported: {' '.join(sorted(_EDIT_CODES))})"
                    )
                if len(params) > 1:
                    raise RpgleError(f"{current.name}: EDTCDE with a fill character is not supported")
                edtcde = code
            elif kw == "PAGNBR":
                pagnbr = True
            else:
                _check_keyword(current.name, kw, record=False)

        if not name:
            m = _QUOTED.search(keyword_area)
            text = m.group(1).replace("''", "'") if m else ""
            current.fields.append(PrintField(kind="const", line_mode=mode, line=ln, col=pos, text=text, indicators=indicators))
            continue

        length_s = col(line, 30, 34)
        dtype = col(line, 35, 35).upper()
        dec_s = col(line, 36, 37)
        numeric = dtype in ("P", "S", "B", "F") or bool(dec_s)
        if not length_s:
            raise RpgleError(f"{current.name}: field {name} needs a length (columns 30-34)")
        current.fields.append(
            PrintField(
                kind="field",
                line_mode=mode,
                line=ln,
                col=pos,
                name=name.upper(),
                length=int(length_s),
                dtype="N" if numeric else "A",
                dec=int(dec_s) if dec_s else 0,
                edtcde=edtcde,
                pagnbr=pagnbr,
                indicators=indicators,
            )
        )
        if pagnbr and not numeric:
            raise RpgleError(f"{current.name}: PAGNBR field {name} must be numeric")

    if not formats:
        raise RpgleError(f"{file_name}: no record formats (R lines) found in the printer DDS")
    return PrintFile(name=file_name.upper(), formats=formats)


def _check_keyword(fmt: str, kw: str, record: bool) -> None:
    if kw in _IGNORED:
        return
    where = "record" if record else "field"
    raise RpgleError(f"{fmt}: the {kw} keyword isn't supported on a printer {where} (it would change what's printed)")


def edit_number(value: float, length: int, dec: int, code: Optional[str]) -> str:
    """Formats a number the way a printer file would.

    Without an edit code: the digits only, zero-filled to the field's
    length, no decimal point (a negative number is an error, since the
    sign would be silently lost). With one, in a fixed width so columns
    line up (right-justified, leading zeros suppressed):

      1 2   commas, decimal point, no sign      3 4   no commas
      A B   commas, decimal point, CR if negative  C D   no commas
      J K   commas, decimal point, - if negative   L M   no commas
      Z     digits only, zero-suppressed, no sign or point

    Odd codes (1 3 A C J L) print a zero as 0 or 0.00; even codes
    (2 4 B D K M) and Z print it as blanks.
    """
    scaled = int(round(abs(value) * 10**dec))
    negative = value < 0 and scaled != 0
    if code is None:
        if negative:
            raise RpgleError(f"a negative number can't be printed without an edit code (EDTCDE), which would lose its sign: {value}")
        return str(scaled).zfill(length)

    commas = code in "12ABJK"
    point = code != "Z"
    suffix_w = 2 if code in "ABCD" else 1 if code in "JKLM" else 0
    int_digits = max(length - dec, 1)
    width = int_digits + (((int_digits - 1) // 3) if commas else 0) + ((1 + dec) if point and dec else 0) + suffix_w

    if scaled == 0 and code in "24BDKMZ":
        return " " * width
    digits = str(scaled).zfill(dec + 1)
    int_part = digits[:-dec] if dec else digits
    frac = digits[-dec:] if dec else ""
    if commas:
        int_part = f"{int(int_part):,}"
    body = int_part + (("." + frac) if point and dec else "")
    if code in "ABCD":
        body += "CR" if negative else "  "
    elif code in "JKLM":
        body += "-" if negative else " "
    return body.rjust(width)
