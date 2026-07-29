'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { parseDds } = require('../src/dds');

const PROG_DIR = path.join(__dirname, '..', 'programs');
const ddsSource = fs.readFileSync(path.join(PROG_DIR, 'adventure.dspf'), 'utf8');

test('parseDds finds all three record formats with their fields and CF03 key', () => {
  const dds = parseDds(ddsSource);
  assert.deepEqual(Object.keys(dds.formats).sort(), ['COMBAT', 'ENDGAME', 'MAIN']);
  assert.equal(dds.formats.MAIN.keys.F3, '03');
  assert.equal(dds.formats.COMBAT.keys.F3, '03');
  const mainFieldNames = dds.formats.MAIN.fields.filter(f => f.kind === 'field').map(f => f.name);
  assert.deepEqual(mainFieldNames, ['LOCTXT', 'PLRHP', 'MSGTXT', 'CMD']);
  const cmdField = dds.formats.MAIN.fields.find(f => f.kind === 'field' && f.name === 'CMD');
  assert.equal(cmdField.usage, 'B');
  const msgField = dds.formats.MAIN.fields.find(f => f.kind === 'field' && f.name === 'MSGTXT');
  assert.deepEqual(msgField.indicators, [{ not: false, num: '90' }]);
});
