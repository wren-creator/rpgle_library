"""Bad input should get a clear RpgleError (or a re-prompt), never a Python
traceback. Each case here was found by feeding the parser and interpreter
malformed input and watching what escaped.
"""

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

from _cols import cspec, dspec
from rpgle import Context, Resume, RpgleError, Var, eval_expr, parse_dds, parse_rpgle, run_program
from rpgle.cli import main

_PROGRAMS = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"

NUM_DDS = "     A          R INQ                       CF03(03)\n     A            CUSTNO         5  0B  3 20\n"
NUM_PROG = "     FINQ       CF   E             WORKSTN\n" + cspec("EXFMT", factor2="INQ") + "\n" + cspec("EXFMT", factor2="INQ") + "\n"


class NumericInputTest(unittest.TestCase):
    def start(self):
        gen = run_program(parse_rpgle(NUM_PROG), parse_dds(NUM_DDS))
        return gen, next(gen)

    def test_screen_data_says_which_inputs_are_numeric(self):
        _, screen = self.start()
        self.assertEqual(screen.numeric_fields, frozenset({"custno"}))

    def test_letters_in_a_numeric_field_are_an_error_that_names_them(self):
        gen, _ = self.start()
        with self.assertRaises(RpgleError) as cm:
            gen.send(Resume(key="ENTER", values={"custno": "abc"}))
        self.assertIn("'abc' is not a number", str(cm.exception))
        self.assertIn("custno", str(cm.exception))

    def test_a_blank_numeric_field_reads_as_zero(self):
        gen, _ = self.start()
        screen = gen.send(Resume(key="ENTER", values={"custno": "   "}))
        self.assertEqual([f.text for f in screen.fields if f.input], ["00000"])

    def test_numbers_typed_with_spaces_or_decimals_still_work(self):
        gen, _ = self.start()
        screen = gen.send(Resume(key="ENTER", values={"custno": " 42 "}))
        self.assertEqual([f.text for f in screen.fields if f.input], ["00042"])

    def test_assigning_text_to_a_number_is_an_error_not_a_crash(self):
        for text in ("x = 'abc'", "x = ''", "x = 'a' + 'b'"):
            with self.subTest(text=text):
                prog = parse_rpgle("**free\ndcl-s x packed(5);\n" + text + ";\n")
                with self.assertRaises(RpgleError) as cm:
                    list(run_program(prog, parse_dds("")))
                self.assertIn("not a number", str(cm.exception))

    def test_the_cli_refuses_letters_in_a_number_field_and_asks_again(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d) / "t.db")
            stdin, out = sys.stdin, io.StringIO()
            sys.stdin = io.StringIO("")
            try:
                with contextlib.redirect_stdout(out):
                    main(["load", str(_PROGRAMS / "custmast.pf"), str(_PROGRAMS / "custmast.csv"), "--db", db])
                sys.stdin = io.StringIO("abc\n1x\n20\nF3\n")
                out = io.StringIO()
                err = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(["run", str(_PROGRAMS / "custinq.rpgle"), "--db", db])
            finally:
                sys.stdin = stdin
        text = out.getvalue()
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(text.count("takes a number"), 2)  # both bad entries were refused, then 20 was accepted
        self.assertIn("Bob Brewer", text)
        self.assertNotIn("Traceback", text + err.getvalue())

    def test_function_keys_are_still_accepted_in_a_numeric_field(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d) / "t.db")
            stdin = sys.stdin
            out = io.StringIO()
            try:
                sys.stdin = io.StringIO("")
                with contextlib.redirect_stdout(io.StringIO()):
                    main(["load", str(_PROGRAMS / "custmast.pf"), str(_PROGRAMS / "custmast.csv"), "--db", db])
                sys.stdin = io.StringIO("F3\n")
                with contextlib.redirect_stdout(out):
                    code = main(["run", str(_PROGRAMS / "custinq.rpgle"), "--db", db])
            finally:
                sys.stdin = stdin
        self.assertEqual(code, 0)
        self.assertNotIn("takes a number", out.getvalue())


class ArithmeticTest(unittest.TestCase):
    def test_division_by_zero_is_an_error(self):
        for src in ("1 / 0", "5 / (2 - 2)"):
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    eval_expr(src, Context(vars={}, indicators={}))
                self.assertIn("by zero", str(cm.exception))

    def test_fixed_form_div_by_zero_is_an_error(self):
        prog = parse_rpgle(
            "\n".join([dspec("A", 5, 0, 7), dspec("B", 5, 0, 0), dspec("Q", 5, 0), cspec("DIV", "a", "b", "q")])
        )
        with self.assertRaises(RpgleError) as cm:
            list(run_program(prog, parse_dds("")))
        self.assertIn("division by zero", str(cm.exception))

    def test_ordinary_division_still_works(self):
        self.assertEqual(eval_expr("7 / 2", Context(vars={}, indicators={})), 3.5)


