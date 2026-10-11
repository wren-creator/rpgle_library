"""A program is code you were handed, so it must not be able to reach files
outside its own folder (and any -I folders you gave). Each test here is one
way in that used to be, or could be, open.
"""

import os
import pathlib
import tempfile
import unittest

from rpgle import ProgramLoader, RpgleError, Spool, parse_dds, parse_rpgle, run_program
from rpgle.loader import find_dds, find_pf, load_pfs, program_key

PRTF = "     A          R SECRETFMT\n     A                                  1  2'LEAKED'\n"


class FileNameTraversalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.prog_dir = self.base / "prog"
        self.out_dir = self.base / "outside"
        self.prog_dir.mkdir()
        self.out_dir.mkdir()
        (self.out_dir / "leak.prtf").write_text(PRTF)
        (self.out_dir / "leak.pf").write_text("     A          R R\n     A            X              3A\n")
        (self.out_dir / "leak.dspf").write_text("     A          R R\n     A                                  1  2'LEAKED'\n")

    BAD = ["../outside/leak", "..", "a/b", "a\\b", "leak.prtf", "x y", "", "/abs/path", "a;b"]

    def test_free_form_dcl_f_rejects_names_that_are_not_object_names(self):
        for bad in self.BAD:
            with self.subTest(bad=bad):
                with self.assertRaises(RpgleError):
                    parse_rpgle(f"**free\ndcl-f {bad} printer;\n" if bad else "**free\ndcl-f ;\n")

    def test_the_original_exploit_no_longer_reads_outside_the_program_folder(self):
        src = "**free\ndcl-f ../outside/leak printer;\nwrite secretfmt;\n*inlr = *on;\n"
        path = self.prog_dir / "p.rpgle"
        path.write_text(src)
        with self.assertRaises(RpgleError):
            prog = parse_rpgle(src, path=path)
            list(run_program(prog, parse_dds(""), None, {}, None, Spool(directories=[self.prog_dir])))

    def test_fixed_form_f_spec_names_are_checked_too(self):
        for bad in ("../x", "a/b", "a.b"):
            with self.subTest(bad=bad):
                line = "     F" + bad.ljust(10) + " O    E             PRINTER"
                with self.assertRaises(RpgleError):
                    parse_rpgle(line)

    def test_the_finders_refuse_bad_names_even_if_the_parser_were_bypassed(self):
        with self.assertRaises(RpgleError):
            find_pf("../outside/leak", [self.prog_dir])
        from rpgle.rpgle import FileSpec, Program

        prog = Program(files=[FileSpec(name="../outside/leak", device="PRINTER", format="E")])
        with self.assertRaises(RpgleError):
            Spool(directories=[self.prog_dir]).load_defs(prog)
        prog = Program(files=[FileSpec(name="../outside/leak", device="DISK", format="E")])
        with self.assertRaises(RpgleError):
            load_pfs(prog, [self.prog_dir])
        prog = Program()
        prog.file.name = "../outside/leak"
        # a bad display file name is skipped, never turned into a path
        self.assertEqual(find_dds(prog, self.prog_dir / "p.rpgle", [self.prog_dir]).formats, {})

    def test_ordinary_names_still_work(self):
        for good in ("custmast", "CUSTMAST", "cust_mast", "CUST$1", "#X", "@Y", "a1"):
            with self.subTest(good=good):
                self.assertEqual(parse_rpgle(f"**free\ndcl-f {good} workstn;\n").files[0].name, good)


class IncludeAndCallTraversalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.prog_dir = self.base / "prog"
        self.out_dir = self.base / "outside"
        self.prog_dir.mkdir()
        self.out_dir.mkdir()
        (self.out_dir / "secret.rpgleinc").write_text("dcl-s stolen packed(1);\n")
        (self.out_dir / "secret.rpgle").write_text("**free\ndcl-pi *n; end-pi;\n")

    def test_a_symlink_inside_the_program_folder_cannot_smuggle_an_include_out(self):
        link = self.prog_dir / "innocent.rpgleinc"
        try:
            os.symlink(self.out_dir / "secret.rpgleinc", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available here")
        path = self.prog_dir / "p.rpgle"
        text = "**free\n/copy innocent\n"
        path.write_text(text)
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle(text, path=path)
        self.assertIn("refusing", str(cm.exception))

    def test_absolute_and_dotdot_includes_are_refused(self):
        path = self.prog_dir / "p.rpgle"
        for directive in (f"/copy '{self.out_dir}/secret.rpgleinc'", "/copy '../outside/secret.rpgleinc'"):
            with self.subTest(directive=directive):
                text = f"**free\n{directive}\n"
                path.write_text(text)
                with self.assertRaises(RpgleError):
                    parse_rpgle(text, path=path)

    def test_program_names_are_never_paths(self):
        for bad in ("../outside/secret", "/etc/passwd", "a b", "x;y", "..", ""):
            with self.subTest(bad=bad):
                try:
                    key = program_key(bad)
                except RpgleError:
                    continue
                # a path is reduced to its last component, which then has to be found in the search folders only
                loader = ProgramLoader([self.prog_dir])
                with self.assertRaises(RpgleError):
                    loader.load(key, None)

    def test_a_program_in_a_sibling_folder_is_not_found_by_walking_up(self):
        loader = ProgramLoader([self.prog_dir])
        with self.assertRaises(RpgleError):
            loader.load(program_key("../outside/secret"), None)


if __name__ == "__main__":
    unittest.main()
