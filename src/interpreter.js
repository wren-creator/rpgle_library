/**
 * src/interpreter.js
 * ─────────────────────────────────────────────────────────────────
 * Runs a parsed RPGLE program (see rpgle.js) against its WORKSTN
 * display file (see dds.js). Real RPG's EXFMT ("write a format, then
 * read it") maps onto a JS generator: it yields the format to render
 * as a plain object -- { formatName, fields, cursor, keys, inputMap }
 * -- and resumes via .next({ key, values }) once the caller has
 * collected the next AID key and input field values, however the
 * caller happens to be presenting the screen (5250 terminal, plain
 * text, a test harness, anything). The library has no opinion about
 * presentation; it only produces and consumes plain data.
 *
 * Supported opcode subset (see docs/LANGUAGE.md for the full RPGLE
 * subset this library implements, and what's explicitly out of
 * scope): EVAL, IF/ELSE/ENDIF, DOW/ENDDO, LEAVE, SELECT/WHEN/OTHER/
 * ENDSL, EXSR/BEGSR/ENDSR, EXFMT, DIV/MVR, RETURN. Expression
 * evaluation (arithmetic, comparisons, %TRIM/%LEN/%SUBST) lives in
 * expr.js.
 *
 * DIV/MVR (not EVAL-expression division) is the classic RPG idiom for
 * modulo: RPG has no %REM until well past V4R3, and relying on
 * intermediate-expression truncation inside a compound EVAL
 * expression is not unambiguous real RPG behavior. DIV followed
 * immediately by MVR is the classic, unambiguous way to get a
 * remainder, real since RPG's earliest versions.
 */

'use strict';

const { evalExpr, splitAssignment, toNumber, pow10, asString } = require('./expr');
const { FileRuntime, FILE_OPS } = require('./fileops');

function buildVars(rpgAst, ddsFormats, pfs) {
  const vars = {};

  // DDS-bound fields: every named field across every record format of
  // the externally-described WORKSTN file becomes a program variable,
  // exactly like real ILE RPG (no separate D-spec needed or allowed).
  // The same field name in more than one format binds to the same
  // variable -- first definition in source order wins for type info.
  for (const fmtName of Object.keys(ddsFormats.formats)) {
    for (const f of ddsFormats.formats[fmtName].fields) {
      if (f.kind !== 'field') continue;
      const key = f.name.toLowerCase();
      if (vars[key]) continue;
      if (f.dtype === 'A') {
        vars[key] = { type: 'A', length: f.length, dec: 0, value: ''.padEnd(f.length, ' ') };
      } else {
        vars[key] = { type: 'N', length: f.length, dec: f.dec || 0, value: 0 };
      }
    }
  }

  // DISK file fields: each externally described file's record format, after
  // the display fields (first definition wins). pfs is keyed by upper-case
  // file name.
  for (const fs of rpgAst.files || []) {
    const pf = (pfs || {})[fs.name.toUpperCase()];
    if (!pf || (fs.device || '').toUpperCase() !== 'DISK') continue;
    for (const f of pf.fields) {
      const key = f.name.toLowerCase();
      if (vars[key]) continue;
      vars[key] = f.dtype === 'A'
        ? { type: 'A', length: f.length, dec: 0, value: ''.padEnd(f.length, ' ') }
        : { type: 'N', length: f.length, dec: f.dec, value: 0 };
    }
  }

  // D-spec standalone fields (v1 supports only definition type 'S').
  for (const d of rpgAst.dspecs) {
    if (d.deftype !== 'S') continue;
    const key = d.name.toLowerCase();
    if (d.dec === null) {
      const init = d.init != null ? d.init.replace(/^'|'$/g, '') : '';
      vars[key] = { type: 'A', length: d.length, dec: 0, value: init.padEnd(d.length, ' ').slice(0, d.length) };
    } else {
      vars[key] = { type: 'N', length: d.length, dec: d.dec, value: d.init != null ? parseFloat(d.init) : 0 };
    }
  }

  return vars;
}

