import os
import tempfile
import unittest

from rpgle import RpgleError, build_vars, parse_dds, parse_rpgle, run_program
from rpgle.database import Database
from rpgle.pf import parse_pf

from _cols import cspec, dds, dds_line, dspec, fspec

CUST = parse_pf(
    "CUSTMAST",
    dds(
        dds_line(keywords="UNIQUE"),
        dds_line("R", "CUSTREC"),
        dds_line("", "CUSTNO", 5, "S", 0),
        dds_line("", "NAME", 10),
        dds_line("", "BAL", 9, "P", 2),
        dds_line("K", "CUSTNO"),
    ),
)
ORDERS = parse_pf(
    "ORDERS",
    dds(
        dds_line("R", "ORDREC"),
        dds_line("", "OCUST", 5, "S", 0),
        dds_line("", "ORDNO", 5, "S", 0),
        dds_line("", "AMT", 7, "P", 2),
        dds_line("K", "OCUST"),
        dds_line("K", "ORDNO"),
    ),
)
OUT = parse_pf("OUT", dds(dds_line("R", "OUTREC"), dds_line("", "MSG", 20), dds_line("", "NUM", 9, "P", 2)))
PFS = {p.name: p for p in (CUST, ORDERS, OUT)}

F_CUST = fspec("CUSTMAST", "U", "F", "A", "E", True)
F_ORD = fspec("ORDERS", "I", "F", "", "E", True)
F_OUT = fspec("OUT", "O", "F", "", "E", False)


def c(*a, **k):
    return cspec(*a, **k)


