// sales-report -- business-report example for rpgle_library.
//
// Runs programs/sales-report.rpgle against programs/sales-report.dspf
// and renders the resulting ScreenData as a positioned 24x80 grid, the
// same row/col layout a real 5250 terminal would put on screen --
// reconstructed here from nothing but this library's parser and
// interpreter, no 5250/3270 protocol involved. The RPGLE program
// computes each region's revenue (units * price) and the grand totals
// with ordinary EVAL arithmetic; this program only renders what Next()
// hands back.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

const (
	rows = 24
	cols = 80
)

func renderGrid(screen *rpgle.ScreenData) string {
	grid := make([][]rune, rows)
	for r := range grid {
		grid[r] = []rune(strings.Repeat(" ", cols))
	}
	for _, f := range screen.Fields {
		for i, ch := range f.Text {
			if f.Row < rows && f.Col+i < cols {
				grid[f.Row][f.Col+i] = ch
			}
		}
	}

	var b strings.Builder
	border := "+" + strings.Repeat("-", cols+2) + "+"
	b.WriteString(border + "\n")
	for _, row := range grid {
		b.WriteString("| " + string(row) + " |\n")
	}
	b.WriteString(border)
	return b.String()
}

func main() {
	progDir := filepath.Join("..", "..", "..", "programs")
	ddsSource, err := os.ReadFile(filepath.Join(progDir, "sales-report.dspf"))
	if err != nil {
		panic(err)
	}
	rpgSource, err := os.ReadFile(filepath.Join(progDir, "sales-report.rpgle"))
	if err != nil {
		panic(err)
	}

	dds, err := rpgle.ParseDDS(string(ddsSource))
	if err != nil {
		panic(err)
	}
	prog, err := rpgle.ParseRPGLE(string(rpgSource))
	if err != nil {
		panic(err)
	}

	r := rpgle.Run(prog, dds)
	screen, done, err := r.Next(nil)
	if err != nil {
		panic(err)
	}
	if done {
		panic("program ended before its first EXFMT")
	}

	fmt.Println("rpgle_library (Go) -- running programs/sales-report.rpgle")
	fmt.Println()
	fmt.Println(renderGrid(screen))

	_, done, err = r.Next(&rpgle.Resume{Key: "ENTER", Values: map[string]string{}})
	if err != nil {
		panic(err)
	}
	if done {
		fmt.Println("\nProgram ended normally (*INLR = *ON).")
	}
}
