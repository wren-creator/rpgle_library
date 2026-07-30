import re
import unittest

from rpgle import Resume, Statement, build_vars, compile_program, parse_dds, parse_rpgle, run_program

from _fixtures import read_adventure_dds, read_adventure_rpgle

_NUMERIC_RE = re.compile(r"^[0-9]+$")


def parse_adventure():
    dds = parse_dds(read_adventure_dds())
    prog = parse_rpgle(read_adventure_rpgle())
    return prog, dds


class TestBuildVarsBindsDdsAndDSpecFields(unittest.TestCase):
    def test_binds_fields(self):
        prog, dds = parse_adventure()
        variables = build_vars(prog, dds)

        plrhp = variables["plrhp"]
        self.assertEqual(plrhp.type, "N")
        self.assertEqual(plrhp.length, 3)
        self.assertEqual(plrhp.value, 0)

        loctxt = variables["loctxt"]
        self.assertEqual(loctxt.type, "A")
        self.assertEqual(loctxt.length, 30)

        self.assertEqual(variables["seed"].value, 17)
        self.assertEqual(variables["loc"].value, 1)
        self.assertEqual(variables["killcnt"].value, 0)


class TestCompileBackpatchesIfElseEndif(unittest.TestCase):
    def test_if_else_endif(self):
        statements = [
            Statement(op="IF", expr="1 = 1"),
            Statement(op="EVAL", expr="a = 1"),
            Statement(op="ELSE"),
            Statement(op="EVAL", expr="a = 2"),
            Statement(op="ENDIF"),
        ]
        compiled = compile_program(statements)
        self.assertEqual(compiled.instrs[0].op, "IF")
        self.assertEqual(compiled.instrs[0].false_target, 3)
        self.assertEqual(compiled.instrs[2].op, "JMP")
        self.assertEqual(compiled.instrs[2].target, 4)
        self.assertEqual(compiled.instrs[4].op, "NOP")


class TestCompileBackpatchesDowEnddoLeave(unittest.TestCase):
    def test_dow_enddo_leave(self):
        statements = [
            Statement(op="DOW", expr="1 = 1"),
            Statement(op="LEAVE"),
            Statement(op="ENDDO"),
        ]
        compiled = compile_program(statements)
        self.assertEqual(compiled.instrs[0].false_target, 3)
        self.assertEqual(compiled.instrs[1].target, 3)
        self.assertEqual(compiled.instrs[2].target, 0)


class TestCompileRecordsSubroutineEntryPoints(unittest.TestCase):
    def test_subroutine_entry_points(self):
        statements = [
            Statement(op="BEGSR", factor1="MYSUB"),
            Statement(op="EVAL", expr="a = 1"),
            Statement(op="ENDSR"),
        ]
        compiled = compile_program(statements)
        self.assertEqual(compiled.subroutines["MYSUB"], 0)


class TestAdventureFirstScreen(unittest.TestCase):
    def test_first_screen(self):
        prog, dds = parse_adventure()
        gen = run_program(prog, dds)
        screen = next(gen)

        self.assertEqual(screen.format_name, "MAIN")
        by_row = {f.row: f.text for f in screen.fields}
        self.assertEqual(by_row[2].strip(), "a dark forest")
        self.assertEqual(by_row[4], "020")
        self.assertNotIn(6, by_row)  # MSGTXT hidden until *IN90 is set


class TestAdventureF3AtMainMenuEndsImmediately(unittest.TestCase):
    def test_f3_ends_program(self):
        prog, dds = parse_adventure()
        gen = run_program(prog, dds)
        next(gen)
        with self.assertRaises(StopIteration):
            gen.send(Resume(key="F3", values={"cmd": ""}))


class TestAdventureAlwaysFightPlaythroughStaysInBoundsAndEnds(unittest.TestCase):
    def test_playthrough(self):
        prog, dds = parse_adventure()
        gen = run_program(prog, dds)
        screen = next(gen)

        saw_combat = saw_endgame = False
        steps = 0
        done = False
        while steps < 500:
            self.assertIn(screen.format_name, ("MAIN", "COMBAT", "ENDGAME"))
            if screen.format_name == "COMBAT":
                saw_combat = True
            if screen.format_name == "ENDGAME":
                saw_endgame = True
            cmd = "M" if screen.format_name == "MAIN" else "F"
            try:
                screen = gen.send(Resume(key="ENTER", values={"cmd": cmd}))
            except StopIteration:
                done = True
                break
            for f in screen.fields:
                trimmed = f.text.strip()
                if _NUMERIC_RE.match(trimmed) and len(f.text) > 6:
                    self.fail(f"numeric field text {f.text!r} exceeds declared width")
            steps += 1

        self.assertTrue(saw_combat, "expected at least one encounter over 500 steps")
        self.assertTrue(saw_endgame, "expected the playthrough to reach an ending over 500 steps")
        self.assertTrue(done, "playthrough should terminate well within the step budget")


if __name__ == "__main__":
    unittest.main()
