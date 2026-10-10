import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest

from rpgle import RpgleError, parse_rpgle
from rpgle.cli import main
from rpgle.include import expand_includes, has_directives

from _cols import cspec, dspec, fspec


class IncludeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name).resolve()

    def put(self, rel: str, text: str) -> pathlib.Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def parse(self, main_text: str, name="main.rpgle", **kw):
        p = self.put(name, main_text)
        return parse_rpgle(main_text, path=p, **kw)


class ResolutionTest(IncludeBase):
    MEMBER = "dcl-s shared packed(5);\n"

    def check(self, directive, member_path):
        self.put(member_path, self.MEMBER)
        prog = self.parse(f"**free\n{directive}\nshared = 1;\n")
        self.assertEqual([d.name for d in prog.dspecs], ["shared"])
        self.assertEqual([s.expr for s in prog.statements], ["shared = 1"])

    def test_member_only(self):
        self.check("/copy defs", "defs.rpgleinc")

    def test_file_comma_member(self):
        self.check("/copy qrpglesrc,defs", "qrpglesrc/defs.rpgleinc")

    def test_file_comma_member_falls_back_to_the_member_alone(self):
        self.check("/copy qrpglesrc,defs", "defs.rpgleinc")

    def test_library_file_member(self):
        self.check("/copy mylib/qrpglesrc,defs", "mylib/qrpglesrc/defs.rpgleinc")

    def test_library_file_member_falls_back_to_file_then_member(self):
        self.check("/copy mylib/qrpglesrc,defs", "qrpglesrc/defs.rpgleinc")

    def test_include_is_a_synonym(self):
        self.check("/include defs", "defs.rpgleinc")

    def test_quoted_path(self):
        self.check("/copy 'inc/defs.rpgleinc'", "inc/defs.rpgleinc")

    def test_extensions_and_case(self):
        for member in ("DEFS", "defs.rpgle", "Defs.COPY".lower(), "defs.inc"):
            with self.subTest(member=member):
                with tempfile.TemporaryDirectory() as d:
                    root = pathlib.Path(d).resolve()
                    (root / member).write_text(self.MEMBER)
                    main_p = root / "m.rpgle"
                    text = "**free\n/COPY Defs\n"
                    main_p.write_text(text)
                    self.assertEqual(len(parse_rpgle(text, path=main_p).dspecs), 1)

    def test_directive_may_be_indented_and_any_case(self):
        self.put("defs.rpgleinc", self.MEMBER)
        prog = self.parse("**free\n   /CoPy defs\n")
        self.assertEqual(len(prog.dspecs), 1)

    def test_include_dirs_are_searched_after_the_including_files_folder(self):
        self.put("libs/defs.rpgleinc", "dcl-s from_libs packed(1);\n")
        self.put("defs.rpgleinc", "dcl-s from_here packed(1);\n")
        prog = self.parse("**free\n/copy defs\n", include_dirs=[self.root / "libs"])
        self.assertEqual([d.name for d in prog.dspecs], ["from_here"])
        (self.root / "defs.rpgleinc").unlink()
        prog = self.parse("**free\n/copy defs\n", include_dirs=[self.root / "libs"])
        self.assertEqual([d.name for d in prog.dspecs], ["from_libs"])

    def test_nested_includes_resolve_beside_the_member_that_asks(self):
        self.put("a/outer.rpgleinc", "/copy inner\ndcl-s outer packed(1);\n")
        self.put("a/inner.rpgleinc", "dcl-s inner packed(1);\n")
        prog = self.parse("**free\n/copy a/outer\n")
        self.assertEqual([d.name for d in prog.dspecs], ["inner", "outer"])

    def test_a_free_marker_in_a_member_is_dropped(self):
        self.put("defs.rpgleinc", "**FREE\ndcl-s shared packed(5);\n")
        self.assertEqual(len(self.parse("**free\n/copy defs\n").dspecs), 1)

    def test_fixed_form_members(self):
        self.put("defs.rpgleinc", dspec("FROMCOPY", 5, 0, 7) + "\n")
        prog = self.parse(fspec("CUSTMAST", "I", "F", "", "E", True) + "\n      /COPY defs\n" + cspec("EVAL", factor2="fromcopy = 1") + "\n")
        self.assertEqual([d.name for d in prog.dspecs], ["FROMCOPY"])
        self.assertEqual(len(prog.statements), 1)
        self.assertEqual(len(prog.files), 1)


