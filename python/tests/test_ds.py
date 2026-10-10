import unittest

from rpgle import RpgleError, build_vars, parse_dds, parse_rpgle, run_program
from test_fileio import PFS
from test_freeform import FreeBase


class DsParseTest(unittest.TestCase):
    def test_explicit_subfields_and_flags(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-ds pt qualified;\n  x packed(5:2);\n  label char(10) inz('hi');\nend-ds;\n"
            "dcl-ds flat;\n  a char(3);\nend-ds;\n"
        )
        pt, flat = prog.dss
        self.assertTrue(pt.qualified)
        self.assertEqual([(f.name, f.length, f.dec) for f in pt.subfields], [("x", 5, 2), ("label", 10, None)])
        self.assertEqual(pt.subfields[1].init, "'hi'")
        self.assertFalse(flat.qualified)

    def test_borrowed_layouts_need_no_end_ds(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-ds a qualified likerec(custrec);\n"
            "dcl-ds b likeds(a);\n"
            "dcl-ds c extname('CUSTMAST') qualified;\n"
            "end-ds;\n"
            "dcl-s after char(1);\n"
        )
        a, b, c = prog.dss
        self.assertEqual((a.likerec, b.likeds, c.extname), ("custrec", "a", "CUSTMAST"))
        self.assertTrue(b.qualified)  # a likeds copy is always qualified
        self.assertEqual([d.name for d in prog.dspecs], ["after"])

    def test_errors(self):
        for src, fragment in [
            ("**free\ndcl-ds a qualified;\ndcl-ds a qualified;\nend-ds;\n", "end-ds"),
            ("**free\ndcl-ds a likeds(a);\n", "declared earlier"),
            ("**free\ndcl-ds a;\n a char(1) dim(3);\nend-ds;\n", "arrays"),
            ("**free\ndcl-ds a len(10);\nend-ds;\n", "not supported"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    prog = parse_rpgle(src)
                    build_vars(prog, parse_dds(""), PFS)
                self.assertIn(fragment, str(cm.exception))

    def test_unknown_likerec_is_an_error_at_run_time(self):
        prog = parse_rpgle("**free\ndcl-ds a qualified likerec(nosuchrec);\n")
        with self.assertRaises(RpgleError):
            build_vars(prog, parse_dds(""), PFS)

    def test_clear_and_ds_targets_parse(self):
        prog = parse_rpgle(
            "**free\ndcl-f custmast keyed;\n"
            "chain k custmast cust;\nchain (a : b) custmast cust;\nread custmast cust;\n"
            "write custrec cust;\nupdate custrec cust;\nreade k custmast cust;\nclear cust;\n"
        )
        got = [(s.op, s.factor1, s.factor2, s.ds, s.expr) for s in prog.statements]
        self.assertEqual(
            got,
            [
                ("CHAIN", "k", "custmast", "cust", None),
                ("CHAIN", "(a : b)", "custmast", "cust", None),
                ("READ", None, "custmast", "cust", None),
                ("WRITE", None, "custrec", "cust", None),
                ("UPDATE", None, "custrec", "cust", None),
                ("READE", "k", "custmast", "cust", None),
                ("CLEAR", None, None, None, "cust"),
            ],
        )


class DsRunTest(FreeBase):
    def test_qualified_ds_subfields_are_read_and_assigned_with_a_dot(self):
        self.run_free("""
dcl-ds pt qualified; x packed(5:2); label char(10) inz('hi'); end-ds;
pt.x = 1.5;
pt.x += 2;
msg = pt.label; num = pt.x; write outrec;
""")
        self.assertEqual((self.msgs(), self.nums()), (["hi"], [3.5]))

    def test_unqualified_ds_subfields_are_plain_variables(self):
        self.run_free("""
dcl-ds flat; a packed(3); b packed(3); end-ds;
a = 4; b = a * 2;
num = b; write outrec;
""")
        self.assertEqual(self.nums(), [8.0])

    def test_chain_into_a_ds_leaves_the_program_fields_alone(self):
        self.run_free("""
dcl-ds cust qualified likerec(custrec);
chain 20 custmast cust;
if %found(custmast); msg = cust.name; num = cust.bal; endif;
write outrec;
msg = name; num = custno; write outrec;
""")
        self.assertEqual(self.msgs(), ["BOB", ""])
        self.assertEqual(self.nums(), [50.5, 0.0])  # name and custno untouched

    def test_read_loop_into_a_ds(self):
        self.run_free("""
dcl-ds cust qualified likerec(custrec);
dcl-s t packed(9:2);
setll *loval custmast;
read custmast cust;
dow not %eof(custmast);
  t += cust.bal;
  read custmast cust;
enddo;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [150.5])

    def test_reade_into_a_ds(self):
        self.run_free("""
dcl-ds o qualified likerec(ordrec);
dcl-s t packed(9:2);
setll 1 orders;
reade 1 orders o;
dow not %eof(orders);
  t += o.amt;
  reade 1 orders o;
enddo;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [60.0])

    def test_write_a_new_record_from_a_ds(self):
        self.run_free("""
dcl-ds cust qualified likerec(custrec);
cust.custno = 77; cust.name = 'ERIN'; cust.bal = 9.5;
write custrec cust;
""")
        rec = self.db.open_file(PFS["CUSTMAST"]).chain((77,))
        self.assertEqual((rec["NAME"], rec["BAL"]), ("ERIN", 9.5))

    def test_update_from_a_ds(self):
        self.run_free("""
dcl-ds cust qualified likerec(custrec);
chain 20 custmast cust;
cust.bal += 100;
update custrec cust;
""")
        self.assertEqual(self.db.open_file(PFS["CUSTMAST"]).chain((20,))["BAL"], 150.5)

    def test_unqualified_explicit_ds_works_as_an_io_target_when_names_match(self):
        self.run_free("""
dcl-ds rec;
  custno packed(5); name char(10); bal packed(9:2);
end-ds;
chain 10 custmast rec;
msg = name; num = bal; write outrec;
""")
        self.assertEqual((self.msgs(), self.nums()), (["ALICE"], [100.0]))

    def test_extname_ds(self):
        self.run_free("""
dcl-ds cust extname('CUSTMAST') qualified; end-ds;
chain 30 custmast cust;
msg = cust.name; write outrec;
""")
        self.assertEqual(self.msgs(), ["CAROL"])

    def test_likeds_copies_are_independent(self):
        self.run_free("""
dcl-ds a qualified likerec(custrec);
dcl-ds b likeds(a);
chain 10 custmast a;
chain 20 custmast b;
msg = a.name; write outrec;
msg = b.name; write outrec;
""")
        self.assertEqual(self.msgs(), ["ALICE", "BOB"])

    def test_likerec_of_a_display_format(self):
        dds_src = "     A          R INQ\n     A            CUSTNO         5  0B  3 20\n     A            NAME          20A  O  5 20\n"
        prog = parse_rpgle("**free\ndcl-ds shown qualified likerec(inq);\nshown.name = 'x';\n")
        v = build_vars(prog, parse_dds(dds_src), PFS)
        self.assertEqual((v["shown.custno"].type, v["shown.name"].length), ("N", 20))

    def test_clear(self):
        self.run_free("""
dcl-ds cust qualified likerec(custrec);
dcl-s s varchar(10); dcl-s i packed(3); dcl-s f ind;
chain 20 custmast cust;
s = 'abc'; i = 5; f = *on;
clear cust; clear s; clear i; clear f;
msg = cust.name; num = cust.bal + i; write outrec;
num = %len(s); write outrec;
if f; msg = 'ON'; else; msg = 'OFF'; endif; write outrec;
""")
        self.assertEqual(self.msgs(), ["", "", "OFF"])
        self.assertEqual(self.nums()[:2], [0.0, 0.0])

    def test_ds_target_must_be_a_ds(self):
        with self.assertRaises(RpgleError):
            self.run_free("dcl-s notads packed(3);\nchain 20 custmast notads;\n")

    def test_clear_unknown_name(self):
        with self.assertRaises(RpgleError):
            self.run_free("clear nothing;\n")


if __name__ == "__main__":
    unittest.main()
