import pathlib
import shutil
import tempfile
import unittest

from _cols import cspec, fspec, pdds_line
from rpgle import ProgramLoader, RpgleError, parse_dds, parse_rpgle, run_program
from rpgle.loader import load_pfs
from rpgle.prtf import parse_prtf
from rpgle.spool import Spool
from test_calls import CallBase

PROGRAMS = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"

REPORT_DDS = "\n".join(
    [
        pdds_line("R", "HEADING", keywords="SKIPB(1) SPACEA(2)"),
        pdds_line("", "", "", "", "", 1, 2, "'CUSTOMER LIST'"),
        pdds_line("", "PAGE", 3, "S", 0, "", 40, "PAGNBR EDTCDE(Z)"),
        pdds_line("R", "DETAIL"),
        pdds_line("", "CUSTNO", 5, "S", 0, "", 2, "EDTCDE(Z)"),
        pdds_line("", "NAME", 20, "", "", "", 10),
        pdds_line("", "BAL", 9, "S", 2, "", 32, "EDTCDE(J)"),
        pdds_line("R", "TOTAL", keywords="SPACEB(2)"),
        pdds_line("", "", "", "", "", "", 2, "'TOTAL'"),
        pdds_line("", "TOT", 9, "S", 2, "", 32, "EDTCDE(J)"),
    ]
) + "\n"

REPORT = """**free
dcl-f custmast keyed;
dcl-f rpt printer oflind(*inof);
dcl-s tot packed(9:2);

write heading;
setll *loval custmast;
read custmast;
dow not %eof(custmast);
  if *inof;
    write heading;
    *inof = *off;
  endif;
  tot += bal;
  write detail;
  read custmast;
enddo;
write total;
*inlr = *on;
return;
"""


