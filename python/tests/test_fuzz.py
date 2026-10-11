"""A small, deterministic version of the fuzzing used while building the
runtime: feed the parsers and interpreter malformed input and require that
the only exception to come out is an RpgleError. The seeds are fixed so a
failure reproduces; a failure prints the input that caused it.

During development this was run with over two million inputs; that found a
path traversal, a crash on letters typed into a number field, and a dozen
smaller leaks. The few thousand here keep those from coming back.
"""

import random
import unittest

from rpgle import RpgleError, parse_dds, parse_pf, parse_prtf, parse_rpgle, run_program

WORDS = [
    "if", "elseif", "else", "endif", "dow", "dou", "enddo", "for", "endfor", "select", "when", "other", "endsl",
    "leave", "iter", "return", "exsr", "begsr", "endsr", "dcl-s", "dcl-c", "dcl-f", "dcl-ds", "end-ds", "dcl-proc",
    "end-proc", "dcl-pi", "end-pi", "dcl-pr", "end-pr", "callp", "clear", "sorta", "exfmt", "write", "update",
    "delete", "chain", "setll", "setgt", "read", "reade", "readp", "readpe", "eval", "ctl-opt", "monitor", "dsply",
    "/copy", "/if", "/define", "/eof", "/else", "/endif",
]
JUNK = [
    "", " ", "(", ")", "()", "'", "''", "x", "x y", "x y z", "1", "-1", "a(", "a(1", "a(1).", "a.b.c", "= 1", "x =",
    "= =", "(((", ")))", ":", "x : y", "'abc", "*in03", "%", "%elem", "%elem(", "%lookup(1:", "dim(", "dim(-1)",
    "packed(", "packed()", "char(abc)", "likeds()", "likerec(", "extpgm(", "usage(", "oflind(", "inz(", "options(",
    "x packed(5) dim(2) dim(3)", "x char(", "@", "$", "\\", "\x00", "\x07", "x" * 300, "1e999",
    "99999999999999999999999999", "x(1)(2)", "*loval", "*hival", "%char(", "%subst(x:1:", "not", "and or", "= = =",
    "+= 1", "-= -", "x += ", "\t", "\r", "-'a'", "1/0", "'a'+1", "'a'-'b'", "../x", "a/b",
]
COLUMN_SOUP = " ABCDFHIOPS*0123456789'()=+-./,:;%_ \t"
ENV = (
    "dcl-s x packed(5); dcl-s t char(10); dcl-s a packed(3) dim(3); "
    "dcl-ds d qualified; y char(3); end-ds; dcl-s i packed(3);\n"
)


def _escapes(fn, source):
    """The exception type if fn(source) raises anything but RpgleError, else None."""
    try:
        fn(source)
    except RpgleError:
        return None
    except Exception as e:  # noqa: BLE001 -- the whole point is to catch strays
        return type(e).__name__
    return None


def _run(source):
    list(run_program(parse_rpgle(source), parse_dds("")))


class FuzzTest(unittest.TestCase):
    def check(self, fn, sources, label):
        strays = {}
        for src in sources:
            kind = _escapes(fn, src)
            if kind:
                strays.setdefault(kind, src)
        self.assertEqual(strays, {}, f"{label}: these inputs raised something other than RpgleError: {strays!r}")

    def free_form(self, rng, n, prefix=""):
        for _ in range(n):
            body = "\n".join(
                f"{rng.choice(WORDS)} {rng.choice(JUNK)} {rng.choice(JUNK)};" for _ in range(rng.randint(1, 4))
            )
            yield "**free\n" + prefix + body + "\n"

    def test_free_form_source_parses_or_raises_rpgle_error(self):
        for seed in (1, 2, 3):
            self.check(parse_rpgle, self.free_form(random.Random(seed), 1500), f"seed {seed}")

    def test_free_form_programs_run_or_raise_rpgle_error(self):
        for seed in (4, 5, 6):
            self.check(_run, self.free_form(random.Random(seed), 600, ENV), f"seed {seed}")

    def test_fixed_form_lines_parse_and_run_or_raise_rpgle_error(self):
        rng = random.Random(7)
        lines = []
        for _ in range(2500):
            soup = "".join(rng.choice(COLUMN_SOUP) for _ in range(rng.randint(0, 90)))
            lines += [soup, "     " + rng.choice("FDCOHI") + soup]
        self.check(parse_rpgle, lines, "parse")
        self.check(_run, ["     C" + line for line in lines[:1500:2]], "run")

    def test_dds_parsers_raise_rpgle_error_on_garbage_columns(self):
        rng = random.Random(8)
        header = "     A          R X\n"
        parsers = {
            "display": lambda s: parse_dds(header + s),
            "physical": lambda s: parse_pf("F", header + s),
            "printer": lambda s: parse_prtf("F", header + s),
        }
        for name, fn in parsers.items():
            lines = []
            for _ in range(1500):
                prefix = rng.choice(["     A", "     A  ", "     A            ", "     A          K ", "     A          R "])
                lines.append(prefix + "".join(rng.choice(COLUMN_SOUP) for _ in range(rng.randint(0, 100))))
            self.check(fn, lines, name)

    def test_every_documented_crash_stays_fixed(self):
        # one hand-picked input per bug the fuzzing found, cheap and obvious when it fails
        for src in (
            "**free\ndcl-f ;\n",
            "**free\ndcl-f ../out/leak printer;\n",
            "**free\ndow 1 -1;\n",
            "**free\n/copy \x00;\n",
            "**free\ndcl-s x packed(5);\nx = 'abc';\n",
            "**free\ndcl-s t char(5);\nt = -'a';\n",
            "**free\ndcl-s x packed(5);\nx = 1 / 0;\n",
            "     D X               S              5A.7\n",
        ):
            with self.subTest(src=src):
                self.assertIsNone(_escapes(_run, src), src)


if __name__ == "__main__":
    unittest.main()
