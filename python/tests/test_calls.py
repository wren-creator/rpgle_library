import pathlib
import shutil
import tempfile
import unittest

from rpgle import CallError, ProgramLoader, Resume, RpgleError, parse_dds, parse_rpgle, run_program
from rpgle.database import Database
from rpgle.loader import find_pf, load_pfs, program_key
from rpgle.pf import parse_pf

PROGRAMS = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"

OUT_PF = "     A          R OUTREC\n     A            MSG           20A\n     A            NUM            9  2\n"

MAIN_HEAD = """**free
dcl-f out usage(*output);
"""


class CallBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name).resolve()
        shutil.copy(PROGRAMS / "custmast.pf", self.root / "custmast.pf")
        (self.root / "out.pf").write_text(OUT_PF)
        self.db = Database()
        self.addCleanup(self.db.close)
        from test_fileio import CUST

        h = self.db.open_file(CUST)
        for no, name, bal in [(10, "ALICE", 100.0), (20, "BOB", 50.5)]:
            h.write({"CUSTNO": no, "NAME": name, "BAL": bal})

    def put(self, rel: str, text: str) -> pathlib.Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def run_main(self, text: str, dirs=None, loader=True, name="main.rpgle"):
        path = self.put(name, text)
        prog = parse_rpgle(text, path=path)
        pfs = load_pfs(prog, [self.root])
        ld = ProgramLoader(dirs or [self.root], main=path) if loader else None
        return list(run_program(prog, parse_dds(""), self.db, pfs, ld))

    def out(self):
        h = self.db.open_file(parse_pf("OUT", OUT_PF))
        h.setll(None)
        rows = []
        while (r := h.read()) is not None:
            rows.append((r["MSG"], r["NUM"]))
        return rows


CUSTNAME = """**free
dcl-pi *n;
  id packed(5) const;
  nm char(20);
end-pi;
dcl-f custmast keyed;
chain id custmast;
if %found(custmast); nm = name; else; nm = 'nobody'; endif;
*inlr = *on;
return;
"""


class LookupAndPassingTest(CallBase):
    def test_callee_is_found_by_name_and_fills_the_callers_variable(self):
        self.put("custname.rpgle", CUSTNAME)
        self.run_main(MAIN_HEAD + """
dcl-pr custname extpgm;
  id packed(5) const;
  nm char(20);
end-pr;
dcl-s who char(20);
custname(20 : who);
msg = who; write outrec;
callp custname(99 : who);
msg = who; write outrec;
""")
        self.assertEqual([m.strip() for m, _ in self.out()], ["BOB", "nobody"])

    def test_extpgm_names_the_program_and_a_library_prefix_is_ignored(self):
        self.put("custname.rpgle", CUSTNAME)
        self.run_main(MAIN_HEAD + """
dcl-pr lookup extpgm('MYLIB/CUSTNAME');
  id packed(5) const;
  nm char(20);
end-pr;
dcl-s who char(20);
lookup(10 : who);
msg = who; write outrec;
""")
        self.assertEqual(self.out()[0][0].strip(), "ALICE")

    def test_the_callee_shares_the_database(self):
        self.put("addcust.rpgle", """**free
dcl-pi *n; no packed(5) const; end-pi;
dcl-f custmast keyed usage(*update:*output);
custno = no; name = 'NEWBIE'; bal = 1;
write custrec;
*inlr = *on;
""")
        self.run_main(MAIN_HEAD + """
dcl-f custmast keyed;
dcl-pr addcust extpgm; no packed(5) const; end-pr;
addcust(77);
chain 77 custmast;
msg = name; write outrec;
""")
        self.assertEqual(self.out()[0][0], "NEWBIE")

    def test_callee_variables_and_indicators_are_its_own(self):
        self.put("sidefx.rpgle", """**free
dcl-pi *n; end-pi;
dcl-s shared packed(5) inz(99);
*in90 = *on;
shared = 1;
*inlr = *on;
""")
        self.run_main(MAIN_HEAD + """
dcl-pr sidefx extpgm; end-pr;
dcl-s shared packed(5) inz(5);
sidefx();
if *in90; msg = 'LEAKED'; else; msg = 'ISOLATED'; endif;
num = shared; write outrec;
write outrec;
""")
        self.assertEqual(self.out()[0][1], 5.0)  # the callee's own `shared`, not ours

    def test_by_reference_by_value_and_const(self):
        self.put("bump.rpgle", """**free
dcl-pi *n;
  r packed(5);
  v packed(5) value;
  c packed(5) const;
end-pi;
r += 10; v += 10;
*inlr = *on;
""")
        self.run_main(MAIN_HEAD + """
dcl-pr bump extpgm; r packed(5); v packed(5) value; c packed(5) const; end-pr;
dcl-s a packed(5) inz(1); dcl-s b packed(5) inz(2);
bump(a : b : 3 + 4);
num = a; write outrec;
num = b; write outrec;
""")
        self.assertEqual([n for _, n in self.out()], [1.0 + 10, 2.0])

    def test_nopass_parameter_defaults(self):
        self.put("opt.rpgle", """**free
dcl-pi *n; a packed(5) const; b packed(5) options(*nopass) const; end-pi;
dcl-f out usage(*output);
num = a + b; write outrec;
*inlr = *on;
""")
        self.run_main(MAIN_HEAD.replace("dcl-f out usage(*output);\n", "") + """
dcl-f out usage(*output);
dcl-pr opt extpgm; a packed(5) const; b packed(5) options(*nopass) const; end-pr;
opt(1); opt(1 : 5);
""")
        self.assertEqual([n for _, n in self.out()], [1.0, 6.0])

    def test_arrays_pass_by_reference(self):
        self.put("fill.rpgle", """**free
dcl-pi *n; a packed(5) dim(3); end-pi;
dcl-s i packed(3);
for i = 1 to %elem(a); a(i) = i * 11; endfor;
*inlr = *on;
""")
        self.run_main(MAIN_HEAD + """
dcl-pr fill extpgm; a packed(5) dim(3); end-pr;
dcl-s n packed(5) dim(3);
fill(n);
num = n(3); write outrec;
""")
        self.assertEqual(self.out()[0][1], 33.0)


