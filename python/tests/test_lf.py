import unittest

from rpgle import RpgleError
from rpgle.lf import find_pfile, find_pfiles, parse_lf
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

ORDERS_PF = parse_pf(
    "ORDERS",
    dds(
        dds_line("R", "ORDREC"),
        dds_line("", "ORDNO", 5, "S", 0),
        dds_line("", "CUSTNO", 5, "S", 0),
        dds_line("", "AMT", 7, "P", 2),
        dds_line("K", "ORDNO"),
    ),
)


class FindPfileTest(unittest.TestCase):
    def test_finds_the_name(self):
        src = dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"), dds_line("K", "ZIP"))
        self.assertEqual(find_pfile(src), "CUSTMAST")

    def test_no_keyword_means_blank(self):
        self.assertEqual(find_pfile(dds(dds_line("R", "R1"), dds_line("K", "ZIP"))), "")

    def test_a_join_logical_files_jfile_is_not_a_single_pfile(self):
        src = dds(dds_line(keywords="JFILE(CUSTMAST ORDERS)"), dds_line("R", "R1"), dds_line("K", "ORDNO"))
        self.assertEqual(find_pfile(src), "")


class FindPfilesTest(unittest.TestCase):
    def test_pfile_gives_one_name(self):
        src = dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "R1"), dds_line("K", "ZIP"))
        self.assertEqual(find_pfiles(src), ["CUSTMAST"])

    def test_jfile_gives_every_name_primary_first(self):
        src = dds(dds_line(keywords="JFILE(CUSTMAST ORDERS)"), dds_line("R", "R1"), dds_line("K", "ORDNO"))
        self.assertEqual(find_pfiles(src), ["CUSTMAST", "ORDERS"])

    def test_no_keyword_means_empty(self):
        self.assertEqual(find_pfiles(dds(dds_line("R", "R1"), dds_line("K", "ZIP"))), [])


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


