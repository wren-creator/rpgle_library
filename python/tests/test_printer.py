import unittest

from _cols import pdds_line
from rpgle import RpgleError
from rpgle.prtf import edit_number, parse_prtf
from rpgle.spool import PrinterFile, Spool


def prtf(*lines: str):
    return parse_prtf("RPT", "\n".join(lines) + "\n")


def printer(defs, page_length=66, overflow_line=60) -> PrinterFile:
    return PrinterFile("RPT", defs, page_length, overflow_line)


def put(pf: PrinterFile, fmt: str, **values):
    return pf.write(fmt, lambda n: values.get(n.lower(), 0), lambda num: values.get("_ind_" + num, False))


class ParseTest(unittest.TestCase):
    def test_formats_fields_keywords_and_line_modes(self):
        d = prtf(
            pdds_line("R", "HEAD", keywords="SKIPB(1) SPACEA(2)"),
            pdds_line("", "", line=1, pos=2, keywords="'MY ''REPORT'''"),
            pdds_line("", "PAGE", 4, "S", 0, "", 70, "PAGNBR EDTCDE(Z)"),
            pdds_line("R", "DETAIL", keywords="SKIPA(40)"),
            pdds_line("", "CUSTNO", 5, "S", 0, "+1", 2),
            pdds_line("", "NAME", 20, "", "", 7, 10, ind="N90"),
        )
        head, detail = d.formats["HEAD"], d.formats["DETAIL"]
        self.assertEqual((head.skipb, head.spacea, head.skipa), (1, 2, None))
        self.assertEqual(detail.skipa, 40)
        c, page = head.fields
        self.assertEqual((c.kind, c.text, c.line_mode, c.line, c.col), ("const", "MY 'REPORT'", "abs", 1, 1))
        self.assertEqual((page.name, page.pagnbr, page.edtcde, page.line_mode, page.col), ("PAGE", True, "Z", "same", 69))
        no, name = detail.fields
        self.assertEqual((no.line_mode, no.line, no.dtype), ("rel", 1, "N"))
        self.assertEqual((name.line_mode, name.line, name.indicators[0].not_, name.indicators[0].num), ("abs", 7, True, "90"))

    def test_ignorable_keywords_are_ignored(self):
        d = prtf(pdds_line("R", "R1", keywords="TEXT('x') INDARA"), pdds_line("", "A", 3, "", "", 1, 1, "DSPATR(HI) COLHDG('A')"))
        self.assertEqual(len(d.formats["R1"].fields), 1)

    def test_errors(self):
        for lines, fragment in [
            ([pdds_line("R", "R1", keywords="SPACEA(0)")], "overprints"),
            ([pdds_line("R", "R1", keywords="SKIPB(2) SPACEB(1)")], "ambiguous"),
            ([pdds_line("R", "R1", keywords="SKIPB")], "needs a whole number"),
            ([pdds_line("R", "R1", keywords="BOGUS(1)")], "BOGUS"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 3, "", "", 1, 1, "DATE")], "DATE"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 3, "", "", 1, 1, "EDTWRD('0 ')")], "EDTWRD"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 5, "S", 0, 1, 1, "EDTCDE(Y)")], "EDTCDE(Y)"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 5, "S", 0, 1, 1, "EDTCDE(J *)")], "fill"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 3, "", "", 1, "")], "position"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 3, "", "", "x", 1)], "not a line number"),
            ([pdds_line("R", "R1"), pdds_line("", "A", 3, "", "", 1, 1, "PAGNBR")], "numeric"),
            ([pdds_line("R", "R1"), pdds_line("R", "R1")], "twice"),
            ([pdds_line("R", "R1"), pdds_line("", "A", "", "", "", 1, 1)], "length"),
            ([], "no record formats"),
        ]:
            with self.subTest(fragment=fragment):
                with self.assertRaises(RpgleError) as cm:
                    prtf(*lines)
                self.assertIn(fragment, str(cm.exception))


