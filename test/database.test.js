'use strict';

// Keyed file access on SQLite. Mirrors python/tests/test_database.py case for case.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { Database } = require('../src/database');
const { parsePf } = require('../src/pf');
const { dds, ddsLine } = require('./cols');

const CUST = parsePf('CUSTMAST', dds(
  ddsLine('', '', '', '', '', 'UNIQUE'),
  ddsLine('R', 'CUSTREC'),
  ddsLine('', 'CUSTNO', 5, 'S', 0),
  ddsLine('', 'NAME', 10),
  ddsLine('', 'BAL', 9, 'P', 2),
  ddsLine('K', 'CUSTNO'),
));

// Orders: key is (CUSTNO, ORDNO); duplicates of the full key are allowed.
const ORDERS = parsePf('ORDERS', dds(
  ddsLine('R', 'ORDREC'),
  ddsLine('', 'CUSTNO', 5, 'S', 0),
  ddsLine('', 'ORDNO', 5, 'S', 0),
  ddsLine('', 'AMT', 7, 'P', 2),
  ddsLine('K', 'CUSTNO'),
  ddsLine('K', 'ORDNO'),
));

const LOG = parsePf('LOG', dds(ddsLine('R', 'LOGREC'), ddsLine('', 'MSG', 10)));

const cust = (no, name, bal = 0) => ({ CUSTNO: no, NAME: name, BAL: bal });
const order = (c, o, amt = 1) => ({ CUSTNO: c, ORDNO: o, AMT: amt });

function withDb(t, fn) {
  const db = new Database();
  t.after(() => db.close());
  return fn(db);
}

function customers(t) {
  return withDb(t, (db) => {
    const f = db.openFile(CUST);
    // Written out of order on purpose: reads must come back in key order.
    for (const [no, name] of [[30, 'CAROL'], [10, 'ALICE'], [20, 'BOB']]) f.write(cust(no, name));
    return f;
  });
}

function orders(t) {
  return withDb(t, (db) => {
    const f = db.openFile(ORDERS);
    for (const [c, o] of [[2, 101], [1, 102], [1, 100], [3, 100], [1, 101]]) f.write(order(c, o, o));
    return f;
  });
}

test('chain found and not found', (t) => {
  const f = customers(t);
  assert.equal(f.chain([20]).NAME, 'BOB');
  assert.equal(f.found, true);
  assert.equal(f.chain([25]), null);
  assert.equal(f.found, false);
});

test('char keys ignore trailing blanks', (t) => {
  withDb(t, (db) => {
    const f = db.openFile(parsePf('NAMES', dds(ddsLine('R', 'R'), ddsLine('', 'N', 5), ddsLine('K', 'N'))));
    f.write({ N: 'AB   ' });
    assert.notEqual(f.chain(['AB']), null);
  });
});

test('read walks in key order, then EOF', (t) => {
  const f = customers(t);
  f.setll(null);
  const names = [];
  for (let r = f.read(); r; r = f.read()) names.push(r.NAME);
  assert.deepEqual(names, ['ALICE', 'BOB', 'CAROL']);
  assert.equal(f.eof, true);
});

test('read with no position starts at the top', (t) => {
  assert.equal(customers(t).read().NAME, 'ALICE');
});

test('setll exact and between', (t) => {
  const f = customers(t);
  f.setll([20]);
  assert.ok(f.found && f.equal);
  assert.equal(f.read().NAME, 'BOB');

  f.setll([15]);
  assert.equal(f.found, true);
  assert.equal(f.equal, false);
  assert.equal(f.read().NAME, 'BOB'); // first key >= 15
});

test('setll past the end is not found', (t) => {
  const f = customers(t);
  f.setll([99]);
  assert.equal(f.found, false);
  assert.equal(f.read(), null);
  assert.equal(f.eof, true);
});

test('setgt skips the equal record', (t) => {
  const f = customers(t);
  f.setgt([20]);
  assert.equal(f.found, true);
  assert.equal(f.read().NAME, 'CAROL');
});

test('hival then readp gives the last record', (t) => {
  const f = customers(t);
  f.setgt(null);
  assert.equal(f.readp().NAME, 'CAROL');
  assert.equal(f.readp().NAME, 'BOB');
});

test('readp after read goes back', (t) => {
  const f = customers(t);
  f.chain([20]);
  assert.equal(f.readp().NAME, 'ALICE');
  assert.equal(f.readp(), null);
  assert.equal(f.eof, true);
});

test('unique key rejects a duplicate write', (t) => {
  assert.throws(() => customers(t).write(cust(20, 'DUPE')), /duplicate key/);
});

test('update changes the current record', (t) => {
  const f = customers(t);
  f.chain([20]);
  f.update(cust(20, 'ROBERT', 5.5));
  const rec = f.chain([20]);
  assert.deepEqual([rec.NAME, rec.BAL], ['ROBERT', 5.5]);
});

test('update with no current record is an error', (t) => {
  assert.throws(() => customers(t).update(cust(1, 'X')), /no current record/);
});