class FileIoTest(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        cust = self.db.open_file(CUST)
        for no, name, bal in [(10, "ALICE", 100.0), (20, "BOB", 50.5), (30, "CAROL", 0.0)]:
            cust.write({"CUSTNO": no, "NAME": name, "BAL": bal})
        orders = self.db.open_file(ORDERS)
        for oc, on, amt in [(1, 100, 10.0), (1, 101, 20.0), (2, 100, 5.0), (1, 102, 30.0)]:
            orders.write({"OCUST": oc, "ORDNO": on, "AMT": amt})
        self.db.open_file(OUT)

    def run_rpg(self, *lines, files=(F_CUST, F_ORD, F_OUT)):
        prog = parse_rpgle("\n".join([*files, *lines]))
        list(run_program(prog, parse_dds(""), self.db, PFS))

    def out(self):
        h = self.db.open_file(OUT)
        h.setll(None)
        rows = []
        while (r := h.read()) is not None:
            rows.append((r["MSG"], r["NUM"]))
        return rows

    # ── CHAIN ────────────────────────────────────────────────────

    def test_chain_found_loads_the_record_into_variables(self):
        self.run_rpg(
            dspec("WANT", 5, 0, 20),
            c("CHAIN", "want", "CUSTMAST", hi="71"),
            c("EVAL", factor2="msg = name"),
            c("EVAL", factor2="num = bal"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out(), [("BOB", 50.5)])

    def test_chain_not_found_sets_indicator_and_conditioned_ops_follow_it(self):
        self.run_rpg(
            dspec("WANT", 5, 0, 99),
            c("CHAIN", "want", "CUSTMAST", hi="71"),
            c("EVAL", factor2="msg = 'NOT FOUND'", cond="71"),
            c("EVAL", factor2="msg = name", cond="N71"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out(), [("NOT FOUND", 0.0)])

    def test_chain_with_a_literal_key(self):
        self.run_rpg(
            c("CHAIN", "30", "CUSTMAST"),
            c("EVAL", factor2="msg = name"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0], "CAROL")

    def test_result_indicator_is_reset_each_time(self):
        # 99 is not found (indicator on), then 10 is found (must go off).
        self.run_rpg(
            dspec("WANT", 5, 0, 99),
            c("CHAIN", "want", "CUSTMAST", hi="71"),
            c("EVAL", factor2="want = 10"),
            c("CHAIN", "want", "CUSTMAST", hi="71"),
            c("EVAL", factor2="msg = 'STILL ON'", cond="71"),
            c("EVAL", factor2="msg = name", cond="N71"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0], "ALICE")

    def test_found_builtin_after_chain(self):
        self.run_rpg(
            c("CHAIN", "20", "CUSTMAST"),
            c("IF", factor2="%FOUND(CUSTMAST)"),
            c("EVAL", factor2="msg = 'YES'"),
            c("ELSE"),
            c("EVAL", factor2="msg = 'NO'"),
            c("ENDIF"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0], "YES")

    # ── READ loops ───────────────────────────────────────────────

    def test_read_loop_until_eof_sums_every_record(self):
        self.run_rpg(
            dspec("TOTAL", 9, 2, 0),
            c("SETLL", "*LOVAL", "CUSTMAST"),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("DOW", factor2="%EOF(CUSTMAST) = *OFF"),
            c("EVAL", factor2="total = total + bal"),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("ENDDO"),
            c("EVAL", factor2="msg = 'TOTAL'"),
            c("EVAL", factor2="num = total"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out(), [("TOTAL", 150.5)])

    def test_eof_indicator_form_of_the_same_loop(self):
        self.run_rpg(
            dspec("N", 3, 0, 0),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("DOW", factor2="*IN75 = *OFF"),
            c("EVAL", factor2="n = n + 1"),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("ENDDO"),
            c("EVAL", factor2="num = n"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][1], 3.0)

    def test_leave_conditioned_on_an_indicator(self):
        self.run_rpg(
            dspec("N", 3, 0, 0),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("DOW", factor2="*IN75 = *OFF"),
            c("EVAL", factor2="n = n + 1"),
            c("IF", factor2="n = 2"),
            c("LEAVE"),
            c("ENDIF"),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("ENDDO"),
            c("EVAL", factor2="num = n"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][1], 2.0)

    def test_leave_conditioned_on_eof_indicator(self):
        self.run_rpg(
            dspec("N", 3, 0, 0),
            c("DOW", factor2="1 = 1"),
            c("READ", factor2="CUSTMAST", eq="75"),
            c("LEAVE", cond="75"),
            c("EVAL", factor2="n = n + 1"),
            c("ENDDO"),
            c("EVAL", factor2="num = n"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][1], 3.0)

    def test_reade_over_a_klist_partial_key(self):
        self.run_rpg(
            dspec("TOTAL", 9, 2, 0),
            dspec("WANT", 5, 0, 1),
            c("KLIST", "ordk"),
            c("KFLD", result="want"),
            c("SETLL", "ordk", "ORDERS"),
            c("READE", "ordk", "ORDERS", eq="75"),
            c("DOW", factor2="%EOF = *OFF"),
            c("EVAL", factor2="total = total + amt"),
            c("READE", "ordk", "ORDERS", eq="75"),
            c("ENDDO"),
            c("EVAL", factor2="num = total"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][1], 60.0)  # customer 1: 10 + 20 + 30

    def test_setll_indicators_not_found_and_equal(self):
        self.run_rpg(
            c("SETLL", "20", "CUSTMAST", hi="71", eq="73"),
            c("EVAL", factor2="msg = 'EQ'", cond="73"),
            c("EVAL", factor2="msg = 'NOEQ'", cond="N73"),
            c("WRITE", factor2="OUTREC"),
            c("SETLL", "99", "CUSTMAST", hi="71", eq="73"),
            c("EVAL", factor2="msg = 'NF'", cond="71"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([m for m, _ in self.out()], ["EQ", "NF"])

    # ── WRITE / UPDATE / DELETE ──────────────────────────────────

    def test_update_the_record_just_chained(self):
        self.run_rpg(
            c("CHAIN", "20", "CUSTMAST"),
            c("EVAL", factor2="bal = bal + 10"),
            c("UPDATE", factor2="CUSTREC"),
        )
        self.assertEqual(self.db.open_file(CUST).chain((20,))["BAL"], 60.5)

    def test_write_a_new_record_from_variables(self):
        self.run_rpg(
            c("EVAL", factor2="custno = 40"),
            c("EVAL", factor2="name = 'DAVE'"),
            c("EVAL", factor2="bal = 12.5"),
            c("WRITE", factor2="CUSTREC"),
        )
        rec = self.db.open_file(CUST).chain((40,))
        self.assertEqual((rec["NAME"], rec["BAL"]), ("DAVE", 12.5))

    def test_delete_current_record(self):
        self.run_rpg(c("CHAIN", "10", "CUSTMAST"), c("DELETE", factor2="CUSTREC"))
        self.assertIsNone(self.db.open_file(CUST).chain((10,)))

    def test_delete_by_key_reports_not_found(self):
        self.run_rpg(
            c("DELETE", "99", "CUSTMAST", hi="71"),
            c("EVAL", factor2="msg = 'NR'", cond="71"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0], "NR")

    def test_duplicate_key_with_error_indicator_does_not_halt(self):
        self.run_rpg(
            c("EVAL", factor2="custno = 10"),
            c("WRITE", factor2="CUSTREC", lo="72"),
            c("EVAL", factor2="msg = 'ERR'", cond="72"),
            c("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0], "ERR")

    def test_duplicate_key_without_error_indicator_halts(self):
        with self.assertRaises(RpgleError):
            self.run_rpg(c("EVAL", factor2="custno = 10"), c("WRITE", factor2="CUSTREC"))

    # ── setup and errors ─────────────────────────────────────────

    def test_unknown_file_is_an_error_even_with_an_error_indicator(self):
        with self.assertRaises(RpgleError):
            self.run_rpg(c("READ", factor2="NOPE", lo="72"))

    def test_disk_files_need_a_database(self):
        prog = parse_rpgle("\n".join([F_CUST, c("READ", factor2="CUSTMAST")]))
        with self.assertRaises(RpgleError):
            list(run_program(prog, parse_dds("")))

    def test_missing_dds_for_a_file_is_an_error(self):
        prog = parse_rpgle("\n".join([F_CUST, c("READ", factor2="CUSTMAST")]))
        with self.assertRaises(RpgleError):
            list(run_program(prog, parse_dds(""), self.db, {}))

    def test_conditioning_a_control_op_is_rejected_loudly(self):
        with self.assertRaises(RpgleError):
            self.run_rpg(c("IF", factor2="1 = 1", cond="71"), c("ENDIF"))

    def test_build_vars_includes_disk_record_fields(self):
        prog = parse_rpgle("\n".join([F_CUST]))
        v = build_vars(prog, parse_dds(""), PFS)
        self.assertEqual((v["custno"].type, v["name"].type, v["bal"].dec), ("N", "A", 2))

    def test_changes_are_committed_when_the_program_ends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            db = Database(path)
            db.open_file(CUST)
            prog = parse_rpgle(
                "\n".join(
                    [
                        F_CUST,
                        c("EVAL", factor2="custno = 7"),
                        c("EVAL", factor2="name = 'GRACE'"),
                        c("WRITE", factor2="CUSTREC"),
                    ]
                )
            )
            list(run_program(prog, parse_dds(""), db, PFS))
            other = Database(path)  # a second connection sees it only if committed
            self.addCleanup(other.close)
            self.assertEqual(other.open_file(CUST).chain((7,))["NAME"], "GRACE")
            db.close()


if __name__ == "__main__":
    unittest.main()
