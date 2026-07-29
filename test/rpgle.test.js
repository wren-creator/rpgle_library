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
