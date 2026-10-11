'use strict';

// DISK file opcodes running inside a program. Mirrors python/tests/test_fileio.py.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { parseDds, parseRpgle, buildVars, runProgram } = require('../src');
const { Database } = require('../src/database');
const { parsePf } = require('../src/pf');
const { cspec, dds, ddsLine, dspec, fspec } = require('./cols');

const CUST = parsePf('CUSTMAST', dds(
  ddsLine('', '', '', '', '', 'UNIQUE'),
  ddsLine('R', 'CUSTREC'),
  ddsLine('', 'CUSTNO', 5, 'S', 0),
  ddsLine('', 'NAME', 10),
  ddsLine('', 'BAL', 9, 'P', 2),
  ddsLine('K', 'CUSTNO'),
));
const ORDERS = parsePf('ORDERS', dds(
  ddsLine('R', 'ORDREC'),
  ddsLine('', 'OCUST', 5, 'S', 0),
  ddsLine('', 'ORDNO', 5, 'S', 0),
  ddsLine('', 'AMT', 7, 'P', 2),
  ddsLine('K', 'OCUST'),
  ddsLine('K', 'ORDNO'),
));
const OUT = parsePf('OUT', dds(ddsLine('R', 'OUTREC'), ddsLine('', 'MSG', 20), ddsLine('', 'NUM', 9, 'P', 2)));
const PFS = { CUSTMAST: CUST, ORDERS, OUT };

const F_CUST = fspec('CUSTMAST', { type: 'U', addition: 'A', keyed: true });
const F_ORD = fspec('ORDERS', { keyed: true });
const F_OUT = fspec('OUT', { type: 'O' });
const c = cspec;

function setup(t) {
  const db = new Database();
  t.after(() => db.close());
  const cust = db.openFile(CUST);
  for (const [no, name, bal] of [[10, 'ALICE', 100], [20, 'BOB', 50.5], [30, 'CAROL', 0]]) {
    cust.write({ CUSTNO: no, NAME: name, BAL: bal });
  }
  const orders = db.openFile(ORDERS);
  for (const [oc, on, amt] of [[1, 100, 10], [1, 101, 20], [2, 100, 5], [1, 102, 30]]) {
    orders.write({ OCUST: oc, ORDNO: on, AMT: amt });
  }
  db.openFile(OUT);

  const run = (...lines) => {
    const prog = parseRpgle([F_CUST, F_ORD, F_OUT, ...lines].join('\n'));
    for (const _ of runProgram(prog, parseDds(''), { db, pfs: PFS })) { /* no screens */ }
  };
  const out = () => {
    const h = db.openFile(OUT);
    h.setll(null);
    const rows = [];
    for (let r = h.read(); r; r = h.read()) rows.push([r.MSG, r.NUM]);
    return rows;
  };
  return { db, run, out };
}

// ── CHAIN ──────────────────────────────────────────────────────────────

