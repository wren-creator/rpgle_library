"""
quick_math -- minimal rpgle_library example.

A tiny RPG program that computes 17 DIV 5 using the classic RPG
DIV/MVR idiom for modulo (RPG has no %REM until well past V4R3),
then displays the result through a single EXFMT. No file I/O, no
subroutines, no control-flow beyond one instruction pair -- just
enough to show parse_dds/parse_rpgle/run_program working end-to-end.
"""

from rpgle import Resume, parse_dds, parse_rpgle, run_program

DDS_SOURCE = """
     A                                      DSPSIZ(24 80 *DS3)

     A          R RESULT
     A                                  1  2'17 DIV 5, via RPG DIV/MVR:'
     A                                  3  2'Quotient:'
     A            QUOT           5  0O  3 13
     A                                  4  2'Remainder:'
     A            REM            5  0O  4 13
     A                                 22  2'Press Enter to continue'
"""

RPG_SOURCE = """
     FRESULT    CF   E             WORKSTN

     Da                S              5  0 INZ(17)
     Db                S              5  0 INZ(5)

     C     a             DIV       b             quot
     C                   MVR                     rem
     C                   EXFMT     RESULT
     C                   EVAL      *inlr = *on
     C                   RETURN
"""


def main() -> None:
    dds = parse_dds(DDS_SOURCE)
    prog = parse_rpgle(RPG_SOURCE)

    gen = run_program(prog, dds)
    screen = next(gen)  # runs DIV/MVR, then yields at the EXFMT

    print(f"\n{screen.format_name}")
    for f in screen.fields:
        print(f.text.rstrip())

    try:
        gen.send(Resume(key="ENTER", values={}))
        print("\n(unexpected: program did not end)")
    except StopIteration:
        print("\n(program ended)")


if __name__ == "__main__":
    main()
