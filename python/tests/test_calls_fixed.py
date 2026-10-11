import unittest

from _cols import cspec, dspec, fspec
from rpgle import CallError, ProgramLoader, RpgleError, parse_dds, parse_rpgle, run_program
from rpgle.loader import load_pfs
from test_calls import CallBase, MAIN_HEAD


def prog(*lines: str) -> str:
    return "\n".join(lines) + "\n"


F_OUT = fspec("OUT", "O", "F", "", "E", False)

# A fixed-form callee: *ENTRY PLIST, then PARMs naming its own fields.
LOOKUP = prog(
    fspec("CUSTMAST", "I", "F", "", "E", True),
    dspec("ID", 5, 0),
    dspec("NM", 20),
    cspec("PLIST", factor1="*ENTRY"),
    cspec("PARM", result="ID"),
    cspec("PARM", result="NM"),
    cspec("CHAIN", factor1="ID", factor2="CUSTMAST", hi="71"),
    cspec("EVAL", factor2="nm = 'nobody'", cond="71"),
    cspec("EVAL", factor2="nm = name", cond="N71"),
    cspec("EVAL", factor2="*inlr = *on"),
    cspec("RETURN"),
)


class FixedFormCallTest(CallBase):
    def run_fixed(self, *lines: str, name="main.rpgle"):
        return self.run_main(prog(F_OUT, *lines), name=name)

    def test_call_parm_and_entry_plist(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_fixed(
            dspec("WANT", 5, 0, 20),
            dspec("WHO", 20),
            cspec("CALL", factor2="'LOOKUP'"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
            cspec("EVAL", factor2="want = 99"),
            cspec("CALL", factor2="'LOOKUP'"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([m.strip() for m, _ in self.out()], ["BOB", "nobody"])

    def test_a_named_plist_shared_by_several_calls(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_fixed(
            dspec("WANT", 5, 0, 10),
            dspec("WHO", 20),
            cspec("PLIST", factor1="PL1"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("CALL", factor2="'LOOKUP'", result="PL1"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
            cspec("EVAL", factor2="want = 20"),
            cspec("CALL", factor2="'LOOKUP'", result="PL1"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([m.strip() for m, _ in self.out()], ["ALICE", "BOB"])

    def test_the_program_name_can_come_from_a_variable(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_fixed(
            dspec("PGM", 10, None, "'LOOKUP'"),
            dspec("WANT", 5, 0, 20),
            dspec("WHO", 20),
            cspec("CALL", factor2="PGM"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0].strip(), "BOB")

    def test_error_indicator_catches_a_call_that_cannot_be_made(self):
        self.run_fixed(
            cspec("CALL", factor2="'GHOST'", lo="72"),
            cspec("EVAL", factor2="msg = 'CANT CALL'", cond="72"),
            cspec("WRITE", factor2="OUTREC"),
            cspec("EVAL", factor2="msg = 'AFTER'"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([m.strip() for m, _ in self.out()], ["CANT CALL", "AFTER"])

    def test_without_an_error_indicator_a_failed_call_raises(self):
        with self.assertRaises(CallError):
            self.run_fixed(cspec("CALL", factor2="'GHOST'"))

    def test_the_lr_indicator_says_whether_the_program_finished(self):
        self.put("done.rpgle", prog(cspec("EVAL", factor2="*inlr = *on"), cspec("RETURN")))
        self.put("stays.rpgle", prog(cspec("RETURN")))
        self.run_fixed(
            cspec("CALL", factor2="'DONE'", lo="72", eq="73"),
            cspec("EVAL", factor2="msg = 'DONE LR'", cond="73"),
            cspec("WRITE", factor2="OUTREC"),
            cspec("CALL", factor2="'STAYS'", lo="72", eq="73"),
            cspec("EVAL", factor2="msg = 'STAYS LR'", cond="73"),
            cspec("EVAL", factor2="msg = 'STAYS NOT LR'", cond="N73"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([m.strip() for m, _ in self.out()], ["DONE LR", "STAYS NOT LR"])

    def test_a_program_that_leaves_lr_off_keeps_its_state(self):
        self.put(
            "counter.rpgle",
            prog(
                dspec("N", 5, 0),
                dspec("CALLS", 5, 0),
                cspec("PLIST", factor1="*ENTRY"),
                cspec("PARM", result="N"),
                cspec("EVAL", factor2="calls = calls + 1"),
                cspec("EVAL", factor2="n = calls"),
                cspec("RETURN"),
            ),
        )
        self.run_fixed(
            dspec("N", 5, 0),
            cspec("CALL", factor2="'COUNTER'"),
            cspec("PARM", result="N"),
            cspec("EVAL", factor2="num = n"),
            cspec("WRITE", factor2="OUTREC"),
            cspec("CALL", factor2="'COUNTER'"),
            cspec("PARM", result="N"),
            cspec("EVAL", factor2="num = n"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual([n for _, n in self.out()], [1.0, 2.0])

    def test_a_fixed_form_caller_can_call_a_free_form_program(self):
        self.put("custname.rpgle", """**free
dcl-pi *n; id packed(5) const; nm char(20); end-pi;
dcl-f custmast keyed;
chain id custmast;
nm = name;
*inlr = *on;
""")
        self.run_fixed(
            dspec("WANT", 5, 0, 10),
            dspec("WHO", 20),
            cspec("CALL", factor2="'CUSTNAME'"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("EVAL", factor2="msg = who"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0].strip(), "ALICE")

    def test_a_free_form_caller_can_call_a_fixed_form_program(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_main(MAIN_HEAD + """
dcl-pr lookup extpgm; id packed(5); nm char(20); end-pr;
dcl-s want packed(5) inz(10); dcl-s who char(20);
lookup(want : who);
msg = who; write outrec;
""")
        self.assertEqual(self.out()[0][0].strip(), "ALICE")

    def test_a_parameter_of_the_wrong_kind_is_a_call_error_the_indicator_catches(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_fixed(
            dspec("WANT", 5, None),  # text, where LOOKUP wants a number
            dspec("WHO", 20),
            cspec("CALL", factor2="'LOOKUP'", lo="72"),
            cspec("PARM", result="WANT"),
            cspec("PARM", result="WHO"),
            cspec("EVAL", factor2="msg = 'MISMATCH'", cond="72"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0].strip(), "MISMATCH")

    def test_fewer_parameters_than_the_callee_declares_is_allowed(self):
        self.put("lookup.rpgle", LOOKUP)
        self.run_fixed(
            dspec("WANT", 5, 0, 10),
            cspec("CALL", factor2="'LOOKUP'"),
            cspec("PARM", result="WANT"),
            cspec("EVAL", factor2="msg = 'OK'"),
            cspec("WRITE", factor2="OUTREC"),
        )
        self.assertEqual(self.out()[0][0].strip(), "OK")

    def test_entry_parm_fields_must_be_declared(self):
        self.put(
            "bad.rpgle",
            prog(cspec("PLIST", factor1="*ENTRY"), cspec("PARM", result="UNDECLARED"), cspec("RETURN")),
        )
        with self.assertRaises(RpgleError) as cm:
            self.run_fixed(dspec("X", 5, 0), cspec("CALL", factor2="'BAD'"), cspec("PARM", result="X"))
        self.assertIn("UNDECLARED", str(cm.exception))


class FixedFormCompileErrorTest(unittest.TestCase):
    def check(self, fragment, *lines):
        with self.assertRaises(RpgleError) as cm:
            list(run_program(parse_rpgle(prog(*lines)), parse_dds("")))
        self.assertIn(fragment, str(cm.exception))

    def test_parm_needs_a_call_or_plist(self):
        self.check("no CALL or PLIST", dspec("X", 5, 0), cspec("PARM", result="X"))

    def test_parm_with_factors_is_refused_rather_than_ignored(self):
        self.check("not supported", dspec("X", 5, 0), cspec("PLIST", factor1="PL"), cspec("PARM", factor1="X", result="X"))
        self.check("not supported", dspec("X", 5, 0), cspec("PLIST", factor1="PL"), cspec("PARM", factor2="X", result="X"))

    def test_parm_needs_a_field(self):
        self.check("result column", cspec("PLIST", factor1="PL"), cspec("PARM"))

    def test_call_needs_a_program(self):
        self.check("needs a program name", cspec("CALL"))

    def test_plist_needs_a_name(self):
        self.check("needs a name", cspec("PLIST"))

    def test_parms_after_an_unrelated_op_no_longer_belong_to_the_call(self):
        self.check(
            "no CALL or PLIST",
            dspec("X", 5, 0),
            cspec("CALL", factor2="'GHOST'", lo="72"),
            cspec("EVAL", factor2="x = 1"),
            cspec("PARM", result="X"),
        )

    def test_naming_a_plist_that_does_not_exist(self):
        self.check("not defined", cspec("CALL", factor2="'GHOST'", result="NOPL"))


if __name__ == "__main__":
    unittest.main()
