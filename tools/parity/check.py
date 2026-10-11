#!/usr/bin/env python3
"""Checks that the Python, JS and Go ports agree.

Runs the same cases (cases.json) through all three ports and compares what each
produced: expression results, screen text for numeric fields, how D-specs and
other source lines parse, which block structures compile, and what happens when
text is typed into a number field. Error *messages* may differ between ports;
whether something is an error, and every value that isn't, must not.

    python3 tools/parity/check.py            # exits 1 if any case differs
    python3 tools/parity/check.py --verbose  # also prints the cases that agree

Needs python3, node and go on the PATH. To add a case, add it to cases.json:
a failing check on a new case is how a behavior difference gets found.
See "Checking port parity" in docs/PUBLISHING.md.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PORTS = ("python", "js", "go")


def run_ports(cases: pathlib.Path, tmp: pathlib.Path) -> dict:
    out = {p: tmp / f"{p}.json" for p in PORTS}
    steps = {
        # A private bytecode cache: Python trusts a .pyc whose source has the same
        # size and modification second, so editing a file twice in one second
        # (a quick edit and revert) could otherwise run the stale compiled copy.
        "python": (
            [sys.executable, "-X", f"pycache_prefix={tmp / 'pycache'}", str(HERE / "run_py.py"), str(cases), str(out["python"])],
            ROOT,
            {},
        ),
        "js": (["node", str(HERE / "run_js.js"), str(cases), str(out["js"])], ROOT, {}),
        "go": (
            ["go", "test", "-run", "TestParityDump", "-count=1", "."],
            ROOT / "go",
            {"PARITY_IN": str(cases), "PARITY_OUT": str(out["go"])},
        ),
    }
    for port, (cmd, cwd, env) in steps.items():
        done = subprocess.run(cmd, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True)
        if done.returncode != 0:
            sys.exit(f"the {port} runner failed:\n{done.stdout}{done.stderr}")
    return {p: json.loads(out[p].read_text()) for p in PORTS}


def canon(x):
    """Numbers become floats (Python writes -5.0 where JS writes -5, and they are the same number)."""
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return round(float(x), 9)
    if isinstance(x, list):
        return [canon(i) for i in x]
    if isinstance(x, dict):
        return {k: canon(v) for k, v in x.items()}
    return x


def norm(x):
    """One comparable form per result; errors collapse because messages may differ."""
    if isinstance(x, dict) and x.get("error"):
        return "ERROR"
    if isinstance(x, dict) and "ok" in x:
        return ["ok", canon(x["ok"])]
    if isinstance(x, dict):
        return [x.get("t"), canon(x.get("v"))]
    return canon(x)


def label(section: str, case) -> str:
    return " ; ".join(case) if isinstance(case, list) and all(isinstance(c, str) for c in case) else (
        " | ".join(str(c) for c in case) if isinstance(case, list) else str(case)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--cases", default=str(HERE / "cases.json"))
    args = ap.parse_args()

    cases = json.loads(pathlib.Path(args.cases).read_text())
    with tempfile.TemporaryDirectory() as d:
        results = run_ports(pathlib.Path(args.cases), pathlib.Path(d))

    total = differ = 0
    for section, items in cases.items():
        for i, case in enumerate(items):
            total += 1
            got = {p: norm(results[p][section][i]) for p in PORTS}
            if len({json.dumps(v, sort_keys=True) for v in got.values()}) > 1:
                differ += 1
                print(f"DIFFERS [{section}] {label(section, case)!r}")
                for p in PORTS:
                    print(f"    {p:7} {got[p]}")
            elif args.verbose:
                print(f"same    [{section}] {label(section, case)!r}: {got['python']}")
    print(f"\n{total} cases, {total - differ} identical across Python, JS and Go, {differ} differ")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
