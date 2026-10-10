import contextlib
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
            import contextlib
            import sys
            old = sys.stdin
            sys.stdin = io.StringIO("\n")
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["run", str(p / "q.rpgle")]), 0)
            finally:
                sys.stdin = old


class DiskCliTest(unittest.TestCase):
    """Runs programs/custinq.rpgle through the real CLI entry point."""

    def setUp(self):
        import contextlib
        import os
        import sys

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "t.db")
        self.progs = _PROGRAMS_DIR
        self._redirect = contextlib.redirect_stdout
        self._sys = sys

    def _main(self, argv, stdin=""):
        old = self._sys.stdin
        self._sys.stdin = io.StringIO(stdin)
        out = io.StringIO()
        try:
            with self._redirect(out):
                code = main(argv)
        finally:
            self._sys.stdin = old
        return code, out.getvalue()

    def _load(self):
        code, out = self._main(["load", str(self.progs / "custmast.pf"), str(self.progs / "custmast.csv"), "--db", self.db])
        self.assertEqual(code, 0)
        self.assertIn("Loaded 4 records into CUSTMAST", out)

    def test_load_then_inquire(self):
        self._load()
        code, out = self._main(["run", str(self.progs / "custinq.rpgle"), "--db", self.db], "20\nF3\n")
        self.assertEqual(code, 0)
        self.assertIn("Bob Brewer", out)
        self.assertIn("0000048.75", out)
        self.assertNotIn("Customer not found", out)

    def test_free_form_program_gives_the_same_answers(self):
        self._load()
        args = ["run", str(self.progs / "custinq-free.rpgle"), "--dspf", str(self.progs / "custinq.dspf"), "--db", self.db]
        code, out = self._main(args, "20\n99\nF3\n")
        self.assertEqual(code, 0)
        self.assertIn("Bob Brewer", out)
        self.assertIn("0000048.75", out)
        self.assertIn("Customer not found", out)

    def test_program_whose_files_come_from_a_copybook(self):
        self._load()
        args = ["run", str(self.progs / "custinq-copy.rpgle"), "--dspf", str(self.progs / "custinq.dspf"), "--db", self.db]
        code, out = self._main(args, "10\nF3\n")
        self.assertEqual(code, 0)
        self.assertIn("Alice Anderson", out)

    def test_not_found_shows_the_message(self):
        self._load()
        code, out = self._main(["run", str(self.progs / "custinq.rpgle"), "--db", self.db], "99\nF3\n")
        self.assertEqual(code, 0)
        self.assertIn("Customer not found", out)

    def test_ctrl_c_exits_quietly_and_keeps_earlier_changes(self):
        self._load()

        class Interrupting:
            def readline(self):
                raise KeyboardInterrupt

        old = self._sys.stdin
        self._sys.stdin = Interrupting()
        err = io.StringIO()
        try:
            with self._redirect(io.StringIO()), contextlib.redirect_stderr(err):
                code = main(["run", str(self.progs / "custinq.rpgle"), "--db", self.db])
        finally:
            self._sys.stdin = old
        self.assertEqual(code, 130)
        self.assertNotIn("Traceback", err.getvalue())

    def test_disk_program_without_db_is_an_error(self):
        code, _ = self._main(["run", str(self.progs / "custinq.rpgle")])
        self.assertEqual(code, 1)

    def test_load_rejects_a_csv_missing_columns(self):
        import os

        bad = os.path.join(self._tmp.name, "bad.csv")
        with open(bad, "w") as fh:
            fh.write("custno,name\n1,X\n")
        code, _ = self._main(["load", str(self.progs / "custmast.pf"), bad, "--db", self.db])
        self.assertEqual(code, 1)

    def test_load_rejects_a_non_numeric_value(self):
        import os

        bad = os.path.join(self._tmp.name, "bad.csv")
        with open(bad, "w") as fh:
            fh.write("custno,name,bal\nabc,X,1\n")
        code, _ = self._main(["load", str(self.progs / "custmast.pf"), bad, "--db", self.db])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
