import unittest

from rpgle import RpgleError, parse_rpgle
from rpgle.pf import parse_pf

from _cols import cspec, dds, dds_line, fspec

CUST_PF = dds(
    dds_line(keywords="UNIQUE"),
    dds_line("R", "CUSTREC"),
    dds_line("", "CUSTNO", 5, "S", 0),
    dds_line("", "NAME", 20),
    dds_line("", "BAL", 9, "P", 2),
    dds_line("K", "CUSTNO"),
)


class PfTest(unittest.TestCase):
    def test_parses_format_fields_and_key(self):
        pf = parse_pf("custmast", CUST_PF)
        self.assertEqual(pf.name, "CUSTMAST")
        self.assertEqual(pf.format, "CUSTREC")
        self.assertEqual([f.name for f in pf.fields], ["CUSTNO", "NAME", "BAL"])
        self.assertEqual([(f.dtype, f.dec) for f in pf.fields], [("N", 0), ("A", 0), ("N", 2)])
        self.assertEqual(pf.keys, ["CUSTNO"])
        self.assertTrue(pf.unique)

    def test_no_key_means_arrival_sequence(self):
        pf = parse_pf("log", dds(dds_line("R", "LOGREC"), dds_line("", "MSG", 40)))
        self.assertEqual(pf.keys, [])
        self.assertFalse(pf.unique)

    def test_descend_is_rejected_loudly(self):
        src = dds(dds_line("R", "R1"), dds_line("", "A", 3), dds_line("K", "A", keywords="DESCEND"))
        with self.assertRaises(RpgleError):
            parse_pf("f", src)

    def test_key_must_be_a_field(self):
        src = dds(dds_line("R", "R1"), dds_line("", "A", 3), dds_line("K", "B"))
        with self.assertRaises(RpgleError):
            parse_pf("f", src)

    def test_missing_record_format(self):
        with self.assertRaises(RpgleError):
            parse_pf("f", "")


class MultiFileFSpecTest(unittest.TestCase):
    def test_collects_every_fspec_and_keeps_workstn_as_file(self):
        src = "\n".join(
            [
                fspec("CUSTMAST", "U", "F", "A", "E", True, "DISK"),
                fspec("INQ", "C", "F", "", "E", False, "WORKSTN"),
                fspec("ORDERS", "I", "F", "", "E", False, "DISK"),
            ]
        )
        prog = parse_rpgle(src)
        self.assertEqual([f.name for f in prog.files], ["CUSTMAST", "INQ", "ORDERS"])
        self.assertEqual(prog.file.name, "INQ")
        cm = prog.files[0]
        self.assertEqual((cm.type, cm.addition, cm.keyed, cm.device), ("U", "A", True, "DISK"))

    def test_result_indicators_read_from_cols_71_to_76(self):
        prog = parse_rpgle(cspec("CHAIN", "custno", "CUSTMAST", hi="71", lo="72", eq="73"))
        s = prog.statements[0]
        self.assertEqual((s.op, s.factor1, s.factor2), ("CHAIN", "custno", "CUSTMAST"))
        self.assertEqual((s.ind_hi, s.ind_lo, s.ind_eq), ("71", "72", "73"))


if __name__ == "__main__":
    unittest.main()
