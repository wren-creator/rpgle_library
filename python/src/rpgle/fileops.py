"""
fileops.py
─────────────────────────────────────────────────────────────────
Runs the DISK file opcodes for the interpreter: opens every DISK
F-spec against a Database, resolves keys (literals, variables,
KLISTs), moves records between the file and the program's variables,
and sets the resulting indicators in columns 71-76.

Which indicator means what depends on the opcode, and it's a classic
source of confusion, so it's all here in one table:

  opcode                      hi (71-72)   lo (73-74)   eq (75-76)
  CHAIN                       not found    error        -
  SETLL                       not found    error        equal
  SETGT                       not found    error        -
  READ READE READP READPE     -            error        end of file
  WRITE UPDATE                -            error        -
  DELETE                      not found    error        -

Every indicator the line names is set explicitly, on or off, on every
execution, so a stale "on" from an earlier iteration can't leak.
An error with no error indicator on the line is raised, which is what
a real program with no *PSSR does too (it halts).
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .database import Database, FileHandle
from .errors import RpgleError
from .expr import split_top
from .lf import LogicalFile
from .pf import PhysicalFile
from .prtf import PrintFile
from .rpgle import Program
from .spool import PrinterFile, Spool

FILE_OPS = frozenset(
    {"CHAIN", "SETLL", "SETGT", "READ", "READE", "READP", "READPE", "WRITE", "UPDATE", "DELETE"}
)


def _closes_at_end(text: str) -> bool:
    """True if the '(' at the start of text is closed by the ')' at its end."""
    depth = 0
    in_str = False
    for i, c in enumerate(text):
        if c == "'":
            in_str = not in_str
        elif not in_str:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i == len(text) - 1
    return False


_LOVAL = "LOVAL"
_HIVAL = "HIVAL"


class FileRuntime:
    def __init__(
        self,
        prog: Program,
        db: Optional[Database],
        pfs: Optional[dict[str, "PhysicalFile | LogicalFile"]],
        klists: dict[str, list[str]],
        spool: Optional[Spool] = None,
        prtfs: Optional[dict[str, PrintFile]] = None,
        open_hook: Optional[Callable[[str, str], "PhysicalFile | LogicalFile"]] = None,
    ):
        self._klists = klists
        self._printers: dict[str, PrinterFile] = {}
        self._print_oflind: dict[str, Optional[str]] = {}
        self._print_by_format: dict[str, str] = {}
        self._files: dict[str, FileHandle] = {}
        self._by_format: dict[str, str] = {}
        self._last: Optional[FileHandle] = None
        self._db = db
        self._open_hook = open_hook
        # USROPN files: declared (their format is already known, for variables and
        # %by-format lookups) but not opened until an OPEN statement runs. Mode is
        # the F-spec's own I/O/U/C, carried here so OPEN knows what to ask for.
        self._pending: dict[str, tuple["PhysicalFile | LogicalFile", str]] = {}
        self._declared: dict[str, tuple["PhysicalFile | LogicalFile", str]] = {}  # USROPN files, kept for CLOSE
        # data structure name -> prefix of its variables ("cust." if qualified)
        self._ds_prefix = {d.name.lower(): (d.name.lower() + "." if d.qualified else "") for d in prog.dss if not d.dim}

        printers = [f for f in prog.files if (f.device or "").upper() == "PRINTER"]
        if printers and spool is None:
            raise RpgleError("Program declares printer files but no spool was given (pass spool=Spool(...), or use the rpgle command)")
        for fs in printers:
            name = fs.name.upper()
            if (fs.format or "").upper() != "E":
                raise RpgleError(f"{name}: only externally described printer files (F-spec column 22 = E) are supported")
            defs = (prtfs or {}).get(name)
            if defs is None:
                raise RpgleError(f"{name}: no printer file DDS was provided")
            self._printers[name] = spool.open(fs.name, defs)  # type: ignore[union-attr]
            self._print_oflind[name] = fs.oflind
            for fmt_name in defs.formats:
                self._print_by_format[fmt_name] = name

        disk = [f for f in prog.files if (f.device or "").upper() == "DISK"]
        if disk and db is None:
            raise RpgleError("Program declares DISK files but no database was given")
        for fs in disk:
            name = fs.name.upper()
            if (fs.format or "").upper() != "E":
                raise RpgleError(f"{name}: only externally described DISK files (F-spec column 22 = E) are supported")
            layout = (pfs or {}).get(name)
            if layout is None:
                raise RpgleError(f"{name}: no physical file DDS was provided")
            self._by_format[layout.format] = name
            if fs.user_open:
                self._pending[name] = self._declared[name] = (layout, fs.type or "I")
                continue
            if isinstance(layout, LogicalFile) and layout.is_join:
                needed = {layout.based_on, *(jf.name for jf in layout.joined)}
                sub = {n: (pfs or {}).get(n) for n in needed}
                missing = next((n for n, v in sub.items() if v is None), None)
                if missing:
                    raise RpgleError(f"{name}: physical file {missing} for this join logical file "
                                     "was not provided")
                self._files[name] = db.open_join(layout, sub, fs.type or "I")  # type: ignore[union-attr]
            elif isinstance(layout, LogicalFile):
                base = (pfs or {}).get(layout.based_on)
                if base is None:
                    raise RpgleError(f"{name}: physical file {layout.based_on} for this logical file "
                                     "was not provided")
                self._files[name] = db.open_logical(layout, base, fs.type or "I")  # type: ignore[union-attr]
            else:
                self._files[name] = db.open_file(layout, fs.type or "I")  # type: ignore[union-attr]

    # ── name and key resolution ──────────────────────────────────

    def _resolve(self, name: Optional[str]) -> FileHandle:
        if not name:
            raise RpgleError("File opcode is missing its file name (factor 2)")
        up = name.upper()
        up = self._by_format.get(up, up)
        h = self._files.get(up)
        if h is None:
            if up in self._pending:
                raise RpgleError(f"File {up} is not open: it is declared USROPN and needs an OPEN first")
            raise RpgleError(f"{name} is not a DISK file or record format in this program")
        return h

    def _key(self, factor1: Optional[str], evaluate: Callable[[str], Any]) -> Any:
        """A key tuple, or the sentinel _LOVAL / _HIVAL. The key is a
        KLIST name, *LOVAL/*HIVAL, or an expression, or several in
        parentheses: custno, 'ABC', (custno : ordno), (a + 1).
        """
        if not factor1:
            raise RpgleError("This file opcode needs a key in factor 1")
        up = factor1.upper()
        if up == "*LOVAL":
            return _LOVAL
        if up == "*HIVAL":
            return _HIVAL
        if up in self._klists:
            return tuple(evaluate(n) for n in self._klists[up])
        text = factor1.strip()
        if text.startswith("(") and _closes_at_end(text):
            text = text[1:-1]
        return tuple(evaluate(part) for part in split_top(text, ":"))

    @staticmethod
    def _value(variables: dict, name: str) -> Any:
        v = variables.get(name.strip().lower())
        if v is None:
            raise RpgleError(f"Unknown RPG variable: {name}")
        return v.value

    # ── execution ────────────────────────────────────────────────

    def exec(
        self,
        instr,
        variables: dict,
        assign: Callable[[str, Any], None],
        set_ind: Callable[[str, bool], None],
        evaluate: Callable[[str], Any],
        ds_prefix: Optional[dict[str, str]] = None,
        is_on: Optional[Callable[[str], bool]] = None,
    ) -> None:
        op = instr.op
        prefix = ""
        if instr.ds:
            known = self._ds_prefix if ds_prefix is None else ds_prefix
            if instr.ds.lower() not in known:
                raise RpgleError(f"{instr.ds} is not a data structure")
            prefix = known[instr.ds.lower()]

        target = (instr.factor2 or "").upper()
        printer_name = self._print_by_format.get(target) or (target if target in self._printers else None)
        if printer_name is not None:
            self._print(instr, printer_name, target, variables, assign, prefix, is_on)
            return

        h = self._resolve(instr.factor2)
        self._last = h
        hi = lo = eq = False  # what each indicator column will be set to
        try:
            if op == "CHAIN":
                rec = h.chain(self._need_key(instr, evaluate))
                if rec:
                    self._load(rec, assign, prefix)
                hi = not h.found
            elif op in ("SETLL", "SETGT"):
                key = self._key(instr.factor1, evaluate)
                if key == _LOVAL:
                    h.setll(None)
                elif key == _HIVAL:
                    h.setgt(None)
                elif op == "SETLL":
                    h.setll(key)
                else:
                    h.setgt(key)
                hi = not h.found
                eq = h.equal
            elif op in ("READ", "READP"):
                rec = h.read() if op == "READ" else h.readp()
                if rec:
                    self._load(rec, assign, prefix)
                eq = h.eof
            elif op in ("READE", "READPE"):
                key = self._need_key(instr, evaluate)
                rec = h.reade(key) if op == "READE" else h.readpe(key)
                if rec:
                    self._load(rec, assign, prefix)
                eq = h.eof
            elif op in ("WRITE", "UPDATE"):
                record = {f.name: self._value(variables, prefix + f.name) for f in h.pf.fields}
                h.write(record) if op == "WRITE" else h.update(record)
            elif op == "DELETE":
                if instr.factor1:
                    h.delete(self._need_key(instr, evaluate))
                    hi = not h.found
                else:
                    h.delete()
        except RpgleError:
            if not instr.ind_lo:
                raise
            hi, lo, eq = False, True, False

        for ind, on in ((instr.ind_hi, hi), (instr.ind_lo, lo), (instr.ind_eq, eq)):
            if ind:
                set_ind(ind, on)

    def _print(self, instr, printer_name: str, target: str, variables: dict, assign, prefix: str, is_on) -> None:
        printer = self._printers[printer_name]
        if instr.op != "WRITE":
            raise RpgleError(f"{printer.name} is a printer file: only WRITE works on it, not {instr.op}")
        if target == printer_name:
            raise RpgleError(f"WRITE to printer file {printer.name} needs one of its record formats: {', '.join(printer.defs.formats)}")
        overflow = printer.write(
            target,
            lambda name: self._value(variables, prefix + name),
            is_on or (lambda num: False),
        )
        oflind = self._print_oflind.get(printer_name)
        if overflow and oflind:
            assign(oflind, True)  # the program turns it off again, usually after printing its headings

    def _need_key(self, instr, evaluate: Callable[[str], Any]) -> tuple:
        key = self._key(instr.factor1, evaluate)
        if key in (_LOVAL, _HIVAL):
            raise RpgleError(f"{instr.op} does not accept *LOVAL or *HIVAL")
        return key

    @staticmethod
    def _load(rec: dict, assign: Callable[[str, Any], None], prefix: str) -> None:
        for name, value in rec.items():
            assign(prefix + name, value)

    def open_close(self, op: str, name: Optional[str]) -> None:
        """OPEN or CLOSE on a USROPN file (the only kind either opcode does anything useful on here).
        OPEN re-resolves the file through open_hook at the moment it runs, so an OVRDBF the program
        issued after it started (through QCMDEXC, say) and before OPEN takes effect -- the whole
        reason USROPN exists. OPEN on a file that is already open, or CLOSE on one that is not, does
        nothing. CLOSE on a file that was never declared USROPN closes it but nothing reopens it; that
        would need auto-reopen-on-next-access, not built."""
        if not name:
            raise RpgleError(f"{op} needs a file name")
        up = name.upper()
        if op == "CLOSE":
            self._files.pop(up, None)
            if up in self._declared:
                self._pending[up] = self._declared[up]
            return
        if up in self._files:
            return  # already open: a no-op, same as the real thing
        if up not in self._pending:
            raise RpgleError(f"{name} is not a DISK file in this program")
        _, mode = self._pending[up]
        if self._open_hook is None:
            raise RpgleError(f"{up}: USROPN needs a host that can open files after the program starts "
                             "(pass open_hook=... to run_program)")
        layout = self._open_hook(up, mode)
        if isinstance(layout, LogicalFile):
            raise RpgleError(f"{up}: opening a logical file with OPEN is not supported yet")
        self._files[up] = self._db.open_file(layout, mode)  # type: ignore[union-attr]
        del self._pending[up]

    def status(self, kind: str, name: Optional[str]) -> bool:
        """%EOF / %FOUND / %EQUAL. kind is 'eof', 'found' or 'equal'."""
        if name:
            h = self._resolve(name)
        elif self._last is not None:
            h = self._last
        else:
            raise RpgleError(f"%{kind.upper()} with no file operation before it")
        return bool(getattr(h, kind))
