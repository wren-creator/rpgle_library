'use strict';

/**
 * quick-math.js — minimal rpgle_library example.
 *
 * A tiny RPG program that computes 17 DIV 5 using the classic RPG
 * DIV/MVR idiom for modulo (RPG has no %REM until well past V4R3),
 * then displays the result through a single EXFMT. No file I/O, no
 * subroutines, no control-flow beyond one instruction pair -- just
 * enough to show parseDds/parseRpgle/runProgram working end-to-end.
 */

const { parseDds, parseRpgle, runProgram } = require('../src/index');

const ddsSource = [
  "     A                                      DSPSIZ(24 80 *DS3)",
  "",
  "     A          R RESULT",
  "     A                                  1  2'17 DIV 5, via RPG DIV/MVR:'",
  "     A                                  3  2'Quotient:'",
  "     A            QUOT           5  0O  3 13",
  "     A                                  4  2'Remainder:'",
  "     A            REM            5  0O  4 13",
  "     A                                 22  2'Press Enter to continue'",
].join('\n');

const rpgSource = [
  "     FRESULT    CF   E             WORKSTN",
  "",
  "     Da                S              5  0 INZ(17)",
  "     Db                S              5  0 INZ(5)",
  "",
  "     C     a             DIV       b             quot",
  "     C                   MVR                     rem",
  "     C                   EXFMT     RESULT",
  "     C                   EVAL      *inlr = *on",
  "     C                   RETURN",
].join('\n');

const dds = parseDds(ddsSource);
const rpg = parseRpgle(rpgSource);
const gen = runProgram(rpg, dds);

const step = gen.next(); // runs DIV/MVR, then yields at the EXFMT
console.log(`\n${step.value.formatName}`);
for (const f of step.value.fields) console.log(f.text);

const done = gen.next({ key: 'ENTER', values: {} });
console.log(done.done ? '\n(program ended)' : '\n(unexpected: program did not end)');