// Compiles the flat statement list into a jump-resolved instruction
// array (classic backpatch compilation: IF/DOW/SELECT frames on a
// stack, each closing keyword patches the jump targets it owns).
function compile(statements) {
  const instrs = [];
  const subroutines = {};
  const klists = {};
  let currentKlist = null;
  const ifStack = [];
  const dowStack = [];
  const selStack = [];

  for (const stmt of statements) {
    const start = instrs.length;
    const cond = parseCond(stmt.indic);
    if (cond && NO_COND_OPS.has(stmt.op)) {
      throw new Error(`Conditioning indicators on ${stmt.op} are not supported`);
    }
    switch (stmt.op) {
      case 'EVAL':
        instrs.push({ op: 'EVAL', expr: stmt.expr });
        break;

      case 'IF': {
        const idx = instrs.length;
        instrs.push({ op: 'IF', expr: stmt.expr, falseTarget: null });
        ifStack.push({ ifIndex: idx, elseJmpIndex: null });
        break;
      }
      case 'ELSE': {
        if (ifStack.length === 0) throw new Error('ELSE with no open IF');
        const frame = ifStack[ifStack.length - 1];
        const jmpIdx = instrs.length;
        instrs.push({ op: 'JMP', target: null });
        frame.elseJmpIndex = jmpIdx;
        instrs[frame.ifIndex].falseTarget = jmpIdx + 1;
        break;
      }
      case 'ENDIF': {
        if (ifStack.length === 0) throw new Error('ENDIF with no open IF');
        const frame = ifStack.pop();
        const idx = instrs.length;
        if (frame.elseJmpIndex === null) instrs[frame.ifIndex].falseTarget = idx;
        else instrs[frame.elseJmpIndex].target = idx;
        instrs.push({ op: 'NOP' });
        break;
      }

      case 'DOW': {
        const idx = instrs.length;
        instrs.push({ op: 'DOW', expr: stmt.expr, falseTarget: null });
        dowStack.push({ dowIndex: idx, leaves: [] });
        break;
      }
      case 'LEAVE': {
        if (dowStack.length === 0) throw new Error('LEAVE with no open DOW');
        const frame = dowStack[dowStack.length - 1];
        frame.leaves.push(instrs.length);
        instrs.push({ op: 'LEAVE', target: null });
        break;
      }
      case 'ENDDO': {
        if (dowStack.length === 0) throw new Error('ENDDO with no open DOW');
        const frame = dowStack.pop();
        const idx = instrs.length;
        instrs[frame.dowIndex].falseTarget = idx + 1;
        for (const li of frame.leaves) instrs[li].target = idx + 1;
        instrs.push({ op: 'ENDDO', target: frame.dowIndex });
        break;
      }

      case 'SELECT':
        selStack.push({ arms: [], pendingJumps: [] });
        instrs.push({ op: 'NOP' });
        break;
      case 'WHEN':
      case 'OTHER': {
        if (selStack.length === 0) throw new Error(`${stmt.op} with no open SELECT`);
        const sframe = selStack[selStack.length - 1];
        if (sframe.arms.length > 0) {
          sframe.pendingJumps.push(instrs.length);
          instrs.push({ op: 'JMP', target: null });
        }
        const idx = instrs.length;
        if (stmt.op === 'WHEN') instrs.push({ op: 'WHEN', expr: stmt.expr, falseTarget: null });
        else instrs.push({ op: 'OTHER' });
        sframe.arms.push({ index: idx, isOther: stmt.op === 'OTHER' });
        break;
      }
      case 'ENDSL': {
        if (selStack.length === 0) throw new Error('ENDSL with no open SELECT');
        const sframe = selStack.pop();
        sframe.pendingJumps.push(instrs.length);
        instrs.push({ op: 'JMP', target: null });
        const endslIdx = instrs.length;
        instrs.push({ op: 'NOP' });
        sframe.arms.forEach((arm, i) => {
          if (arm.isOther) return;
          const nextIdx = sframe.arms[i + 1] ? sframe.arms[i + 1].index : endslIdx;
          instrs[arm.index].falseTarget = nextIdx;
        });
        for (const j of sframe.pendingJumps) instrs[j].target = endslIdx;
        break;
      }

      case 'BEGSR':
        subroutines[stmt.factor1] = instrs.length;
        break;
      case 'ENDSR':
        instrs.push({ op: 'ENDSR' });
        break;
      case 'EXSR':
        instrs.push({ op: 'EXSR', target: stmt.expr });
        break;

      case 'EXFMT':
        instrs.push({ op: 'EXFMT', format: stmt.expr });
        break;

      case 'DIV':
        instrs.push({ op: 'DIV', factor1: stmt.factor1, factor2: stmt.factor2, result: stmt.result });
        break;
      case 'MVR':
        instrs.push({ op: 'MVR', result: stmt.result });
        break;

      case 'RETURN':
        instrs.push({ op: 'RETURN' });
        break;

      case 'KLIST':
        currentKlist = (stmt.factor1 || '').toUpperCase();
        klists[currentKlist] = [];
        break;

      case 'KFLD':
        if (currentKlist === null) throw new Error('KFLD with no KLIST before it');
        klists[currentKlist].push(stmt.result);
        break;

      default:
        if (FILE_OPS.has(stmt.op)) {
          instrs.push({
            op: stmt.op, factor1: stmt.factor1, factor2: stmt.factor2,
            indHi: stmt.indHi, indLo: stmt.indLo, indEq: stmt.indEq,
          });
          break;
        }
        throw new Error(`Unsupported RPG opcode: ${stmt.op}`);
    }
    for (const ins of instrs.slice(start)) ins.cond = cond;
  }

  // A block left open has jump targets that were never filled in; running it
  // would crash on a missing address, so say so now.
  if (ifStack.length > 0) throw new Error('IF with no ENDIF');
  if (dowStack.length > 0) throw new Error('DOW loop with no ENDDO');
  if (selStack.length > 0) throw new Error('SELECT with no ENDSL');
  return { instrs, subroutines, klists };
}

