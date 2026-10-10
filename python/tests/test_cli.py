import io
import pathlib
import tempfile
import unittest

from rpgle import parse_dds, parse_rpgle
from rpgle.cli import drive, main, render_grid

from _fixtures import _PROGRAMS_DIR

QUICK_DSPF = """
     A          R RESULT
     A                                  1  2'Quotient:'
     A            QUOT           5  0O  1 13
"""

QUICK_RPG = """
     FRESULT    CF   E             WORKSTN

     Da                S              5  0 INZ(17)
     Db                S              5  0 INZ(5)

     C     a             DIV       b             quot
     C                   EXFMT     RESULT
     C                   EVAL      *inlr = *on
     C                   RETURN
"""


def run(rpg, dspf, text):
    out = io.StringIO()
    code = drive(parse_rpgle(rpg), parse_dds(dspf), io.StringIO(text), out)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def test_output_only_screen_runs_to_end(self):
        code, out = run(QUICK_RPG, QUICK_DSPF, "\n")
        self.assertEqual(code, 0)
        self.assertIn("|  Quotient:  00003", out)  # 17 DIV 5
        self.assertIn("(program ended)", out)

    def test_grid_is_24_rows_by_80_cols(self):
        _, out = run(QUICK_RPG, QUICK_DSPF, "\n")
        box = [l for l in out.splitlines() if l.startswith(("|", "+"))]
        self.assertEqual(len(box), 26)
        self.assertTrue(all(len(l) == 84 for l in box))

    def test_eof_exits_cleanly(self):
        code, out = run(QUICK_RPG, QUICK_DSPF, "")
        self.assertEqual(code, 0)
        self.assertIn("(end of input)", out)

    def test_adventure_input_and_f3(self):
        rpg = (_PROGRAMS_DIR / "adventure.rpgle").read_text()
        dspf = (_PROGRAMS_DIR / "adventure.dspf").read_text()
        code, out = run(rpg, dspf, "M\nF3\n")
        self.assertEqual(code, 0)
        self.assertIn("cmd", out)
        self.assertIn("(program ended)", out)

    def test_main_missing_file_is_an_error(self):
        self.assertEqual(main(["run", "/nonexistent/x.rpgle"]), 1)

    def test_main_picks_up_sibling_dspf(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "q.rpgle").write_text(QUICK_RPG)
            (p / "q.dspf").write_text(QUICK_DSPF)
            import sys
            old = sys.stdin
            sys.stdin = io.StringIO("\n")
            try:
                self.assertEqual(main(["run", str(p / "q.rpgle")]), 0)
            finally:
                sys.stdin = old


if __name__ == "__main__":
    unittest.main()