class EditCodeTest(unittest.TestCase):
    def test_table(self):
        cases = [
            ((1250, 9, 2, "1"), "    1,250.00"),
            ((1250, 9, 2, "3"), "   1250.00"),  # 7 digits + point + 2 = 10 wide
            ((1250, 9, 2, "J"), "    1,250.00 "),
            ((-1250.5, 9, 2, "J"), "    1,250.50-"),
            ((-1250.5, 9, 2, "L"), "   1250.50-"),  # 10 + the sign column = 11 wide
            ((-1250.5, 9, 2, "A"), "    1,250.50CR"),
            ((-1250.5, 9, 2, "C"), "   1250.50CR"),  # 10 + CR = 12 wide
            ((-1250.5, 9, 2, "1"), "    1,250.50"),  # codes 1-4 drop the sign
            ((0, 9, 2, "J"), "        0.00 "),
            ((0, 9, 2, "1"), "        0.00"),
            ((0, 9, 2, "K"), " " * 13),
            ((0, 9, 2, "2"), " " * 12),
            ((0, 5, 0, "Z"), " " * 5),
            ((7, 5, 0, "Z"), "    7"),
            ((1234567.891, 9, 2, "2"), "1,234,567.89"),
            ((12, 5, 0, None), "00012"),
            ((5, 7, 2, None), "0000500"),
            ((0.5, 5, 2, "J"), "  0.50 "),  # 3 digits + point + 2 + sign column = 7 wide
        ]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(edit_number(*args), want)

    def test_the_width_is_the_same_for_every_value(self):
        widths = {len(edit_number(v, 9, 2, "J")) for v in (0, 1, -1, 12.5, 1234567.89, -1234567.89)}
        self.assertEqual(len(widths), 1)

    def test_a_negative_number_with_no_edit_code_is_an_error_not_a_lost_sign(self):
        with self.assertRaises(RpgleError):
            edit_number(-5, 5, 0, None)


