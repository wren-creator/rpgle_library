/**
 * src/expr.js
 * ─────────────────────────────────────────────────────────────────
 * A small recursive-descent evaluator for the RPG IV extended-factor-2
 * expressions this interpreter supports: EVAL right-hand sides and
 * IF/DOW/WHEN boolean conditions.
 *
 * Grammar (case-insensitive keywords):
 *   expr       := orExpr
 *   orExpr     := andExpr (OR andExpr)*
 *   andExpr    := notExpr (AND notExpr)*
 *   notExpr    := NOT notExpr | compare
 *   compare    := additive ((= | <> | <= | >= | < | >) additive)?
 *   additive   := term ((+ | -) term)*
 *   term       := unary ((* | /) unary)*
 *   unary      := '-' unary | primary
 *   primary    := NUMBER | STRING | '*ON' | '*OFF' | '*BLANKS' | '*ZEROS'
 *               | '*IN' DIGIT DIGIT | '*INLR'
 *               | '%' NAME '(' expr (':' expr)* ')'
 *               | NAME | '(' expr ')'
 *
 * Truncation-on-assignment (the classic RPG idiom used for modulo,
 * since RPG has no %REM until well past V4R3) is applied by the
 * caller, not here -- this module only evaluates the right-hand side
 * to a plain JS number/string/boolean.
 */

'use strict';

function tokenize(src) {
  const tokens = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) { i++; continue; }
    if (c === "'") {
      let j = i + 1, s = '';
      while (j < src.length && src[j] !== "'") { s += src[j]; j++; }
      tokens.push({ type: 'string', value: s });
      i = j + 1;
      continue;
    }
    if (/[0-9]/.test(c)) {
      let j = i, s = '';
      while (j < src.length && /[0-9.]/.test(src[j])) { s += src[j]; j++; }
      tokens.push({ type: 'number', value: parseFloat(s) });
      i = j;
      continue;
    }
    if (c === '*' && /[A-Za-z]/.test(src[i + 1] || '')) {
      let j = i + 1, s = '*';
      while (j < src.length && /[A-Za-z0-9]/.test(src[j])) { s += src[j]; j++; }
      tokens.push({ type: 'indicator-or-const', value: s.toUpperCase() });
      i = j;
      continue;
    }
    if (c === '%') {
      let j = i + 1, s = '%';
      while (j < src.length && /[A-Za-z0-9]/.test(src[j])) { s += src[j]; j++; }
      tokens.push({ type: 'func', value: s.toUpperCase() });
      i = j;
      continue;
    }
    if (/[A-Za-z_]/.test(c)) {
      let j = i, s = '';
      while (j < src.length && /[A-Za-z0-9_]/.test(src[j])) { s += src[j]; j++; }
      const upper = s.toUpperCase();
      if (upper === 'AND' || upper === 'OR' || upper === 'NOT') tokens.push({ type: 'logic', value: upper });
      else tokens.push({ type: 'name', value: s });
      i = j;
      continue;
    }
    if (c === '<' && src[i + 1] === '>') { tokens.push({ type: 'op', value: '<>' }); i += 2; continue; }
    if (c === '<' && src[i + 1] === '=') { tokens.push({ type: 'op', value: '<=' }); i += 2; continue; }
    if (c === '>' && src[i + 1] === '=') { tokens.push({ type: 'op', value: '>=' }); i += 2; continue; }
    if ('=<>+-*/():'.includes(c)) { tokens.push({ type: 'op', value: c }); i++; continue; }
    throw new Error(`Unexpected character in RPG expression: ${JSON.stringify(c)} in ${JSON.stringify(src)}`);
  }
  return tokens;
}

// RPG compares character data as if both sides were blank-padded, so a
// 20-character field holding BOB equals 'BOB'. Trailing blanks never matter.
function valuesEqual(a, b) {
  if (typeof a === 'string' && typeof b === 'string') return a.trimEnd() === b.trimEnd();
  return a === b;
}

