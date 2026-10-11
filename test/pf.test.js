'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { parsePf } = require('../src/pf');
const { parseRpgle } = require('../src/rpgle');
const { fspec, cspec, ddsLine, dds } = require('./cols');

const CUST_PF = dds(
  ddsLine('', '', '', '', '', 'UNIQUE'),
  ddsLine('R', 'CUSTREC'),
  ddsLine('', 'CUSTNO', 5, 'S', 0),
  ddsLine('', 'NAME', 20),
  ddsLine('', 'BAL', 9, 'P', 2),
  ddsLine('K', 'CUSTNO'),
);

// ── physical file DDS (parity with python/tests/test_pf.py) ─────────────

test('parsePf reads the format, fields and key', () => {
  const pf = parsePf('custmast', CUST_PF);
  assert.equal(pf.name, 'CUSTMAST');
  assert.equal(pf.format, 'CUSTREC');
  assert.deepEqual(pf.fields.map((f) => f.name), ['CUSTNO', 'NAME', 'BAL']);
  assert.deepEqual(pf.fields.map((f) => [f.dtype, f.dec]), [['N', 0], ['A', 0], ['N', 2]]);
  assert.deepEqual(pf.keys, ['CUSTNO']);
  assert.equal(pf.unique, true);
});

test('no K line means arrival sequence', () => {
  const pf = parsePf('log', dds(ddsLine('R', 'LOGREC'), ddsLine('', 'MSG', 40)));
  assert.deepEqual(pf.keys, []);
  assert.equal(pf.unique, false);
});

test('parsePf refuses what it cannot honour, loudly', () => {
  const cases = [
    [dds(ddsLine('R', 'R1'), ddsLine('', 'A', 3), ddsLine('K', 'A', '', '', '', 'DESCEND')), /DESCEND/],
    [dds(ddsLine('R', 'R1'), ddsLine('', 'A', 3), ddsLine('K', 'B')), /key field B is not in/],
    ['', /no record format/],
    [dds(ddsLine('R', 'R1'), ddsLine('R', 'R2')), /only one record format/],
    [dds(ddsLine('R', 'R1'), ddsLine('', 'A', '3X')), /length '3X' is not a number/],
    [dds(ddsLine('R', 'R1'), ddsLine('', 'A', 3, 'S', 'q')), /decimal positions 'q' is not a number/],
  ];
  for (const [src, pattern] of cases) assert.throws(() => parsePf('F', src), pattern, src);
});

test('comment lines and lines before the R line are ignored', () => {
  const pf = parsePf('f', ['     A* a comment', ddsLine('R', 'R1'), ddsLine('', 'X', 1)].join('\n'));
  assert.deepEqual(pf.fields.map((f) => f.name), ['X']);
});

// ── F-specs and resulting indicators ────────────────────────────────────

test('every F-spec is collected and file stays the WORKSTN one', () => {
  const rpg = parseRpgle([
    fspec('CUSTMAST', { type: 'U', desig: 'F', addition: 'A', fmt: 'E', keyed: true, device: 'DISK' }),
    fspec('INQ', { type: 'C', device: 'WORKSTN' }),
    fspec('ORDERS', { type: 'I', device: 'DISK' }),
  ].join('\n'));
  assert.deepEqual(rpg.files.map((f) => f.name), ['CUSTMAST', 'INQ', 'ORDERS']);
  assert.equal(rpg.file.name, 'INQ');
  const cm = rpg.files[0];
  assert.deepEqual([cm.type, cm.addition, cm.keyed, cm.device], ['U', 'A', true, 'DISK']);
});

test('the first WORKSTN file wins, not the last F-spec', () => {
  const rpg = parseRpgle([fspec('A', { type: 'C', device: 'WORKSTN' }), fspec('B', { type: 'C', device: 'WORKSTN' })].join('\n'));
  assert.equal(rpg.file.name, 'A');
});

test('result indicators are read from columns 71 to 76', () => {
  const s = parseRpgle(cspec('CHAIN', { factor1: 'custno', factor2: 'CUSTMAST', hi: '71', lo: '72', eq: '73' })).statements[0];
  assert.deepEqual([s.op, s.factor1, s.factor2], ['CHAIN', 'custno', 'CUSTMAST']);
  assert.deepEqual([s.indHi, s.indLo, s.indEq], ['71', '72', '73']);
});

test('a file name must be an object name, so it can never be a path', () => {
  for (const bad of ['../x', 'a/b', 'a.b']) {
    assert.throws(() => parseRpgle('     F' + bad.padEnd(10) + ' O    E             PRINTER'), /not a valid file name/, bad);
  }
  assert.equal(parseRpgle(fspec('CUST$1', { device: 'DISK' })).files[0].name, 'CUST$1');
});
