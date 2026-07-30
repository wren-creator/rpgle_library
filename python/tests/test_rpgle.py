import unittest

from rpgle import parse_rpgle

from _fixtures import read_adventure_rpgle


class TestParseRpgle(unittest.TestCase):
    def test_reads_f_and_d_and_c_specs(self):
        prog = parse_rpgle(read_adventure_rpgle())

        self.assertEqual(prog.file.name, "SCREEN")
        self.assertEqual(prog.file.device, "WORKSTN")
        self.assertEqual(prog.file.format, "E")

        dnames = sorted(d.name for d in prog.dspecs)
        self.assertEqual(dnames, ["dmg", "killcnt", "loc", "quot", "roll", "seed"])

        self.assertGreater(len(prog.statements), 100)
        self.assertTrue(any(s.op == "DIV" for s in prog.statements))
        self.assertTrue(any(s.op == "MVR" for s in prog.statements))


if __name__ == "__main__":
    unittest.main()
