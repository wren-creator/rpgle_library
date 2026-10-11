"""Runs the parity cases through the Python port. Usage: run_py.py CASES.json OUT.json"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "python" / "src"))
from rpgle import Context, Var, eval_expr, parse_rpgle, parse_dds, run_program, compile_program, Resume, RpgleError
from rpgle.interpreter import _format_field_text
C = json.load(open(sys.argv[1]))
def val(v):
    if isinstance(v, bool): return {"t": "b", "v": v}
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")): return {"error": True, "nonfinite": True}
        return {"t": "n", "v": float(v)}
    return {"t": "s", "v": v}
out = {}
ctx = Context(vars={"name": Var(type="A", length=20, dec=0, value="BOB".ljust(20)), "other": Var(type="A", length=5, dec=0, value="BOB  "), "money": Var(type="N", length=9, dec=2, value=5.0), "whole": Var(type="N", length=5, dec=0, value=5.0)}, indicators={"03": True})
out["exprs"] = []
for e in C["exprs"]:
    try: out["exprs"].append(val(eval_expr(e, ctx)))
    except RpgleError: out["exprs"].append({"error": True})
out["render"] = [_format_field_text(Var(type="N", length=l, dec=d, value=float(v))) for v, d, l in C["render"]]
def dline(kw, name, length, dec):
    return "     D" + name.ljust(17) + "S" + str(length).rjust(15) + (str(dec).rjust(3) if dec != "" else "   ") + " " + kw
out["dspecs"] = []
for kw, name, length, dec in C["dspecs"]:
    try:
        p = parse_rpgle(dline(kw, name, length, dec)); d = p.dspecs[0]
        out["dspecs"].append({"ok": [d.name, d.length, d.dec, d.init]})
    except RpgleError: out["dspecs"].append({"error": True})
out["specs"] = []
for s in C["specs"]:
    try: parse_rpgle(s); out["specs"].append({"ok": True})
    except RpgleError: out["specs"].append({"error": True})
def cs(op, f2=""): return "     C                   " + op.split(" ")[0].ljust(10) + (op.split(" ", 1)[1] if " " in op else "")
out["compile"] = []
for lines in C["compile"]:
    try: compile_program(parse_rpgle("\n".join(cs(l) for l in lines)).statements); out["compile"].append({"ok": True})
    except RpgleError: out["compile"].append({"error": True})
DDS = "     A          R INQ                       CF03(03)\n     A            CUSTNO         5  0B  3 20\n"
PROG = "     FINQ       CF   E             WORKSTN\n     C                   EXFMT     INQ\n     C                   EXFMT     INQ\n"
out["typed"] = []
for t in C["typed"]:
    try:
        g = run_program(parse_rpgle(PROG), parse_dds(DDS)); next(g)
        s = g.send(Resume(key="ENTER", values={"custno": t}))
        out["typed"].append({"ok": [f.text for f in s.fields if f.input][0]})
    except RpgleError: out["typed"].append({"error": True})
out["assign"] = []
for a in C["assign"]:
    try:
        g = run_program(parse_rpgle("     Dx                S              5  2\n     C                   EVAL      x = " + a + "\n     C                   EXFMT     X\n"), parse_dds("     A          R X\n     A            X              5  2O  1  1\n"))
        s = next(g); out["assign"].append({"ok": s.fields[0].text})
    except RpgleError: out["assign"].append({"error": True})
json.dump(out, open(sys.argv[2], "w"))
