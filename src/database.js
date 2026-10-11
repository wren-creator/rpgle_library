/**
 * src/database.js
 * ─────────────────────────────────────────────────────────────────
 * DISK file access on SQLite: the part of RPG that makes it a database
 * language. Each physical file (see pf.js) becomes one table, and a
 * FileHandle gives it the keyed-access model RPG programs expect:
 *
 *   SETLL / SETGT   position the file relative to a key, read nothing
 *   CHAIN           position at a key and read that record
 *   READ / READE    read the next record / next record with this key
 *   READP / READPE  read the previous record / previous with this key
 *   WRITE / UPDATE / DELETE
 *
 * The position model is the thing to understand. A file always has a
 * "current position" that the next READ moves from:
 *
 *   ['rec', key, rrn]   sitting on a record we just read or chained to
 *   ['setll', prefix]   just before the first record whose key >= prefix
 *   ['setgt', prefix]   just after the last record whose key <= prefix
 *
 * Positions are stored as keys, never as SQL cursors, so deleting or
 * updating records (including the current one) can't invalidate them. A
 * partial key (fewer values than the file has key fields) compares on the
 * leading key fields only, which is how RPG's KLIST partial keys and READE
 * loops over a customer's orders work.
 *
 * Every record also has a relative record number, rrn (SQLite's own row
 * id, kept in the _rrn column), which breaks ties between duplicate keys in
 * arrival order and is the key for files with no K lines.
 *
 * Mirrors python/src/rpgle/database.py. SQLite comes from better-sqlite3,
 * which is an optional peer dependency: the rest of the library needs
 * nothing, and it is only loaded when a program actually declares DISK files.
 * ─────────────────────────────────────────────────────────────────
 */

'use strict';

const { toNumber, asString } = require('./expr');

const RRN = '_rrn';

