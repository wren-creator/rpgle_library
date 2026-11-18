import unittest

from rpgle import RpgleError
from rpgle.lf import find_pfile, parse_lf
from rpgle.pf import parse_pf

from _cols import dds, dds_line

CUST_PF = parse_pf(
    "CUSTMAST",
    dds(
        dds_line(keywords="UNIQUE"),
        dds_line("R", "CUSTREC"),
        dds_line("", "CUSTNO", 5, "S", 0),
        dds_line("", "NAME", 20),
        dds_line("", "ZIP", 5),
        dds_line("K", "CUSTNO"),
    ),
)


class FindPfileTest(unittest.TestCase):
    def test_finds_the_name(self):
        src = dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"), dds_line("K", "ZIP"))
        self.assertEqual(find_pfile(src), "CUSTMAST")

    def test_no_keyword_means_blank(self):
        self.assertEqual(find_pfile(dds(dds_line("R", "R1"), dds_line("K", "ZIP"))), "")


class ParseLfTest(unittest.TestCase):
    def test_re_keys_over_every_physical_file_field_in_order(self):
        lf = parse_lf(
            "custbyzip",
            dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "CUSTREC"), dds_line("K", "ZIP")),
            CUST_PF,
        )
        self.assertEqual(lf.name, "CUSTBYZIP")
        self.assertEqual(lf.based_on, "CUSTMAST")
        self.assertEqual(lf.format, "CUSTREC")
        self.assertEqual([f.name for f in lf.fields], ["CUSTNO", "NAME", "ZIP"])
        self.assertEqual(lf.keys, ["ZIP"])
        self.assertFalse(lf.unique)

    def test_unique_keyword(self):
        lf = parse_lf(
            "custbyzip",
            dds(dds_line(keywords="PFILE(CUSTMAST) UNIQUE"), dds_line("R", "R1"), dds_line("K", "ZIP")),
            CUST_PF,
        )
        self.assertTrue(lf.unique)

    def test_multiple_key_fields_in_order(self):
        lf = parse_lf(
            "custbyname",
            dds(
                dds_line(keywords="PFILE(CUSTMAST)"),
                dds_line("R", "R1"),
                dds_line("K", "ZIP"),
                dds_line("K", "NAME"),
            ),
            CUST_PF,
        )
        self.assertEqual(lf.keys, ["ZIP", "NAME"])

    def test_pfile_must_match_the_given_physical_file(self):
        src = dds(dds_line(keywords="PFILE(ORDERS)"), dds_line("R", "R1"), dds_line("K", "CUSTNO"))
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_missing_pfile_keyword(self):
        src = dds(dds_line("R", "R1"), dds_line("K", "CUSTNO"))
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_missing_record_format(self):
        with self.assertRaises(RpgleError):
            parse_lf("f", dds(dds_line(keywords="PFILE(CUSTMAST)")), CUST_PF)

    def test_needs_at_least_one_key(self):
        src = dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"))
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_key_must_be_a_physical_file_field(self):
        src = dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"), dds_line("K", "NOPE"))
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_descend_is_rejected_loudly(self):
        src = dds(
            dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"), dds_line("K", "CUSTNO", keywords="DESCEND")
        )
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_field_selection_is_not_supported(self):
        src = dds(
            dds_line(keywords="PFILE(CUSTMAST)"),
            dds_line("R", "R1"),
            dds_line("", "NAME"),
            dds_line("K", "CUSTNO"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)


if __name__ == "__main__":
    unittest.main()
