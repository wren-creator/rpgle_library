package rpgle_test

// Builds fixed-form RPG and DDS lines by column, so a test doesn't hide a
// misplaced space inside a hand-counted string. (Same helpers as
// python/tests/_cols.py and test/cols.js.)

import (
	"fmt"
	"strings"
)

func putAt(buf []byte, start int, text string) []byte {
	for i := 0; i < len(text); i++ {
		idx := start - 1 + i
		for len(buf) <= idx {
			buf = append(buf, ' ')
		}
		buf[idx] = text[i]
	}
	return buf
}

func blankSpec(letter byte) []byte { return []byte{' ', ' ', ' ', ' ', ' ', letter} }

func finish(buf []byte) string { return strings.TrimRight(string(buf), " ") }

type fspecOpts struct {
	typ, desig, addition, fmt, device string
	keyed                             bool
}

func fspec(name string, o fspecOpts) string {
	if o.typ == "" {
		o.typ = "I"
	}
	if o.desig == "" {
		o.desig = "F"
	}
	if o.fmt == "" {
		o.fmt = "E"
	}
	if o.device == "" {
		o.device = "DISK"
	}
	b := blankSpec('F')
	b = putAt(b, 7, name)
	b = putAt(b, 17, o.typ)
	b = putAt(b, 18, o.desig)
	b = putAt(b, 20, o.addition)
	b = putAt(b, 22, o.fmt)
	if o.keyed {
		b = putAt(b, 34, "K")
	}
	b = putAt(b, 36, o.device)
	return finish(b)
}

type cspecOpts struct {
	factor1, factor2, result, hi, lo, eq, cond string
}

func cspec(opcode string, o cspecOpts) string {
	b := blankSpec('C')
	b = putAt(b, 9, o.cond)
	b = putAt(b, 12, o.factor1)
	b = putAt(b, 26, opcode)
	b = putAt(b, 36, o.factor2)
	b = putAt(b, 50, o.result)
	b = putAt(b, 71, o.hi)
	b = putAt(b, 73, o.lo)
	b = putAt(b, 75, o.eq)
	return finish(b)
}

// dspec builds a standalone field. dec "" means a character field; init "" means none.
func dspec(name string, length int, dec, init string) string {
	b := blankSpec('D')
	b = putAt(b, 7, name)
	b = putAt(b, 24, "S")
	b = putAt(b, 33, fmt.Sprintf("%7d", length))
	if dec != "" {
		b = putAt(b, 41, fmt.Sprintf("%2s", dec))
	}
	if init != "" {
		b = putAt(b, 45, "INZ("+init+")")
	}
	return finish(b)
}

// ddsLine builds one DDS line; length and dec are "" when absent.
func ddsLine(kind, name, length, dtype, dec, keywords string) string {
	b := blankSpec('A')
	b = putAt(b, 17, kind)
	b = putAt(b, 19, name)
	if length != "" {
		b = putAt(b, 30, fmt.Sprintf("%5s", length))
	}
	b = putAt(b, 35, dtype)
	if dec != "" {
		b = putAt(b, 36, fmt.Sprintf("%2s", dec))
	}
	b = putAt(b, 45, keywords)
	return finish(b)
}

func ddsText(lines ...string) string { return strings.Join(lines, "\n") + "\n" }
