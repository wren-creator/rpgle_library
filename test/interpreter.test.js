'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const { parseDds } = require('../src/dds');
const { parseRpgle } = require('../src/rpgle');
const { runProgram, buildVars, compile, renderFormat } = require('../src/interpreter');

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

// A numeric field with decimals is shown with its decimal point (one column
// wider than a real 5250 draws it), so what's on screen survives being typed
// back in: "000004875" would parse as 4875, not 48.75. (Parity with Python.)
test('a numeric field with decimals shows its decimal point', () => {
  const dds = parseDds('     A          R MAIN\n     A            AMT            9  2O  1  1\n');
  const show = (value, dec, length) =>
    renderFormat(dds.formats.MAIN, { amt: { type: 'N', length, dec, value } }, {}).fields[0].text;

  assert.equal(show(48.75, 2, 9), '0000048.75');
  assert.equal(show(-48.75, 2, 9), '-000048.75');
  assert.equal(show(0, 2, 9), '0000000.00');
  assert.equal(show(1250, 2, 9), '0001250.00');
  assert.equal(parseFloat(show(48.75, 2, 9)), 48.75); // round-trips
  assert.equal(parseFloat(show(-48.75, 2, 9)), -48.75);
  // whole-number fields are unchanged
  assert.equal(show(42, 0, 5), '00042');
  assert.equal(show(-5, 0, 5), '-0005');
});

// RPG is case-insensitive, so EXFMT main finds the format DDS calls MAIN, and
// the screen reports the DDS's own spelling. (Parity with the Python port.)
test('EXFMT format names are case-insensitive', () => {
  const dds = parseDds("     A          R RESULT\n     A                                  1  2'HELLO'\n");
  for (const written of ['RESULT', 'result', 'Result']) {
    const rpg = parseRpgle(`     FRESULT    CF   E             WORKSTN\n     C                   EXFMT     ${written}\n`);
    const screen = runProgram(rpg, dds).next().value;
    assert.equal(screen.formatName, 'RESULT', `EXFMT ${written}`);
    assert.equal(screen.fields[0].text, 'HELLO');
  }
});

test('EXFMT of a format that does not exist is still an error', () => {
  const dds = parseDds("     A          R RESULT\n     A                                  1  2'HELLO'\n");
  const rpg = parseRpgle('     FRESULT    CF   E             WORKSTN\n     C                   EXFMT     nosuch\n');
  assert.throws(() => runProgram(rpg, dds).next(), /Unknown DDS record format: nosuch/);
});

// ── numbers: what happens when the data isn't one ──────────────────────
// Letters typed into a number field used to show "00NaN" (JS) or quietly
// become 0 (Go). It is an error that names the field, a blank field reads as
// zero, and a front end can ask which inputs are numeric. (Parity with Python.)

const NUM_DDS = "     A          R INQ                       CF03(03)\n" +
  "     A            CUSTNO         5  0B  3 20\n" +
  "     A            NAME          10A  B  4 20\n";
const NUM_PROG = '     FINQ       CF   E             WORKSTN\n' +
  '     C                   EXFMT     INQ\n     C                   EXFMT     INQ\n';

function typeInto(fieldValues) {
  const gen = runProgram(parseRpgle(NUM_PROG), parseDds(NUM_DDS));
  gen.next();
  return gen.next({ key: 'ENTER', values: fieldValues }).value;
}

test('screen data says which inputs are numeric', () => {
  const screen = runProgram(parseRpgle(NUM_PROG), parseDds(NUM_DDS)).next().value;
  assert.deepEqual([...screen.numericFields], ['custno']);
});

test('letters in a numeric field are an error that names the field', () => {
  for (const typed of ['abc', '12x', '1.2.3']) {
    assert.throws(() => typeInto({ custno: typed }), new RegExp(`'${typed}' is not a number.*custno`), typed);
  }
});

test('a blank numeric field reads as zero, and padded numbers still work', () => {
  const text = (v) => typeInto({ custno: v }).fields.find((f) => f.input && f.row === 2).text;
  assert.equal(text('   '), '00000');
  assert.equal(text(''), '00000');
  assert.equal(text(' 42 '), '00042');
  assert.equal(text('-7'), '-0007');
});

test('text typed into a character field is untouched', () => {
  const screen = typeInto({ name: 'abc' });
  assert.ok(screen.fields.some((f) => f.text.startsWith('abc')));
});

test('assigning text to a number is an error, a numeric string is fine', () => {
  const run = (expr) => runProgram(parseRpgle(
    '     Dx                S              5  0\n' +
    '     C                   EVAL      ' + expr + '\n'), parseDds('')).next();
  assert.throws(() => run("x = 'abc'"), /'abc' is not a number.*field x/);
  assert.throws(() => run("x = ''"), /not a number/);
  assert.doesNotThrow(() => run("x = '5'"));
});

test('the DIV opcode refuses to divide by zero', () => {
  const src = '     Da                S              5  0 INZ(7)\n' +
    '     Db                S              5  0 INZ(0)\n' +
    '     Dq                S              5  0\n' +
    '     C     a             DIV       b             q\n';
  assert.throws(() => runProgram(parseRpgle(src), parseDds('')).next(), /division by zero/i);
});

test('resuming with nothing at all does not crash', () => {
  const gen = runProgram(parseRpgle(NUM_PROG), parseDds(NUM_DDS));
  gen.next();
  assert.doesNotThrow(() => gen.next());
});
