'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { evalExpr, splitAssignment } = require('../src/expr');

test('AND/OR always consume both operands even when the left side short-circuits', () => {
  // Regression test: parseAnd/parseOr used to skip parsing the right
  // side when JS's && / || short-circuited on a false/true left side,
  // leaving trailing tokens and throwing.
  const ctx = { vars: {}, indicators: {} };
  assert.equal(evalExpr('1 > 2 and 3 > 4', ctx), false);
  assert.equal(evalExpr('1 < 2 and 3 < 4', ctx), true);
  assert.equal(evalExpr('1 > 2 or 3 < 4', ctx), true);
  assert.equal(evalExpr('1 > 2 or 3 > 4', ctx), false);
});

test('comparison operators', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.equal(evalExpr('1 = 1', ctx), true);
  assert.equal(evalExpr('1 <> 2', ctx), true);
  assert.equal(evalExpr('1 <= 1', ctx), true);
  assert.equal(evalExpr('2 >= 3', ctx), false);
  assert.equal(evalExpr('1 < 2', ctx), true);
  assert.equal(evalExpr('2 > 1', ctx), true);
  assert.equal(evalExpr("'AB' = 'AB'", ctx), true);
  assert.equal(evalExpr("'AB' <> 'CD'", ctx), true);
});

test('arithmetic operator precedence', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.equal(evalExpr('2 + 3 * 4', ctx), 14);
  assert.equal(evalExpr('(2 + 3) * 4', ctx), 20);
  assert.equal(evalExpr('10 - 2 - 3', ctx), 5);
  assert.equal(evalExpr('-5 + 2', ctx), -3);
});

test('built-in functions: %TRIM, %LEN, %SUBST', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.equal(evalExpr("%TRIM('  hi  ')", ctx), 'hi');
  assert.equal(evalExpr("%LEN('hello')", ctx), 5);
  assert.equal(evalExpr("%SUBST('hello' : 2)", ctx), 'ello');
  assert.equal(evalExpr("%SUBST('hello' : 2 : 3)", ctx), 'ell');
});

test('figurative constants: *ON, *OFF, *INnn, *INLR, *INRT', () => {
  const ctx = { vars: {}, indicators: { '90': true, '80': false, LR: true, RT: false } };
  assert.equal(evalExpr('*ON', ctx), true);
  assert.equal(evalExpr('*OFF', ctx), false);
  assert.equal(evalExpr('*IN90', ctx), true);
  assert.equal(evalExpr('*IN80', ctx), false);
  assert.equal(evalExpr('*INLR', ctx), true);
  assert.equal(evalExpr('*INRT', ctx), false);
});

test('variable lookup reads the current value out of ctx.vars', () => {
  const ctx = { vars: { plrhp: { type: 'N', length: 3, dec: 0, value: 20 } }, indicators: {} };
  assert.equal(evalExpr('plrhp', ctx), 20);
  assert.equal(evalExpr('plrhp > 10', ctx), true);
});

test('splitAssignment splits on the first top-level "="', () => {
  assert.deepEqual(splitAssignment('plrhp = 20'), { lvalue: 'plrhp', rvalueSrc: '20' });
  assert.deepEqual(splitAssignment('loctxt = %TRIM(loctxt)'), { lvalue: 'loctxt', rvalueSrc: '%TRIM(loctxt)' });
});
