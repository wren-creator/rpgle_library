// Runs the parity cases through the JS port. Usage: node run_js.js CASES.json OUT.json
const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..", "..");
const { evalExpr } = require(root + "/src/expr");
const { parseRpgle } = require(root + "/src/rpgle");
const { parseDds } = require(root + "/src/dds");
const { runProgram, compile, renderFormat } = require(root + "/src/interpreter");
const C = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const val = (v) => typeof v === "boolean" ? { t: "b", v } : typeof v === "number" ? { t: "n", v } : { t: "s", v };
const out = {};
const ctx = { vars: { name: { type: "A", length: 20, dec: 0, value: "BOB".padEnd(20) }, other: { type: "A", length: 5, dec: 0, value: "BOB  " }, money: { type: "N", length: 9, dec: 2, value: 5 }, whole: { type: "N", length: 5, dec: 0, value: 5 } }, indicators: { "03": true } };
const safe = (f, onErr) => { try { return f(); } catch (e) { return onErr; } };
out.exprs = C.exprs.map((e) => safe(() => { const v = evalExpr(e, ctx); return typeof v === "number" && !Number.isFinite(v) ? { error: true, nonfinite: true } : val(v); }, { error: true }));
const dds1 = parseDds("     A          R MAIN\n     A            AMT            9  2O  1  1\n");
out.render = C.render.map(([v, d, l]) => renderFormat(dds1.formats.MAIN, { amt: { type: "N", length: l, dec: d, value: v } }, {}).fields[0].text);
const dline = (kw, name, length, dec) => "     D" + name.padEnd(17) + "S" + String(length).padStart(15) + (dec !== "" ? String(dec).padStart(3) : "   ") + " " + kw;
out.dspecs = C.dspecs.map(([kw, name, length, dec]) => safe(() => { const d = parseRpgle(dline(kw, name, length, dec)).dspecs[0]; return { ok: [d.name, d.length, d.dec, d.init] }; }, { error: true }));
out.specs = C.specs.map((s) => safe(() => { parseRpgle(s); return { ok: true }; }, { error: true }));
const cs = (op) => "     C                   " + op.split(" ")[0].padEnd(10) + (op.includes(" ") ? op.split(" ").slice(1).join(" ") : "");
out.compile = C.compile.map((lines) => safe(() => { compile(parseRpgle(lines.map(cs).join("\n")).statements); return { ok: true }; }, { error: true }));
const DDS = "     A          R INQ                       CF03(03)\n     A            CUSTNO         5  0B  3 20\n";
const PROG = "     FINQ       CF   E             WORKSTN\n     C                   EXFMT     INQ\n     C                   EXFMT     INQ\n";
out.typed = C.typed.map((t) => safe(() => { const g = runProgram(parseRpgle(PROG), parseDds(DDS)); g.next(); const s = g.next({ key: "ENTER", values: { custno: t } }).value; return { ok: s.fields.filter((f) => f.input)[0].text }; }, { error: true }));
out.assign = C.assign.map((a) => safe(() => { const g = runProgram(parseRpgle("     Dx                S              5  2\n     C                   EVAL      x = " + a + "\n     C                   EXFMT     X\n"), parseDds("     A          R X\n     A            X              5  2O  1  1\n")); return { ok: g.next().value.fields[0].text }; }, { error: true }));
// DISK files: each case is a whole program run against freshly seeded tables;
// what is compared is what is left in every table, in key order.
const { Database } = require(root + "/src/database");
const { parsePf } = require(root + "/src/pf");
out.files = C.files.map((c) => {
  let db;
  try {
    db = new Database();
    const pfs = {};
    const handles = {};
    for (const [n, f] of Object.entries(C._files.pfs)) {
      pfs[n] = parsePf(n, f.dds);
      handles[n] = db.openFile(pfs[n]);
      for (const row of f.seed) handles[n].write(Object.fromEntries(pfs[n].fields.map((fld, i) => [fld.name, row[i]])));
    }
    for (const _ of runProgram(parseRpgle(c.source), parseDds(""), { db, pfs })) { /* no screens */ }
    const dump = {};
    for (const [n, h] of Object.entries(handles)) {
      h.setll(null);
      const rows = [];
      for (let rec = h.read(); rec; rec = h.read()) rows.push(pfs[n].fields.map((fld) => rec[fld.name]));
      dump[n] = rows;
    }
    return { ok: dump };
  } catch (e) {
    return { error: true };
  } finally {
    if (db) db.close();
  }
});
fs.writeFileSync(process.argv[3], JSON.stringify(out));