class PrinterProgramTest(CallBase):
    def setUp(self):
        super().setUp()
        from test_fileio import CUST

        h = self.db.open_file(CUST)
        for i in range(30, 50, 10):
            h.write({"CUSTNO": i, "NAME": f"CUSTOMER {i}", "BAL": float(i)})
        self.defs = {"RPT": parse_prtf("RPT", REPORT_DDS)}

    def report(self, text=REPORT, page_length=66, overflow_line=60, name="rep.rpgle"):
        path = self.put(name, text)
        prog = parse_rpgle(text, path=path)
        spool = Spool(page_length=page_length, overflow_line=overflow_line, defs=self.defs)
        list(run_program(prog, parse_dds(""), self.db, load_pfs(prog, [self.root]), None, spool))
        return spool

    def test_a_report_prints_headings_detail_and_a_total(self):
        out = dict(self.report().outputs())["rpt"].split("\n")
        self.assertEqual(out[0], " CUSTOMER LIST" + " " * 25 + "  1")  # PAGNBR in column 40
        self.assertEqual(out[1], "")  # SPACEA(2) leaves a blank line under the heading
        self.assertTrue(out[2].startswith("    10   ALICE"), out[2])
        self.assertTrue(out[2].rstrip().endswith("100.00"), out[2])
        self.assertTrue(out[3].startswith("    20   BOB"), out[3])
        self.assertTrue(out[3].rstrip().endswith("50.50"), out[3])
        total = [l for l in out if "TOTAL" in l]
        self.assertEqual(len(total), 1)
        self.assertTrue(total[0].rstrip().endswith("220.50"), total[0])  # 100 + 50.5 + 30 + 40
        self.assertEqual(out[out.index(total[0]) - 1], "")  # SPACEB(2): one blank line before it

    def test_the_overflow_indicator_drives_page_breaks(self):
        pages = dict(self.report(page_length=6, overflow_line=5).outputs())["rpt"].split("\n\f\n")
        self.assertEqual(len(pages), 2)
        self.assertTrue(pages[0].split("\n")[0].rstrip().endswith("1"))
        self.assertTrue(pages[1].split("\n")[0].rstrip().endswith("2"))  # the second heading is numbered 2
        for page in pages:
            self.assertLessEqual(len(page.rstrip("\n").split("\n")), 6)

    def test_every_customer_is_printed_exactly_once(self):
        text = dict(self.report(page_length=6, overflow_line=5).outputs())["rpt"]
        for name in ("ALICE", "BOB", "CUSTOMER 30", "CUSTOMER 40"):
            self.assertEqual(text.count(name), 1, name)

    def test_printer_files_are_output_only(self):
        for bad in ("read rpt;", "chain 1 rpt;", "setll *loval rpt;", "delete rpt;"):
            with self.subTest(bad=bad):
                with self.assertRaises(RpgleError) as cm:
                    self.report("**free\ndcl-f rpt printer;\n" + bad + "\n")
                self.assertIn("only WRITE", str(cm.exception))

    def test_write_needs_a_record_format_not_the_file(self):
        with self.assertRaises(RpgleError) as cm:
            self.report("**free\ndcl-f rpt printer;\nwrite rpt;\n")
        self.assertIn("record format", str(cm.exception))

    def test_no_spool_is_a_clear_error(self):
        path = self.put("p.rpgle", "**free\ndcl-f rpt printer;\nwrite heading;\n")
        prog = parse_rpgle(path.read_text(), path=path)
        with self.assertRaises(RpgleError) as cm:
            list(run_program(prog, parse_dds("")))
        self.assertIn("spool", str(cm.exception))

    def test_the_dds_is_found_as_a_prtf_file_beside_the_program(self):
        self.put("rpt.prtf", REPORT_DDS)
        path = self.put("p.rpgle", "**free\ndcl-f rpt printer;\nwrite heading;\n*inlr = *on;\n")
        prog = parse_rpgle(path.read_text(), path=path)
        spool = Spool(directories=[self.root])
        list(run_program(prog, parse_dds(""), self.db, {}, None, spool))
        self.assertIn("CUSTOMER LIST", dict(spool.outputs())["rpt"])

    def test_a_missing_prtf_says_what_it_expected(self):
        path = self.put("p.rpgle", "**free\ndcl-f nosuch printer;\nwrite x;\n")
        prog = parse_rpgle(path.read_text(), path=path)
        with self.assertRaises(RpgleError) as cm:
            list(run_program(prog, parse_dds(""), self.db, {}, None, Spool(directories=[self.root])))
        self.assertIn("nosuch.prtf", str(cm.exception))

    def test_overflow_indicator_can_be_an_ind_variable(self):
        text = REPORT.replace("oflind(*inof)", "oflind(full)").replace("*inof", "full").replace("dcl-s tot", "dcl-s full ind;\ndcl-s tot")
        spool = self.report(text, page_length=6, overflow_line=5)
        self.assertEqual(len(dict(spool.outputs())["rpt"].split("\n\f\n")), 2)

    def test_a_program_that_never_resets_the_indicator_reprints_headings_every_record(self):
        from test_fileio import CUST

        h = self.db.open_file(CUST)
        for i in range(50, 120, 10):
            h.write({"CUSTNO": i, "NAME": f"CUSTOMER {i}", "BAL": float(i)})  # eleven customers in all
        kept = len(dict(self.report(page_length=6, overflow_line=5).outputs())["rpt"].split("\n\f\n"))
        text = REPORT.replace("    *inof = *off;\n", "")
        stuck = len(dict(self.report(text, page_length=6, overflow_line=5, name="stuck.rpgle").outputs())["rpt"].split("\n\f\n"))
        self.assertGreater(stuck, kept)  # once on, every record starts a new page

    def test_a_callee_prints_to_its_own_spool_file(self):
        self.put("rpt.prtf", REPORT_DDS)
        self.put("printer.rpgle", "**free\ndcl-pi *n; end-pi;\ndcl-f rpt printer;\nwrite heading;\n*inlr = *on;\n")
        path = self.put("main.rpgle", "**free\ndcl-f rpt printer;\ndcl-pr printer extpgm; end-pr;\nwrite heading;\nprinter();\nprinter();\n")
        prog = parse_rpgle(path.read_text(), path=path)
        spool = Spool(directories=[self.root])
        list(run_program(prog, parse_dds(""), self.db, {}, ProgramLoader([self.root], main=path), spool))
        self.assertEqual([n for n, _ in spool.outputs()], ["rpt", "rpt-2", "rpt-3"])


class FixedFormPrinterTest(CallBase):
    def test_fixed_form_write_and_the_overflow_indicator_conditioning(self):
        defs = {"RPT": parse_prtf("RPT", REPORT_DDS)}
        text = "\n".join(
            [
                "     FRPT       O    E             PRINTER OFLIND(*INOF)",
                cspec("WRITE", factor2="HEADING"),
                cspec("EVAL", factor2="*inof = *on"),
                cspec("WRITE", factor2="HEADING", cond="OF"),
                cspec("EVAL", factor2="*inof = *off"),
                cspec("WRITE", factor2="HEADING", cond="NOF"),
            ]
        ) + "\n"
        prog = parse_rpgle(text)
        self.assertEqual(prog.files[0].oflind, "*INOF")
        spool = Spool(defs=defs)
        list(run_program(prog, parse_dds(""), self.db, {}, None, spool))
        self.assertEqual(len(dict(spool.outputs())["rpt"].split("\n\f\n")), 3)

    def test_o_specs_are_refused_not_ignored(self):
        text = "     FQPRINT    O    F  132        PRINTER\n     OQPRINT    D    01\n"
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle(text)
        self.assertIn("O-specs", str(cm.exception))

    def test_program_described_printer_files_are_refused(self):
        prog = parse_rpgle("     FQPRINT    O    F  132        PRINTER\n")
        with self.assertRaises(RpgleError) as cm:
            list(run_program(prog, parse_dds(""), None, {}, None, Spool()))
        self.assertIn("externally described", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