class RetainedStateTest(CallBase):
    def counter(self, lr: str) -> str:
        return f"""**free
dcl-pi *n; n packed(5); end-pi;
dcl-s calls packed(5);
calls += 1;
n = calls;
{lr}
return;
"""

    MAIN = MAIN_HEAD + """
dcl-pr count extpgm; n packed(5); end-pr;
dcl-s n packed(5);
count(n); num = n; write outrec;
count(n); num = n; write outrec;
count(n); num = n; write outrec;
"""

    def test_a_program_that_leaves_lr_off_keeps_its_variables_between_calls(self):
        self.put("count.rpgle", self.counter(""))
        self.run_main(self.MAIN)
        self.assertEqual([n for _, n in self.out()], [1.0, 2.0, 3.0])

    def test_a_program_that_sets_lr_on_starts_afresh_each_time(self):
        self.put("count.rpgle", self.counter("*inlr = *on;"))
        self.run_main(self.MAIN)
        self.assertEqual([n for _, n in self.out()], [1.0, 1.0, 1.0])

    def test_a_retained_program_keeps_its_file_position(self):
        self.put("nextcust.rpgle", """**free
dcl-pi *n; nm char(20); end-pi;
dcl-f custmast keyed;
read custmast;
if %eof(custmast); nm = '*end'; else; nm = name; endif;
return;
""")
        self.run_main(MAIN_HEAD + """
dcl-pr nextcust extpgm; nm char(20); end-pr;
dcl-s who char(20);
nextcust(who); msg = who; write outrec;
nextcust(who); msg = who; write outrec;
nextcust(who); msg = who; write outrec;
""")
        self.assertEqual([m.strip() for m, _ in self.out()], ["ALICE", "BOB", "*end"])

    def test_a_program_that_fails_is_not_retained(self):
        self.put("flaky.rpgle", """**free
dcl-pi *n; fail packed(1) const; end-pi;
dcl-s calls packed(5);
calls += 1;
num = calls; write outrec;
if fail = 1; num = 1 / 0; endif;
""")
        main = MAIN_HEAD + """
dcl-f out usage(*output);
dcl-pr flaky extpgm; fail packed(1) const; end-pr;
flaky(0); flaky(0); flaky(1);
"""
        with self.assertRaises(Exception):
            self.run_main(main)


