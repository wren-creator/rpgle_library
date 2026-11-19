import unittest

from rpgle import RpgleError
from rpgle.database import Database
from rpgle.lf import LogicalFile, parse_lf
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

CUST_BY_NAME = parse_lf(
    "custbyname",
    dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "CUSTREC"), dds_line("K", "NAME")),
    CUST,
)


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

    def test_numeric_keys_refuse_text_that_is_not_a_number(self):
        with self.assertRaises(RpgleError):
            self.f.chain(("abc",))
        self.assertEqual(self.f.chain(("20  ",))["NAME"], "BOB")  # plain numbers in text are fine
        self.assertEqual(self.f.chain((20.9,))["NAME"], "BOB")  # and a fraction is dropped

    def test_a_number_used_as_a_character_key_reads_as_rpg_shows_it(self):
        db = Database()
        self.addCleanup(db.close)
        pf = parse_pf("CODES", dds(dds_line("R", "R"), dds_line("", "C", 5), dds_line("K", "C")))
        f = db.open_file(pf)
        f.write({"C": "20"})
        self.assertIsNotNone(f.chain((20.0,)))  # 20, not "20.0"

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

    def test_commit_after_close_is_harmless(self):
        db = Database()
        db.close()
        db.commit()

    def test_file_not_open(self):
        db = Database()
        self.addCleanup(db.close)
        with self.assertRaises(RpgleError):
            db.file("NOPE")



class HostHooksTest(unittest.TestCase):
    """What a host that keeps the data and the rules itself needs: ask before every operation, and
    load and dump rows without going through the program."""

    def test_authorize_is_asked_at_open_and_for_each_operation(self):
        asked = []
        db = Database(authorize=lambda name, op: asked.append((name, op)))
        self.addCleanup(db.close)
        f = db.open_file(CUST, "U")
        f.write(cust(1, "A"))
        f.chain((1,))
        f.update(cust(1, "B"))
        f.delete()
        self.assertEqual([op for _, op in asked], ["open:U", "add", "read", "update", "delete"])
        self.assertEqual({n for n, _ in asked}, {"CUSTMAST"})

    def test_authorize_can_refuse_and_nothing_is_changed(self):
        def deny_add(name, op):
            if op == "add":
                raise RpgleError("Not authorized")
        db = Database(authorize=deny_add)
        self.addCleanup(db.close)
        f = db.open_file(CUST)
        with self.assertRaises(RpgleError):
            f.write(cust(1, "A"))
        self.assertEqual(db.dump_rows("CUSTMAST"), [])

    def test_load_and_dump_round_trip_without_asking(self):
        asked = []
        db = Database(authorize=lambda name, op: asked.append(op))
        self.addCleanup(db.close)
        db.load_rows(CUST, [cust(2, "BOB", 5.5), cust(1, "ALICE")])
        self.assertEqual(asked, [])
        self.assertEqual([r["NAME"].strip() for r in db.dump_rows("custmast")], ["BOB", "ALICE"])
        f = db.open_file(CUST)                       # a program opening it later sees the rows, in key order
        self.assertEqual(f.read()["NAME"].strip(), "ALICE")
        self.assertEqual(asked, ["open:I", "read"])


class RecordLockingTest(unittest.TestCase):
    """lock/unlock are asked only for a file opened U or C, once per record a positioned read lands
    on, and let go on UPDATE, DELETE, or moving to a different record."""

    def setUp(self):
        self.held: set = set()

        def lock(name, rrn):
            key = (name, rrn)
            if key in self.held:
                return False
            self.held.add(key)
            return True

        def unlock(name, rrn):
            self.held.discard((name, rrn))

        self.db = Database(lock=lock, unlock=unlock)
        self.addCleanup(self.db.close)
        self.f = self.db.open_file(CUST, "U")
        self.f.write(cust(1, "ALICE"))
        self.f.write(cust(2, "BOB"))

    def test_a_read_only_open_never_locks(self):
        db2 = Database(lock=lambda *a: self.fail("should not be asked"))
        self.addCleanup(db2.close)
        db2.open_file(CUST).chain((1,))

    def test_chain_locks_the_record_and_update_releases_it(self):
        self.f.chain((1,))
        self.assertEqual(self.held, {("CUSTMAST", 1)})
        self.f.update(cust(1, "ALICE2"))
        self.assertEqual(self.held, set())

    def test_delete_releases_the_lock(self):
        self.f.chain((2,))
        self.f.delete()
        self.assertEqual(self.held, set())

    def test_moving_to_another_record_releases_the_one_before_it(self):
        self.f.chain((1,))
        self.f.chain((2,))
        self.assertEqual(self.held, {("CUSTMAST", 2)})

    def test_a_record_already_locked_refuses_and_leaves_the_file_positioned(self):
        self.held.add(("CUSTMAST", 2))
        with self.assertRaises(RpgleError):
            self.f.chain((2,))

    def test_two_handles_on_the_same_table_contend_for_the_same_lock(self):
        """A logical file opens the same table under its own name, so it has to lock by the physical
        file's name, not whatever name it was opened under, or two views of one record could both
        grab it."""
        g = self.db.open_logical(
            parse_lf("custbyname", dds(dds_line(keywords="PFILE(CUSTMAST)"), dds_line("R", "CUSTREC"),
                                       dds_line("K", "NAME")), CUST),
            CUST, "U",
        )
        self.f.chain((1,))                            # customer 1 is ALICE
        with self.assertRaises(RpgleError):
            g.chain(("ALICE",))


