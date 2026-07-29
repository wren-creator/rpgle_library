// quick-math — minimal rpgle_library example.
//
// A tiny RPG program that computes 17 DIV 5 using the classic RPG
// DIV/MVR idiom for modulo (RPG has no %REM until well past V4R3),
// then displays the result through a single EXFMT. No file I/O, no
// subroutines, no control-flow beyond one instruction pair -- just
// enough to show ParseDDS/ParseRPGLE/Run working end-to-end.
package main

import (
	"fmt"
	"strings"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

const ddsSource = `
     A                                      DSPSIZ(24 80 *DS3)

     A          R RESULT
     A                                  1  2'17 DIV 5, via RPG DIV/MVR:'
     A                                  3  2'Quotient:'
     A            QUOT           5  0O  3 13
     A                                  4  2'Remainder:'
     A            REM            5  0O  4 13
     A                                 22  2'Press Enter to continue'
`

const rpgSource = `
     FRESULT    CF   E             WORKSTN

     Da                S              5  0 INZ(17)
     Db                S              5  0 INZ(5)

     C     a             DIV       b             quot
     C                   MVR                     rem
     C                   EXFMT     RESULT
     C                   EVAL      *inlr = *on
     C                   RETURN
`

func main() {
	dds, err := rpgle.ParseDDS(ddsSource)
	if err != nil {
		panic(err)
	}
	prog, err := rpgle.ParseRPGLE(rpgSource)
	if err != nil {
		panic(err)
	}

	r := rpgle.Run(prog, dds)
	screen, done, err := r.Next(nil) // runs DIV/MVR, then yields at the EXFMT
	if err != nil {
		panic(err)
	}
	if done {
		panic("program ended before its first EXFMT")
	}

	fmt.Printf("\n%s\n", screen.FormatName)
	for _, f := range screen.Fields {
		fmt.Println(strings.TrimRight(f.Text, " "))
	}

	_, done, err = r.Next(&rpgle.Resume{Key: "ENTER", Values: map[string]string{}})
	if err != nil {
		panic(err)
	}
	if done {
		fmt.Println("\n(program ended)")
	} else {
		fmt.Println("\n(unexpected: program did not end)")
	}
}