class ErrorTest(CallBase):
    def call(self, body: str, **kw):
        return self.run_main(MAIN_HEAD + body, **kw)

    def test_a_missing_program_says_where_it_looked(self):
        with self.assertRaises(CallError) as cm:
            self.call("dcl-pr ghost extpgm; end-pr;\nghost();\n")
        self.assertIn("GHOST not found", str(cm.exception))
        self.assertIn(str(self.root), str(cm.exception))

    def test_a_program_cannot_call_itself_directly_or_through_another(self):
        self.put("ping.rpgle", "**free\ndcl-pi *n; end-pi;\ndcl-pr pong extpgm; end-pr;\npong();\n")
        self.put("pong.rpgle", "**free\ndcl-pi *n; end-pi;\ndcl-pr ping extpgm; end-pr;\nping();\n")
        with self.assertRaises(CallError) as cm:
            self.call("dcl-pr ping extpgm; end-pr;\nping();\n")
        self.assertIn("already running", str(cm.exception))

    def test_calling_the_main_program_by_name_is_recursion_too(self):
        with self.assertRaises(CallError) as cm:
            self.call("dcl-pr main extpgm; end-pr;\nmain();\n")
        self.assertIn("already running", str(cm.exception))

    def test_wrong_parameters(self):
        self.put("one.rpgle", "**free\ndcl-pi *n; n packed(5); end-pi;\nn += 1;\n*inlr = *on;\n")
        for body, fragment in [
            ("dcl-s t char(5);\none(t);\n", "expects a number"),
            ("one();\n", "missing argument"),
            ("dcl-s a packed(5); dcl-s b packed(5);\none(a : b);\n", "takes 1"),
            ("one(5);\n", "by reference"),
        ]:
            with self.subTest(body=body):
                with self.assertRaises(CallError) as cm:
                    self.call("dcl-pr one extpgm; n packed(5); end-pr;\n" + body)
                self.assertIn(fragment, str(cm.exception))

    def test_a_syntax_error_in_the_callee_names_its_file(self):
        self.put("broken.rpgle", "**free\ndcl-pi *n; end-pi;\nfoo bar;\n")
        with self.assertRaises(RpgleError) as cm:
            self.call("dcl-pr broken extpgm; end-pr;\nbroken();\n")
        self.assertIn("broken.rpgle", str(cm.exception))

    def test_a_program_name_is_a_name_never_a_path(self):
        for bad in ("..", "a b", "x;y", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(CallError):
                    program_key(bad)
        self.assertEqual(program_key("mylib/custname"), "CUSTNAME")
        self.assertEqual(program_key("'Custname '"), "CUSTNAME")

    def test_a_path_cannot_reach_a_program_outside_the_search_folders(self):
        outside = self.root.parent / (self.root.name + "_outside")
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "secret.rpgle").write_text("**free\ndcl-pi *n; end-pi;\n")
        with self.assertRaises(CallError) as cm:
            self.call(f"dcl-pr s extpgm('../{outside.name}/secret'); end-pr;\ns();\n")
        self.assertIn("SECRET not found", str(cm.exception))

    def test_no_loader_is_a_clear_error(self):
        with self.assertRaises(CallError) as cm:
            self.call("dcl-pr one extpgm; end-pr;\none();\n", loader=False)
        self.assertIn("loader", str(cm.exception))

    def test_a_program_cannot_be_called_from_an_expression(self):
        self.put("one.rpgle", "**free\ndcl-pi *n; end-pi;\n*inlr = *on;\n")
        with self.assertRaises(RpgleError) as cm:
            self.call("dcl-pr one extpgm; end-pr;\ndcl-s x packed(1);\nx = one();\n")
        self.assertIn("CALLP", str(cm.exception))

    def test_extproc_is_refused(self):
        with self.assertRaises(RpgleError) as cm:
            self.call("dcl-pr svc extproc('SVC'); end-pr;\nsvc();\n")
        self.assertIn("extproc", str(cm.exception))

    def test_extpgm_needs_a_quoted_name(self):
        with self.assertRaises(RpgleError):
            parse_rpgle("**free\ndcl-pr x extpgm(somevar); end-pr;\n")


class FoldersTest(CallBase):
    def test_the_callers_own_folder_is_searched_first(self):
        self.put("lib/helper.rpgle", "**free\ndcl-pi *n; m char(5); end-pi;\nm = 'lib';\n*inlr = *on;\n")
        self.put("lib/outer.rpgle", "**free\ndcl-pi *n; m char(5); end-pi;\ndcl-pr helper extpgm; m char(5); end-pr;\nhelper(m);\n*inlr = *on;\n")
        self.put("helper.rpgle", "**free\ndcl-pi *n; m char(5); end-pi;\nm = 'root';\n*inlr = *on;\n")
        self.run_main(MAIN_HEAD + """
dcl-pr outer extpgm; m char(5); end-pr;
dcl-pr helper extpgm; m char(5); end-pr;
dcl-s a char(5); dcl-s b char(5);
outer(a); helper(b);
msg = a; write outrec;
msg = b; write outrec;
""", dirs=[self.root / "lib", self.root])
        # outer lives in lib/, so its helper() is lib/helper; main's helper() finds the first folder with one
        self.assertEqual([m.strip() for m, _ in self.out()], ["lib", "lib"])

    def test_the_callee_finds_its_own_display_file_and_screens_reach_the_caller(self):
        self.put("askcust.dspf", "     A          R INQ                       CF03(03)\n     A            CUSTNO         5  0B  3 20\n     A            NAME          20A  O  5 20\n")
        self.put("askcust.rpgle", """**free
dcl-pi *n; end-pi;
dcl-f custmast keyed;
dcl-f askcust workstn;
exfmt inq;
dow not *in03;
  chain custno custmast;
  exfmt inq;
enddo;
*inlr = *on;
""")
        path = self.put("main.rpgle", "**free\ndcl-pr askcust extpgm; end-pr;\naskcust();\nreturn;\n")
        prog = parse_rpgle(path.read_text(), path=path)
        gen = run_program(prog, parse_dds(""), self.db, {}, ProgramLoader([self.root], main=path))
        screen = next(gen)  # the callee's EXFMT reaches whoever is driving the caller
        self.assertEqual(screen.format_name, "INQ")
        screen = gen.send(Resume(key="ENTER", values={"custno": "10"}))
        self.assertIn("ALICE", [f.text.strip() for f in screen.fields])
        with self.assertRaises(StopIteration):
            gen.send(Resume(key="F3", values={}))

    def test_find_pf_error_names_the_expected_file(self):
        with self.assertRaises(RpgleError) as cm:
            find_pf("nosuch", [self.root])
        self.assertIn("nosuch.pf", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
