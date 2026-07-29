'use strict';

/**
 * adventure-cli.js — realistic-workflow rpgle_library example.
 *
 * Drives programs/adventure.rpgle (a small text-adventure) as a plain
 * stdin/stdout loop: read the { fields, cursor, inputMap } screen the
 * interpreter yields at each EXFMT, render it as plain text, read a
 * line of input, and resume. There's no 5250/3270 protocol code here
 * at all -- the library's job ends at producing/consuming plain data;
 * how you present that data (a real terminal, plain text, a GUI) is
 * entirely up to the caller.
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { parseDds, parseRpgle, runProgram } = require('../src/index');

const PROG_DIR = path.join(__dirname, '..', 'programs');
const dds = parseDds(fs.readFileSync(path.join(PROG_DIR, 'adventure.dspf'), 'utf8'));
const rpg = parseRpgle(fs.readFileSync(path.join(PROG_DIR, 'adventure.rpgle'), 'utf8'));
const gen = runProgram(rpg, dds);

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

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
  const inputName = Object.values(screen.inputMap)[0]; // one input field per format here
  rl.question('> ', (line) => {
    const key = /^f3$/i.test(line.trim()) ? 'F3' : 'ENTER';
    step({ key, values: inputName ? { [inputName]: line.trim() } : {} });
  });
}

step();