class ErrorTest(IncludeBase):
    def test_missing_member_says_where_it_looked_and_what_line_asked(self):
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n\n/copy nosuch\n")
        msg = str(cm.exception)
        self.assertIn("main.rpgle:3", msg)
        self.assertIn("nosuch", msg)
        self.assertIn(str(self.root), msg)

    def test_errors_inside_a_member_name_the_member_and_its_line(self):
        self.put("defs.rpgleinc", "dcl-s ok packed(5);\n\nfoo bar;\n")
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\ndcl-s a packed(1);\n/copy defs\n")
        self.assertTrue(str(cm.exception).startswith("defs.rpgleinc:3:"), str(cm.exception))

    def test_errors_in_the_main_file_after_an_include_still_point_at_the_main_file(self):
        self.put("defs.rpgleinc", "dcl-s ok packed(5);\ndcl-s ok2 packed(5);\n")
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n/copy defs\nfoo bar;\n")
        self.assertTrue(str(cm.exception).startswith("main.rpgle:3:"), str(cm.exception))

    def test_a_file_including_itself_is_a_cycle(self):
        self.put("a.rpgleinc", "/copy b\n")
        self.put("b.rpgleinc", "/copy a\n")
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n/copy a\n")
        self.assertIn("cycle", str(cm.exception))

    def test_including_the_main_file_is_a_cycle(self):
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n/copy main.rpgle\n")
        self.assertIn("cycle", str(cm.exception))

    def test_includes_cannot_escape_the_programs_folder(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "secret.txt").write_text("dcl-s stolen packed(1);\n")
        for directive in (f"/copy '{outside.name}/secret.txt'", "/copy '../" + pathlib.Path(outside.name).name + "/secret.txt'"):
            with self.subTest(directive=directive):
                with self.assertRaises(RpgleError) as cm:
                    self.parse("**free\n" + directive + "\n")
                self.assertIn("refusing", str(cm.exception))

    def test_an_include_folder_widens_what_may_be_read(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "shared.rpgleinc").write_text("dcl-s from_outside packed(1);\n")
        prog = self.parse("**free\n/copy shared\n", include_dirs=[pathlib.Path(outside.name)])
        self.assertEqual([d.name for d in prog.dspecs], ["from_outside"])

    def test_empty_member_name(self):
        with self.assertRaises(RpgleError):
            self.parse("**free\n/copy\n")


class ConditionalTest(IncludeBase):
    GUARDED = "/if defined(DEFS_DONE)\n/eof\n/endif\n/define DEFS_DONE\ndcl-s once packed(5);\n"

    def test_include_guard_lets_a_member_be_included_twice(self):
        self.put("defs.rpgleinc", self.GUARDED)
        prog = self.parse("**free\n/copy defs\n/copy defs\n")
        self.assertEqual([d.name for d in prog.dspecs], ["once"])

    def test_without_a_guard_the_second_include_is_a_real_duplicate(self):
        self.put("defs.rpgleinc", "dcl-ds twice qualified;\n  x packed(1);\nend-ds;\n")
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n/copy defs\n/copy defs\n")
        self.assertIn("twice", str(cm.exception))

    def test_if_else_endif_and_not_defined(self):
        prog = self.parse(
            "**free\n"
            "/define A\n"
            "/if defined(A)\ndcl-s yes_a packed(1);\n/else\ndcl-s no_a packed(1);\n/endif\n"
            "/if not defined(B)\ndcl-s no_b packed(1);\n/endif\n"
            "/if defined(B)\ndcl-s yes_b packed(1);\n/endif\n"
        )
        self.assertEqual([d.name for d in prog.dspecs], ["yes_a", "no_b"])

    def test_elseif_takes_only_the_first_true_branch(self):
        prog = self.parse(
            "**free\n/define B\n/define C\n"
            "/if defined(A)\ndcl-s a packed(1);\n"
            "/elseif defined(B)\ndcl-s b packed(1);\n"
            "/elseif defined(C)\ndcl-s c packed(1);\n"
            "/else\ndcl-s other packed(1);\n/endif\n"
        )
        self.assertEqual([d.name for d in prog.dspecs], ["b"])

    def test_nested_conditions_and_skipped_branches_hide_everything_inside(self):
        prog = self.parse(
            "**free\n"
            "/if defined(NOPE)\n"
            "/copy does_not_exist\n/define LEAK\n"
            "/if defined(LEAK)\ndcl-s leaked packed(1);\n/endif\n"
            "/endif\n"
            "/if not defined(LEAK)\ndcl-s clean packed(1);\n/endif\n"
        )
        self.assertEqual([d.name for d in prog.dspecs], ["clean"])

    def test_undefine(self):
        prog = self.parse("**free\n/define X\n/undefine X\n/if defined(X)\ndcl-s x packed(1);\n/endif\n")
        self.assertEqual(prog.dspecs, [])

    def test_names_are_case_insensitive(self):
        prog = self.parse("**free\n/define Mixed\n/if defined(MIXED)\ndcl-s ok packed(1);\n/endif\n")
        self.assertEqual(len(prog.dspecs), 1)

    def test_defines_from_the_caller(self):
        text = "**free\n/if defined(DEBUG)\ndcl-s dbg packed(1);\n/endif\n"
        self.assertEqual(len(self.parse(text, defines=["debug"]).dspecs), 1)
        self.assertEqual(len(self.parse(text).dspecs), 0)

    def test_eof_only_stops_the_member_it_is_in(self):
        self.put("defs.rpgleinc", "dcl-s early packed(1);\n/eof\ndcl-s never packed(1);\n")
        prog = self.parse("**free\n/copy defs\ndcl-s after packed(1);\n")
        self.assertEqual([d.name for d in prog.dspecs], ["early", "after"])

    def test_errors(self):
        for text, fragment in [
            ("**free\n/if defined(A)\n", "no /ENDIF"),
            ("**free\n/endif\n", "no /IF"),
            ("**free\n/else\n", "no /IF"),
            ("**free\n/elseif defined(A)\n", "no /IF"),
            ("**free\n/if *version >= 7\n/endif\n", "only /IF DEFINED"),
            ("**free\n/define\n", "needs a name"),
        ]:
            with self.subTest(text=text):
                with self.assertRaises(RpgleError) as cm:
                    self.parse(text)
                self.assertIn(fragment, str(cm.exception))

    def test_conditions_must_balance_within_each_member(self):
        self.put("defs.rpgleinc", "/if defined(A)\n")
        with self.assertRaises(RpgleError) as cm:
            self.parse("**free\n/copy defs\n/endif\n")
        self.assertIn("no /ENDIF", str(cm.exception))


