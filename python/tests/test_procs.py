import unittest

from rpgle import Resume, RpgleError, parse_dds, parse_rpgle, run_program
from test_fileio import PFS
from test_freeform import FreeBase


class ProcParseTest(unittest.TestCase):
    def test_procedure_shape(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-s g char(1);\n"
            "g = 'x';\n"
            "dcl-proc total export;\n"
            "  dcl-pi *n packed(9:2);\n"
            "    a packed(9:2) const;\n"
            "    b packed(5) value;\n"
            "    c char(10);\n"
            "    d packed(3) options(*nopass) const;\n"
            "  end-pi;\n"
            "  dcl-s t packed(9:2);\n"
            "  t = a + b;\n"
            "  return t;\n"
            "end-proc;\n"
        )
        self.assertEqual([s.expr for s in prog.statements], ["g = 'x'"])  # main line only
        (p,) = prog.procs
        self.assertEqual((p.name, p.export), ("total", True))
        self.assertEqual((p.returns.length, p.returns.dec), (9, 2))
        self.assertEqual(
            [(x.spec.name, x.mode, x.nopass) for x in p.params],
            [("a", "const", False), ("b", "value", False), ("c", "ref", False), ("d", "const", True)],
        )
        self.assertEqual([d.name for d in p.dspecs], ["t"])
        self.assertEqual([s.op for s in p.statements], ["EVAL", "RETURN"])
        self.assertEqual(p.statements[1].expr, "t")

    def test_prototypes_are_skipped_and_calls_become_callp(self):
        prog = parse_rpgle(
            "**free\n"
            "dcl-pr bump;\n  n packed(5);\nend-pr;\n"
            "dcl-s c packed(5);\n"
            "bump(c);\n"
            "callp bump(c);\n"
            "callp(n) bump(c);\n"
            "x = f(1) + 2;\n"
        )
        self.assertEqual([(s.op, s.expr) for s in prog.statements][:3], [("CALLP", "bump(c)")] * 3)
        self.assertEqual(prog.statements[3].op, "EVAL")  # a call inside an expression stays an expression
        self.assertEqual(prog.procs, [])

    def test_errors(self):
        for src, fragment in [
            ("**free\ndcl-proc a;\n", "end-proc"),
            ("**free\ndcl-proc a;\ndcl-proc b;\nend-proc;\n", "end-proc missing"),
            ("**free\nend-proc;\n", "no dcl-proc"),
            ("**free\ndcl-proc a;\nend-proc;\ndcl-proc a;\nend-proc;\n", "twice"),
            ("**free\ndcl-pi *n;\nend-pi;\n", "outside a procedure"),
            ("**free\ndcl-proc a;\ndcl-pi *n;\n  x packed(3);\n", "end-pi"),
            ("**free\ndcl-proc a;\ndcl-pi *n;\n  x packed(3);\n  x packed(3);\nend-pi;\nend-proc;\n", "declared twice"),
            ("**free\ndcl-proc a;\ndcl-pi *n;\n  x packed(3) options(*omit);\nend-pi;\nend-proc;\n", "options"),
            ("**free\ndcl-proc a;\ndcl-f custmast;\nend-proc;\n", "dcl-f inside a procedure"),
            ("**free\ndcl-proc a extproc('X');\nend-proc;\n", "not supported"),
            ("**free\ncallp(e) x(1);\n", "%ERROR"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    parse_rpgle(src)
                self.assertIn(fragment, str(cm.exception))


DOUBLE = """
dcl-proc double;
  dcl-pi *n packed(9:2);
    n packed(9:2) const;
  end-pi;
  return n * 2;
end-proc;
"""


class ProcRunTest(FreeBase):
    def test_function_in_an_expression(self):
        self.run_free("""
dcl-s r packed(9:2);
r = double(21) + 1;
num = r; write outrec;
return;
""" + DOUBLE)
        self.assertEqual(self.nums(), [43.0])

    def test_const_accepts_a_variable_a_literal_and_an_expression(self):
        self.run_free("""
dcl-s v packed(9:2) inz(5);
num = double(v); write outrec;
num = double(7); write outrec;
num = double(v + 1); write outrec;
return;
""" + DOUBLE)
        self.assertEqual(self.nums(), [10.0, 14.0, 12.0])

    def test_by_reference_changes_the_callers_variable(self):
        self.run_free("""
dcl-s c packed(5) inz(1);
bump(c); bump(c); callp bump(c);
num = c; write outrec;
return;
dcl-proc bump;
  dcl-pi *n; n packed(5); end-pi;
  n += 10;
end-proc;
""")
        self.assertEqual(self.nums(), [31.0])

    def test_by_value_leaves_the_callers_variable_alone(self):
        self.run_free("""
dcl-s c packed(5) inz(1);
dcl-s r packed(5);
r = bump(c);
num = c; write outrec;
num = r; write outrec;
return;
dcl-proc bump;
  dcl-pi *n packed(5); n packed(5) value; end-pi;
  n += 10;
  return n;
end-proc;
""")
        self.assertEqual(self.nums(), [1.0, 11.0])

    def test_by_reference_needs_a_variable(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("""
bump(5);
return;
dcl-proc bump; dcl-pi *n; n packed(5); end-pi; n += 1; end-proc;
""")
        self.assertIn("by reference", str(cm.exception))

    def test_nopass_parameter_uses_its_default(self):
        self.run_free("""
num = add(1); write outrec;
num = add(1 : 5); write outrec;
return;
dcl-proc add;
  dcl-pi *n packed(9:2); a packed(9:2) const; b packed(9:2) options(*nopass) const; end-pi;
  return a + b;
end-proc;
""")
        self.assertEqual(self.nums(), [1.0, 6.0])

    def test_wrong_argument_counts_and_types(self):
        body = "return;\ndcl-proc f; dcl-pi *n packed(5); a packed(5) const; end-pi; return a; end-proc;\n"
        for src, fragment in [
            ("num = f(); write outrec;\n", "missing argument"),
            ("num = f(1 : 2); write outrec;\n", "takes 1"),
            ("num = nosuch(1); write outrec;\n", "Unknown"),
            ("nosuch(1);\n", "external"),
        ]:
            with self.subTest(src=src):
                with self.assertRaises(RpgleError) as cm:
                    self.run_free(src + body)
                self.assertIn(fragment, str(cm.exception))

    def test_by_reference_type_mismatch(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("""
dcl-s t char(5);
bump(t);
return;
dcl-proc bump; dcl-pi *n; n packed(5); end-pi; n += 1; end-proc;
""")
        self.assertIn("expects a number", str(cm.exception))

    def test_recursion_has_fresh_locals_per_call(self):
        self.run_free("""
num = fact(10); write outrec;
num = fact(1); write outrec;
return;
dcl-proc fact;
  dcl-pi *n packed(15); n packed(5) const; end-pi;
  dcl-s r packed(15);
  if n <= 1;
    return 1;
  endif;
  r = n * fact(n - 1);
  return r;
end-proc;
""")
        self.assertEqual(self.nums(), [3628800.0, 1.0])

    def test_reasonably_deep_recursion(self):
        self.run_free("""
num = depth(25); write outrec;
return;
dcl-proc depth;
  dcl-pi *n packed(5); n packed(5) const; end-pi;
  if n = 0;
    return 0;
  endif;
  return 1 + depth(n - 1);
end-proc;
""")
        self.assertEqual(self.nums(), [25.0])

    def test_runaway_recursion_is_an_error_not_a_crash(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("""
num = forever(1); write outrec;
return;
dcl-proc forever;
  dcl-pi *n packed(5); n packed(5) const; end-pi;
  return forever(n);
end-proc;
""")
        self.assertIn("too deeply", str(cm.exception))

    def test_locals_shadow_globals_and_start_fresh_every_call(self):
        self.run_free("""
dcl-s x packed(5) inz(100);
dcl-s a packed(5); dcl-s b packed(5);
a = tick(); b = tick();
num = a; write outrec;
num = b; write outrec;
num = x; write outrec;
return;
dcl-proc tick;
  dcl-pi *n packed(5); end-pi;
  dcl-s x packed(5) inz(1);
  x += 1;
  return x;
end-proc;
""")
        self.assertEqual(self.nums(), [2.0, 2.0, 100.0])

    def test_procedures_see_globals_and_may_change_them(self):
        self.run_free("""
dcl-s g packed(5) inz(7);
setg();
num = g; write outrec;
return;
dcl-proc setg;
  dcl-pi *n; end-pi;
  g = g * 2;
end-proc;
""")
        self.assertEqual(self.nums(), [14.0])

    def test_indicators_are_global(self):
        self.run_free("""
flag();
if *in90; msg = 'ON'; endif;
write outrec;
return;
dcl-proc flag; dcl-pi *n; end-pi; *in90 = *on; end-proc;
""")
        self.assertEqual(self.msgs(), ["ON"])

    def test_ind_returning_function_works_in_conditions(self):
        self.run_free("""
dcl-s x packed(5) inz(150);
if big(x) and not big(5);
  msg = 'OK';
endif;
write outrec;
return;
dcl-proc big;
  dcl-pi *n ind; n packed(5) const; end-pi;
  return n > 100;
end-proc;
""")
        self.assertEqual(self.msgs(), ["OK"])

    def test_text_function_and_return_conversion(self):
        self.run_free("""
msg = greet('Bob');
write outrec;
num = half(7); write outrec;
return;
dcl-proc greet;
  dcl-pi *n varchar(30); who char(10) const; end-pi;
  return 'Hello ' + %trim(who);
end-proc;
dcl-proc half;
  dcl-pi *n packed(5); n packed(5) const; end-pi;
  return n / 2;
end-proc;
""")
        self.assertEqual(self.msgs()[0], "Hello Bob")
        self.assertEqual(self.nums()[1], 3.0)  # converted to packed(5): truncated, not 3.5

    def test_a_function_must_return_a_value(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("""
num = oops(); write outrec;
return;
dcl-proc oops; dcl-pi *n packed(5); end-pi; dcl-s x packed(5); x = 1; end-proc;
""")
        self.assertIn("must RETURN", str(cm.exception))

    def test_early_return_from_inside_loops(self):
        self.run_free("""
num = firstover(3); write outrec;
return;
dcl-proc firstover;
  dcl-pi *n packed(5); limit packed(5) const; end-pi;
  dcl-s i packed(5);
  for i = 1 to 100;
    dow 1 = 1;
      if i * i > limit; return i; endif;
      leave;
    enddo;
  endfor;
  return 0;
end-proc;
""")
        self.assertEqual(self.nums(), [2.0])

    def test_subroutine_names_are_local_to_each_procedure(self):
        self.run_free("""
dcl-s a packed(5); dcl-s b packed(5);
a = one(); b = two();
num = a; write outrec;
num = b; write outrec;
return;
dcl-proc one; dcl-pi *n packed(5); end-pi;
  dcl-s r packed(5);
  exsr go; return r;
  begsr go; r = 1; endsr;
end-proc;
dcl-proc two; dcl-pi *n packed(5); end-pi;
  dcl-s r packed(5);
  exsr go; return r;
  begsr go; r = 2; endsr;
end-proc;
""")
        self.assertEqual(self.nums(), [1.0, 2.0])

    def test_main_line_subroutines_are_not_visible_inside_procedures(self):
        with self.assertRaises(RpgleError) as cm:
            self.run_free("""
p();
return;
begsr mine; msg = 'x'; endsr;
dcl-proc p; dcl-pi *n; end-pi; exsr mine; end-proc;
""")
        self.assertIn("Unknown RPG subroutine", str(cm.exception))

    def test_procedures_can_use_files_and_local_data_structures(self):
        self.run_free("""
msg = who(20); write outrec;
msg = who(99); write outrec;
return;
dcl-proc who;
  dcl-pi *n char(20); id packed(5) const; end-pi;
  dcl-ds c qualified likerec(custrec);
  chain id custmast c;
  if not %found(custmast);
    return 'nobody';
  endif;
  return c.name;
end-proc;
""")
        self.assertEqual([m.strip() for m in self.msgs()], ["BOB", "nobody"])

    def test_prototype_before_the_procedure_is_harmless(self):
        self.run_free("""
dcl-pr double packed(9:2);
  n packed(9:2) const;
end-pr;
num = double(4); write outrec;
return;
""" + DOUBLE)
        self.assertEqual(self.nums(), [8.0])

    def test_procedures_may_come_before_the_main_line(self):
        self.run_free(DOUBLE + """
num = double(4); write outrec;
""")
        self.assertEqual(self.nums(), [8.0])

    def test_procedures_call_each_other(self):
        self.run_free("""
num = quad(3); write outrec;
return;
dcl-proc quad; dcl-pi *n packed(9:2); n packed(9:2) const; end-pi; return twice(twice(n)); end-proc;
dcl-proc twice; dcl-pi *n packed(9:2); n packed(9:2) const; end-pi; return n * 2; end-proc;
""")
        self.assertEqual(self.nums(), [12.0])


class ProcScreenTest(FreeBase):
    DDS = """
     A          R INQ                       CF03(03)
     A            CUSTNO         5  0B  3 20
     A            NAME          20A  O  5 20
"""

    def test_a_procedure_called_with_callp_can_show_screens(self):
        prog = parse_rpgle("""**free
dcl-f custmast keyed;
dcl-f custinq workstn;
ask();
*inlr = *on;
return;
dcl-proc ask;
  dcl-pi *n; end-pi;
  exfmt inq;
  dow not *in03;
    chain custno custmast;
    exfmt inq;
  enddo;
end-proc;
""")
        gen = run_program(prog, parse_dds(self.DDS), self.db, PFS)
        screen = next(gen)  # the EXFMT inside the procedure reaches the caller
        screen = gen.send(Resume(key="ENTER", values={"custno": "20"}))
        self.assertIn("BOB", [f.text.strip() for f in screen.fields])
        with self.assertRaises(StopIteration):
            gen.send(Resume(key="F3", values={}))

    def test_a_screen_inside_an_expression_called_procedure_is_refused(self):
        prog = parse_rpgle("""**free
dcl-f custinq workstn;
dcl-s r packed(5);
r = ask();
return;
dcl-proc ask;
  dcl-pi *n packed(5); end-pi;
  exfmt inq;
  return 1;
end-proc;
""")
        gen = run_program(prog, parse_dds(self.DDS), self.db, PFS)
        with self.assertRaises(RpgleError) as cm:
            next(gen)
        self.assertIn("CALLP", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
