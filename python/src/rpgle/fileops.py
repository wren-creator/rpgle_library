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
from .pf import PhysicalFile
from .rpgle import Program

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
        pfs: Optional[dict[str, PhysicalFile]],
        klists: dict[str, list[str]],
    ):
        self._klists = klists
        self._files: dict[str, FileHandle] = {}
        self._by_format: dict[str, str] = {}
        self._last: Optional[FileHandle] = None
        # data structure name -> prefix of its variables ("cust." if qualified)
        self._ds_prefix = {d.name.lower(): (d.name.lower() + "." if d.qualified else "") for d in prog.dss}

        disk = [f for f in prog.files if (f.device or "").upper() == "DISK"]
        if disk and db is None:
            raise RpgleError("Program declares DISK files but no database was given")
        for fs in disk:
            name = fs.name.upper()
            if (fs.format or "").upper() != "E":
                raise RpgleError(f"{name}: only externally described DISK files (F-spec column 22 = E) are supported")
            pf = (pfs or {}).get(name)
            if pf is None:
                raise RpgleError(f"{name}: no physical file DDS was provided")
            self._files[name] = db.open_file(pf)  # type: ignore[union-attr]
            self._by_format[pf.format] = name

    # ── name and key resolution ──────────────────────────────────

    def _resolve(self, name: Optional[str]) -> FileHandle:
        if not name:
            raise RpgleError("File opcode is missing its file name (factor 2)")
        up = name.upper()
        up = self._by_format.get(up, up)
        h = self._files.get(up)
        if h is None:
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
    ) -> None:
        h = self._resolve(instr.factor2)
        self._last = h
        op = instr.op
        prefix = ""
        if instr.ds:
            if instr.ds.lower() not in self._ds_prefix:
                raise RpgleError(f"{instr.ds} is not a data structure")
            prefix = self._ds_prefix[instr.ds.lower()]
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

    def _need_key(self, instr, evaluate: Callable[[str], Any]) -> tuple:
        key = self._key(instr.factor1, evaluate)
        if key in (_LOVAL, _HIVAL):
            raise RpgleError(f"{instr.op} does not accept *LOVAL or *HIVAL")
        return key

    @staticmethod
    def _load(rec: dict, assign: Callable[[str, Any], None], prefix: str) -> None:
        for name, value in rec.items():
            assign(prefix + name, value)

    def status(self, kind: str, name: Optional[str]) -> bool:
        """%EOF / %FOUND / %EQUAL. kind is 'eof', 'found' or 'equal'."""
        if name:
            h = self._resolve(name)
        elif self._last is not None:
            h = self._last
        else:
            raise RpgleError(f"%{kind.upper()} with no file operation before it")
        return bool(getattr(h, kind))
