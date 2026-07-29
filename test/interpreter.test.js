'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { parseDds } = require('../src/dds');
const { parseRpgle } = require('../src/rpgle');
const { runProgram, buildVars, compile } = require('../src/interpreter');

const PROG_DIR = path.join(__dirname, '..', 'programs');
const ddsSource = fs.readFileSync(path.join(PROG_DIR, 'adventure.dspf'), 'utf8');
const rpgSource = fs.readFileSync(path.join(PROG_DIR, 'adventure.rpgle'), 'utf8');

test('buildVars binds DDS fields and D-spec standalone fields, DDS field wins on name collision', () => {
  const dds = parseDds(ddsSource);
  const rpg = parseRpgle(rpgSource);
  const vars = buildVars(rpg, dds);

  // DDS-bound field (numeric, from MAIN's PLRHP 3 0).
  assert.equal(vars.plrhp.type, 'N');
  assert.equal(vars.plrhp.length, 3);
  assert.equal(vars.plrhp.value, 0);

  // DDS-bound field (character, from MAIN's LOCTXT 30A).
  assert.equal(vars.loctxt.type, 'A');
  assert.equal(vars.loctxt.length, 30);

  // D-spec standalone field with INZ.
  assert.equal(vars.seed.type, 'N');
  assert.equal(vars.seed.value, 17);
  assert.equal(vars.loc.value, 1);
  assert.equal(vars.killcnt.value, 0);
});

test('compile backpatches IF/ELSE/ENDIF jump targets correctly', () => {
  const statements = [
    { op: 'IF', expr: '1 = 1' },
    { op: 'EVAL', expr: 'a = 1' },
    { op: 'ELSE' },
    { op: 'EVAL', expr: 'a = 2' },
    { op: 'ENDIF' },
  ];
  const { instrs } = compile(statements);
  assert.equal(instrs[0].op, 'IF');
  // False branch of IF jumps past the ELSE's own JMP, to the EVAL a=2.
  assert.equal(instrs[0].falseTarget, 3);
  assert.equal(instrs[2].op, 'JMP');
  // The ELSE's JMP lands on ENDIF's own NOP marker instruction.
  assert.equal(instrs[2].target, 4);
  assert.equal(instrs[4].op, 'NOP');
});

test('compile backpatches DOW/ENDDO/LEAVE jump targets correctly', () => {
  const statements = [
    { op: 'DOW', expr: '1 = 1' },
    { op: 'LEAVE' },
    { op: 'ENDDO' },
  ];
  const { instrs } = compile(statements);
  assert.equal(instrs[0].op, 'DOW');
  assert.equal(instrs[0].falseTarget, 3);
  assert.equal(instrs[1].op, 'LEAVE');
  assert.equal(instrs[1].target, 3);
  assert.equal(instrs[2].op, 'ENDDO');
  assert.equal(instrs[2].target, 0);
});

test('compile records BEGSR entry points in subroutines', () => {
  const statements = [
    { op: 'BEGSR', factor1: 'MYSUB' },
    { op: 'EVAL', expr: 'a = 1' },
    { op: 'ENDSR' },
  ];
  const { instrs, subroutines } = compile(statements);
  assert.equal(subroutines.MYSUB, 0);
  assert.equal(instrs[0].op, 'EVAL');
});

test('ADVENTURE: first screen shows the starting location, full HP, and no message', () => {
  const dds = parseDds(ddsSource);
  const rpg = parseRpgle(rpgSource);
  const gen = runProgram(rpg, dds);
  const first = gen.next().value;
  assert.equal(first.formatName, 'MAIN');
  const byRow = Object.fromEntries(first.fields.map(f => [f.row, f.text]));
  assert.equal(byRow[2].trim(), 'a dark forest');
  assert.equal(byRow[4], '020');
  assert.equal(byRow[6], undefined, 'MSGTXT should be hidden until *IN90 is set');
});

test('ADVENTURE: F3 at the main menu ends the program immediately', () => {
  const dds = parseDds(ddsSource);
  const rpg = parseRpgle(rpgSource);
  const gen = runProgram(rpg, dds);
  gen.next();
  const r = gen.next({ key: 'F3', values: { cmd: '' } });
  assert.equal(r.done, true);
});

test('ADVENTURE: always-fight playthrough stays in bounds and eventually ends (win or lose)', () => {
  const dds = parseDds(ddsSource);
  const rpg = parseRpgle(rpgSource);
  const gen = runProgram(rpg, dds);
  let y = gen.next().value;
  let steps = 0;
  let sawCombat = false;
  let sawEndgame = false;
  while (steps++ < 500) {
    assert.ok(['MAIN', 'COMBAT', 'ENDGAME'].includes(y.formatName));
    if (y.formatName === 'COMBAT') sawCombat = true;
    if (y.formatName === 'ENDGAME') sawEndgame = true;
    const cmd = y.formatName === 'MAIN' ? 'M' : 'F';
    const r = gen.next({ key: 'ENTER', values: { cmd } });
    if (r.done) break;
    y = r.value;
    // Every numeric field must stay within its declared display width
    // (a DIV/MVR bounding bug would show up here as a longer string).
    for (const f of y.fields) {
      if (/^[0-9]+$/.test(f.text.trim())) assert.ok(f.text.length <= 6);
    }
  }
  assert.ok(sawCombat, 'expected at least one encounter over 500 steps');
  assert.ok(sawEndgame, 'expected the playthrough to reach an ending over 500 steps');
  assert.ok(steps < 500, 'playthrough should terminate well within the step budget');
});