test('update can change the key and keep position', (t) => {
  const f = customers(t);
  f.chain([20]);
  f.update(cust(25, 'BOB'));
  assert.equal(f.read().NAME, 'CAROL');
  assert.equal(f.chain([20]), null);
  assert.notEqual(f.chain([25]), null);
});

test('delete current and by key', (t) => {
  const f = customers(t);
  f.chain([20]);
  assert.equal(f.delete(), true);
  assert.equal(f.chain([20]), null);
  assert.equal(f.delete([10]), true);
  assert.equal(f.delete([10]), false);
  assert.equal(f.found, false);
});

test('delete with no current record is an error', (t) => {
  assert.throws(() => customers(t).delete(), /no current record/);
});

test('delete during a read loop keeps the position', (t) => {
  const f = customers(t);
  f.setll(null);
  const seen = [];
  for (let r = f.read(); r; r = f.read()) {
    seen.push(r.NAME);
    f.delete();
  }
  assert.deepEqual(seen, ['ALICE', 'BOB', 'CAROL']);
  f.setll(null);
  assert.equal(f.read(), null);
});

test('numbers round trip', (t) => {
  const f = customers(t);
  f.write(cust(40, 'DAVE', 1234.56));
  assert.equal(f.chain([40]).BAL, 1234.56);
  assert.equal(f.chain([40]).CUSTNO, 40);
});

test('numeric fields refuse text that is not a number', (t) => {
  assert.throws(() => customers(t).chain(['abc']), /not a number/);
});

// ── partial keys ────────────────────────────────────────────────────────

test('reade loops over one customer\'s orders', (t) => {
  const f = orders(t);
  f.setll([1]);
  assert.equal(f.equal, true);
  const got = [];
  for (let r = f.reade([1]); r; r = f.reade([1])) got.push(r.ORDNO);
  assert.deepEqual(got, [100, 101, 102]);
  assert.equal(f.eof, true);
});

test('reade with no match is an immediate EOF', (t) => {
  const f = orders(t);
  f.setll([9]);
  assert.equal(f.reade([9]), null);
  assert.equal(f.eof, true);
});

test('chain on a partial key finds the first match', (t) => {
  assert.equal(orders(t).chain([1]).ORDNO, 100);
});

test('chain on the full key', (t) => {
  const f = orders(t);
  assert.equal(f.chain([1, 102]).AMT, 102);
  assert.equal(f.chain([1, 999]), null);
});

test('readpe walks backward within a key', (t) => {
  const f = orders(t);
  f.setgt([1]);
  const got = [];
  for (let r = f.readpe([1]); r; r = f.readpe([1])) got.push(r.ORDNO);
  assert.deepEqual(got, [102, 101, 100]);
});

test('too many key values is an error', (t) => {
  assert.throws(() => orders(t).chain([1, 2, 3]), /key has 3 fields/);
});

test('duplicate full keys are allowed and read in arrival order', (t) => {
  const f = orders(t);
  f.write(order(1, 100, 7));
  f.setll([1, 100]);
  assert.deepEqual([f.read().AMT, f.read().AMT], [100, 7]);
});

// ── arrival sequence ────────────────────────────────────────────────────

test('arrival sequence files read in arrival order', (t) => {
  const f = withDb(t, (db) => {
    const h = db.openFile(LOG);
    for (const m of ['one', 'two', 'three']) h.write({ MSG: m });
    return h;
  });
  assert.deepEqual([f.read().MSG, f.read().MSG, f.read().MSG], ['one', 'two', 'three']);
  assert.equal(f.read(), null);
});

test('arrival sequence files chain by relative record number', (t) => {
  const f = withDb(t, (db) => {
    const h = db.openFile(LOG);
    for (const m of ['one', 'two', 'three']) h.write({ MSG: m });
    return h;
  });
  assert.equal(f.chain([2]).MSG, 'two');
});

// ── the database itself ─────────────────────────────────────────────────

function tmpDb() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpgle-'));
  return { dir, file: path.join(dir, 't.db') };
}

test('data survives a reopen', () => {
  const { dir, file } = tmpDb();
  try {
    const db = new Database(file);
    db.openFile(CUST).write(cust(1, 'ALICE'));
    db.close();
    const db2 = new Database(file);
    assert.equal(db2.openFile(CUST).chain([1]).NAME, 'ALICE');
    db2.close();
  } finally {
    fs.rmSync(dir, { recursive: true });
  }
});

test('a changed DDS fails loudly', () => {
  const { dir, file } = tmpDb();
  try {
    const db = new Database(file);
    db.openFile(CUST);
    db.close();
    const other = parsePf('CUSTMAST', dds(ddsLine('R', 'R'), ddsLine('', 'ZZZ', 3)));
    const db3 = new Database(file);
    assert.throws(() => db3.openFile(other), /don't match/);
    db3.close();
  } finally {
    fs.rmSync(dir, { recursive: true });
  }
});

test('close twice and commit after close are harmless', () => {
  const db = new Database();
  db.close();
  db.close();
  db.commit();
});

test('a file that is not open is an error', (t) => {
  withDb(t, (db) => assert.throws(() => db.file('NOPE'), /not open/));
});