// Ops that expand into jumps, so skipping one on a condition would
// desynchronize the jump targets. Real RPG allows conditioning these; here
// it's rejected loudly rather than run wrong.
const NO_COND_OPS = new Set([
  'IF', 'ELSE', 'ENDIF', 'DOW', 'ENDDO', 'SELECT', 'WHEN', 'OTHER', 'ENDSL', 'BEGSR', 'ENDSR', 'KLIST', 'KFLD',
]);

// Columns 9-11: an indicator that must be on, or N and one that must be off.
function parseCond(raw) {
  if (!raw) return null;
  const m = /^(N?)(\d\d|LR)$/i.exec(raw);
  if (!m) throw new Error(`Unsupported conditioning indicator: '${raw}'`);
  return { negated: m[1] !== '', num: m[2].toUpperCase() };
}

function readValue(vars, name) {
  const key = name.trim().toLowerCase();
  if (!(key in vars)) throw new Error(`Unknown RPG variable: ${name}`);
  return vars[key].value;
}

function assign(vars, indicators, lvalue, rvalue) {
  const indM = /^\*IN(\d\d)$/i.exec(lvalue);
  if (indM) { indicators[indM[1]] = Boolean(rvalue); return; }
  if (/^\*INLR$/i.test(lvalue)) { indicators.LR = Boolean(rvalue); return; }
  const key = lvalue.trim().toLowerCase();
  const v = vars[key];
  if (!v) throw new Error(`Unknown RPG variable on assignment: ${lvalue}`);
  if (v.type === 'A') {
    v.value = asString(rvalue).slice(0, v.length).padEnd(v.length, ' ');
  } else {
    // Truncate-on-assign (no rounding) -- the real RPG default, and
    // what DIV/MVR-based modulo relies on for boundedness.
    const n = toNumber(rvalue);
    if (!Number.isFinite(n)) {
      const what = typeof rvalue === 'string' ? `'${rvalue}'` : 'that value';
      throw new Error(`${what} is not a number, so it can't be put in the numeric field ${lvalue.trim()}`);
    }
    const factor = pow10(v.dec || 0);
    v.value = Math.trunc(n * factor) / factor;
  }
}

