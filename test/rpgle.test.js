'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { parseRpgle } = require('../src/rpgle');

const PROG_DIR = path.join(__dirname, '..', 'programs');
const rpgSource = fs.readFileSync(path.join(PROG_DIR, 'adventure.rpgle'), 'utf8');

test('parseRpgle reads the WORKSTN F-spec, D-specs, and every C-spec line', () => {
  const rpg = parseRpgle(rpgSource);
  assert.equal(rpg.file.name, 'SCREEN');
  assert.equal(rpg.file.device, 'WORKSTN');
  assert.equal(rpg.file.format, 'E');
  const dnames = rpg.dspecs.map(d => d.name);
  assert.deepEqual(dnames.sort(), ['dmg', 'killcnt', 'loc', 'quot', 'roll', 'seed']);
  assert.ok(rpg.statements.length > 100, 'expected a substantial C-spec statement count');
  assert.ok(rpg.statements.some(s => s.op === 'DIV'));
  assert.ok(rpg.statements.some(s => s.op === 'MVR'));
});

// Build a fixed-form D-spec line by column: name from col 7, S in col 24,
// length ending col 39, decimals in 41-42, keywords from col 44.
function dline(name, length, dec, keywords = '') {
  return '     D' + name.padEnd(17) + 'S' + String(length).padStart(15) +
    (dec === null ? '   ' : String(dec).padStart(3)) + ' ' + keywords;
}

// A source line the parser can't honour must be an error. Silently ignoring
// it lets a program run and quietly do the wrong thing. (Parity with Python.)
test('a DIM keyword is an error, not a silently scalar variable', () => {
  for (const kw of ['DIM(10)', 'dim(10)', 'Dim (10)']) {
    assert.throws(() => parseRpgle(dline('ARR', 5, 0, kw)), /ARR.*DIM|DIM.*ARR/i, kw);
  }
});

test('a text literal that merely mentions DIM is not an array', () => {
  const rpg = parseRpgle(dline('NOTE', 20, null, "INZ('see DIM(3) below')"));
  assert.equal(rpg.dspecs[0].name, 'NOTE');
});

test('an O-spec is an error, not silently ignored', () => {
  assert.throws(() => parseRpgle('     OQPRINT    D    01'), /O-spec/);
});

test('a non-numeric length or decimals is an error naming the field', () => {
  assert.throws(() => parseRpgle(dline('PRICE', '5A', 0)), /PRICE.*length.*not a number/);
  assert.throws(() => parseRpgle(dline('PRICE', 5, 'x')), /PRICE.*decimal positions.*not a number/);
});

test('ordinary D-specs still parse, blank decimals meaning a character field', () => {
  const rpg = parseRpgle(dline('NAME', 20, null) + '\n' + dline('QTY', 5, 0, 'INZ(42)'));
  assert.deepEqual(rpg.dspecs.map((d) => [d.name, d.length, d.dec, d.init]), [['NAME', 20, null, null], ['QTY', 5, 0, '42']]);
});
