"""
spool.py
─────────────────────────────────────────────────────────────────
Printer output. A Spool collects what programs WRITE to printer files
and turns each into a text "spool file": one per printer file opened,
pages separated by a form feed.

Page model (see prtf.py for the DDS side). Each open printer file tracks
`line`, the number of the last line printed on the current page (0 = a
fresh page). To write a record:

  before    SKIPB(n): go to line n (a new page first if n is not below the
            lines already used). SPACEB(n): n lines down from the last
            line. Neither: the next line.
  body      each field's line is absolute (page line n; at or above the
            last line used means a new page, the classic printer-DDS
            gotcha), relative (+n), or blank (same as the previous field).
  after     SKIPA(n) / SPACEA(n); the default is the next line.
  overflow  if the last body line reaches the overflow line, the file's
            overflow indicator (OFLIND) is turned on. The program turns it
            off again, usually after printing its headings.

A record that doesn't fit on the rest of the page starts a new one.
Defaults match CRTPRTF: 66 lines to a page, overflow at line 60.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ._helpers import is_object_name
from .errors import RpgleError
from .prtf import PrintFile, PrintFormat, edit_number, parse_prtf
from .rpgle import Program

FORM_FEED = "\f"


class PrinterFile:
    """One open printer file: the page being built and the pages finished."""

    def __init__(self, name: str, defs: PrintFile, page_length: int, overflow_line: int):
        self.name = name
        self.defs = defs
        self.page_length = page_length
        self.overflow_line = overflow_line
        self.pages: list[dict[int, str]] = [{}]  # each page: line number -> text
        self.line = 0  # the last line printed on the current page
        self.page_number = 1

    # ── page control ─────────────────────────────────────────────

    @property
    def _page(self) -> dict[int, str]:
        return self.pages[-1]

    def _new_page(self) -> None:
        if self._page:  # an empty page isn't worth a form feed
            self.pages.append({})
            self.page_number += 1
        self.line = 0

    def _skip_to(self, n: int) -> None:
        """Position so the next line printed is line n (a new page if n is
        at or above a line already used)."""
        if n > self.page_length:
            raise RpgleError(f"{self.name}: skip to line {n} is past the page length of {self.page_length}")
        if n <= self.line:
            self._new_page()
        self.line = n - 1

    # ── writing a record ─────────────────────────────────────────

    def write(self, fmt_name: str, value_of: Callable[[str], Any], is_on: Callable[[str], bool]) -> bool:
        """Prints one record. value_of(field name) gives a field's value;
        is_on(indicator) tests a field's conditioning indicator. Returns
        whether the overflow line was reached.
        """
        fmt = self.defs.formats.get(fmt_name.upper())
        if fmt is None:
            raise RpgleError(f"{self.name} has no record format {fmt_name}")

        if fmt.skipb is not None:
            self._skip_to(fmt.skipb)
        elif fmt.spaceb is not None:
            self.line += fmt.spaceb - 1

        fields = [f for f in fmt.fields if all(is_on(c.num) != c.not_ for c in f.indicators)]
        targets = self._plan(fields, self.line)
        if targets and (self._needs_new_page(fields, targets)):
            self._new_page()
            targets = self._plan(fields, 0)
            if max(targets) > self.page_length:
                raise RpgleError(
                    f"{fmt.name}: a record {max(targets)} lines deep doesn't fit on a {self.page_length}-line page"
                )

        for f, line_no in zip(fields, targets):
            text = self._text(fmt, f, value_of)
            row = self._page.get(line_no, "")
            if len(row) < f.col:
                row = row.ljust(f.col)
            self._page[line_no] = row[: f.col] + text + row[f.col + len(text) :]
        if targets:
            self.line = max(targets)

        if fmt.skipa is not None:
            self._skip_to(fmt.skipa)
        elif fmt.spacea is not None:
            self.line += fmt.spacea - 1
        return bool(targets) and max(targets) >= self.overflow_line

    def _plan(self, fields: list, last: int) -> list[int]:
        """The page line each field lands on, given `last`, the last line
        already used. The first field on a blank line goes on the next
        line; later blank lines mean "the same line as the field before".
        """
        out: list[int] = []
        for f in fields:
            if f.line_mode == "abs":
                line_no = f.line
            elif f.line_mode == "rel":
                line_no = (out[-1] if out else last) + f.line
            else:
                line_no = out[-1] if out else last + 1
            out.append(line_no)
        return out

    def _needs_new_page(self, fields: list, targets: list[int]) -> bool:
        for f, t in zip(fields, targets):
            if f.line_mode == "abs" and t <= self.line:
                return True
        return max(targets) > self.page_length

    def _text(self, fmt: PrintFormat, f, value_of: Callable[[str], Any]) -> str:
        if f.kind == "const":
            return f.text
        value = float(self.page_number) if f.pagnbr else value_of(f.name)
        if f.dtype == "A":
            return str(value if value is not None else "")[: f.length].ljust(f.length)
        return edit_number(float(value or 0), f.length, f.dec, f.edtcde)

    # ── output ───────────────────────────────────────────────────

    def text(self) -> str:
        pages = []
        for page in self.pages:
            if not page:
                continue
            last = max(page)
            pages.append("\n".join(page.get(i, "").rstrip() for i in range(1, last + 1)))
        return ("\n" + FORM_FEED + "\n").join(pages) + ("\n" if pages else "")  # a form feed on a line of its own


class Spool:
    """Collects every printer file the program (and any program it calls)
    opens, and finds each one's DDS (<file>.prtf) in `directories`.
    """

    def __init__(
        self,
        directories: Iterable[Path] = (),
        page_length: int = 66,
        overflow_line: int = 60,
        defs: Optional[dict[str, PrintFile]] = None,
    ):
        if not 1 <= overflow_line <= page_length:
            raise RpgleError(f"overflow line {overflow_line} must be between 1 and the page length {page_length}")
        self.directories = [Path(d) for d in directories]
        self.page_length = page_length
        self.overflow_line = overflow_line
        self.defs = {k.upper(): v for k, v in (defs or {}).items()}
        self.files: list[PrinterFile] = []

    def load_defs(self, prog: Program, directories: Iterable[Path] = ()) -> dict[str, PrintFile]:
        """The printer DDS for every printer file the program declares."""
        out: dict[str, PrintFile] = {}
        dirs = [*[Path(d) for d in directories], *self.directories]
        for fs in prog.files:
            if (fs.device or "").upper() != "PRINTER":
                continue
            key = fs.name.upper()
            if not is_object_name(fs.name):
                raise RpgleError(f"{fs.name!r} is not a valid printer file name")
            if (fs.format or "").upper() != "E":
                raise RpgleError(f"{key}: only externally described printer files (F-spec column 22 = E) are supported")
            if key in self.defs:
                out[key] = self.defs[key]
                continue
            found = None
            for d in dirs:
                for cand in (fs.name.lower(), fs.name.upper(), fs.name):
                    p = d / f"{cand}.prtf"
                    if p.is_file():
                        found = p
                        break
                if found:
                    break
            if found is None:
                where = dirs[0] if dirs else Path(".")
                raise RpgleError(f"No printer file DDS for {fs.name}: expected {where / (fs.name.lower() + '.prtf')}")
            out[key] = parse_prtf(fs.name, found.read_text())
        return out

    def open(self, name: str, defs: PrintFile) -> PrinterFile:
        pf = PrinterFile(name, defs, self.page_length, self.overflow_line)
        self.files.append(pf)
        return pf

    def outputs(self) -> list[tuple[str, str]]:
        """(spool file name, text) for every printer file that printed
        anything. Two opens of the same printer file get -2, -3 ... suffixes.
        """
        seen: dict[str, int] = {}
        out = []
        for pf in self.files:
            text = pf.text()
            if not text:
                continue
            base = pf.name.lower()
            seen[base] = seen.get(base, 0) + 1
            out.append((base if seen[base] == 1 else f"{base}-{seen[base]}", text))
        return out