function valueLess(a, b) {
  if (typeof a === 'string' && typeof b === 'string') return a.trimEnd() < b.trimEnd();
  return toNumber(a) < toNumber(b);
}

// ── numbers and text ─────────────────────────────────────────────────
// These mirror the Python port's helpers exactly; tools/parity checks it.

// What a number looks like as text: an optional sign, digits, an optional
// decimal point and an optional exponent. Anything else ("12x", "", "1,5",
// "0x10") is not a number, rather than being read as a prefix, 0 or NaN.
const NUMBER_TEXT = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

// Reads a value as a number: NaN if it isn't one.
function toNumber(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'string') {
    const t = value.trim();
    return NUMBER_TEXT.test(t) ? Number(t) : NaN;
  }
  return NaN;
}

// 10 to the power n, correctly rounded. Math.pow(10, 23) is not: it gives
// 1.0000000000000001e23, where Python's float(10 ** 23) is 1e23.
function pow10(n) { return Number('1e' + n); }

// A number as text: a whole number prints every digit exactly; anything else
// prints the shortest digits that round-trip, and never in exponent form.
function formatNumber(n) {
  if (!Number.isFinite(n)) throw new Error("That value is not a number, so it can't be turned into text");
  if (Number.isInteger(n)) return BigInt(n).toString();
  const text = String(n);
  if (!/e/i.test(text)) return text;
  const [mantissa, exp] = text.split(/e/i);
  const negative = mantissa.startsWith('-');
  const m = negative ? mantissa.slice(1) : mantissa;
  const dot = m.indexOf('.');
  const digits = m.replace('.', '');
  const point = (dot === -1 ? m.length : dot) + parseInt(exp, 10);
  const plain = point <= 0
    ? '0.' + '0'.repeat(-point) + digits
    : point >= digits.length ? digits + '0'.repeat(point - digits.length) : digits.slice(0, point) + '.' + digits.slice(point);
  return (negative ? '-' : '') + plain;
}

function asString(v) {
  if (typeof v === 'string') return v;
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return formatNumber(v);
  return '';
}

// ── built-in functions ───────────────────────────────────────────────
// Each takes (name, args, firstVar): firstVar is the variable if the first
// argument was a bare variable name, else null. %CHAR needs it for the
// variable's declared decimals, which a value alone doesn't carry.

function arity(name, args, lo, hi = lo) {
  if (args.length < lo || args.length > hi) {
    const want = lo === hi ? String(lo) : `${lo} to ${hi}`;
    throw new Error(`${name} takes ${want} argument(s), got ${args.length}`);
  }
}

// A number argument: text must look like one.
function num(name, v) {
  const n = toNumber(v);
  if (typeof v === 'string' && Number.isNaN(n)) throw new Error(`${name}: '${v}' is not a number`);
  return n;
}

// A position, length or count: must be a number; any fraction is dropped.
function whole(name, v) {
  const n = toNumber(v);
  if (!Number.isFinite(n)) throw new Error(`${name}: ${typeof v === 'string' ? `'${v}'` : 'that value'} is not a whole number`);
  return Math.trunc(n);
}

const truncTo = (n, dec) => { const f = pow10(dec); return Math.trunc(n * f) / f; };
const halfAdjust = (n, dec) => { const f = pow10(dec); const r = Math.floor(Math.abs(n) * f + 0.5); return (n < 0 ? -r : r) / f; };

// Text is counted in characters (code points), not UTF-16 units.
const chars = (s) => Array.from(s);

const strFn = (fn) => (name, args) => { arity(name, args, 1); return fn(asString(args[0])); };

