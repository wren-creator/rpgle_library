import unittest

from rpgle import Context, Var, eval_expr, split_assignment


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


if __name__ == "__main__":
    unittest.main()
