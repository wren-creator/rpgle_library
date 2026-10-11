/**
 * src/fileops.js
 * ─────────────────────────────────────────────────────────────────
 * Runs the DISK file opcodes for the interpreter: opens every DISK
 * F-spec against a Database, resolves keys (literals, variables,
 * KLISTs), moves records between the file and the program's variables,
 * and sets the resulting indicators in columns 71-76.
 *
 * Which indicator means what depends on the opcode, and it's a classic
 * source of confusion, so it's all here in one table:
 *
 *   opcode                      hi (71-72)   lo (73-74)   eq (75-76)
 *   CHAIN                       not found    error        -
 *   SETLL                       not found    error        equal
 *   SETGT                       not found    error        -
 *   READ READE READP READPE     -            error        end of file
 *   WRITE UPDATE                -            error        -
 *   DELETE                      not found    error        -
 *
 * Every indicator the line names is set explicitly, on or off, on every
 * execution, so a stale "on" from an earlier iteration can't leak.
 * An error with no error indicator on the line is raised, which is what
 * a real program with no *PSSR does too (it halts).
 *
 * Mirrors python/src/rpgle/fileops.py.
 * ─────────────────────────────────────────────────────────────────
 */

'use strict';

const { splitTop } = require('./expr');

const FILE_OPS = new Set(['CHAIN', 'SETLL', 'SETGT', 'READ', 'READE', 'READP', 'READPE', 'WRITE', 'UPDATE', 'DELETE']);

// True if the '(' at the start of text is closed by the ')' at its end.
function closesAtEnd(text) {
  let depth = 0;
  let inStr = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "'") inStr = !inStr;
    else if (!inStr) {
      if (c === '(') depth++;
      else if (c === ')') {
        depth--;
        if (depth === 0) return i === text.length - 1;
      }
    }
  }
  return false;
}

const LOVAL = Symbol('LOVAL');
const HIVAL = Symbol('HIVAL');

class FileRuntime {
  /**
   * @param prog    the parsed program
   * @param db      a Database (see database.js), or undefined
   * @param pfs     physical file definitions by upper-case file name
   * @param klists  KLIST name -> list of KFLD field names
   */
  constructor(prog, db, pfs, klists) {
    this._klists = klists;
    this._files = new Map();
    this._byFormat = new Map();
    this._last = null;

    const disk = prog.files.filter((f) => (f.device || '').toUpperCase() === 'DISK');
    if (disk.length && !db) throw new Error('Program declares DISK files but no database was given');
    for (const fs of disk) {
      const name = fs.name.toUpperCase();
      if ((fs.format || '').toUpperCase() !== 'E') {
        throw new Error(`${name}: only externally described DISK files (F-spec column 22 = E) are supported`);
      }
      const pf = (pfs || {})[name];
      if (!pf) throw new Error(`${name}: no physical file DDS was provided`);
      this._files.set(name, db.openFile(pf));
      this._byFormat.set(pf.format, name);
    }
  }

  // ── name and key resolution ────────────────────────────────────

  _resolve(name) {
    if (!name) throw new Error('File opcode is missing its file name (factor 2)');
    let up = name.toUpperCase();
    up = this._byFormat.get(up) || up;
    const h = this._files.get(up);
    if (!h) throw new Error(`${name} is not a DISK file or record format in this program`);
    return h;
  }

  /**
   * A key array, or the sentinel LOVAL / HIVAL. The key is a KLIST name,
   * *LOVAL/*HIVAL, or an expression, or several in parentheses:
   * custno, 'ABC', (custno : ordno), (a + 1).
   */
  _key(factor1, evaluate) {
    if (!factor1) throw new Error('This file opcode needs a key in factor 1');
    const up = factor1.toUpperCase();
    if (up === '*LOVAL') return LOVAL;
    if (up === '*HIVAL') return HIVAL;
    if (this._klists[up]) return this._klists[up].map((n) => evaluate(n));
    let text = factor1.trim();
    if (text.startsWith('(') && closesAtEnd(text)) text = text.slice(1, -1);
    return splitTop(text, ':').map((part) => evaluate(part));
  }

  _needKey(instr, evaluate) {
    const key = this._key(instr.factor1, evaluate);
    if (key === LOVAL || key === HIVAL) throw new Error(`${instr.op} does not accept *LOVAL or *HIVAL`);
    return key;
  }

  static _value(variables, name) {
    const v = variables[name.trim().toLowerCase()];
    if (!v) throw new Error(`Unknown RPG variable: ${name}`);
    return v.value;
  }

  // ── execution ──────────────────────────────────────────────────

  exec(instr, variables, assign, setInd, evaluate) {
    const op = instr.op;
    const h = this._resolve(instr.factor2);
    this._last = h;
    let hi = false, lo = false, eq = false; // what each indicator column will be set to
    try {
      if (op === 'CHAIN') {
        const rec = h.chain(this._needKey(instr, evaluate));
        if (rec) this._load(rec, assign);
        hi = !h.found;
      } else if (op === 'SETLL' || op === 'SETGT') {
        const key = this._key(instr.factor1, evaluate);
        if (key === LOVAL) h.setll(null);
        else if (key === HIVAL) h.setgt(null);
        else if (op === 'SETLL') h.setll(key);
        else h.setgt(key);
        hi = !h.found;
        eq = h.equal;
      } else if (op === 'READ' || op === 'READP') {
        const rec = op === 'READ' ? h.read() : h.readp();
        if (rec) this._load(rec, assign);
        eq = h.eof;
      } else if (op === 'READE' || op === 'READPE') {
        const key = this._needKey(instr, evaluate);
        const rec = op === 'READE' ? h.reade(key) : h.readpe(key);
        if (rec) this._load(rec, assign);
        eq = h.eof;
      } else if (op === 'WRITE' || op === 'UPDATE') {
        const record = {};
        for (const f of h.pf.fields) record[f.name] = FileRuntime._value(variables, f.name);
        if (op === 'WRITE') h.write(record);
        else h.update(record);
      } else if (op === 'DELETE') {
        if (instr.factor1) {
          h.delete(this._needKey(instr, evaluate));
          hi = !h.found;
        } else {
          h.delete();
        }
      }
    } catch (e) {
      if (!instr.indLo) throw e;
      hi = false; lo = true; eq = false;
    }

    for (const [ind, on] of [[instr.indHi, hi], [instr.indLo, lo], [instr.indEq, eq]]) {
      if (ind) setInd(ind, on);
    }
  }

  _load(rec, assign) {
    for (const [name, value] of Object.entries(rec)) assign(name, value);
  }

  /** %EOF / %FOUND / %EQUAL. kind is 'eof', 'found' or 'equal'. */
  status(kind, name) {
    let h;
    if (name) h = this._resolve(name);
    else if (this._last) h = this._last;
    else throw new Error(`%${kind.toUpperCase()} with no file operation before it`);
    return Boolean(h[kind]);
  }
}

module.exports = { FileRuntime, FILE_OPS };
