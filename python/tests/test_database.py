import unittest

from rpgle import RpgleError
from rpgle.database import Database
from rpgle.pf import parse_pf

from _cols import dds, dds_line

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

# Orders: key is (CUSTNO, ORDNO); duplicates of the full key are allowed.
ORDERS = parse_pf(
    "ORDERS",
    dds(
        dds_line("R", "ORDREC"),
        dds_line("", "CUSTNO", 5, "S", 0),
        dds_line("", "ORDNO", 5, "S", 0),
        dds_line("", "AMT", 7, "P", 2),
        dds_line("K", "CUSTNO"),
        dds_line("K", "ORDNO"),
    ),
)

LOG = parse_pf("LOG", dds(dds_line("R", "LOGREC"), dds_line("", "MSG", 10)))


def cust(no, name, bal=0.0):
    return {"CUSTNO": no, "NAME": name, "BAL": bal}


def order(c, o, amt=1.0):
    return {"CUSTNO": c, "ORDNO": o, "AMT": amt}


class KeyedAccessTest(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        self.f = self.db.open_file(CUST)
        # Written out of order on purpose: reads must come back in key order.
        for no, name in [(30, "CAROL"), (10, "ALICE"), (20, "BOB")]:
            self.f.write(cust(no, name))

    def test_chain_found_and_not_found(self):
        self.assertEqual(self.f.chain((20,))["NAME"], "BOB")
        self.assertTrue(self.f.found)
        self.assertIsNone(self.f.chain((25,)))
        self.assertFalse(self.f.found)

    def test_char_keys_ignore_trailing_blanks(self):
        db = Database()
        self.addCleanup(db.close)
        pf = parse_pf("NAMES", dds(dds_line("R", "R"), dds_line("", "N", 5), dds_line("K", "N")))
        f = db.open_file(pf)
        f.write({"N": "AB   "})
        self.assertIsNotNone(f.chain(("AB",)))

    def test_read_walks_in_key_order_then_eof(self):
        self.f.setll(None)
        names = []
        while (rec := self.f.read()) is not None:
            names.append(rec["NAME"])
        self.assertEqual(names, ["ALICE", "BOB", "CAROL"])
        self.assertTrue(self.f.eof)

    def test_read_with_no_position_starts_at_the_top(self):
        self.assertEqual(self.f.read()["NAME"], "ALICE")

    def test_setll_exact_and_between(self):
        self.f.setll((20,))
        self.assertTrue(self.f.found and self.f.equal)
        self.assertEqual(self.f.read()["NAME"], "BOB")

        self.f.setll((15,))
        self.assertTrue(self.f.found)
        self.assertFalse(self.f.equal)
        self.assertEqual(self.f.read()["NAME"], "BOB")  # first key >= 15

    def test_setll_past_the_end_is_not_found(self):
        self.f.setll((99,))
        self.assertFalse(self.f.found)
        self.assertIsNone(self.f.read())
        self.assertTrue(self.f.eof)

    def test_setgt_skips_the_equal_record(self):
        self.f.setgt((20,))
        self.assertTrue(self.f.found)
        self.assertEqual(self.f.read()["NAME"], "CAROL")

    def test_hival_then_readp_gives_the_last_record(self):
        self.f.setgt(None)
        self.assertEqual(self.f.readp()["NAME"], "CAROL")
        self.assertEqual(self.f.readp()["NAME"], "BOB")

    def test_readp_after_read_goes_back(self):
        self.f.chain((20,))
        self.assertEqual(self.f.readp()["NAME"], "ALICE")
        self.assertIsNone(self.f.readp())
        self.assertTrue(self.f.eof)

    def test_unique_key_rejects_duplicate_write(self):
        with self.assertRaises(RpgleError):
            self.f.write(cust(20, "DUPE"))

    def test_update_changes_current_record(self):
        self.f.chain((20,))
        self.f.update(cust(20, "ROBERT", 5.5))
        rec = self.f.chain((20,))
        self.assertEqual((rec["NAME"], rec["BAL"]), ("ROBERT", 5.5))

    def test_update_with_no_current_record_is_an_error(self):
        with self.assertRaises(RpgleError):
            self.f.update(cust(1, "X"))

    def test_update_can_change_the_key_and_keep_position(self):
        self.f.chain((20,))
        self.f.update(cust(25, "BOB"))
        self.assertEqual(self.f.read()["NAME"], "CAROL")
        self.assertIsNone(self.f.chain((20,)))
        self.assertIsNotNone(self.f.chain((25,)))

    def test_delete_current_and_by_key(self):
        self.f.chain((20,))
        self.assertTrue(self.f.delete())
        self.assertIsNone(self.f.chain((20,)))
        self.assertTrue(self.f.delete((10,)))
        self.assertFalse(self.f.delete((10,)))
        self.assertFalse(self.f.found)

    def test_delete_with_no_current_record_is_an_error(self):
        with self.assertRaises(RpgleError):
            self.f.delete()

    def test_delete_during_a_read_loop_keeps_the_position(self):
        self.f.setll(None)
        seen = []
        while (rec := self.f.read()) is not None:
            seen.append(rec["NAME"])
            self.f.delete()
        self.assertEqual(seen, ["ALICE", "BOB", "CAROL"])
        self.f.setll(None)
        self.assertIsNone(self.f.read())

    def test_numbers_round_trip(self):
        self.f.write(cust(40, "DAVE", 1234.56))
        self.assertEqual(self.f.chain((40,))["BAL"], 1234.56)
        self.assertIsInstance(self.f.chain((40,))["CUSTNO"], int)


class PartialKeyTest(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        self.f = self.db.open_file(ORDERS)
        for c, o in [(2, 101), (1, 102), (1, 100), (3, 100), (1, 101)]:
            self.f.write(order(c, o, float(o)))

    def test_reade_loops_over_one_customers_orders(self):
        self.f.setll((1,))
        self.assertTrue(self.f.equal)
        got = []
        while (rec := self.f.reade((1,))) is not None:
            got.append(rec["ORDNO"])
        self.assertEqual(got, [100, 101, 102])
        self.assertTrue(self.f.eof)

    def test_reade_with_no_match_is_immediate_eof(self):
        self.f.setll((9,))
        self.assertIsNone(self.f.reade((9,)))
        self.assertTrue(self.f.eof)

    def test_chain_on_partial_key_finds_first_match(self):
        self.assertEqual(self.f.chain((1,))["ORDNO"], 100)

    def test_chain_on_full_key(self):
        self.assertEqual(self.f.chain((1, 102))["AMT"], 102.0)
        self.assertIsNone(self.f.chain((1, 999)))

    def test_readpe_walks_backward_within_a_key(self):
        self.f.setgt((1,))
        got = []
        while (rec := self.f.readpe((1,))) is not None:
            got.append(rec["ORDNO"])
        self.assertEqual(got, [102, 101, 100])

    def test_too_many_key_values_is_an_error(self):
        with self.assertRaises(RpgleError):
            self.f.chain((1, 2, 3))

    def test_duplicate_full_keys_are_allowed_and_read_in_arrival_order(self):
        self.f.write(order(1, 100, 7.0))
        self.f.setll((1, 100))
        self.assertEqual([self.f.read()["AMT"], self.f.read()["AMT"]], [100.0, 7.0])


class ArrivalSequenceTest(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        self.f = self.db.open_file(LOG)
        for m in ["one", "two", "three"]:
            self.f.write({"MSG": m})

    def test_read_is_in_arrival_order(self):
        self.assertEqual([self.f.read()["MSG"] for _ in range(3)], ["one", "two", "three"])
        self.assertIsNone(self.f.read())

    def test_chain_by_relative_record_number(self):
        self.assertEqual(self.f.chain((2,))["MSG"], "two")


class DatabaseTest(unittest.TestCase):
    def test_data_survives_reopen(self, ):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            db = Database(path)
            db.open_file(CUST).write(cust(1, "ALICE"))
            db.close()
            db2 = Database(path)
            self.addCleanup(db2.close)
            self.assertEqual(db2.open_file(CUST).chain((1,))["NAME"], "ALICE")
            db2.close()

    def test_changed_dds_fails_loudly(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            db = Database(path)
            db.open_file(CUST)
            db.close()
            other = parse_pf("CUSTMAST", dds(dds_line("R", "R"), dds_line("", "ZZZ", 3)))
            db3 = Database(path)
            self.addCleanup(db3.close)
            with self.assertRaises(RpgleError):
                db3.open_file(other)

    def test_file_not_open(self):
        db = Database()
        self.addCleanup(db.close)
        with self.assertRaises(RpgleError):
            db.file("NOPE")


if __name__ == "__main__":
    unittest.main()
