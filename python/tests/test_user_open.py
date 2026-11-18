"""USROPN: an F-spec that isn't opened at program start, needs an explicit OPEN statement, and lets
a host re-check authority and re-apply an override at the moment OPEN runs rather than at program
start -- the reason USROPN exists at all (see fileops.py's open_close and interpreter.py's open_hook).
"""

import unittest

from rpgle import RpgleError, parse_dds, parse_rpgle, run_program
from rpgle.database import Database
from rpgle.pf import parse_pf

from _cols import dds, dds_line, fspec

CUST = parse_pf(
    "CUSTMAST",
    dds(
        dds_line(keywords="UNIQUE"),
        dds_line("R", "CUSTREC"),
        dds_line("", "CUSTNO", 5, "S", 0),
        dds_line("", "NAME", 10),
        dds_line("K", "CUSTNO"),
    ),
)
PFS = {"CUSTMAST": CUST}


def src(*lines: str) -> str:
    return "\n".join(["**free", "dcl-f custmast disk usropn;", *lines]) + "\n"


def run_all(program, db, open_hook=None):
    gen = run_program(parse_rpgle(program), parse_dds(""), db, PFS, open_hook=open_hook)
    for _ in gen:
        pass


class FreeFormParseTest(unittest.TestCase):
    def test_usropn_keyword_is_recognized(self):
        prog = parse_rpgle("**free\ndcl-f custmast disk usropn;\n")
        self.assertTrue(prog.files[0].user_open)

    def test_without_usropn_the_flag_is_false(self):
        prog = parse_rpgle("**free\ndcl-f custmast disk;\n")
        self.assertFalse(prog.files[0].user_open)


class FixedFormParseTest(unittest.TestCase):
    def test_usropn_in_the_keyword_area(self):
        prog = parse_rpgle(fspec("CUSTMAST", "I", "F", "", "E", False, "DISK", keywords="USROPN") + "\n")
        self.assertTrue(prog.files[0].user_open)

    def test_without_the_keyword_the_flag_is_false(self):
        prog = parse_rpgle(fspec("CUSTMAST", "I", "F", "", "E", False, "DISK") + "\n")
        self.assertFalse(prog.files[0].user_open)


class UserOpenTest(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)

    def test_a_usropn_file_is_not_open_at_the_start(self):
        with self.assertRaises(RpgleError) as cm:
            run_all(src("chain 10 custmast;", "*inlr = *on;"), self.db, open_hook=lambda name, mode: CUST)
        self.assertIn("not open", str(cm.exception))

    def test_open_then_chain_works(self):
        self.db.load_rows(CUST, [{"CUSTNO": 10, "NAME": "ALICE"}])
        opened = []

        def hook(name, mode):
            opened.append((name, mode))
            return CUST

        run_all(src(
            "open custmast;",
            "chain 10 custmast;",
            "if not %found(custmast);",
            "  *inlr = *on;",  # would trip only if the chain failed to find the seeded row
            "  return;",
            "endif;",
            "*inlr = *on;",
        ), self.db, open_hook=hook)
        self.assertEqual(opened, [("CUSTMAST", "I")])

    def test_open_with_no_hook_is_refused(self):
        with self.assertRaises(RpgleError) as cm:
            run_all(src("open custmast;", "*inlr = *on;"), self.db)
        self.assertIn("open_hook", str(cm.exception))

    def test_open_is_a_no_op_once_already_open(self):
        self.db.load_rows(CUST, [{"CUSTNO": 10, "NAME": "ALICE"}])
        calls = []

        def hook(name, mode):
            calls.append(name)
            return CUST

        run_all(src("open custmast;", "open custmast;", "*inlr = *on;"), self.db, open_hook=hook)
        self.assertEqual(calls, ["CUSTMAST"])  # the second OPEN did nothing

    def test_close_then_chain_fails_again(self):
        self.db.load_rows(CUST, [{"CUSTNO": 10, "NAME": "ALICE"}])
        with self.assertRaises(RpgleError) as cm:
            run_all(src(
                "open custmast;",
                "close custmast;",
                "chain 10 custmast;",
                "*inlr = *on;",
            ), self.db, open_hook=lambda name, mode: CUST)
        self.assertIn("not open", str(cm.exception))

    def test_open_can_be_refused_by_the_host(self):
        def refuse(name, mode):
            raise RpgleError(f"not authorized to {name}")

        with self.assertRaises(RpgleError) as cm:
            run_all(src("open custmast;", "*inlr = *on;"), self.db, open_hook=refuse)
        self.assertIn("not authorized", str(cm.exception))

    def test_open_takes_the_declared_mode_to_the_hook(self):
        seen = []

        def hook(name, mode):
            seen.append(mode)
            return CUST

        prog = "\n".join([
            "**free",
            "dcl-f custmast disk usropn usage(*update);",
            "open custmast;",
            "*inlr = *on;",
        ]) + "\n"
        run_all(prog, self.db, open_hook=hook)
        self.assertEqual(seen, ["U"])


if __name__ == "__main__":
    unittest.main()
