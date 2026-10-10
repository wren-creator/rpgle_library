import unittest

from _cols import dspec
from rpgle import RpgleError, parse_rpgle
from test_freeform import FreeBase


class ArrayParseTest(unittest.TestCase):
    def test_dim_on_dcl_s_and_dcl_ds(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-c max 4;\n"
            "dcl-s a packed(5) dim(10);\n"
            "dcl-s b char(3) dim(max) inz('x');\n"
            "dcl-ds items qualified dim(3);\n  qty packed(5);\nend-ds;\n"
        )
        d = {x.name: x for x in prog.dspecs}
        self.assertEqual((d["a"].dim, d["b"].dim, d["b"].init), (10, 4, "'x'"))
        self.assertEqual(prog.dss[0].dim, 3)

    def test_sorta_forms(self):
        prog = parse_rpgle("**free\nsorta a;\nsorta(d) a;\nsorta(a) a;\n")
        self.assertEqual(
            [(s.op, s.expr, s.down) for s in prog.statements],
            [("SORTA", "a", False), ("SORTA", "a", True), ("SORTA", "a", False)],
        )

    def test_assignment_to_elements_and_subfields_is_an_assignment(self):
        prog = parse_rpgle("**free\na(1) = 5;\nitems(i + 1).qty += 2;\n")
        self.assertEqual([s.expr for s in prog.statements], ["a(1) = 5", "items(i + 1).qty = items(i + 1).qty + (2)"])

    def test_errors(self):
        for src, fragment in [
            ("**free\ndcl-s a char(3) dim(0);\n", "at least one"),
            ("**free\ndcl-s a char(3) dim(nope);\n", "dcl-c"),
            ("**free\ndcl-ds a dim(3);\n  x char(1);\nend-ds;\n", "qualified"),
            ("**free\ndcl-ds a qualified;\n  x char(1) dim(3);\nend-ds;\n", "inside data structures"),
            ("**free\nsorta 1;\n", "array name"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    parse_rpgle(src)
                self.assertIn(fragment, str(cm.exception))

    def test_fixed_form_dim_is_refused_not_ignored(self):
        line = dspec("ARR", 5, 0) + " DIM(10)"
        with self.assertRaises(RpgleError) as cm:
            parse_rpgle(line)
        self.assertIn("fixed-form", str(cm.exception))


class ArrayRunTest(FreeBase):
    def test_elements_default_to_blank_or_zero_and_take_inz(self):
        self.run_free("""
dcl-s n packed(5) dim(3);
dcl-s t char(4) dim(2) inz('ab');
num = n(2); write outrec;
msg = t(2); write outrec;
""")
        self.assertEqual(self.nums()[0], 0.0)
        self.assertEqual(self.msgs()[1], "ab")

    def test_read_write_and_index_expressions(self):
        self.run_free("""
dcl-s n packed(5) dim(5);
dcl-s i packed(3); dcl-s p packed(3) dim(2);
for i = 1 to 5; n(i) = i * 10; endfor;
p(1) = 2; p(2) = 4;
n(p(1)) += 1;
num = n(2); write outrec;
num = n(p(2) + 1); write outrec;
num = n(i - 1); write outrec;
""")
        self.assertEqual(self.nums(), [21.0, 50.0, 50.0])  # i is 6 after the loop, so n(i - 1) is n(5)

    def test_out_of_range_is_an_error_not_wraparound(self):
        for idx in (0, 4, -1):
            with self.subTest(idx=idx):
                with self.assertRaises(RpgleError) as cm:
                    self.run_free(f"dcl-s n packed(5) dim(3);\nnum = n({idx}); write outrec;\n")
                self.assertIn("out of range", str(cm.exception))
        with self.assertRaises(RpgleError):
            self.run_free("dcl-s n packed(5) dim(3);\nn(4) = 1;\n")

    def test_elem_in_a_loop(self):
        self.run_free("""
dcl-s n packed(5) dim(6);
dcl-s i packed(3); dcl-s t packed(5);
for i = 1 to %elem(n); n(i) = i; endfor;
for i = 1 to %elem(n); t += n(i); endfor;
num = %elem(n); write outrec;
num = t; write outrec;
""")
        self.assertEqual(self.nums(), [6.0, 21.0])

    def test_lookup(self):
        self.run_free("""
dcl-s w char(5) dim(4);
dcl-s n packed(5) dim(4);
dcl-s f ind dim(3);
w(1) = 'red'; w(2) = 'green'; w(3) = 'red'; w(4) = 'blue';
n(1) = 5; n(2) = 7; n(3) = 9; n(4) = 7;
f(2) = *on;
num = %lookup('red' : w); write outrec;
num = %lookup('red' : w : 2); write outrec;
num = %lookup('pink' : w); write outrec;
num = %lookup(7 : n); write outrec;
num = %lookup(7 : n : 3); write outrec;
num = %lookup(7 : n : 1 : 1); write outrec;
num = %lookup(*on : f); write outrec;
""")
        self.assertEqual(self.nums(), [1.0, 3.0, 0.0, 2.0, 4.0, 0.0, 2.0])

    def test_sorta(self):
        self.run_free("""
dcl-s n packed(5:1) dim(5);
dcl-s w char(6) dim(3);
n(1) = 3.5; n(2) = 1; n(3) = 9; n(4) = 2; n(5) = 3.25;
w(1) = 'pear'; w(2) = 'apple'; w(3) = 'fig';
sorta n;
num = n(1); write outrec; num = n(5); write outrec;
sorta(d) n;
num = n(1); write outrec; num = n(5); write outrec;
sorta w;
msg = w(1); write outrec; msg = w(3); write outrec;
""")
        self.assertEqual(self.nums()[:4], [1.0, 9.0, 9.0, 1.0])
        self.assertEqual(self.msgs()[4:], ["apple", "pear"])

    def test_clear_and_whole_array_assignment(self):
        self.run_free("""
dcl-s n packed(5) dim(3); dcl-s m packed(5) dim(3); dcl-s t char(3) dim(2);
n(1) = 1; n(2) = 2; n(3) = 3;
m = n;
n(1) = 99;
num = m(1); write outrec;
clear n;
num = n(1) + n(2) + n(3); write outrec;
m = 7;
num = m(2); write outrec;
t(1) = 'a'; t(2) = 'b';
t = *blanks;
msg = t(1) + t(2) + 'x'; write outrec;
n(2) = 5; clear n(2);
num = n(2); write outrec;
""")
        self.assertEqual(self.nums()[:3], [1.0, 0.0, 7.0])
        self.assertEqual(self.msgs()[3].strip(), "x")  # two blank 3-character elements, then x
        self.assertEqual(self.nums()[4], 0.0)

    def test_assigning_arrays_of_different_sizes_is_an_error(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("dcl-s a packed(3) dim(2); dcl-s b packed(3) dim(3);\na = b;\n")
        self.assertIn("elements", str(cm.exception))

    def test_elements_keep_their_own_decimals(self):
        self.run_free("""
dcl-s m packed(9:2) dim(2);
m(1) = 1.5;
msg = %char(m(1)); write outrec;
m(2) = 1.256;
num = m(2); write outrec;
""")
        self.assertEqual(self.msgs()[0], "1.50")
        self.assertEqual(self.nums()[1], 1.25)  # truncated to two decimals, like any packed(9:2)

    def test_an_array_name_alone_is_an_error_with_a_hint(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("dcl-s n packed(5) dim(3);\nnum = n; write outrec;\n")
        self.assertIn("n(index)", str(cm.exception))

    def test_reading_a_file_into_an_array_and_sorting_it(self):
        self.run_free("""
dcl-s bals packed(9:2) dim(10);
dcl-s count packed(3);
setll *loval custmast;
read custmast;
dow not %eof(custmast) and count < %elem(bals);
  count += 1;
  bals(count) = bal;
  read custmast;
enddo;
sorta(d) bals;
num = count; write outrec;
num = bals(1); write outrec;
num = bals(2); write outrec;
""")
        self.assertEqual(self.nums(), [3.0, 100.0, 50.5])


class DsArrayTest(FreeBase):
    def test_subfields_by_element(self):
        self.run_free("""
dcl-ds line qualified dim(3);
  sku char(6);
  qty packed(5);
  price packed(7:2);
end-ds;
dcl-s i packed(3); dcl-s total packed(9:2);
for i = 1 to %elem(line);
  line(i).sku = 'SKU' + %char(i);
  line(i).qty = i * 2;
  line(i).price = 1.5;
endfor;
for i = 1 to %elem(line);
  total += line(i).qty * line(i).price;
endfor;
msg = line(2).sku; num = total; write outrec;
line(3).qty += 100;
num = line(3).qty; write outrec;
""")
        self.assertEqual(self.msgs()[0].strip(), "SKU2")
        self.assertEqual(self.nums(), [18.0, 106.0])

    def test_clear_the_whole_array_or_one_element(self):
        self.run_free("""
dcl-ds r qualified dim(2); q packed(3); end-ds;
r(1).q = 5; r(2).q = 6;
clear r(1);
num = r(1).q; write outrec;
num = r(2).q; write outrec;
clear r;
num = r(2).q; write outrec;
""")
        self.assertEqual(self.nums(), [0.0, 6.0, 0.0])

    def test_layout_can_be_borrowed_with_likerec_and_likeds(self):
        self.run_free("""
dcl-ds rows qualified dim(2) likerec(custrec);
dcl-ds more likeds(rows) dim(3);
rows(1).name = 'ZOE';
more(3).bal = 4.5;
msg = rows(1).name; write outrec;
num = more(3).bal; write outrec;
num = %elem(more); write outrec;
""")
        self.assertEqual(self.msgs()[0], "ZOE")
        self.assertEqual(self.nums()[1:], [4.5, 3.0])

    def test_mistakes_are_named(self):
        for body, fragment in [
            ("dcl-ds r qualified dim(2); q packed(3); end-ds;\nnum = r(1); write outrec;\n", "name a subfield"),
            ("dcl-ds r qualified dim(2); q packed(3); end-ds;\nnum = r(1).nope; write outrec;\n", "no subfield"),
            ("dcl-ds r qualified dim(2); q packed(3); end-ds;\nnum = r(3).q; write outrec;\n", "out of range"),
        ]:
            with self.subTest(body=body):
                with self.assertRaises(RpgleError) as cm:
                    self.run_free(body)
                self.assertIn(fragment, str(cm.exception))


class ArrayParamTest(FreeBase):
    FILL = """
dcl-proc fill;
  dcl-pi *n; a packed(5) dim(4); start packed(5) const; end-pi;
  dcl-s i packed(3);
  for i = 1 to %elem(a); a(i) = start + i; endfor;
end-proc;
dcl-proc total;
  dcl-pi *n packed(9); a packed(5) dim(4) const; end-pi;
  dcl-s i packed(3); dcl-s t packed(9);
  for i = 1 to %elem(a); t += a(i); endfor;
  return t;
end-proc;
"""

    def test_by_reference_arrays_are_shared_with_the_caller(self):
        self.run_free("""
dcl-s n packed(5) dim(4);
fill(n : 10);
num = n(1); write outrec;
num = n(4); write outrec;
num = total(n); write outrec;
return;
""" + self.FILL)
        self.assertEqual(self.nums(), [11.0, 14.0, 50.0])

    def test_by_value_arrays_are_copies(self):
        self.run_free("""
dcl-s n packed(5) dim(4);
n(1) = 1;
bump(n);
num = n(1); write outrec;
return;
dcl-proc bump;
  dcl-pi *n; a packed(5) dim(4) value; end-pi;
  a(1) = 99;
end-proc;
""")
        self.assertEqual(self.nums(), [1.0])

    def test_local_arrays_are_fresh_per_call(self):
        self.run_free("""
num = count(); write outrec;
num = count(); write outrec;
return;
dcl-proc count;
  dcl-pi *n packed(5); end-pi;
  dcl-s hits packed(5) dim(2);
  hits(1) += 1;
  return hits(1);
end-proc;
""")
        self.assertEqual(self.nums(), [1.0, 1.0])

    def test_wrong_shapes_are_refused(self):
        for call, fragment in [
            ("fill(m : 1);", "dim(4)"),
            ("fill(5 : 1);", "pass a whole array"),
            ("bad(n);", "single value"),
        ]:
            with self.subTest(call=call):
                with self.assertRaises(RpgleError) as cm:
                    self.run_free(
                        "dcl-s n packed(5) dim(4); dcl-s m packed(5) dim(3);\n"
                        + call
                        + "\nreturn;\n"
                        + self.FILL
                        + "dcl-proc bad; dcl-pi *n; x packed(5); end-pi; end-proc;\n"
                    )
                self.assertIn(fragment, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