// Display text for a field. Numeric fields are zero-filled to their length.
// With decimals the decimal point is written out (so the field is one
// column wider than a real 5250 would draw it, which shows bare digits with
// an implied point): bare digits would not survive being typed back in,
// since "000004875" parses as 4875, not 48.75.
function formatFieldText(v) {
  if (v.type === 'A') return v.value;
  const n = v.value;
  if (v.dec > 0) {
    const body = Math.abs(n).toFixed(v.dec).padStart(v.length + 1, '0');
    return n < 0 ? '-' + body.slice(1) : body;
  }
  if (n < 0) return '-' + String(Math.round(-n)).padStart(Math.max(1, v.length - 1), '0');
  return String(Math.round(n)).padStart(v.length, '0');
}

// Renders a DDS record format against the current variable/indicator
// state into a plain { fields, inputMap, cursor } shape, protocol-
// agnostic (no 5250/3270 datastream concerns here -- that's a
// presentation-layer concern for the caller). inputMap maps each
// input field's screen row to its field name, so the caller can read
// input back after the next AID key using whatever row/field
// convention its own renderer already uses.
function renderFormat(format, vars, indicators) {
  const fields = [];
  const inputMap = {};
  let cursor = null;

  const conditionOk = (indicatorsList) => {
    for (const c of indicatorsList) {
      const on = Boolean(indicators[c.num]);
      if (c.not ? on : !on) return false;
    }
    return true;
  };

  for (const f of format.fields) {
    if (!conditionOk(f.indicators)) continue;
    if (f.kind === 'const') {
      fields.push({ row: f.row, col: f.col, text: f.text, input: false });
      continue;
    }
    const v = vars[f.name.toLowerCase()];
    const text = formatFieldText(v);
    const isInput = f.usage === 'I' || f.usage === 'B';
    fields.push({ row: f.row, col: f.col, text, input: isInput, length: isInput ? f.length : undefined });
    if (isInput) {
      inputMap[f.row] = f.name.toLowerCase();
      if (!cursor) cursor = { row: f.row, col: f.col };
    }
  }

  return { fields, inputMap, cursor: cursor || { row: 0, col: 0 } };
}