class MalformedSourceTest(unittest.TestCase):
    def test_empty_dcl_f(self):
        with self.assertRaises(RpgleError):
            parse_rpgle("**free\ndcl-f ;\n")

    def test_a_non_numeric_length_in_a_fixed_form_d_spec(self):
        bad = dspec("X", 5, 0).replace("    5", " 5A.7")
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle(bad)
        self.assertIn("is not a number", str(cm.exception))

    def test_a_control_character_in_a_copy_name(self):
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle("**free\n/copy \x00;\n")
        self.assertIn("control character", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class UnterminatedBlockTest(unittest.TestCase):
    def check(self, source, fragment):
        with self.assertRaises(RpgleError) as cm:
            list(run_program(parse_rpgle(source), parse_dds("")))
        self.assertIn(fragment, str(cm.exception))

    def test_blocks_left_open_are_an_error_not_a_crash(self):
        self.check("**free\ndow 1 = 1;\n", "no ENDDO")
        self.check("**free\ndou 1 = 1;\n", "no ENDDO")
        self.check("**free\ndcl-s i packed(3);\nfor i = 1 to 3;\n", "no ENDFOR")
        self.check("**free\nif 1 = 1;\n", "no ENDIF")
        self.check("**free\nif 1 = 1;\nelseif 2 = 2;\n", "no ENDIF")
        self.check("**free\nselect;\nwhen 1 = 1;\n", "no ENDSL")

    def test_the_innermost_open_loop_is_named(self):
        self.check("**free\ndcl-s i packed(3);\nfor i = 1 to 2;\nendfor;\ndow 1 = 1;\n", "DOW loop")

    def test_fixed_form_too(self):
        self.check(cspec("DOW", factor2="1 = 1"), "no ENDDO")

    def test_balanced_blocks_are_unaffected(self):
        list(run_program(parse_rpgle("**free\nif 1 = 1;\nendif;\ndow 1 = 2;\nenddo;\nselect;\nwhen 1 = 1;\nendsl;\n"), parse_dds("")))


class TextArithmeticTest(unittest.TestCase):
    def test_negating_text_is_an_error(self):
        with self.assertRaises(RpgleError) as cm:
            eval_expr("-'a'", Context(vars={}, indicators={}))
        self.assertIn("negate", str(cm.exception))

    def test_negating_a_number_still_works(self):
        self.assertEqual(eval_expr("-(2 + 3)", Context(vars={}, indicators={})), -5.0)
        self.assertEqual(eval_expr("- -4", Context(vars={}, indicators={})), 4.0)


class DdsColumnTest(unittest.TestCase):
    """Letters in a DDS numeric column (length, decimals, line, position) used
    to escape as a bare ValueError from int()."""

    def test_each_dds_parser_reports_a_clear_error(self):
        from _cols import dds_line, pdds_line
        from rpgle import parse_pf, parse_prtf

        r = pdds_line("R", "R1")
        cases = [
            ("display length", lambda: parse_dds(r + "\n" + pdds_line("", "F", "3X", "", "", 1, 1))),
            ("display line", lambda: parse_dds(r + "\n" + pdds_line("", "F", 3, "", "", "zz", 5))),
            ("display position", lambda: parse_dds(r + "\n" + pdds_line("", "F", 3, "", "", 1, "q"))),
            ("display decimals", lambda: parse_dds(r + "\n" + pdds_line("", "F", 3, "", "x", 1, 1))),
            ("physical length", lambda: parse_pf("F", dds_line("R", "R1") + "\n" + dds_line("", "A", "3X"))),
            ("physical decimals", lambda: parse_pf("F", dds_line("R", "R1") + "\n" + dds_line("", "A", 3, "S", "q"))),
            ("printer length", lambda: parse_prtf("F", r + "\n" + pdds_line("", "A", "3X", "", "", 1, 1))),
            ("printer decimals", lambda: parse_prtf("F", r + "\n" + pdds_line("", "A", 3, "S", "q", 1, 1))),
        ]
        for label, build in cases:
            with self.subTest(label):
                with self.assertRaises(RpgleError) as cm:
                    build()
                self.assertIn("not a number", str(cm.exception))

    def test_good_columns_are_unaffected(self):
        from rpgle import parse_prtf

        from _cols import pdds_line

        d = parse_dds(pdds_line_r() + "\n" + pdds_line("", "F", 3, "A", "", 2, 4))
        self.assertEqual((d.formats["R1"].fields[0].length, d.formats["R1"].fields[0].row), (3, 1))
        self.assertEqual(parse_prtf("F", pdds_line_r() + "\n" + pdds_line("", "N", 5, "S", 2, 1, 1)).formats["R1"].fields[0].dec, 2)


def pdds_line_r() -> str:
    from _cols import pdds_line

    return pdds_line("R", "R1")


class OverlongNameTest(unittest.TestCase):
    """A name longer than the filesystem allows raised OSError on Python 3.13
    (and was quietly 'not found' on 3.14), so a fuzz test passed on one
    version and failed on another. Names are capped, and a lookup the
    filesystem rejects is simply 'not found'."""

    LONG = "x" * 300

    def test_a_huge_copy_member_is_not_found(self):
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle(f"**free\n/copy {self.LONG};\n")
        self.assertIn("can't find", str(cm.exception))

    def test_a_huge_quoted_copy_path_is_not_found(self):
        with self.assertRaises(RpgleError):
            parse_rpgle(f"**free\n/copy '{self.LONG}/{self.LONG}.rpgleinc';\n")

    def test_a_huge_file_name_is_refused_at_the_parser(self):
        with self.assertRaises(RpgleError):
            parse_rpgle(f"**free\ndcl-f {self.LONG} workstn;\n")

    def test_a_huge_program_name_is_refused(self):
        from rpgle.loader import program_key

        with self.assertRaises(RpgleError):
            program_key(self.LONG)

    def test_ordinary_long_names_still_work(self):
        self.assertEqual(parse_rpgle("**free\ndcl-f " + "a" * 90 + " workstn;\n").files[0].name, "a" * 90)