class LayoutTest(unittest.TestCase):
    D = [
        pdds_line("R", "HEAD", keywords="SKIPB(1) SPACEA(2)"),
        pdds_line("", "", "", "", "", 1, 1, "'REPORT'"),
        pdds_line("", "PAGE", 3, "S", 0, "", 20, "PAGNBR EDTCDE(Z)"),
        pdds_line("R", "DETAIL"),
        pdds_line("", "NO", 3, "S", 0, "", 1, "EDTCDE(Z)"),
        pdds_line("", "NAME", 8, "", "", "", 6),
        pdds_line("R", "SPACED", keywords="SPACEB(3) SPACEA(2)"),
        pdds_line("", "NAME", 8, "", "", "", 1),
        pdds_line("R", "TWOLINE"),
        pdds_line("", "NAME", 8, "", "", "", 1),
        pdds_line("", "NO", 3, "S", 0, "+1", 3, "EDTCDE(Z)"),
        pdds_line("R", "ABS"),
        pdds_line("", "", "", "", "", 5, 1, "'AT FIVE'"),
        pdds_line("R", "COND"),
        pdds_line("", "", "", "", "", "", 1, "'ALWAYS'"),
        pdds_line("", "", "", "", "", "", 10, "'ONLY IF 90'", ind=" 90"),
        pdds_line("", "", "", "", "", "", 25, "'ONLY IF NOT 90'", ind="N90"),
        pdds_line("R", "SKIPAFTER", keywords="SKIPA(10)"),
        pdds_line("", "", "", "", "", "", 1, "'X'"),
        pdds_line("R", "OVERLAY"),
        pdds_line("", "", "", "", "", "", 1, "'AAAAAAAA'"),
        pdds_line("", "", "", "", "", "", 4, "'bb'"),
    ]

    def pf(self, **kw):
        return printer(prtf(*self.D), **kw)

    def lines(self, pf):
        return pf.text().split("\n")

    def test_records_print_on_consecutive_lines_by_default(self):
        pf = self.pf()
        put(pf, "DETAIL", no=1, name="ONE")
        put(pf, "DETAIL", no=2, name="TWO")
        self.assertEqual(self.lines(pf)[:3], ["  1  ONE", "  2  TWO", ""])

    def test_skipb_at_the_top_and_spacea_leave_a_blank_line(self):
        pf = self.pf()
        put(pf, "HEAD")
        put(pf, "DETAIL", no=1, name="ONE")
        # no page was wasted before the heading; SPACEA(2) leaves one blank line under it
        self.assertEqual(self.lines(pf)[:4], ["REPORT               1", "", "  1  ONE", ""])

    def test_skipb_ejects_a_page_when_the_current_one_has_text(self):
        pf = self.pf()
        put(pf, "HEAD")
        put(pf, "DETAIL", no=1, name="ONE")
        put(pf, "HEAD")
        put(pf, "DETAIL", no=2, name="TWO")
        self.assertEqual(
            self.lines(pf),
            ["REPORT               1", "", "  1  ONE", "\f", "REPORT               2", "", "  2  TWO", ""],
        )

    def test_spaceb_and_spacea(self):
        pf = self.pf()
        put(pf, "DETAIL", no=1, name="A")
        put(pf, "SPACED", name="B")  # SPACEB(3): two blank lines before
        put(pf, "DETAIL", no=2, name="C")  # SPACEA(2) on SPACED left one blank line after it
        self.assertEqual(self.lines(pf)[:7], ["  1  A", "", "", "B", "", "  2  C", ""])

    def test_relative_lines_within_a_record(self):
        pf = self.pf()
        put(pf, "DETAIL", no=1, name="A")
        put(pf, "TWOLINE", name="B", no=7)  # NAME on the next line, NO one line below that
        self.assertEqual(self.lines(pf)[:4], ["  1  A", "B", "    7", ""])

    def test_absolute_lines_and_the_eject_when_already_past_them(self):
        pf = self.pf()
        put(pf, "ABS")  # page line 5
        self.assertEqual(self.lines(pf)[:5], ["", "", "", "", "AT FIVE"])
        put(pf, "ABS")  # line 5 again: already used, so a new page
        self.assertEqual(self.lines(pf).count("\f"), 1)

    def test_conditioned_fields_follow_their_indicator(self):
        pf = self.pf()
        put(pf, "COND")
        put(pf, "COND", _ind_90=True)
        self.assertEqual(self.lines(pf)[:2], ["ALWAYS".ljust(24) + "ONLY IF NOT 90", "ALWAYS".ljust(9) + "ONLY IF 90"])

    def test_skipa_moves_the_next_record_to_that_line(self):
        pf = self.pf()
        put(pf, "SKIPAFTER")
        put(pf, "DETAIL", no=1, name="A")
        self.assertEqual(self.lines(pf)[0], "X")
        self.assertEqual(self.lines(pf)[9], "  1  A")

    def test_skipa_to_a_line_already_passed_starts_a_new_page(self):
        pf = self.pf()
        for _ in range(12):
            put(pf, "DETAIL", no=1, name="A")
        put(pf, "SKIPAFTER")  # prints on line 13, then goes to line 10: a new page
        put(pf, "DETAIL", no=9, name="B")
        text = pf.text().split("\f\n")
        self.assertEqual(len(text), 2)
        self.assertTrue(text[1].split("\n")[9].startswith("  9  B"))

    def test_fields_overlay_on_the_same_line(self):
        pf = self.pf()
        put(pf, "OVERLAY")
        self.assertEqual(self.lines(pf)[0], "AAAbbAAA")

    def test_page_numbers_count_pages(self):
        pf = self.pf()
        for _ in range(3):
            put(pf, "HEAD")
        self.assertEqual([l for l in self.lines(pf) if l.startswith("REPORT")], ["REPORT               1", "REPORT               2", "REPORT               3"])

    def test_a_full_page_starts_a_new_one_and_the_overflow_line_is_reported(self):
        pf = self.pf(page_length=6, overflow_line=5)
        flags = [put(pf, "DETAIL", no=i, name="L") for i in range(1, 8)]
        # on from line 5 (reached) through line 6 (the last on the page); record 7 is on a new page
        self.assertEqual(flags, [False, False, False, False, True, True, False])
        self.assertEqual(pf.text().count("\f"), 1)

    def test_a_record_taller_than_the_page_is_an_error(self):
        pf = self.pf(page_length=3)
        with self.assertRaises(RpgleError) as cm:
            put(pf, "ABS")
        self.assertIn("doesn't fit", str(cm.exception))

    def test_character_fields_are_padded_and_cut_to_length(self):
        pf = self.pf()
        put(pf, "DETAIL", no=1, name="TOOLONGNAME")
        self.assertEqual(self.lines(pf)[0], "  1  TOOLONGN")

    def test_unknown_format(self):
        with self.assertRaises(RpgleError):
            put(self.pf(), "NOSUCH")

    def test_an_empty_printer_file_produces_no_output(self):
        self.assertEqual(self.pf().text(), "")


class SpoolTest(unittest.TestCase):
    def test_overflow_line_must_fit_the_page(self):
        with self.assertRaises(RpgleError):
            Spool(page_length=10, overflow_line=11)

    def test_two_opens_of_one_file_get_separate_numbered_outputs(self):
        d = prtf(pdds_line("R", "R1"), pdds_line("", "", "", "", "", 1, 1, "'hello'"))
        sp = Spool()
        for _ in range(2):
            put(sp.open("RPT", d), "R1")
        self.assertEqual([n for n, _ in sp.outputs()], ["rpt", "rpt-2"])

    def test_files_that_printed_nothing_are_left_out(self):
        d = prtf(pdds_line("R", "R1"), pdds_line("", "", "", "", "", 1, 1, "'x'"))
        sp = Spool()
        sp.open("QUIET", d)
        put(sp.open("LOUD", d), "R1")
        self.assertEqual([n for n, _ in sp.outputs()], ["loud"])


if __name__ == "__main__":
    unittest.main()