test('chain found loads the record into variables', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('WANT', 5, 0, 20),
    c('CHAIN', { factor1: 'want', factor2: 'CUSTMAST', hi: '71' }),
    c('EVAL', { factor2: 'msg = name' }),
    c('EVAL', { factor2: 'num = bal' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.deepEqual(out(), [['BOB', 50.5]]);
});

test('chain not found sets the indicator and conditioned ops follow it', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('WANT', 5, 0, 99),
    c('CHAIN', { factor1: 'want', factor2: 'CUSTMAST', hi: '71' }),
    c('EVAL', { factor2: "msg = 'NOT FOUND'", cond: '71' }),
    c('EVAL', { factor2: 'msg = name', cond: 'N71' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.deepEqual(out(), [['NOT FOUND', 0]]);
});

test('chain with a literal key', (t) => {
  const { run, out } = setup(t);
  run(
    c('CHAIN', { factor1: '30', factor2: 'CUSTMAST' }),
    c('EVAL', { factor2: 'msg = name' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][0], 'CAROL');
});

test('a result indicator is reset each time', (t) => {
  // 99 is not found (indicator on), then 10 is found (must go off).
  const { run, out } = setup(t);
  run(
    dspec('WANT', 5, 0, 99),
    c('CHAIN', { factor1: 'want', factor2: 'CUSTMAST', hi: '71' }),
    c('EVAL', { factor2: 'want = 10' }),
    c('CHAIN', { factor1: 'want', factor2: 'CUSTMAST', hi: '71' }),
    c('EVAL', { factor2: "msg = 'STILL ON'", cond: '71' }),
    c('EVAL', { factor2: 'msg = name', cond: 'N71' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][0], 'ALICE');
});

test('%FOUND after chain', (t) => {
  const { run, out } = setup(t);
  run(
    c('CHAIN', { factor1: '20', factor2: 'CUSTMAST' }),
    c('IF', { factor2: '%FOUND(CUSTMAST)' }),
    c('EVAL', { factor2: "msg = 'YES'" }),
    c('ELSE'),
    c('EVAL', { factor2: "msg = 'NO'" }),
    c('ENDIF'),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][0], 'YES');
});

// ── READ loops ─────────────────────────────────────────────────────────

test('a read loop until EOF sums every record', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('TOTAL', 9, 2, 0),
    c('SETLL', { factor1: '*LOVAL', factor2: 'CUSTMAST' }),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('DOW', { factor2: '%EOF(CUSTMAST) = *OFF' }),
    c('EVAL', { factor2: 'total = total + bal' }),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('ENDDO'),
    c('EVAL', { factor2: "msg = 'TOTAL'" }),
    c('EVAL', { factor2: 'num = total' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.deepEqual(out(), [['TOTAL', 150.5]]);
});

test('the EOF indicator form of the same loop', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('N', 3, 0, 0),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('DOW', { factor2: '*IN75 = *OFF' }),
    c('EVAL', { factor2: 'n = n + 1' }),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('ENDDO'),
    c('EVAL', { factor2: 'num = n' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][1], 3);
});

test('LEAVE inside an IF on a counter', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('N', 3, 0, 0),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('DOW', { factor2: '*IN75 = *OFF' }),
    c('EVAL', { factor2: 'n = n + 1' }),
    c('IF', { factor2: 'n = 2' }),
    c('LEAVE'),
    c('ENDIF'),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('ENDDO'),
    c('EVAL', { factor2: 'num = n' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][1], 2);
});

test('LEAVE conditioned on the EOF indicator', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('N', 3, 0, 0),
    c('DOW', { factor2: '1 = 1' }),
    c('READ', { factor2: 'CUSTMAST', eq: '75' }),
    c('LEAVE', { cond: '75' }),
    c('EVAL', { factor2: 'n = n + 1' }),
    c('ENDDO'),
    c('EVAL', { factor2: 'num = n' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][1], 3);
});

test('READE over a KLIST partial key', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('TOTAL', 9, 2, 0),
    dspec('WANT', 5, 0, 1),
    c('KLIST', { factor1: 'ordk' }),
    c('KFLD', { result: 'want' }),
    c('SETLL', { factor1: 'ordk', factor2: 'ORDERS' }),
    c('READE', { factor1: 'ordk', factor2: 'ORDERS', eq: '75' }),
    c('DOW', { factor2: '%EOF = *OFF' }),
    c('EVAL', { factor2: 'total = total + amt' }),
    c('READE', { factor1: 'ordk', factor2: 'ORDERS', eq: '75' }),
    c('ENDDO'),
    c('EVAL', { factor2: 'num = total' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][1], 60); // customer 1: 10 + 20 + 30
});

