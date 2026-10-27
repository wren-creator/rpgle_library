"""What a host that runs other people's programs needs: a cap on instructions between screens, and
programs the host answers itself (QCMDEXC in Silverlake's case)."""

import unittest

from _cols import cspec, dspec
from rpgle import CallError, RpgleError, parse_dds, parse_rpgle, run_program


def src(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def run_all(program, **kw):
    gen = run_program(parse_rpgle(program), parse_dds(""), **kw)
    for _ in gen:
        pass


LOOP = src(
    dspec("N", 5, 0),
    cspec("DOW", factor2="*in01 = *off"),
    cspec("EVAL", factor2="n = n + 1"),
    cspec("ENDDO"),
)


class StepCapTest(unittest.TestCase):
    def test_a_runaway_loop_stops_with_an_error_instead_of_hanging(self):
        with self.assertRaises(RpgleError) as cm:
            run_all(LOOP, max_steps=1000)
        self.assertIn("1000 instructions", str(cm.exception))

    def test_no_cap_by_default_so_a_finite_loop_runs_to_the_end(self):
        run_all(src(
            dspec("N", 5, 0),
            cspec("DOW", factor2="n < 500"),
            cspec("EVAL", factor2="n = n + 1"),
            cspec("ENDDO"),
        ))

    def test_a_cap_larger_than_the_work_is_not_hit(self):
        run_all(src(
            dspec("N", 5, 0),
            cspec("DOW", factor2="n < 50"),
            cspec("EVAL", factor2="n = n + 1"),
            cspec("ENDDO"),
        ), max_steps=10_000)


class SystemProgramTest(unittest.TestCase):
    CALLER = src(
        dspec("CMD", 20),
        dspec("LEN", 15, 5),
        cspec("EVAL", factor2="cmd = 'DSPLIBL'"),
        cspec("EVAL", factor2="len = 7"),
        cspec("CALL", factor2="'QCMDEXC'"),
        cspec("PARM", result="CMD"),
        cspec("PARM", result="LEN"),
    )

    def test_call_runs_the_hook_with_the_argument_values(self):
        seen = []
        run_all(self.CALLER, system_programs={"qcmdexc": seen.append})
        self.assertEqual(seen[0][0].strip(), "DSPLIBL")
        self.assertEqual(float(seen[0][1]), 7.0)

    def test_a_library_prefix_still_reaches_the_hook(self):
        seen = []
        run_all(self.CALLER.replace("'QCMDEXC'", "'QSYS/QCMDEXC'"), system_programs={"QCMDEXC": seen.append})
        self.assertEqual(len(seen), 1)

    def test_the_hook_can_refuse_with_a_call_error(self):
        def refuse(args):
            raise CallError("not allowed")
        with self.assertRaises(CallError):
            run_all(self.CALLER, system_programs={"QCMDEXC": refuse})

    def test_other_names_still_go_to_the_loader(self):
        with self.assertRaises(CallError):
            run_all(self.CALLER.replace("'QCMDEXC'", "'OTHER'"), system_programs={"QCMDEXC": lambda a: None})


if __name__ == "__main__":
    unittest.main()
