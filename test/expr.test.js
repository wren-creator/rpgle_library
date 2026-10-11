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

// Character values compare as if blank-padded, as in RPG: a 20-character
// field holding BOB is equal to 'BOB'. (Parity with the Python port.)
test('character comparison ignores trailing blanks', () => {
  const ctx = {
    vars: {
      name: { type: 'A', length: 20, dec: 0, value: 'BOB'.padEnd(20, ' ') },
      other: { type: 'A', length: 5, dec: 0, value: 'BOB  ' },
    },
    indicators: {},
  };
  assert.equal(evalExpr("name = 'BOB'", ctx), true);
  assert.equal(evalExpr("'BOB' = name", ctx), true);
  assert.equal(evalExpr("name <> 'BOB'", ctx), false);
  assert.equal(evalExpr('name = other', ctx), true);
  assert.equal(evalExpr("name = 'BOBB'", ctx), false);
  assert.equal(evalExpr("name < 'BOBB'", ctx), true); // 'BOB' < 'BOBB', padding ignored
  assert.equal(evalExpr("name > 'BOBB'", ctx), false);
  assert.equal(evalExpr("name <= 'BOB'", ctx), true);
  assert.equal(evalExpr("name >= 'BOB'", ctx), true);
  assert.equal(evalExpr("'a' = 'A'", ctx), false); // still case-sensitive
});

test('comparing text with a number is still never equal', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.equal(evalExpr("'1' = 1", ctx), false);
  assert.equal(evalExpr('1 = 1', ctx), true);
});

// + joins two texts; text mixed with a number, - on text, and negating
// text are errors rather than a silent NaN or "a1". (Parity with Python.)
test('+ joins two character values, padding included', () => {
  const ctx = { vars: { name: { type: 'A', length: 6, dec: 0, value: 'BOB   ' } }, indicators: {} };
  assert.equal(evalExpr("'ab' + 'cd'", ctx), 'abcd');
  assert.equal(evalExpr("name + 'x'", ctx), 'BOB   x'); // padding is part of the value
  assert.equal(evalExpr('1 + 2', ctx), 3);
  assert.equal(evalExpr('7 - 2', ctx), 5);
});

test('text and numbers do not mix in + and -', () => {
  const ctx = { vars: {}, indicators: {} };
  for (const src of ["'a' + 1", "1 + 'a'", "'a' - 1", "1 - 'a'", "'a' - 'b'"]) {
    assert.throws(() => evalExpr(src, ctx), /text/i, src);
  }
});

test('negating text is an error, negating a number still works', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.throws(() => evalExpr("-'a'", ctx), /negate text/i);
  assert.equal(evalExpr('-(2 + 3)', ctx), -5);
  assert.equal(evalExpr('- -4', ctx), 4);
});

// Dividing by zero is an error, not Infinity. (Parity with the Python port.)
test('division by zero is an error, ordinary division still works', () => {
  const ctx = { vars: {}, indicators: {} };
  assert.throws(() => evalExpr('1 / 0', ctx), /by zero/i);
  assert.throws(() => evalExpr('5 / (2 - 2)', ctx), /by zero/i);
  assert.equal(evalExpr('7 / 2', ctx), 3.5);
  assert.equal(evalExpr('0 / 5', ctx), 0);
});

// ── built-in functions (parity with the Python port) ────────────────────
// Every case here is also in python/tests/test_expr.py and tools/parity.

const ev = (src, vars = {}) => evalExpr(src, { vars, indicators: {} });
const eachCase = (cases, vars) => {
  for (const [src, want] of cases) assert.deepEqual(ev(src, vars), want, src);
};
const fails = (sources, pattern) => {
  for (const src of sources) assert.throws(() => ev(src), pattern, src);
};

test('numeric built-ins', () => {
  eachCase([
    ['%abs(-5)', 5], ['%abs(3)', 3], ['%int(7.9)', 7], ['%int(-7.9)', -7],
    ['%inth(7.5)', 8], ['%inth(-7.5)', -8], ["%dec('12.345' : 5 : 2)", 12.34], ["%dech('12.345' : 5 : 2)", 12.35],
    ['%rem(17 : 5)', 2], ['%div(17 : 5)', 3], ['%sqrt(16)', 4], ['%min(4 : 2 : 9)', 2], ['%max(4 : 2 : 9)', 9],
    ["%min('b' : 'a')", 'a'], ['%rem(-17 : 5)', -2], ['%div(-17 : 5)', -3],
  ]);
});