const BIFS = {
  '%TRIM': strFn((s) => s.trim()),
  '%TRIML': strFn((s) => s.trimStart()),
  '%TRIMR': strFn((s) => s.trimEnd()),
  '%UPPER': strFn((s) => s.toUpperCase()),
  '%LOWER': strFn((s) => s.toLowerCase()),
  '%LEN': (name, args) => { arity(name, args, 1); return chars(asString(args[0])).length; },
  '%SUBST': (name, args) => {
    arity(name, args, 2, 3);
    const s = chars(asString(args[0]));
    let start = whole(name, args[1]) - 1; // RPG %SUBST start is 1-based
    const length = args.length <= 2 ? s.length - start : whole(name, args[2]);
    start = Math.max(0, Math.min(start, s.length));
    const end = Math.max(start, Math.min(start + length, s.length));
    return s.slice(start, end).join('');
  },
  '%CHAR': (name, args, firstVar) => {
    arity(name, args, 1);
    const v = args[0];
    if (typeof v === 'string') return v;
    if (typeof v === 'boolean') return v ? '1' : '0';
    if (firstVar && firstVar.type === 'N' && firstVar.dec) return Number(v).toFixed(firstVar.dec);
    return formatNumber(Number(v));
  },
  '%DEC': (name, args) => decimal(name, args, truncTo),
  '%DECH': (name, args) => decimal(name, args, halfAdjust),
  '%INT': (name, args) => { arity(name, args, 1); return Math.trunc(num(name, args[0])); },
  '%INTH': (name, args) => { arity(name, args, 1); return halfAdjust(num(name, args[0]), 0); },
  '%ABS': (name, args) => { arity(name, args, 1); return Math.abs(num(name, args[0])); },
  '%REM': (name, args) => {
    arity(name, args, 2);
    const a = num(name, args[0]), b = num(name, args[1]);
    if (b === 0) throw new Error(`${name}: division by zero`);
    return a - Math.trunc(a / b) * b;
  },
  '%DIV': (name, args) => {
    arity(name, args, 2);
    const a = num(name, args[0]), b = num(name, args[1]);
    if (b === 0) throw new Error(`${name}: division by zero`);
    return Math.trunc(a / b);
  },
  '%SQRT': (name, args) => {
    arity(name, args, 1);
    const n = num(name, args[0]);
    if (n < 0) throw new Error('%SQRT of a negative number');
    return Math.sqrt(n);
  },
  '%MIN': (name, args) => minMax(name, args),
  '%MAX': (name, args) => minMax(name, args),
  '%SCAN': (name, args) => {
    arity(name, args, 2, 3);
    const needle = chars(asString(args[0])), hay = chars(asString(args[1]));
    const start = args.length === 3 ? whole(name, args[2]) : 1;
    if (needle.length === 0 || start < 1) return 0;
    for (let i = start - 1; i + needle.length <= hay.length; i++) {
      if (needle.every((c, j) => hay[i + j] === c)) return i + 1;
    }
    return 0;
  },
  '%REPLACE': (name, args) => {
    arity(name, args, 2, 4);
    const repl = chars(asString(args[0])), src = chars(asString(args[1]));
    const start = args.length >= 3 ? whole(name, args[2]) : 1;
    const length = args.length === 4 ? whole(name, args[3]) : repl.length;
    if (start < 1) throw new Error('%REPLACE: start must be 1 or more');
    return [...src.slice(0, start - 1), ...repl, ...src.slice(start - 1 + length)].join('');
  },
};

function decimal(name, args, round) {
  arity(name, args, 3);
  const n = num(name, args[0]);
  const dec = whole(name, args[2]);
  if (dec < 0 || dec > 63) throw new Error(`${name}: decimal positions must be 0 to 63, got ${dec}`);
  return round(n, dec);
}

function minMax(name, args) {
  arity(name, args, 2, 99);
  const better = name === '%MIN' ? (a, b) => a < b : (a, b) => a > b;
  if (args.every((a) => typeof a === 'string')) {
    // compared without trailing blanks, but the original value comes back
    return args.reduce((best, a) => (better(a.trimEnd(), best.trimEnd()) ? a : best));
  }
  const nums = args.map((a) => num(name, a));
  return nums.reduce((best, n) => (better(n, best) ? n : best));
}