class LogicalFileTest(unittest.TestCase):
    """A logical file shares the physical file's table but reads it in its own key order,
    through its own name, with its own independent position."""

    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        self.pf = self.db.open_file(CUST)
        # CUSTNO order and NAME order disagree on purpose, so a test that gets the same
        # answer from both couldn't be fooled into passing by accident.
        for no, name in [(30, "ALICE"), (10, "CAROL"), (20, "BOB")]:
            self.pf.write(cust(no, name))
        self.lf = self.db.open_logical(CUST_BY_NAME, CUST)

    def _names(self, handle) -> list:
        handle.setll(None)
        names = []
        while (rec := handle.read()) is not None:
            names.append(rec["NAME"].strip())
        return names

    def test_the_physical_file_still_reads_in_its_own_key_order(self):
        self.assertEqual(self._names(self.pf), ["CAROL", "BOB", "ALICE"])

    def test_the_logical_file_reads_in_its_own_key_order(self):
        self.assertEqual(self._names(self.lf), ["ALICE", "BOB", "CAROL"])

    def test_chain_by_the_logical_key(self):
        rec = self.lf.chain(("BOB",))
        self.assertEqual(rec["CUSTNO"], 20)
        self.assertTrue(self.lf.found)
        self.assertIsNone(self.lf.chain(("NOBODY",)))
        self.assertFalse(self.lf.found)

    def test_two_handles_over_the_same_table_have_independent_positions(self):
        self.pf.chain((10,))     # CAROL
        self.lf.chain(("ALICE",))
        self.assertEqual(self.pf.read()["NAME"].strip(), "BOB")    # next by CUSTNO after 10
        self.assertEqual(self.lf.read()["NAME"].strip(), "BOB")    # next by NAME after ALICE, same record here

    def test_a_write_through_the_physical_handle_is_visible_through_the_logical_one(self):
        self.pf.write(cust(40, "DAVE"))
        self.assertIsNotNone(self.lf.chain(("DAVE",)))

    def test_open_logical_rejects_a_mismatched_physical_file(self):
        bogus = LogicalFile(name="X", based_on="NOTCUST", format="R", fields=list(CUST.fields), keys=["NAME"])
        with self.assertRaises(RpgleError):
            self.db.open_logical(bogus, CUST)

    def test_authorize_and_errors_see_the_logical_files_own_name(self):
        asked = []
        db = Database(authorize=lambda name, op: asked.append((name, op)))
        self.addCleanup(db.close)
        lf = db.open_logical(CUST_BY_NAME, CUST)
        asked.clear()
        lf.chain(("BOB",))                            # not found: the table is empty here
        self.assertEqual(asked, [("CUSTBYNAME", "read")])
        with self.assertRaisesRegex(RpgleError, r"^CUSTBYNAME:"):
            lf.delete()  # no current record: the error names the file as it was opened


CUST_OVER_100 = parse_lf(
    "custover100",
    dds(
        dds_line(keywords="PFILE(CUSTMAST)"),
        dds_line("R", "CUSTREC"),
        dds_line("K", "CUSTNO"),
        dds_line("S", "BAL", keywords="COMP(GT 100)"),
    ),
    CUST,
)

CUST_NOT_BOB = parse_lf(
    "custnotbob",
    dds(
        dds_line(keywords="PFILE(CUSTMAST)"),
        dds_line("R", "CUSTREC"),
        dds_line("K", "CUSTNO"),
        dds_line("O", "NAME", keywords="COMP(EQ 'BOB')"),
    ),
    CUST,
)


