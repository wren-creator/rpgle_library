"""
sales_report -- business-report example for rpgle_library.

Runs programs/sales-report.rpgle against programs/sales-report.dspf and
renders the resulting ScreenData as a positioned 24x80 grid, the same
row/col layout a real 5250 terminal would put on screen -- reconstructed
here from nothing but this library's parser and interpreter, no
5250/3270 protocol involved. The RPGLE program computes each region's
revenue (units * price) and the grand totals with ordinary EVAL
arithmetic; this script only renders what run_program() hands back.
"""

import pathlib

from rpgle import Resume, ScreenData, parse_dds, parse_rpgle, run_program

PROG_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"
ROWS, COLS = 24, 80


def render_grid(screen: ScreenData) -> str:
    grid = [[" "] * COLS for _ in range(ROWS)]
    for f in screen.fields:
        for i, ch in enumerate(f.text):
            if f.row < ROWS and f.col + i < COLS:
                grid[f.row][f.col + i] = ch

    lines = ["+" + "-" * (COLS + 2) + "+"]
    for row in grid:
        lines.append("| " + "".join(row) + " |")
    lines.append("+" + "-" * (COLS + 2) + "+")
    return "\n".join(lines)


def main() -> None:
    dds = parse_dds((PROG_DIR / "sales-report.dspf").read_text())
    prog = parse_rpgle((PROG_DIR / "sales-report.rpgle").read_text())

    gen = run_program(prog, dds)
    screen = next(gen)

    print("rpgle_library (Python) -- running programs/sales-report.rpgle\n")
    print(render_grid(screen))

    try:
        gen.send(Resume(key="ENTER", values={}))
    except StopIteration:
        print("\nProgram ended normally (*INLR = *ON).")


if __name__ == "__main__":
    main()