class Parser {
  constructor(tokens, ctx) {
    this.tokens = tokens;
    this.pos = 0;
    this.ctx = ctx; // { vars, indicators }
  }
  peek() { return this.tokens[this.pos]; }
  next() { return this.tokens[this.pos++]; }
  expectOp(v) {
    const t = this.next();
    if (!t || t.type !== 'op' || t.value !== v) throw new Error(`Expected '${v}' in RPG expression`);
  }

  parseExpr() { return this.parseOr(); }

  parseOr() {
    let v = this.parseAnd();
    while (this.peek() && this.peek().type === 'logic' && this.peek().value === 'OR') {
      this.next();
      // Parse (consume tokens for) the right side unconditionally --
      // JS's || would short-circuit and skip parsing it, leaving
      // trailing tokens whenever the left side is already true.
      const rhs = this.parseAnd();
      v = Boolean(v) || Boolean(rhs);
    }
    return v;
  }

  // NOT binds looser than a comparison and tighter than AND.
  parseNot() {
    if (this.peek() && this.peek().type === 'logic' && this.peek().value === 'NOT') {
      this.next();
      return !Boolean(this.parseNot());
    }
    return this.parseCompare();
  }

  parseAnd() {
    let v = this.parseNot();
    while (this.peek() && this.peek().type === 'logic' && this.peek().value === 'AND') {
      this.next();
      // Same reasoning as parseOr: always parse the right side, even
      // if the left side is already false.
      const rhs = this.parseNot();
      v = Boolean(v) && Boolean(rhs);
    }
    return v;
  }

  parseCompare() {
    const left = this.parseAdditive();
    const t = this.peek();
    if (t && t.type === 'op' && ['=', '<>', '<=', '>=', '<', '>'].includes(t.value)) {
      this.next();
      const right = this.parseAdditive();
      switch (t.value) {
        case '=': return valuesEqual(left, right);
        case '<>': return !valuesEqual(left, right);
        case '<': return valueLess(left, right);
        case '>': return valueLess(right, left);
        case '<=': return !valueLess(right, left);
        case '>=': return !valueLess(left, right);
      }
    }
    return left;
  }

  parseAdditive() {
    let v = this.parseTerm();
    while (this.peek() && this.peek().type === 'op' && (this.peek().value === '+' || this.peek().value === '-')) {
      const op = this.next().value;
      const rhs = this.parseTerm();
      if (typeof v === 'string' || typeof rhs === 'string') {
        // + joins two character values (padding and all). Anything else
        // involving text is a mistake that would otherwise be a silent
        // "a1" or NaN, so it's an error.
        if (op === '+' && typeof v === 'string' && typeof rhs === 'string') v = v + rhs;
        else throw new Error(`Can't use ${op} between text and a number, or - between two texts`);
      } else {
        v = op === '+' ? v + rhs : v - rhs;
      }
    }
    return v;
  }

  parseTerm() {
    let v = this.parseUnary();
    while (this.peek() && this.peek().type === 'op' && (this.peek().value === '*' || this.peek().value === '/')) {
      const op = this.next().value;
      const rhs = this.parseUnary();
      const a = toNumber(v), b = toNumber(rhs);
      if (op === '/' && b === 0) throw new Error('Division by zero');
      v = op === '*' ? a * b : a / b;
    }
    return v;
  }

  parseUnary() {
    if (this.peek() && this.peek().type === 'op' && this.peek().value === '-') {
      this.next();
      const operand = this.parseUnary();
      if (typeof operand === 'string') throw new Error("Can't negate text");
      return -operand;
    }
    return this.parsePrimary();
  }

  parsePrimary() {
    const t = this.next();
    if (!t) throw new Error('Unexpected end of RPG expression');
    if (t.type === 'number') return t.value;
    if (t.type === 'string') return t.value;
    if (t.type === 'indicator-or-const') return this.resolveIndicatorOrConst(t.value);
    if (t.type === 'func') return this.parseFuncCall(t.value);
    if (t.type === 'name') return this.readVar(t.value);
    if (t.type === 'op' && t.value === '(') {
      const v = this.parseExpr();
      this.expectOp(')');
      return v;
    }
    throw new Error(`Unexpected token in RPG expression: ${JSON.stringify(t)}`);
  }

