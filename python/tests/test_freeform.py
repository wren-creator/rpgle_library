import unittest

from rpgle import RpgleError, parse_dds, parse_rpgle, run_program
from rpgle.database import Database
from rpgle.freeform import is_free

from test_fileio import CUST, ORDERS, OUT, PFS

HEAD = """**free
dcl-f custmast keyed usage(*update:*output);
dcl-f orders keyed;
dcl-f out usage(*output);
"""


class ParseTest(unittest.TestCase):
    def test_detects_free_form_case_insensitively_and_ignores_leading_blanks(self):
        self.assertTrue(is_free("**FREE\nx = 1;"))
        self.assertTrue(is_free("\n\n  **free\n"))
        self.assertFalse(is_free("     C                   EVAL      x = 1"))
        self.assertFalse(is_free(""))

    def test_statements_can_span_lines_share_a_line_and_carry_comments(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-s a packed(5);   // a comment; with a semicolon\n"
            "/* block ; comment\n   over lines */\n"
            "a = 1; a = a +\n"
            "   2;\n"
        )
        self.assertEqual([s.expr for s in prog.statements], ["a = 1", "a = a +    2"])

    def test_semicolons_and_slashes_inside_strings_are_text(self):
        prog = parse_rpgle("**free\ndcl-s t char(20);\nt = 'a;b // c';\n")
        self.assertEqual(prog.statements[0].expr, "t = 'a;b // c'")

    def test_statement_lines_are_recorded(self):
        prog = parse_rpgle("**free\n\ndcl-s a packed(3);\n\na = 1;\n")
        self.assertEqual(prog.statements[0].line, 5)

    def test_declarations(self):
        prog = parse_rpgle(
            "**free\n"
            "ctl-opt dftactgrp(*no);\n"
            "dcl-s name char(10) inz('BOB');\n"
            "dcl-s total packed(9:2) inz(1.5);\n"
            "dcl-s n int(10);\n"
            "dcl-s big varchar(50);\n"
            "dcl-s ok ind;\n"
            "dcl-s again like(name);\n"
            "dcl-c limit 100;\n"
            "dcl-c greeting const('hi');\n"
        )
        d = {x.name: x for x in prog.dspecs}
        self.assertEqual((d["name"].length, d["name"].dec, d["name"].init), (10, None, "'BOB'"))
        self.assertEqual((d["total"].length, d["total"].dec, d["total"].init), (9, 2, "1.5"))
        self.assertEqual((d["n"].length, d["n"].dec), (10, 0))
        self.assertEqual(d["big"].kind, "varchar")
        self.assertEqual((d["ok"].kind, d["ok"].init), ("ind", "0"))
        self.assertEqual((d["again"].length, d["again"].dec), (10, None))
        self.assertEqual((d["limit"].length, d["limit"].dec, d["limit"].init), (3, 0, "100"))
        self.assertEqual((d["greeting"].length, d["greeting"].init), (2, "hi"))

    def test_dcl_f(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-f custmast keyed usage(*update:*output);\n"
            "dcl-f orders keyed;\n"
            "dcl-f screen workstn;\n"
        )
        cm, od, sc = prog.files
        self.assertEqual((cm.type, cm.addition, cm.keyed, cm.device, cm.format), ("U", "A", True, "DISK", "E"))
        self.assertEqual((od.type, od.addition, od.device), ("I", None, "DISK"))
        self.assertEqual((sc.type, sc.device), ("C", "WORKSTN"))
        self.assertIs(prog.file, sc)

    def test_assignment_forms(self):
        prog = parse_rpgle(
            "**free\ndcl-s a packed(5);\n"
            "a = 1;\neval a = 2;\na += 3;\na -= 1;\na *= 2;\na /= 2;\n*in03 = *on;\n"
            "if a <= 3 and a <> 2;\nendif;\n"
        )
        self.assertEqual(
            [s.expr for s in prog.statements[:7]],
            ["a = 1", "a = 2", "a = a + (3)", "a = a - (1)", "a = a * (2)", "a = a / (2)", "*in03 = *on"],
        )
        self.assertEqual(prog.statements[7].expr, "a <= 3 and a <> 2")  # not mistaken for assignment

    def test_file_op_statements(self):
        prog = parse_rpgle(
            "**free\n"
            "chain custno custmast;\nchain (a : b) orders;\nsetll *loval custmast;\n"
            "reade (a) orders;\nread custmast;\nwrite custrec;\ndelete custmast;\ndelete 5 custmast;\n"
            "chain(n) k custmast;\nexfmt inq;\n"
        )
        got = [(s.op, s.factor1, s.factor2, s.expr) for s in prog.statements]
        self.assertEqual(
            got,
            [
                ("CHAIN", "custno", "custmast", None),
                ("CHAIN", "(a : b)", "orders", None),
                ("SETLL", "*loval", "custmast", None),
                ("READE", "(a)", "orders", None),
                ("READ", None, "custmast", None),
                ("WRITE", None, "custrec", None),
                ("DELETE", None, "custmast", None),
                ("DELETE", "5", "custmast", None),
                ("CHAIN", "k", "custmast", None),
                ("EXFMT", None, None, "inq"),
            ],
        )

    def test_errors_name_the_line(self):
        for src, fragment in [
            ("**free\ndcl-s a packed(3);\nmonitor;\n", "line 3"),
            ("**free\ndcl-ds x;\n  a char(3);\n", "end-ds"),
            ("**free\ndcl-ds x;\n  a char(3) pos(1);\nend-ds;\n", "POS"),
            ("**free\n\nfoo bar;\n", "line 3"),
            ("**free\ndcl-s a packed(3);\na = 1\n", "semicolon"),
            ("**free\nchain(e) k custmast;\n", "%ERROR"),
            ("**free\ndcl-s a date;\n", "not supported"),
            ("**free\ndcl-s a char(3) dim(0);\n", "at least one"),
            ("**free\n/copy foo\n", "/copy"),
            ("**free\nt = 'oops;\n", "unterminated"),
            ("**free\nfor i = 1;\n", "for needs"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    parse_rpgle(src)
                self.assertIn(fragment, str(cm.exception))


class FixedFormGuardTest(unittest.TestCase):
    def test_fixed_form_for_is_a_clear_error(self):
        from _cols import cspec

        prog = parse_rpgle(cspec("FOR", factor2="i = 1 TO 3") + "\n" + cspec("ENDFOR"))
        with self.assertRaises(RpgleError) as cm:
            list(run_program(prog, parse_dds("")))
        self.assertIn("free-form", str(cm.exception))


class FreeBase(unittest.TestCase):
    """Seeded database plus helpers; no tests of its own."""

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

    def run_free(self, body, head=HEAD, dds_src=""):
        prog = parse_rpgle(head + body)
        return list(run_program(prog, parse_dds(dds_src), self.db, PFS))

    def nums(self):
        h = self.db.open_file(OUT)
        h.setll(None)
        rows = []
        while (r := h.read()) is not None:
            rows.append(r["NUM"])
        return rows

    def msgs(self):
        h = self.db.open_file(OUT)
        h.setll(None)
        rows = []
        while (r := h.read()) is not None:
            rows.append(r["MSG"])
        return rows


class RunTest(FreeBase):
    def test_for_loop_to_and_by(self):
        self.run_free("""
dcl-s i packed(3); dcl-s t packed(5);
for i = 1 to 10 by 3;  t += i;  endfor;
num = t; write outrec;
num = i; write outrec;
""")
        self.assertEqual(self.nums(), [22.0, 13.0])  # 1+4+7+10, and i ends one step past

    def test_for_downto(self):
        self.run_free("""
dcl-s i packed(3); dcl-s t packed(5);
for i = 3 downto 1;  t = t * 10 + i;  endfor;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [321.0])

    def test_for_that_never_runs(self):
        self.run_free("""
dcl-s i packed(3); dcl-s t packed(5);
for i = 5 to 1;  t += 1;  endfor;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [0.0])

    def test_for_evaluates_its_limit_once(self):
        self.run_free("""
dcl-s i packed(3); dcl-s lim packed(3) inz(3); dcl-s n packed(3);
for i = 1 to lim;  lim += 5;  n += 1;  endfor;
num = n; write outrec;
""")
        self.assertEqual(self.nums(), [3.0])

    def test_for_rejects_non_positive_by(self):
        with self.assertRaises(RpgleError):
            self.run_free("dcl-s i packed(3);\nfor i = 1 to 3 by 0; endfor;\n")

    def test_dou_runs_once_even_when_the_condition_is_already_true(self):
        self.run_free("""
dcl-s n packed(3);
dou n >= 0;  n += 1;  enddo;
num = n; write outrec;
""")
        self.assertEqual(self.nums(), [1.0])

    def test_dow_does_not_run_when_false(self):
        self.run_free("""
dcl-s n packed(3);
dow n > 0;  n += 1;  enddo;
num = n; write outrec;
""")
        self.assertEqual(self.nums(), [0.0])

    def test_iter_and_leave_in_each_loop_kind(self):
        self.run_free("""
dcl-s i packed(3); dcl-s a packed(3); dcl-s b packed(3); dcl-s c packed(3);
for i = 1 to 6;
  if i = 2; iter; endif;
  if i = 5; leave; endif;
  a += i;
endfor;
i = 0;
dow i < 6;
  i += 1;
  if i = 2; iter; endif;
  if i = 5; leave; endif;
  b += i;
enddo;
i = 0;
dou i >= 6;
  i += 1;
  if i = 2; iter; endif;
  if i = 5; leave; endif;
  c += i;
enddo;
num = a; write outrec;
num = b; write outrec;
num = c; write outrec;
""")
        self.assertEqual(self.nums(), [8.0, 8.0, 8.0])  # 1+3+4 for each

    def test_nested_loops_leave_only_the_inner_one(self):
        self.run_free("""
dcl-s i packed(3); dcl-s j packed(3); dcl-s n packed(3);
for i = 1 to 3;
  for j = 1 to 10;
    if j = 2; leave; endif;
    n += 1;
  endfor;
endfor;
num = n; write outrec;
""")
        self.assertEqual(self.nums(), [3.0])

    def test_elseif_chain_picks_one_branch(self):
        for v, want in [(1, "ONE"), (2, "TWO"), (3, "THREE"), (9, "OTHER")]:
            with self.subTest(v=v):
                self.db.open_file(OUT)._conn.execute('DELETE FROM "OUT"')
                self.run_free(f"""
dcl-s v packed(3) inz({v});
if v = 1; msg = 'ONE';
elseif v = 2; msg = 'TWO';
elseif v = 3; msg = 'THREE';
else; msg = 'OTHER';
endif;
write outrec;
""")
                self.assertEqual(self.msgs(), [want])

    def test_elseif_without_else_and_statements_after_endif(self):
        self.run_free("""
dcl-s v packed(3) inz(9);
if v = 1; msg = 'ONE';
elseif v = 2; msg = 'TWO';
endif;
msg = 'AFTER'; write outrec;
""")
        self.assertEqual(self.msgs(), ["AFTER"])

    def test_nested_if_inside_elseif(self):
        self.run_free("""
dcl-s v packed(3) inz(2);
if v = 1; msg = 'ONE';
elseif v = 2;
  if v > 1; msg = 'INNER'; else; msg = 'NOPE'; endif;
else; msg = 'OTHER';
endif;
write outrec;
""")
        self.assertEqual(self.msgs(), ["INNER"])

    def test_select_when_other(self):
        self.run_free("""
dcl-s v packed(3) inz(2);
select;
  when v = 1; msg = 'ONE';
  when v = 2; msg = 'TWO';
  other; msg = 'OTHER';
endsl;
write outrec;
""")
        self.assertEqual(self.msgs(), ["TWO"])

    def test_ind_variables_and_not(self):
        self.run_free("""
dcl-s ok ind; dcl-s done ind inz(*on);
if not ok; msg = 'NOT OK'; endif;
ok = *on;
if ok and done; msg = 'BOTH'; endif;
if not (ok and not done); msg = 'PAREN'; endif;
write outrec;
""")
        self.assertEqual(self.msgs(), ["PAREN"])

    def test_not_binds_looser_than_comparison(self):
        self.run_free("""
dcl-s a packed(3) inz(1);
if not a = 2; msg = 'YES'; endif;
write outrec;
""")
        self.assertEqual(self.msgs(), ["YES"])

    def test_subroutine(self):
        self.run_free("""
dcl-s n packed(3);
exsr bump; exsr bump;
num = n; write outrec;
return;
begsr bump; n += 5; endsr;
""")
        self.assertEqual(self.nums(), [10.0])

    # ── files ────────────────────────────────────────────────────

    def test_chain_found_not_found_and_percent_found(self):
        self.run_free("""
dcl-s want packed(5) inz(20);
chain want custmast;
if %found(custmast); msg = name; else; msg = 'NONE'; endif;
write outrec;
want = 99;
chain want custmast;
if %found(custmast); msg = name; else; msg = 'NONE'; endif;
write outrec;
""")
        self.assertEqual(self.msgs(), ["BOB", "NONE"])

    def test_character_comparison_ignores_trailing_blanks(self):
        self.run_free("""
chain 20 custmast;
if name = 'BOB'; msg = 'EQ'; endif;
if name <> 'BOB'; msg = 'NE'; endif;
if name < 'BOBB'; msg = 'LT'; endif;
write outrec;
""")
        self.assertEqual(self.msgs(), ["LT"])

    def test_varchar_keeps_its_length_and_char_pads(self):
        self.run_free("""
dcl-s v varchar(20); dcl-s c char(5);
v = 'ab'; c = 'ab';
num = %len(v); write outrec;
num = %len(c); write outrec;
v = v + 'cd';
num = %len(v); write outrec;
v = 'this is far too long for the field';
num = %len(v); write outrec;
""")
        self.assertEqual(self.nums(), [2.0, 5.0, 4.0, 20.0])

    def test_building_text_and_numbers_together(self):
        self.run_free("""
dcl-s total packed(9:2) inz(48.75);
dcl-s label varchar(40);
chain 20 custmast;
label = %trim(name) + ' owes ' + %char(total);
msg = label;
write outrec;
""")
        self.assertEqual(self.msgs(), ["BOB owes 48.75"])

    def test_read_loop_with_eof(self):
        self.run_free("""
dcl-s t packed(9:2);
setll *loval custmast;
read custmast;
dow not %eof(custmast);
  t += bal;
  read custmast;
enddo;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [150.5])

    def test_reade_with_a_key_list_and_expression_keys(self):
        self.run_free("""
dcl-s who packed(5) inz(1); dcl-s t packed(9:2);
setll who orders;
reade who orders;
dow not %eof(orders);
  t += amt;
  reade who orders;
enddo;
num = t; write outrec;
chain (who : 101 + 1) orders;
num = amt; write outrec;
""")
        self.assertEqual(self.nums(), [60.0, 30.0])

    def test_update_delete_write(self):
        self.run_free("""
chain 20 custmast;
bal += 10;
update custrec;
chain 30 custmast;
delete custmast;
custno = 40; name = 'DAVE'; bal = 1;
write custrec;
""")
        h = self.db.open_file(CUST)
        self.assertEqual(h.chain((20,))["BAL"], 60.5)
        self.assertIsNone(h.chain((30,)))
        self.assertEqual(h.chain((40,))["NAME"], "DAVE")

    def test_display_file_program_end_to_end(self):
        dds_src = """
     A          R INQ                       CF03(03)
     A            CUSTNO         5  0B  3 20
     A            NAME          20A  O  5 20
     A 90         MSGTXT        30A  O  8  3
"""
        prog = parse_rpgle("""**free
dcl-f custmast keyed;
dcl-f custinq workstn;
exfmt inq;
dow not *in03;
  *in90 = *off;
  chain custno custmast;
  if not %found(custmast);
    *in90 = *on;
    msgtxt = 'Customer not found';
    name = ' ';
  endif;
  exfmt inq;
enddo;
*inlr = *on;
return;
""")
        from rpgle import Resume

        gen = run_program(prog, parse_dds(dds_src), self.db, PFS)
        screen = next(gen)
        screen = gen.send(Resume(key="ENTER", values={"custno": "20"}))
        self.assertIn("BOB", [f.text.strip() for f in screen.fields])
        screen = gen.send(Resume(key="ENTER", values={"custno": "99"}))
        texts = [f.text.strip() for f in screen.fields]
        self.assertIn("Customer not found", texts)
        self.assertNotIn("BOB", texts)
        with self.assertRaises(StopIteration):
            gen.send(Resume(key="F3", values={}))


if __name__ == "__main__":
    unittest.main()
