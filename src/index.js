/**
 * rpgle_library
 * ─────────────────────────────────────────────────────────────────
 * A zero-dependency RPG IV (RPGLE) and DDS parser/interpreter.
 *
 * Quick start:
 *   const { parseDds, parseRpgle, buildVars, compile, runProgram } = require('rpgle_library');
 *   const dds = parseDds(dspfSource);
 *   const rpg = parseRpgle(rpgleSource);
 *   const gen = runProgram(rpg, dds);
 *   let step = gen.next();
 *   while (!step.done) {
 *     console.log(step.value.fields);
 *     step = gen.next({ key: 'ENTER', values: { ... } });
 *   }
 * ─────────────────────────────────────────────────────────────────
 */

'use strict';

const { parseDds } = require('./dds');
const { parseRpgle } = require('./rpgle');
const { evalExpr, splitAssignment } = require('./expr');
const { runProgram, buildVars, compile, renderFormat } = require('./interpreter');

module.exports = {
  parseDds,
  parseRpgle,
  evalExpr,
  splitAssignment,
  runProgram,
  buildVars,
  compile,
  renderFormat,
};