  resolveIndicatorOrConst(name) {
    if (name === '*ON') return true;
    if (name === '*OFF') return false;
    if (name === '*BLANK' || name === '*BLANKS') return '';
    if (name === '*ZERO' || name === '*ZEROS') return 0;
    if (name === '*INLR' || name === '*INRT') return Boolean(this.ctx.indicators[name.slice(3)]);
    const m = /^\*IN(\d\d)$/.exec(name);
    if (m) return Boolean(this.ctx.indicators[m[1]]);
    throw new Error(`Unsupported RPG figurative constant: ${name}`);
  }

  // %EOF, %FOUND, %EQUAL: the argument is a file name, not an expression,
  // and may be left off to mean the last file used.
  parseFileStatus(name) {
    let fname = null;
    const t = this.peek();
    if (t && t.type === 'op' && t.value === '(') {
      this.next();
      const nt = this.next();
      if (!nt || nt.type !== 'name') throw new Error(`${name} takes a file name`);
      fname = nt.value;
      this.expectOp(')');
    }
    if (!this.ctx.fileStatus) throw new Error(`${name} used but the program has no DISK files`);
    return this.ctx.fileStatus(name.slice(1).toLowerCase(), fname);
  }

  parseFuncCall(name) {
    if (name === '%EOF' || name === '%FOUND' || name === '%EQUAL') return this.parseFileStatus(name);
    const fn = BIFS[name];
    if (!fn) throw new Error(`Unsupported RPG built-in: ${name}`);
    this.expectOp('(');
    // Remember if the first argument was a bare variable: %CHAR needs its
    // declared decimals.
    const before = this.pos;
    const firstToken = this.tokens[before];
    const args = [this.parseExpr()];
    const firstVar = this.pos - before === 1 && firstToken && firstToken.type === 'name'
      ? this.ctx.vars[firstToken.value.toLowerCase()] || null : null;
    while (this.peek() && this.peek().type === 'op' && this.peek().value === ':') {
      this.next();
      args.push(this.parseExpr());
    }
    this.expectOp(')');
    return fn(name, args, firstVar);
  }

  readVar(name) {
    const key = name.toLowerCase();
    if (!(key in this.ctx.vars)) throw new Error(`Unknown RPG variable: ${name}`);
    return this.ctx.vars[key].value;
  }
}

// Evaluates a full extended-factor-2 expression string (a condition, or
// the right-hand side of an EVAL) against the given { vars, indicators }.
function evalExpr(src, ctx) {
  const tokens = tokenize(src);
  const parser = new Parser(tokens, ctx);
  const v = parser.parseExpr();
  if (parser.pos < tokens.length) throw new Error(`Trailing tokens in RPG expression: ${JSON.stringify(src)}`);
  return v;
}

// Splits text on sep, ignoring any sep inside quotes or parentheses:
// splitTop("a : f(b:c) : 'x:y'", ':') is three parts.
function splitTop(text, sep) {
  const parts = [];
  let depth = 0, cur = '', i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "'") {
      let j = i + 1;
      while (j < text.length && text[j] !== "'") j++;
      cur += text.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (c === '(') depth++;
    else if (c === ')') depth--;
    if (c === sep && depth === 0) { parts.push(cur.trim()); cur = ''; } else cur += c;
    i++;
  }
  parts.push(cur.trim());
  return parts;
}

// Splits an EVAL statement's extended-factor-2 text into its lvalue
// name and rvalue expression source, on the first top-level '='.
// RPG EVAL statements never nest a bare assignment inside the
// right-hand side, so the first '=' is always the assignment operator.
function splitAssignment(src) {
  const eq = src.indexOf('=');
  if (eq === -1) throw new Error(`EVAL statement missing '=': ${JSON.stringify(src)}`);
  return { lvalue: src.slice(0, eq).trim(), rvalueSrc: src.slice(eq + 1).trim() };
}

module.exports = { evalExpr, splitAssignment, splitTop, toNumber, pow10, asString };
