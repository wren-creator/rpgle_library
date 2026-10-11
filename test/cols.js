'use strict';

// Builds fixed-form RPG and DDS lines by column, so a test doesn't hide a
// misplaced space inside a hand-counted string. (Same helpers as
// python/tests/_cols.py.)

function put(buf, start, text) {
  for (let i = 0; i < text.length; i++) {
    const idx = start - 1 + i;
    while (buf.length <= idx) buf.push(' ');
    buf[idx] = text[i];
  }
}

const blank = (specLetter) => [' ', ' ', ' ', ' ', ' ', specLetter];
const done = (buf) => buf.join('').replace(/\s+$/, '');

function fspec(name, { type = 'I', desig = 'F', addition = '', fmt = 'E', keyed = false, device = 'DISK' } = {}) {
  const b = blank('F');
  put(b, 7, name); put(b, 17, type); put(b, 18, desig); put(b, 20, addition); put(b, 22, fmt);
  if (keyed) put(b, 34, 'K');
  put(b, 36, device);
  return done(b);
}

function cspec(opcode, { factor1 = '', factor2 = '', result = '', hi = '', lo = '', eq = '', cond = '' } = {}) {
  const b = blank('C');
  put(b, 9, cond); put(b, 12, factor1); put(b, 26, opcode); put(b, 36, factor2); put(b, 50, result);
  put(b, 71, hi); put(b, 73, lo); put(b, 75, eq);
  return done(b);
}

function dspec(name, length, dec = null, init = null) {
  const b = blank('D');
  put(b, 7, name); put(b, 24, 'S'); put(b, 33, String(length).padStart(7));
  if (dec !== null) put(b, 41, String(dec).padStart(2));
  if (init !== null) put(b, 45, `INZ(${init})`);
  return done(b);
}

function ddsLine(kind = '', name = '', length = '', dtype = '', dec = '', keywords = '') {
  const b = blank('A');
  put(b, 17, kind); put(b, 19, name);
  if (length !== '') put(b, 30, String(length).padStart(5));
  put(b, 35, dtype);
  if (dec !== '') put(b, 36, String(dec).padStart(2));
  put(b, 45, keywords);
  return done(b);
}

const dds = (...lines) => lines.join('\n') + '\n';

module.exports = { fspec, cspec, dspec, ddsLine, dds };