test('text built-ins', () => {
  eachCase([
    ["%upper('abC')", 'ABC'], ["%lower('abC')", 'abc'], ["%triml('  a  ')", 'a  '], ["%trimr('  a  ')", '  a'],
    ["%trim('  a  ')", 'a'], ["%scan('lo' : 'hello world')", 4], ["%scan('o' : 'hello world' : 6)", 8],
    ["%scan('zz' : 'hello')", 0], ["%replace('XY' : 'hello' : 2 : 3)", 'hXYo'], ["%replace('XY' : 'hello' : 3 : 0)", 'heXYllo'],
    ["%replace('XY' : 'hello')", 'XYllo'], ["%subst('hello' : 2 : 3)", 'ell'], ["%len('abc')", 3],
  ]);
});

test('%CHAR uses a bare variable\'s declared decimals, an expression does not', () => {
  const vars = {
    money: { type: 'N', length: 9, dec: 2, value: 5 },
    whole: { type: 'N', length: 5, dec: 0, value: 5 },
  };
  eachCase([['%char(money)', '5.00'], ['%char(whole)', '5'], ['%char(money + 1)', '6'], ["%char('x')", 'x'], ['%char(*on)', '1'], ['%char(*off)', '0']], vars);
});

test('built-ins agree on characters, not bytes or UTF-16 units', () => {
  eachCase([
    ["%len('café')", 4], ["%len('\u{1f600}x')", 2], ["%subst('cafés' : 4 : 2)", 'és'],
    ["%scan('é' : 'café')", 4], ["%replace('\u{1f600}' : 'abc' : 2 : 1)", 'a\u{1f600}c'],
  ]);
});

test('%SUBST clamps instead of failing', () => {
  eachCase([
    ["%subst('hello' : 0 : 2)", 'he'], ["%subst('hello' : -3 : 2)", 'he'], ["%subst('hello' : 9)", ''],
    ["%subst('hello' : 3 : 99)", 'llo'], ["%subst('hello' : 3 : -1)", ''], ["%subst('hello' : 2)", 'ello'],
  ]);
});

test('numbers become text in plain decimal, never exponent form', () => {
  eachCase([
    ['%char(0.0000001)', '0.0000001'], ['%char(0.00001234)', '0.00001234'],
    ['%char(10000000000000000000000)', '10000000000000000000000'], ['%char(123456789012345678)', '123456789012345680'],
    ['%char(0.1 + 0.2)', '0.30000000000000004'], ['%char(-2.5)', '-2.5'], ['%char(7)', '7'],
  ]);
});

test('a position or length that is not a whole number is an error', () => {
  fails([
    "%dec('5' : 5 : 'x')", "%scan('a' : 'abc' : 'x')", "%replace('a' : 'bcd' : 'x')", "%replace('a' : 'bcd' : 1 : 'x')",
    "%subst('abc' : 'x')", "%subst('abc' : 1 : 'x')",
  ], /not a whole number/);
});

test('decimal positions are limited to what RPG allows', () => {
  fails(['%dec(1 : 5 : 64)', '%dec(1 : 5 : -1)', '%dech(1 : 5 : 400)'], /0 to 63/);
  eachCase([["%dec('12.345' : 5 : 0)", 12], ['%dec(1 : 63 : 63)', 1]]);
});

test('wrong argument counts and unknown functions are errors', () => {
  fails(["%len('a' : 'b')", "%subst('a')", "%scan('a')", "%replace('a')", '%min(1)'], /takes .* argument/);
  fails(['%trim()', '%abs()']); // no argument at all is a parse error before the count is checked
  fails(['%nosuch(1)'], /Unsupported RPG built-in/);
});

test('built-in errors: not a number, divide by zero, negative square root', () => {
  fails(["%dec('abc' : 5 : 2)", "%int('12x')", "%abs('')"], /is not a number/);
  fails(['%rem(1 : 0)', '%div(1 : 0)'], /division by zero/);
  fails(['%sqrt(-1)'], /negative/);
});

test('NOT binds looser than comparison and tighter than AND', () => {
  eachCase([
    ['not 1 = 2', true], ['not 1 = 1 or 2 = 2', true], ['not (1 = 1 and 2 = 2)', false], ['not not 1 = 1', true],
    ['not 1 = 1 and 2 = 2', false],
  ]);
});

test('*BLANKS and *ZEROS are figurative constants', () => {
  eachCase([['*blanks', ''], ['*blank', ''], ['*zeros', 0], ['*zero', 0], ['*blanks = *blanks', true]]);
});

test('only plain numbers are numbers in arithmetic and ordering too', () => {
  for (const src of ["'0x10' * 1", "'1_000' * 1", "'inf' * 1", "'abc' * 2"]) {
    assert.ok(Number.isNaN(ev(src)), src); // not a number: NaN, never 16 or 1000
  }
  eachCase([["'5' * 2", 10], ["' 7 ' * 1", 7], ["'1e2' * 1", 100], ["'0x10' < 20", false], ["'9' < 10", true]]);
});