test('SETLL indicators: not found and equal', (t) => {
  const { run, out } = setup(t);
  run(
    c('SETLL', { factor1: '20', factor2: 'CUSTMAST', hi: '71', eq: '73' }),
    c('EVAL', { factor2: "msg = 'EQ'", cond: '73' }),
    c('EVAL', { factor2: "msg = 'NOEQ'", cond: 'N73' }),
    c('WRITE', { factor2: 'OUTREC' }),
    c('SETLL', { factor1: '99', factor2: 'CUSTMAST', hi: '71', eq: '73' }),
    c('EVAL', { factor2: "msg = 'NF'", cond: '71' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.deepEqual(out().map(([m]) => m), ['EQ', 'NF']);
});

test('a key can be a list of expressions in parentheses', (t) => {
  const { run, out } = setup(t);
  run(
    dspec('N', 5, 0, 1),
    c('CHAIN', { factor1: '(n : 100 + 1)', factor2: 'ORDERS', hi: '71' }),
    c('EVAL', { factor2: 'num = amt' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][1], 20);
});

// ── WRITE / UPDATE / DELETE ────────────────────────────────────────────

test('update the record just chained', (t) => {
  const { db, run } = setup(t);
  run(
    c('CHAIN', { factor1: '20', factor2: 'CUSTMAST' }),
    c('EVAL', { factor2: 'bal = bal + 10' }),
    c('UPDATE', { factor2: 'CUSTREC' }),
  );
  assert.equal(db.openFile(CUST).chain([20]).BAL, 60.5);
});

test('write a new record from variables', (t) => {
  const { db, run } = setup(t);
  run(
    c('EVAL', { factor2: 'custno = 40' }),
    c('EVAL', { factor2: "name = 'DAVE'" }),
    c('EVAL', { factor2: 'bal = 12.5' }),
    c('WRITE', { factor2: 'CUSTREC' }),
  );
  const rec = db.openFile(CUST).chain([40]);
  assert.deepEqual([rec.NAME, rec.BAL], ['DAVE', 12.5]);
});

test('delete the current record', (t) => {
  const { db, run } = setup(t);
  run(c('CHAIN', { factor1: '10', factor2: 'CUSTMAST' }), c('DELETE', { factor2: 'CUSTREC' }));
  assert.equal(db.openFile(CUST).chain([10]), null);
});

test('delete by key reports not found', (t) => {
  const { run, out } = setup(t);
  run(
    c('DELETE', { factor1: '99', factor2: 'CUSTMAST', hi: '71' }),
    c('EVAL', { factor2: "msg = 'NR'", cond: '71' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][0], 'NR');
});

test('a duplicate key with an error indicator does not halt', (t) => {
  const { run, out } = setup(t);
  run(
    c('EVAL', { factor2: 'custno = 10' }),
    c('WRITE', { factor2: 'CUSTREC', lo: '72' }),
    c('EVAL', { factor2: "msg = 'ERR'", cond: '72' }),
    c('WRITE', { factor2: 'OUTREC' }),
  );
  assert.equal(out()[0][0], 'ERR');
});

test('a duplicate key without an error indicator halts', (t) => {
  const { run } = setup(t);
  assert.throws(() => run(c('EVAL', { factor2: 'custno = 10' }), c('WRITE', { factor2: 'CUSTREC' })), /duplicate key/);
});

// ── setup and errors ───────────────────────────────────────────────────

test('an unknown file is an error even with an error indicator', (t) => {
  const { run } = setup(t);
  assert.throws(() => run(c('READ', { factor2: 'NOPE', lo: '72' })), /not a DISK file/);
});

test('file and format names are matched ignoring case', (t) => {
  const { run, out } = setup(t);
  run(
    c('CHAIN', { factor1: '10', factor2: 'custmast' }),
    c('EVAL', { factor2: 'msg = name' }),
    c('WRITE', { factor2: 'outrec' }),
  );
  assert.equal(out()[0][0], 'ALICE');
});

test('DISK files need a database', () => {
  const prog = parseRpgle([F_CUST, c('READ', { factor2: 'CUSTMAST' })].join('\n'));
  assert.throws(() => [...runProgram(prog, parseDds(''))], /no database was given/);
});

test('a missing DDS for a file is an error', (t) => {
  const { db } = setup(t);
  const prog = parseRpgle([F_CUST, c('READ', { factor2: 'CUSTMAST' })].join('\n'));
  assert.throws(() => [...runProgram(prog, parseDds(''), { db, pfs: {} })], /no physical file DDS/);
});

test('a DISK file must be externally described', (t) => {
  const { db } = setup(t);
  const prog = parseRpgle(fspec('CUSTMAST', { fmt: 'F' }));
  assert.throws(() => [...runProgram(prog, parseDds(''), { db, pfs: PFS })], /externally described/);
});

test('conditioning a control op is rejected loudly', (t) => {
  const { run } = setup(t);
  assert.throws(() => run(c('IF', { factor2: '1 = 1', cond: '71' }), c('ENDIF')), /Conditioning indicators on IF/);
});

test('an unusable conditioning indicator is rejected loudly', (t) => {
  const { run } = setup(t);
  assert.throws(() => run(c('EVAL', { factor2: 'x = 1', cond: 'XX' })), /conditioning indicator/i);
});

test('buildVars includes DISK record fields', () => {
  const prog = parseRpgle(F_CUST);
  const v = buildVars(prog, parseDds(''), PFS);
  assert.deepEqual([v.custno.type, v.name.type, v.bal.dec], ['N', 'A', 2]);
});

test('changes are durable when the program ends', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpgle-'));
  t.after(() => fs.rmSync(dir, { recursive: true }));
  const file = path.join(dir, 't.db');
  const db = new Database(file);
  db.openFile(CUST);
  const prog = parseRpgle([
    F_CUST,
    c('EVAL', { factor2: 'custno = 7' }),
    c('EVAL', { factor2: "name = 'GRACE'" }),
    c('WRITE', { factor2: 'CUSTREC' }),
  ].join('\n'));
  for (const _ of runProgram(prog, parseDds(''), { db, pfs: PFS })) { /* no screens */ }
  const other = new Database(file); // a second connection sees it only if committed
  t.after(() => other.close());
  assert.equal(other.openFile(CUST).chain([7]).NAME, 'GRACE');
  db.close();
});
