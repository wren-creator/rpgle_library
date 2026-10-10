import unittest

from rpgle import Context, RpgleError, Var, eval_expr, split_assignment


def empty_ctx() -> Context:
    return Context(vars={}, indicators={})


class TestAndOrAlwaysConsumeBothOperands(unittest.TestCase):
    # Regression test: _parse_and/_parse_or must not short-circuit token
    # consumption, or the right side is left unparsed and the parser
    # raises a spurious trailing-tokens error.
    def test_cases(self):
        ctx = empty_ctx()
        cases = [
            ("1 > 2 and 3 > 4", False),
            ("1 < 2 and 3 < 4", True),
            ("1 > 2 or 3 < 4", True),
            ("1 > 2 or 3 > 4", False),
        ]
        for src, want in cases:
            with self.subTest(src=src):
                self.assertEqual(eval_expr(src, ctx), want)


class TestComparisonOperators(unittest.TestCase):
    def test_cases(self):
        ctx = empty_ctx()
        checks = {
            "1 = 1": True,
            "1 <> 2": True,
            "1 <= 1": True,
            "2 >= 3": False,
            "1 < 2": True,
            "2 > 1": True,
            "'AB' = 'AB'": True,
            "'AB' <> 'CD'": True,
        }
        for src, want in checks.items():
            with self.subTest(src=src):
                self.assertEqual(eval_expr(src, ctx), want)


class TestArithmeticPrecedence(unittest.TestCase):
    def test_cases(self):
        ctx = empty_ctx()
        checks = {
            "2 + 3 * 4": 14,
            "(2 + 3) * 4": 20,
            "10 - 2 - 3": 5,
            "-5 + 2": -3,
        }
        for src, want in checks.items():
            with self.subTest(src=src):
                self.assertEqual(eval_expr(src, ctx), want)


class TestBuiltinFunctions(unittest.TestCase):
    def test_functions(self):
        ctx = empty_ctx()
        self.assertEqual(eval_expr("%TRIM('  hi  ')", ctx), "hi")
        self.assertEqual(eval_expr("%LEN('hello')", ctx), 5)
        self.assertEqual(eval_expr("%SUBST('hello' : 2)", ctx), "ello")
        self.assertEqual(eval_expr("%SUBST('hello' : 2 : 3)", ctx), "ell")


class TestFigurativeConstants(unittest.TestCase):
    def test_constants(self):
        ctx = Context(vars={}, indicators={"90": True, "80": False, "LR": True, "RT": False})
        self.assertEqual(eval_expr("*ON", ctx), True)
        self.assertEqual(eval_expr("*OFF", ctx), False)
        self.assertEqual(eval_expr("*IN90", ctx), True)
        self.assertEqual(eval_expr("*IN80", ctx), False)
        self.assertEqual(eval_expr("*INLR", ctx), True)
        self.assertEqual(eval_expr("*INRT", ctx), False)


class TestVariableLookup(unittest.TestCase):
    def test_lookup(self):
        ctx = Context(vars={"plrhp": Var(type="N", length=3, dec=0, value=20.0)}, indicators={})
        self.assertEqual(eval_expr("plrhp", ctx), 20)
        self.assertEqual(eval_expr("plrhp > 10", ctx), True)


class TestSplitAssignment(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_assignment("plrhp = 20"), ("plrhp", "20"))
        self.assertEqual(split_assignment("loctxt = %TRIM(loctxt)"), ("loctxt", "%TRIM(loctxt)"))


def ctx_with(**vars_) -> Context:
    return Context(vars=vars_, indicators={})


class TestBuiltIns(unittest.TestCase):
    def check(self, cases, ctx=None):
        ctx = ctx or empty_ctx()
        for src, want in cases:
            with self.subTest(src=src):
                self.assertEqual(eval_expr(src, ctx), want)

    def test_numeric(self):
        self.check(
            [
                ("%abs(-5)", 5.0),
                ("%abs(3)", 3.0),
                ("%int(7.9)", 7.0),
                ("%int(-7.9)", -7.0),
                ("%inth(7.5)", 8.0),
                ("%inth(-7.5)", -8.0),
                ("%dec('12.345' : 5 : 2)", 12.34),
                ("%dech('12.345' : 5 : 2)", 12.35),
                ("%rem(17 : 5)", 2.0),
                ("%div(17 : 5)", 3.0),
                ("%sqrt(16)", 4.0),
                ("%min(4 : 2 : 9)", 2.0),
                ("%max(4 : 2 : 9)", 9.0),
                ("%min('b' : 'a')", "a"),
            ]
        )

    def test_text(self):
        self.check(
            [
                ("%upper('abC')", "ABC"),
                ("%lower('abC')", "abc"),
                ("%triml('  a  ')", "a  "),
                ("%trimr('  a  ')", "  a"),
                ("%trim('  a  ')", "a"),
                ("%scan('lo' : 'hello world')", 4.0),
                ("%scan('o' : 'hello world' : 6)", 8.0),
                ("%scan('zz' : 'hello')", 0.0),
                ("%replace('XY' : 'hello' : 2 : 3)", "hXYo"),
                ("%replace('XY' : 'hello' : 3 : 0)", "heXYllo"),
                ("%replace('XY' : 'hello')", "XYllo"),
                ("%subst('hello' : 2 : 3)", "ell"),
                ("%len('abc')", 3.0),
            ]
        )

    def test_char_uses_the_variables_declared_decimals(self):
        ctx = ctx_with(
            money=Var(type="N", length=9, dec=2, value=5.0),
            whole=Var(type="N", length=5, dec=0, value=5.0),
        )
        self.check([("%char(money)", "5.00"), ("%char(whole)", "5"), ("%char(money + 1)", "6"), ("%char('x')", "x")], ctx)

    def test_string_plus_joins_text(self):
        self.check([("'ab' + 'cd'", "abcd"), ("'a' + %char(5) + 'b'", "a5b"), ("1 + 2", 3.0)])

    def test_text_and_number_do_not_mix(self):
        for src in ("'a' + 1", "1 + 'a'", "'a' - 'b'"):
            with self.subTest(src=src):
                with self.assertRaises(RpgleError):
                    eval_expr(src, empty_ctx())

    def test_errors(self):
        for src in ("%rem(1 : 0)", "%div(1 : 0)", "%sqrt(-1)", "%dec('abc' : 5 : 2)", "%scan('a')", "%nosuch(1)"):
            with self.subTest(src=src):
                with self.assertRaises(RpgleError):
                    eval_expr(src, empty_ctx())

    def test_not_precedence(self):
        self.check([("not 1 = 2", True), ("not 1 = 1 or 2 = 2", True), ("not (1 = 1 and 2 = 2)", False)])


if __name__ == "__main__":
    unittest.main()