class SelectOmitTest(unittest.TestCase):
    def test_a_select_line_requires_a_match(self):
        lf = parse_lf(
            "f",
            dds(
                dds_line(keywords="PFILE(CUSTMAST)"),
                dds_line("R", "R1"),
                dds_line("K", "CUSTNO"),
                dds_line("S", "ZIP", keywords="COMP(EQ '90210')"),
            ),
            CUST_PF,
        )
        self.assertEqual(len(lf.conditions), 1)
        c = lf.conditions[0]
        self.assertEqual((c.field, c.exclude, c.op, c.comp, c.args), ("ZIP", False, "COMP", "EQ", ("'90210'",)))

    def test_an_omit_line_excludes_a_match(self):
        lf = parse_lf(
            "f",
            dds(
                dds_line(keywords="PFILE(CUSTMAST)"),
                dds_line("R", "R1"),
                dds_line("K", "CUSTNO"),
                dds_line("O", "ZIP", keywords="COMP(EQ '90210')"),
            ),
            CUST_PF,
        )
        self.assertTrue(lf.conditions[0].exclude)

    def test_range(self):
        lf = parse_lf(
            "f",
            dds(
                dds_line(keywords="PFILE(CUSTMAST)"),
                dds_line("R", "R1"),
                dds_line("K", "CUSTNO"),
                dds_line("S", "CUSTNO", keywords="RANGE(1 100)"),
            ),
            CUST_PF,
        )
        self.assertEqual(lf.conditions[0].op, "RANGE")
        self.assertEqual(lf.conditions[0].args, ("1", "100"))

    def test_values(self):
        lf = parse_lf(
            "f",
            dds(
                dds_line(keywords="PFILE(CUSTMAST)"),
                dds_line("R", "R1"),
                dds_line("K", "CUSTNO"),
                dds_line("S", "ZIP", keywords="VALUES('90210' '10001')"),
            ),
            CUST_PF,
        )
        self.assertEqual(lf.conditions[0].op, "VALUES")
        self.assertEqual(lf.conditions[0].args, ("'90210'", "'10001'"))

    def test_comp_needs_a_recognized_operator(self):
        src = dds(
            dds_line(keywords="PFILE(CUSTMAST)"),
            dds_line("R", "R1"),
            dds_line("K", "CUSTNO"),
            dds_line("S", "ZIP", keywords="COMP(ZZ 1)"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_select_omit_field_must_be_a_physical_file_field(self):
        src = dds(
            dds_line(keywords="PFILE(CUSTMAST)"),
            dds_line("R", "R1"),
            dds_line("K", "CUSTNO"),
            dds_line("S", "NOPE", keywords="COMP(EQ 1)"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)

    def test_a_select_omit_line_needs_a_comparison_keyword(self):
        src = dds(
            dds_line(keywords="PFILE(CUSTMAST)"),
            dds_line("R", "R1"),
            dds_line("K", "CUSTNO"),
            dds_line("S", "ZIP"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("f", src, CUST_PF)


def _join_dds(*extra_j_lines):
    return dds(
        dds_line(keywords="JFILE(CUSTMAST ORDERS)"),
        dds_line("R", "JOINREC"),
        dds_line("J", keywords="JOIN(1)"),
        dds_line("", keywords="JFLD(CUSTNO CUSTNO)"),
        *extra_j_lines,
        dds_line("K", "ORDNO"),
    )


class JoinLogicalFileTest(unittest.TestCase):
    def test_combines_every_field_of_both_files_joined_fields_shared(self):
        lf = parse_lf("custord", _join_dds(), CUST_PF, {"ORDERS": ORDERS_PF})
        self.assertTrue(lf.is_join)
        self.assertEqual(lf.based_on, "CUSTMAST")
        self.assertEqual([jf.name for jf in lf.joined], ["ORDERS"])
        self.assertEqual(lf.joined[0].on, [("CUSTNO", "CUSTNO")])
        self.assertEqual([f.name for f in lf.fields], ["CUSTNO", "NAME", "ZIP", "ORDNO", "AMT"])
        self.assertEqual(lf.keys, ["ORDNO"])

    def test_jfile_must_match_the_secondaries_given(self):
        with self.assertRaises(RpgleError):
            parse_lf("custord", _join_dds(), CUST_PF, {})

    def test_join_must_be_to_the_primary(self):
        src = dds(
            dds_line(keywords="JFILE(CUSTMAST ORDERS)"),
            dds_line("R", "JOINREC"),
            dds_line("J", keywords="JOIN(2)"),
            dds_line("", keywords="JFLD(CUSTNO CUSTNO)"),
            dds_line("K", "ORDNO"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("custord", src, CUST_PF, {"ORDERS": ORDERS_PF})

    def test_jdupseq_is_rejected_loudly(self):
        src = dds(
            dds_line(keywords="JFILE(CUSTMAST ORDERS)"),
            dds_line("R", "JOINREC"),
            dds_line("J", keywords="JOIN(1) JDUPSEQ(ORDNO)"),
            dds_line("", keywords="JFLD(CUSTNO CUSTNO)"),
            dds_line("K", "ORDNO"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("custord", src, CUST_PF, {"ORDERS": ORDERS_PF})

    def test_jfld_field_must_exist_in_both_files(self):
        src = dds(
            dds_line(keywords="JFILE(CUSTMAST ORDERS)"),
            dds_line("R", "JOINREC"),
            dds_line("J", keywords="JOIN(1)"),
            dds_line("", keywords="JFLD(NOPE CUSTNO)"),
            dds_line("K", "ORDNO"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("custord", src, CUST_PF, {"ORDERS": ORDERS_PF})

    def test_an_unrelated_field_collision_is_an_error(self):
        other = parse_pf(
            "OTHERF",
            dds(
                dds_line("R", "OTHREC"),
                dds_line("", "ORDNO", 5, "S", 0),
                dds_line("", "NAME", 10),  # collides with CUSTMAST's NAME, not a join field
                dds_line("K", "ORDNO"),
            ),
        )
        src = dds(
            dds_line(keywords="JFILE(CUSTMAST OTHERF)"),
            dds_line("R", "JOINREC"),
            dds_line("J", keywords="JOIN(1)"),
            dds_line("", keywords="JFLD(CUSTNO ORDNO)"),
            dds_line("K", "ORDNO"),
        )
        with self.assertRaises(RpgleError):
            parse_lf("custord", src, CUST_PF, {"OTHERF": other})


if __name__ == "__main__":
    unittest.main()
