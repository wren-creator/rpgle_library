"""
database.py
─────────────────────────────────────────────────────────────────
DISK file access on SQLite: the part of RPG that makes it a database
language. Each physical file (see pf.py) becomes one table, and a
FileHandle gives it the keyed-access model RPG programs expect:

  SETLL / SETGT   position the file relative to a key, read nothing
  CHAIN           position at a key and read that record
  READ / READE    read the next record / next record with this key
  READP / READPE  read the previous record / previous with this key
  WRITE / UPDATE / DELETE

The position model is the thing to understand. A file always has a
"current position" that the next READ moves from:

  ("rec", key, rrn)   sitting on a record we just read or chained to
  ("setll", prefix)   just before the first record whose key >= prefix
  ("setgt", prefix)   just after the last record whose key <= prefix

Positions are stored as keys, never as SQL cursors, so deleting or
updating records (including the current one) can't invalidate them.
A partial key (fewer values than the file has key fields) compares on
the leading key fields only, which is how RPG's KLIST partial keys and
READE loops over a customer's orders work.

Every record also has a relative record number, rrn (SQLite's own
row id, kept in the _rrn column), which breaks ties between duplicate
keys in arrival order and is the key for files with no K lines.

Errors raise RpgleError; the interpreter turns that into the error
indicator when the program asked for one.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .errors import RpgleError
from .pf import PfField, PhysicalFile

_RRN = "_rrn"
_MAX_RRN = 2**62


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _to_db(f: PfField, value: Any) -> Any:
    """Program value -> stored value. Character data is stored without
    trailing blanks so 'A' and 'A    ' are the same key, as in RPG.
    """
    if f.dtype == "A":
        return ("" if value is None else str(value)).rstrip()
    n = float(value or 0)
    return int(n) if f.dec == 0 else n


class FileHandle:
    """One open DISK file."""

    def __init__(self, conn: sqlite3.Connection, pf: PhysicalFile):
        self._conn = conn
        self.pf = pf
        self._table = _q(pf.name)
        self._cols = [f.name for f in pf.fields]
        self._field = {f.name: f for f in pf.fields}
        # Arrival-sequence files are "keyed" by relative record number.
        self.key_cols = list(pf.keys) if pf.keys else [_RRN]
        self._pos: tuple = ("setll", ())
        self._current: Optional[int] = None  # rrn of the record last read/chained
        self.found = False
        self.eof = False
        self.equal = False

    # ── key handling ─────────────────────────────────────────────

    def _key_field(self, col: str) -> Optional[PfField]:
        return self._field.get(col)

    def _coerce_key(self, values: tuple) -> tuple:
        if len(values) > len(self.key_cols):
            raise RpgleError(f"{self.pf.name}: key has {len(values)} fields, file has {len(self.key_cols)}")
        out = []
        for col, v in zip(self.key_cols, values):
            f = self._key_field(col)
            if f is None:  # relative record number
                out.append(int(float(v or 0)))
            else:
                out.append(_to_db(f, v))
        return tuple(out)

    def _record_key(self, row: dict) -> tuple:
        return tuple(row[c] for c in self.key_cols)

    # ── reading ──────────────────────────────────────────────────

    def _row_to_record(self, row: sqlite3.Row) -> dict:
        return {c: row[c] for c in self._cols}

    def _select(self, where: str, params: list, forward: bool) -> Optional[sqlite3.Row]:
        order = ", ".join(f"{_q(c)} {'ASC' if forward else 'DESC'}" for c in [*self.key_cols, _RRN])
        sql = f"SELECT * FROM {self._table} WHERE {where} ORDER BY {order} LIMIT 1"
        cur = self._conn.execute(sql, params)
        return cur.fetchone()

    def _step_condition(self, forward: bool) -> tuple[str, list]:
        kind = self._pos[0]
        if kind == "rec":
            _, key, rrn = self._pos
            names = ", ".join(_q(c) for c in [*self.key_cols, _RRN])
            marks = ", ".join("?" for _ in range(len(key) + 1))
            return f"({names}) {'>' if forward else '<'} ({marks})", [*key, rrn]
        prefix = self._pos[1]
        if not prefix:
            # Positioned before everything (setll) or after everything (setgt).
            return ("1" if (kind == "setll") == forward else "0"), []
        names = ", ".join(_q(c) for c in self.key_cols[: len(prefix)])
        marks = ", ".join("?" for _ in prefix)
        if forward:
            op = ">=" if kind == "setll" else ">"
        else:
            op = "<" if kind == "setll" else "<="
        return f"({names}) {op} ({marks})", list(prefix)

    def _land(self, row: Optional[sqlite3.Row]) -> Optional[dict]:
        if row is None:
            self.eof = True
            return None
        rec = self._row_to_record(row)
        key = self._record_key({c: row[c] for c in self.key_cols})
        self._pos = ("rec", key, row[_RRN])
        self._current = row[_RRN]
        self.eof = False
        return rec

    def _step(self, forward: bool, match: Optional[tuple] = None) -> Optional[dict]:
        where, params = self._step_condition(forward)
        row = self._select(where, params, forward)
        if row is None:
            self.eof = True
            return None
        rec = self._land(row)
        if match is not None:
            n = len(match)
            if tuple(row[c] for c in self.key_cols[:n]) != match:
                # Positioned past the last match; nothing is current.
                self.eof = True
                self._current = None
                return None
        return rec

    def read(self) -> Optional[dict]:
        return self._step(True)

    def readp(self) -> Optional[dict]:
        return self._step(False)

    def reade(self, key: tuple) -> Optional[dict]:
        return self._step(True, self._coerce_key(key))

    def readpe(self, key: tuple) -> Optional[dict]:
        return self._step(False, self._coerce_key(key))

    def chain(self, key: tuple) -> Optional[dict]:
        k = self._coerce_key(key)
        where = " AND ".join(f"{_q(c)} = ?" for c in self.key_cols[: len(k)]) or "1"
        row = self._select(where, list(k), True)
        self.found = row is not None
        if row is None:
            return None
        return self._land(row)

    def setll(self, key: Optional[tuple]) -> None:
        """Position before the first record with key >= the given key.
        key None means *LOVAL (the very start of the file).
        """
        prefix = () if key is None else self._coerce_key(key)
        self._position("setll", prefix)

    def setgt(self, key: Optional[tuple]) -> None:
        """Position after the last record with key <= the given key.
        key None means *HIVAL (the very end of the file).
        """
        if key is None:
            self._pos = ("setgt", ())
            self.found = False
            self.equal = False
            self.eof = False
            return
        self._position("setgt", self._coerce_key(key))

    def _position(self, kind: str, prefix: tuple) -> None:
        self._pos = (kind, prefix)
        self.eof = False
        if not prefix:
            self.found = self._count("1", []) > 0 if kind == "setll" else False
            self.equal = False
            return
        names = ", ".join(_q(c) for c in self.key_cols[: len(prefix)])
        marks = ", ".join("?" for _ in prefix)
        op = ">=" if kind == "setll" else ">"
        self.found = self._count(f"({names}) {op} ({marks})", list(prefix)) > 0
        eq = " AND ".join(f"{_q(c)} = ?" for c in self.key_cols[: len(prefix)])
        self.equal = self._count(eq, list(prefix)) > 0

    def _count(self, where: str, params: list) -> int:
        return self._conn.execute(f"SELECT COUNT(*) FROM {self._table} WHERE {where}", params).fetchone()[0]

    # ── writing ──────────────────────────────────────────────────

    def _values(self, record: dict) -> list:
        missing = [c for c in self._cols if c not in record]
        if missing:
            raise RpgleError(f"{self.pf.name}: record is missing fields {missing}")
        return [_to_db(self._field[c], record[c]) for c in self._cols]

    def write(self, record: dict) -> None:
        cols = ", ".join(_q(c) for c in self._cols)
        marks = ", ".join("?" for _ in self._cols)
        try:
            self._conn.execute(f"INSERT INTO {self._table} ({cols}) VALUES ({marks})", self._values(record))
        except sqlite3.IntegrityError:
            raise RpgleError(f"{self.pf.name}: duplicate key") from None

    def update(self, record: dict) -> None:
        if self._current is None:
            raise RpgleError(f"{self.pf.name}: UPDATE with no current record (read or chain one first)")
        sets = ", ".join(f"{_q(c)} = ?" for c in self._cols)
        try:
            cur = self._conn.execute(
                f"UPDATE {self._table} SET {sets} WHERE {_q(_RRN)} = ?", [*self._values(record), self._current]
            )
        except sqlite3.IntegrityError:
            raise RpgleError(f"{self.pf.name}: duplicate key") from None
        if cur.rowcount == 0:
            raise RpgleError(f"{self.pf.name}: the current record no longer exists")
        # The key may have changed; keep the position on this record.
        row = self._conn.execute(
            f"SELECT * FROM {self._table} WHERE {_q(_RRN)} = ?", [self._current]
        ).fetchone()
        self._pos = ("rec", self._record_key({c: row[c] for c in self.key_cols}), self._current)

    def delete(self, key: Optional[tuple] = None) -> bool:
        """Deletes the record with the given key (first match), or the
        current record when key is None. Returns whether one was deleted.
        """
        if key is None:
            if self._current is None:
                raise RpgleError(f"{self.pf.name}: DELETE with no current record (read or chain one first)")
            rrn = self._current
        else:
            k = self._coerce_key(key)
            where = " AND ".join(f"{_q(c)} = ?" for c in self.key_cols[: len(k)])
            row = self._select(where, list(k), True)
            if row is None:
                self.found = False
                return False
            rrn = row[_RRN]
        cur = self._conn.execute(f"DELETE FROM {self._table} WHERE {_q(_RRN)} = ?", [rrn])
        self.found = cur.rowcount > 0
        if rrn == self._current:
            self._current = None
        return self.found


class Database:
    """A SQLite database holding DISK files. path ':memory:' (the
    default) is private to this process and vanishes on close.
    """

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._handles: dict[str, FileHandle] = {}

    def open_file(self, pf: PhysicalFile) -> FileHandle:
        """Creates the file's table if it doesn't exist, and returns a
        handle. Opening an existing table checks the columns match, so
        a changed DDS fails loudly instead of corrupting data.
        """
        table = _q(pf.name)
        defs = []
        for f in pf.fields:
            sql_type = "TEXT" if f.dtype == "A" else ("INTEGER" if f.dec == 0 else "REAL")
            defs.append(f"{_q(f.name)} {sql_type} NOT NULL")
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ({_q(_RRN)} INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(defs)})"
        )
        existing = [r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")]
        if existing[1:] != [f.name for f in pf.fields]:
            raise RpgleError(
                f"{pf.name}: table columns {existing[1:]} don't match the DDS fields {[f.name for f in pf.fields]}"
            )
        if pf.keys:
            idx = _q(f"{pf.name}_KEY")
            cols = ", ".join(_q(k) for k in pf.keys)
            self._conn.execute(f"CREATE {'UNIQUE ' if pf.unique else ''}INDEX IF NOT EXISTS {idx} ON {table} ({cols})")
        h = FileHandle(self._conn, pf)
        self._handles[pf.name] = h
        return h

    def file(self, name: str) -> FileHandle:
        try:
            return self._handles[name.upper()]
        except KeyError:
            raise RpgleError(f"File {name} is not open") from None

    def commit(self) -> None:
        """Commits pending changes. A no-op once the database is closed."""
        try:
            self._conn.commit()
        except sqlite3.ProgrammingError:
            pass  # already closed

    def close(self) -> None:
        """Commits and closes. Safe to call more than once."""
        try:
            self._conn.commit()
        except sqlite3.ProgrammingError:
            return  # already closed
        self._conn.close()
