'use strict';

/**
 * customer-inquiry.js — rpgle_library example with a real DISK file.
 *
 * Drives programs/custinq.rpgle: type a customer number, the program CHAINs
 * to the CUSTMAST file and shows the name and balance, or "Customer not
 * found". The file lives in an in-memory SQLite database loaded from
 * programs/custmast.csv, so there is nothing to set up.
 *
 * DISK files are the one part of the library with a dependency: install
 * better-sqlite3 first (npm install, in this repo it is already a dev
 * dependency). It needs Node 20 or newer.
 *
 *   node examples/customer-inquiry.js      (try 20, then 99, then f3)
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { parseDds, parseRpgle, parsePf, Database, runProgram } = require('../src/index');

const PROG_DIR = path.join(__dirname, '..', 'programs');
const read = (name) => fs.readFileSync(path.join(PROG_DIR, name), 'utf8');

const pf = parsePf('CUSTMAST', read('custmast.pf'));
const db = new Database();
const file = db.openFile(pf);

// custmast.csv: a header row of field names, then one customer per line.
const [header, ...rows] = read('custmast.csv').trim().split('\n');
const names = header.split(',').map((n) => n.trim().toUpperCase());
for (const line of rows) {
  const cells = line.split(',');
  file.write(Object.fromEntries(names.map((n, i) => [n, cells[i]])));
}

const dds = parseDds(read('custinq.dspf'));
const rpg = parseRpgle(read('custinq.rpgle'));
const gen = runProgram(rpg, dds, { db, pfs: { CUSTMAST: pf } });

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('close', () => db.close());

function renderPlainText(screen) {
  console.log('\n' + '─'.repeat(40));
  for (const f of screen.fields) console.log(f.text);
  const keyList = Object.entries(screen.keys).map(([k, v]) => `${k}->F${v}`).join(', ');
  if (keyList) console.log(`Keys: ${keyList}`);
}

function step(resume) {
  const r = resume ? gen.next(resume) : gen.next();
  if (r.done) {
    console.log('\n(program ended)');
    rl.close();
    return;
  }
  const screen = r.value;
  renderPlainText(screen);
  const inputName = Object.values(screen.inputMap)[0]; // one input field on this format
  rl.question('> ', (line) => {
    const key = /^f3$/i.test(line.trim()) ? 'F3' : 'ENTER';
    step({ key, values: inputName && key === 'ENTER' ? { [inputName]: line.trim() } : {} });
  });
}

step();