// Runs the program. Yields { formatName, fields, cursor, keys,
// inputMap } on every EXFMT; resume with .next({ key, values }) where
// key is whatever function key the caller recognizes (matched against
// the format's own CFnn/CAnn-declared keys) and values is
// { fieldName: typedText } for every input field on the format just
// shown.
//
// DISK files need options.db (a Database from database.js) and options.pfs
// (physical file definitions from parsePf, keyed by upper-case file name).
function* runProgram(rpgAst, ddsFormats, options = {}) {
  const vars = buildVars(rpgAst, ddsFormats, options.pfs);
  const indicators = {};
  const { instrs, subroutines, klists } = compile(rpgAst.statements);
  const files = new FileRuntime(rpgAst, options.db, options.pfs, klists);
  const ctx = { vars, indicators, fileStatus: (kind, name) => files.status(kind, name) };
  const callStack = [];
  let pc = 0;
  let lastDiv = { quotient: 0, remainder: 0 };
  let currentFormat = null;
  let currentInputMap = {};

  while (pc < instrs.length) {
    const instr = instrs[pc];

    if (instr.cond) {
      // Skipped unless the indicator is in the state the line asks for.
      if (Boolean(indicators[instr.cond.num]) === instr.cond.negated) { pc++; continue; }
    }

    switch (instr.op) {
      case 'NOP':
        pc++;
        break;
      case 'ENDSR':
        if (!callStack.length) throw new Error('ENDSR with no active EXSR call');
        pc = callStack.pop();
        break;

      case 'EVAL': {
        const { lvalue, rvalueSrc } = splitAssignment(instr.expr);
        const rvalue = evalExpr(rvalueSrc, ctx);
        assign(vars, indicators, lvalue, rvalue);
        pc++;
        break;
      }

      case 'IF':
      case 'WHEN': {
        const cond = evalExpr(instr.expr, ctx);
        pc = cond ? pc + 1 : instr.falseTarget;
        break;
      }
      case 'OTHER':
        pc++;
        break;
      case 'JMP':
        pc = instr.target;
        break;

      case 'DOW': {
        const cond = evalExpr(instr.expr, ctx);
        pc = cond ? pc + 1 : instr.falseTarget;
        break;
      }
      case 'ENDDO':
        pc = instr.target;
        break;
      case 'LEAVE':
        pc = instr.target;
        break;

      case 'EXSR': {
        const target = subroutines[instr.target];
        if (target === undefined) throw new Error(`Unknown RPG subroutine: ${instr.target}`);
        callStack.push(pc + 1);
        pc = target;
        break;
      }

      case 'DIV': {
        const f1 = /^-?\d+(\.\d+)?$/.test(instr.factor1) ? parseFloat(instr.factor1) : readValue(vars, instr.factor1);
        const f2 = /^-?\d+(\.\d+)?$/.test(instr.factor2) ? parseFloat(instr.factor2) : readValue(vars, instr.factor2);
        if (Number(f2) === 0) throw new Error('DIV: division by zero');
        const quotient = Math.trunc(f1 / f2);
        lastDiv = { quotient, remainder: f1 - quotient * f2 };
        assign(vars, indicators, instr.result, quotient);
        pc++;
        break;
      }
      case 'MVR':
        assign(vars, indicators, instr.result, lastDiv.remainder);
        pc++;
        break;

      case 'EXFMT': {
        // RPG is case-insensitive: EXFMT main finds the format DDS calls MAIN.
        const wanted = instr.format.toUpperCase();
        const formatKey = Object.keys(ddsFormats.formats).find((k) => k.toUpperCase() === wanted);
        currentFormat = formatKey === undefined ? undefined : ddsFormats.formats[formatKey];
        if (!currentFormat) throw new Error(`Unknown DDS record format: ${instr.format}`);
        const rendered = renderFormat(currentFormat, vars, indicators);
        currentInputMap = rendered.inputMap;
        // numericFields lets a front end refuse letters in a number field as
        // they're typed, as a real terminal would.
        const numericFields = Object.values(rendered.inputMap).filter((n) => vars[n] && vars[n].type === 'N');
        // Resuming with nothing at all (gen.next()) means "no key, no input".
        const resume = (yield {
          formatName: currentFormat.name, fields: rendered.fields, cursor: rendered.cursor,
          keys: currentFormat.keys, inputMap: rendered.inputMap, numericFields,
        }) || {};

        // Response indicators reset before applying this read's key,
        // scoped to only the indicators this format's own CF/CA
        // keywords own (real RPG behavior -- other formats' key
        // indicators are untouched).
        for (const num of Object.values(currentFormat.keys)) indicators[num] = false;
        const indForKey = currentFormat.keys[resume.key];
        if (indForKey) indicators[indForKey] = true;

        for (const [row, fieldName] of Object.entries(currentInputMap)) {
          if (resume.values && fieldName in resume.values) {
            let typed = resume.values[fieldName];
            // A blank numeric field reads as zero.
            if (vars[fieldName].type === 'N' && String(typed).trim() === '') typed = '0';
            assign(vars, indicators, fieldName, typed);
          }
        }
        pc++;
        break;
      }

      case 'RETURN':
        return;

      default:
        if (FILE_OPS.has(instr.op)) {
          files.exec(
            instr, vars,
            (name, value) => assign(vars, indicators, name, value),
            (num, on) => { indicators[num] = on; },
            (src) => evalExpr(src, ctx),
          );
          pc++;
          break;
        }
        throw new Error(`Unimplemented RPG instruction: ${instr.op}`);
    }
  }
}

module.exports = { runProgram, buildVars, compile, renderFormat };