function q(name) {
  return '"' + name.replace(/"/g, '""') + '"';
}

// Loaded on first use, so programs without DISK files never need it.
function loadSqlite() {
  try {
    return require('better-sqlite3');
  } catch (e) {
    if (e && e.code === 'MODULE_NOT_FOUND') {
      throw new Error(
        'DISK files need the better-sqlite3 package (Node 20 or newer). Install it with: npm install better-sqlite3'
      );
    }
    throw e;
  }
}

// A value as a number: blank is 0, anything else must be a plain number.
function asNumber(value, name) {
  if (value === null || value === undefined) return 0;
  if (typeof value === 'string' && value.trim() === '') return 0;
  const n = toNumber(value);
  if (!Number.isFinite(n)) {
    const what = typeof value === 'string' ? `'${value.trim()}'` : 'that value';
    throw new Error(`${name}: ${what} is not a number`);
  }
  return n;
}

// Program value -> stored value. Character data is stored without trailing
// blanks so 'A' and 'A    ' are the same key, as in RPG.
function toDb(f, value) {
  if (f.dtype === 'A') return (value === null || value === undefined ? '' : asString(value)).replace(/\s+$/, '');
  const n = asNumber(value, f.name);
  return f.dec === 0 ? Math.trunc(n) : n;
}

function isConstraint(e) {
  return e && typeof e.code === 'string' && e.code.startsWith('SQLITE_CONSTRAINT');
}

class FileHandle {
  constructor(conn, pf) {
    this._conn = conn;
    this.pf = pf;
    this._table = q(pf.name);
    this._cols = pf.fields.map((f) => f.name);
    this._field = new Map(pf.fields.map((f) => [f.name, f]));
    // Arrival-sequence files are "keyed" by relative record number.
    this.keyCols = pf.keys.length ? [...pf.keys] : [RRN];
    this._pos = ['setll', []];
    this._current = null; // rrn of the record last read or chained to
    this.found = false;
    this.eof = false;
    this.equal = false;
  }

  _coerceKey(values) {
    if (values.length > this.keyCols.length) {
      throw new Error(`${this.pf.name}: key has ${values.length} fields, file has ${this.keyCols.length}`);
    }
    return values.map((v, i) => {
      const f = this._field.get(this.keyCols[i]);
      if (!f) return Math.trunc(asNumber(v, 'relative record number')); // relative record number
      return toDb(f, v);
    });
  }

  _recordKey(row) {
    return this.keyCols.map((c) => row[c]);
  }

  _toRecord(row) {
    const rec = {};
    for (const c of this._cols) rec[c] = row[c];
    return rec;
  }

  _select(where, params, forward) {
    const dir = forward ? 'ASC' : 'DESC';
    const order = [...this.keyCols, RRN].map((c) => `${q(c)} ${dir}`).join(', ');
    return this._conn.prepare(`SELECT * FROM ${this._table} WHERE ${where} ORDER BY ${order} LIMIT 1`).get(...params);
  }

  _stepCondition(forward) {
    const kind = this._pos[0];
    if (kind === 'rec') {
      const [, key, rrn] = this._pos;
      const names = [...this.keyCols, RRN].map(q).join(', ');
      const marks = new Array(key.length + 1).fill('?').join(', ');
      return [`(${names}) ${forward ? '>' : '<'} (${marks})`, [...key, rrn]];
    }
    const prefix = this._pos[1];
    if (prefix.length === 0) {
      // Positioned before everything (setll) or after everything (setgt).
      return [(kind === 'setll') === forward ? '1' : '0', []];
    }
    const names = this.keyCols.slice(0, prefix.length).map(q).join(', ');
    const marks = prefix.map(() => '?').join(', ');
    let op;
    if (forward) op = kind === 'setll' ? '>=' : '>';
    else op = kind === 'setll' ? '<' : '<=';
    return [`(${names}) ${op} (${marks})`, [...prefix]];
  }

  _land(row) {
    this._pos = ['rec', this._recordKey(row), row[RRN]];
    this._current = row[RRN];
    this.eof = false;
    return this._toRecord(row);
  }

  _step(forward, match) {
    const [where, params] = this._stepCondition(forward);
    const row = this._select(where, params, forward);
    if (!row) {
      this.eof = true;
      return null;
    }
    const rec = this._land(row);
    if (match) {
      const n = match.length;
      if (this.keyCols.slice(0, n).some((c, i) => row[c] !== match[i])) {
        // Positioned past the last match; nothing is current.
        this.eof = true;
        this._current = null;
        return null;
      }
    }
    return rec;
  }

  read() {
    return this._step(true);
  }

  readp() {
    return this._step(false);
  }

  reade(key) {
    return this._step(true, this._coerceKey(key));
  }

  readpe(key) {
    return this._step(false, this._coerceKey(key));
  }

  chain(key) {
    const k = this._coerceKey(key);
    const where = this.keyCols.slice(0, k.length).map((c) => `${q(c)} = ?`).join(' AND ') || '1';
    const row = this._select(where, k, true);
    this.found = row !== undefined;
    return row ? this._land(row) : null;
  }

  /** Position before the first record with key >= the given key. null means *LOVAL. */
  setll(key) {
    this._position('setll', key === null ? [] : this._coerceKey(key));
  }

  /** Position after the last record with key <= the given key. null means *HIVAL. */
  setgt(key) {
    if (key === null) {
      this._pos = ['setgt', []];
      this.found = false;
      this.equal = false;
      this.eof = false;
      return;
    }
    this._position('setgt', this._coerceKey(key));
  }

  _position(kind, prefix) {
    this._pos = [kind, prefix];
    this.eof = false;
    if (prefix.length === 0) {
      this.found = kind === 'setll' ? this._count('1', []) > 0 : false;
      this.equal = false;
      return;
    }
    const names = this.keyCols.slice(0, prefix.length).map(q).join(', ');
    const marks = prefix.map(() => '?').join(', ');
    const op = kind === 'setll' ? '>=' : '>';
    this.found = this._count(`(${names}) ${op} (${marks})`, prefix) > 0;
    const eq = this.keyCols.slice(0, prefix.length).map((c) => `${q(c)} = ?`).join(' AND ');
    this.equal = this._count(eq, prefix) > 0;
  }

  _count(where, params) {
    return this._conn.prepare(`SELECT COUNT(*) AS n FROM ${this._table} WHERE ${where}`).get(...params).n;
  }

  _values(record) {
    const missing = this._cols.filter((c) => !(c in record));
    if (missing.length) throw new Error(`${this.pf.name}: record is missing fields ${JSON.stringify(missing)}`);
    return this._cols.map((c) => toDb(this._field.get(c), record[c]));
  }

  write(record) {
    const cols = this._cols.map(q).join(', ');
    const marks = this._cols.map(() => '?').join(', ');
    const values = this._values(record);
    try {
      this._conn.prepare(`INSERT INTO ${this._table} (${cols}) VALUES (${marks})`).run(...values);
    } catch (e) {
      if (isConstraint(e)) throw new Error(`${this.pf.name}: duplicate key`);
      throw e;
    }
  }

  update(record) {
    if (this._current === null) {
      throw new Error(`${this.pf.name}: UPDATE with no current record (read or chain one first)`);
    }
    const sets = this._cols.map((c) => `${q(c)} = ?`).join(', ');
    const values = this._values(record);
    let info;
    try {
      info = this._conn
        .prepare(`UPDATE ${this._table} SET ${sets} WHERE ${q(RRN)} = ?`)
        .run(...values, this._current);
    } catch (e) {
      if (isConstraint(e)) throw new Error(`${this.pf.name}: duplicate key`);
      throw e;
    }
    if (info.changes === 0) throw new Error(`${this.pf.name}: the current record no longer exists`);
    // The key may have changed; keep the position on this record.
    const row = this._conn.prepare(`SELECT * FROM ${this._table} WHERE ${q(RRN)} = ?`).get(this._current);
    this._pos = ['rec', this._recordKey(row), this._current];
  }

  /**
   * Deletes the record with the given key (first match), or the current
   * record when key is null. Returns whether one was deleted.
   */
  delete(key = null) {
    let rrn;
    if (key === null) {
      if (this._current === null) {
        throw new Error(`${this.pf.name}: DELETE with no current record (read or chain one first)`);
      }
      rrn = this._current;
    } else {
      const k = this._coerceKey(key);
      const where = this.keyCols.slice(0, k.length).map((c) => `${q(c)} = ?`).join(' AND ');
      const row = this._select(where, k, true);
      if (!row) {
        this.found = false;
        return false;
      }
      rrn = row[RRN];
    }
    const info = this._conn.prepare(`DELETE FROM ${this._table} WHERE ${q(RRN)} = ?`).run(rrn);
    this.found = info.changes > 0;
    if (rrn === this._current) this._current = null;
    return this.found;
  }
}

/** A SQLite database holding DISK files. path ':memory:' (the default) vanishes on close. */
class Database {
  constructor(path = ':memory:') {
    const Sqlite = loadSqlite();
    this._conn = new Sqlite(path);
    this._handles = new Map();
  }

  /**
   * Creates the file's table if it doesn't exist, and returns a handle.
   * Opening an existing table checks the columns match, so a changed DDS
   * fails loudly instead of corrupting data.
   */
  openFile(pf) {
    const table = q(pf.name);
    const defs = pf.fields.map((f) => {
      const type = f.dtype === 'A' ? 'TEXT' : f.dec === 0 ? 'INTEGER' : 'REAL';
      return `${q(f.name)} ${type} NOT NULL`;
    });
    this._conn.exec(
      `CREATE TABLE IF NOT EXISTS ${table} (${q(RRN)} INTEGER PRIMARY KEY AUTOINCREMENT, ${defs.join(', ')})`
    );
    const existing = this._conn.prepare(`PRAGMA table_info(${table})`).all().map((r) => r.name).slice(1);
    const wanted = pf.fields.map((f) => f.name);
    if (existing.length !== wanted.length || existing.some((n, i) => n !== wanted[i])) {
      throw new Error(
        `${pf.name}: table columns ${JSON.stringify(existing)} don't match the DDS fields ${JSON.stringify(wanted)}`
      );
    }
    if (pf.keys.length) {
      const idx = q(`${pf.name}_KEY`);
      const cols = pf.keys.map(q).join(', ');
      this._conn.exec(`CREATE ${pf.unique ? 'UNIQUE ' : ''}INDEX IF NOT EXISTS ${idx} ON ${table} (${cols})`);
    }
    const h = new FileHandle(this._conn, pf);
    this._handles.set(pf.name, h);
    return h;
  }

  file(name) {
    const h = this._handles.get(name.toUpperCase());
    if (!h) throw new Error(`File ${name} is not open`);
    return h;
  }

  /** Writes are already durable (autocommit); kept so callers can treat both ports alike. */
  commit() {}

  /** Closes the database. Safe to call more than once. */
  close() {
    if (this._conn.open) this._conn.close();
  }
}

module.exports = { Database, FileHandle };