class SelectOmitTest(unittest.TestCase):
    """A select/omit logical file reads the same table as its physical file, but only the
    records its S/O lines allow through: every read, chain and position acts as if the other
    records were not there at all."""

    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        pf = self.db.open_file(CUST)
        for no, name, bal in [(10, "ALICE", 50.0), (20, "BOB", 200.0), (30, "CAROL", 150.0)]:
            pf.write(cust(no, name, bal))

    def _names(self, handle) -> list:
        handle.setll(None)
        names = []
        while (rec := handle.read()) is not None:
            names.append(rec["NAME"].strip())
        return names

    def test_a_select_line_only_shows_matching_records(self):
        lf = self.db.open_logical(CUST_OVER_100, CUST)
        self.assertEqual(self._names(lf), ["BOB", "CAROL"])

    def test_an_omit_line_hides_matching_records(self):
        lf = self.db.open_logical(CUST_NOT_BOB, CUST)
        self.assertEqual(self._names(lf), ["ALICE", "CAROL"])

    def test_chain_also_respects_the_filter(self):
        lf = self.db.open_logical(CUST_OVER_100, CUST)
        self.assertIsNone(lf.chain((10,)))       # ALICE: BAL 50, filtered out
        self.assertFalse(lf.found)
        self.assertIsNotNone(lf.chain((20,)))    # BOB: BAL 200, passes
        self.assertTrue(lf.found)

    def test_the_underlying_physical_file_still_sees_every_record(self):
        lf = self.db.open_logical(CUST_OVER_100, CUST)
        pf = self.db.open_file(CUST)
        self.assertEqual(self._names(lf), ["BOB", "CAROL"])
        self.assertEqual(self._names(pf), ["ALICE", "BOB", "CAROL"])


ORDJOIN = parse_lf(
    "custord",
    dds(
        dds_line(keywords="JFILE(CUSTMAST ORDERS)"),
        dds_line("R", "JOINREC"),
        dds_line("J", keywords="JOIN(1)"),
        dds_line("", keywords="JFLD(CUSTNO CUSTNO)"),
        dds_line("K", "ORDNO"),
    ),
    CUST,
    {"ORDERS": ORDERS},
)


class JoinLogicalFileTest(unittest.TestCase):
    """A join logical file combines a primary and its secondaries into one read-only view,
    one row per matching combination, in the join logical file's own key order."""

    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.close)
        pf = self.db.open_file(CUST)
        for no, name, bal in [(10, "ALICE", 50.0), (20, "BOB", 200.0)]:
            pf.write(cust(no, name, bal))
        of = self.db.open_file(ORDERS)
        of.write(order(10, 1, 9.0))
        of.write(order(10, 2, 19.0))
        of.write(order(20, 3, 29.0))
        self.lf = self.db.open_join(ORDJOIN, {"CUSTMAST": CUST, "ORDERS": ORDERS})

    def test_every_field_of_both_files_shows(self):
        self.lf.setll(None)
        rec = self.lf.read()
        self.assertEqual(set(rec.keys()), {"CUSTNO", "NAME", "BAL", "ORDNO", "AMT"})

    def test_reads_in_the_join_logical_files_own_key_order(self):
        self.lf.setll(None)
        orders = []
        while (rec := self.lf.read()) is not None:
            orders.append(rec["ORDNO"])
        self.assertEqual(orders, [1, 2, 3])

    def test_a_customer_with_no_orders_does_not_show_an_inner_join(self):
        self.db.open_file(CUST).write(cust(30, "CAROL", 0.0))
        self.lf.setll(None)
        names = []
        while (rec := self.lf.read()) is not None:
            names.append(rec["NAME"].strip())
        self.assertNotIn("CAROL", names)

    def test_chain_by_the_join_logical_files_key(self):
        rec = self.lf.chain((2,))
        self.assertEqual((rec["CUSTNO"], rec["NAME"].strip(), rec["AMT"]), (10, "ALICE", 19.0))

    def test_a_join_logical_file_is_read_only(self):
        with self.assertRaisesRegex(RpgleError, "read-only"):
            self.lf.write({"CUSTNO": 10, "NAME": "X", "BAL": 0.0, "ORDNO": 9, "AMT": 1.0})
        self.lf.chain((1,))
        with self.assertRaisesRegex(RpgleError, "read-only"):
            self.lf.update({"CUSTNO": 10, "NAME": "X", "BAL": 0.0, "ORDNO": 1, "AMT": 1.0})
        with self.assertRaisesRegex(RpgleError, "read-only"):
            self.lf.delete()

    def test_open_join_needs_every_secondary_physical_file_given(self):
        with self.assertRaises(KeyError):
            self.db.open_join(ORDJOIN, {"CUSTMAST": CUST})


if __name__ == "__main__":
    unittest.main()
