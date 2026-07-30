"""
adventure_cli -- realistic-workflow rpgle_library example.

Drives programs/adventure.rpgle (a small text-adventure) as a plain
stdin/stdout loop: read the ScreenData the interpreter sends at each
EXFMT, render it as plain text, read a line of input, and resume.
There's no 5250/3270 protocol code here at all -- the library's job
ends at producing/consuming plain data; how you present that data (a
real terminal, plain text, a GUI) is entirely up to the caller.
"""

import pathlib

from rpgle import Resume, ScreenData, parse_dds, parse_rpgle, run_program

PROG_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"


def render_plain_text(screen: ScreenData) -> None:
    print("\n" + "─" * 40)
    for f in screen.fields:
        print(f.text)
    if screen.keys:
        key_strs = [f"{k}->F{v}" for k, v in screen.keys.items()]
        print("Keys: " + ", ".join(key_strs))


def main() -> None:
    dds_source = (PROG_DIR / "adventure.dspf").read_text()
    rpg_source = (PROG_DIR / "adventure.rpgle").read_text()

    dds = parse_dds(dds_source)
    prog = parse_rpgle(rpg_source)

    gen = run_program(prog, dds)
    try:
        screen = next(gen)
    except StopIteration:
        print("\n(program ended)")
        return

    while True:
        render_plain_text(screen)

        input_name = next(iter(screen.input_map.values()), None)  # exactly one input field per format

        line = input("> ").strip()
        key = "F3" if line.upper() == "F3" else "ENTER"
        values = {input_name: line} if input_name else {}

        try:
            screen = gen.send(Resume(key=key, values=values))
        except StopIteration:
            print("\n(program ended)")
            return


if __name__ == "__main__":
    main()