class PassThroughTest(IncludeBase):
    def test_sources_without_directives_are_untouched(self):
        src = "**free\ndcl-s a packed(5);\na = 4 / 2;\n"
        self.assertFalse(has_directives(src))
        self.assertEqual(parse_rpgle(src).statements[0].expr, "a = 4 / 2")

    def test_a_slash_at_the_start_of_a_line_is_not_a_directive_unless_it_names_one(self):
        src = "**free\ndcl-s a packed(5);\na = 8\n  /2;\n"
        self.assertFalse(has_directives(src))
        self.assertEqual(parse_rpgle(src).statements[0].expr, "a = 8   /2")

    def test_other_slash_lines_pass_through_and_do_not_break_parsing(self):
        self.put("d.rpgleinc", "dcl-s x packed(1);\n")
        prog = self.parse("**free\n/copy d\n/eject\n/title hello\nx = 1;\n")
        self.assertEqual([s.expr for s in prog.statements], ["x = 1"])

    def test_expand_includes_returns_a_line_map(self):
        p = self.put("m.rpgle", "")
        self.put("d.rpgleinc", "one\ntwo\n")
        text, origin = expand_includes("a\n/copy d\nb\n", path=p)
        lines = text.split("\n")
        self.assertEqual(lines[:4], ["a", "one", "two", ""])  # d.rpgleinc ends in a newline, so it has a blank third line
        self.assertEqual(origin[0], ("m.rpgle", 1))
        self.assertEqual(origin[1:4], [("d.rpgleinc", 1), ("d.rpgleinc", 2), ("d.rpgleinc", 3)])
        self.assertEqual(origin[4], ("m.rpgle", 3))
        self.assertEqual(lines[4], "b")


class CliIncludeTest(IncludeBase):
    def run_cli(self, argv, stdin="\n"):
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    SCREEN = """
     A          R RESULT
     A            MSG           20A  O  1  2
"""

    def program(self):
        self.put("show.dspf", self.SCREEN)
        self.put("inc/screen.rpgleinc", "dcl-f show workstn;\n")
        return self.put(
            "show.rpgle",
            "**free\n/copy screen\n/if defined(LOUD)\nmsg = 'LOUD';\n/else\nmsg = 'quiet';\n/endif\nexfmt result;\nreturn;\n",
        )

    def test_dash_I_finds_members_and_dash_D_sets_conditions(self):
        prog = self.program()
        code, out, err = self.run_cli(["run", str(prog), "-I", str(self.root / "inc")])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("quiet", out)
        code, out, err = self.run_cli(["run", str(prog), "-I", str(self.root / "inc"), "-D", "LOUD"])
        self.assertEqual(code, 0)
        self.assertIn("LOUD", out)

    def test_a_missing_include_is_a_clean_error(self):
        prog = self.program()
        code, out, err = self.run_cli(["run", str(prog)])
        self.assertEqual(code, 1)
        self.assertIn("can't find /COPY member", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
