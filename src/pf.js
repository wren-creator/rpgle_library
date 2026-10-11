/**
 * src/pf.js
 * ─────────────────────────────────────────────────────────────────
 * Minimal DDS parser for DISK (physical) files: the record format, its
 * fields, and the key. This is what makes a DISK file "externally
 * described": the RPG program declares only the file name (F-spec,
 * column 22 = E) and every field here becomes a program variable, the
 * same way a display file's fields do. Mirrors python/src/rpgle/pf.py.
 *
 * Column layout is the DDS-for-physical-files one:
 *   6      Form type ('A')
 *   7      Comment marker ('*')
 *   17     'R' starts the record format, 'K' is a key field line
 *   19-28  Name
 *   30-34  Length
 *   35     Data type (blank = character, or zoned if decimals given;
 *          P packed, S zoned, B binary, F float are all numeric here)
 *   36-37  Decimal positions
 *   45-80  Keywords (UNIQUE before the R line makes the key unique;
 *          DESCEND on a key line is parsed but rejected)
 *
 * Not supported: DESCEND keys, join and select/omit logical files, REF,
 * more than one record format per file.
 */

'use strict';

const { col, digits } = require('./util');

function parsePf(fileName, source) {
  const pf = { name: fileName.toUpperCase(), format: '', fields: [], keys: [], unique: false };

  for (const raw of source.split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (line.length < 6 || line[5] !== 'A') continue;
    if (line.length > 6 && line[6] === '*') continue;

    const kind = col(line, 17, 17);
    const name = col(line, 19, 28).toUpperCase();
    const keywords = (line.length >= 45 ? line.slice(44) : '').toUpperCase();

    if (kind === 'R') {
      if (pf.format) throw new Error(`${pf.name}: only one record format per DISK file is supported`);
      pf.format = name;
      continue;
    }
    if (kind === 'K') {
      if (keywords.includes('DESCEND')) throw new Error(`${pf.name}: DESCEND keys are not supported`);
      pf.keys.push(name);
      continue;
    }
    if (!pf.format) {
      // File-level keywords before the R line.
      if (keywords.split(/\s+/).includes('UNIQUE')) pf.unique = true;
      continue;
    }
    if (!name) continue;

    const lengthS = col(line, 30, 34);
    const dtype = col(line, 35, 35).toUpperCase();
    const decS = col(line, 36, 37);
    const numeric = ['P', 'S', 'B', 'F'].includes(dtype) || decS !== '';
    pf.fields.push({
      name,
      length: digits(lengthS, 'length', name),
      dtype: numeric ? 'N' : 'A',
      dec: digits(decS, 'decimal positions', name),
    });
  }

  if (!pf.format) throw new Error(`${pf.name}: no record format (R line) found in DDS`);
  for (const k of pf.keys) {
    if (!pf.fields.some((f) => f.name === k)) {
      throw new Error(`${pf.name}: key field ${k} is not in record format ${pf.format}`);
    }
  }
  return pf;
}

module.exports = { parsePf };
