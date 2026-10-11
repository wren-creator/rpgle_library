/**
 * src/util.js
 * ─────────────────────────────────────────────────────────────────
 * Small helpers shared by the parsers. They mirror the Python port's
 * _helpers.py.
 */

'use strict';

// A 1-indexed, inclusive column range, bounds-safe, trimmed.
function col(line, a, b) {
  return (line.slice(a - 1, b) || '').trim();
}

// A numeric column of a spec or DDS line: blank is 0, anything else must be
// whole digits. parseInt('5A') would quietly give 5, so check first.
function digits(text, what, owner) {
  if (!text) return 0;
  if (!/^\d+$/.test(text)) throw new Error(`${owner}: ${what} '${text}' is not a number`);
  return parseInt(text, 10);
}

// An IBM i object name: letters, digits and _ $ # @, not starting with a
// digit, up to 100 characters. File and program names become names on disk,
// so they must never contain a path separator or a dot.
const OBJECT_NAME = /^[A-Za-z_$#@][A-Za-z0-9_$#@]*$/;
function isObjectName(name) {
  return name.length <= 100 && OBJECT_NAME.test(name);
}

module.exports = { col, digits, isObjectName };
