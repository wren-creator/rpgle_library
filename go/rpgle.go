package rpgle

import (
	"regexp"
	"strconv"
	"strings"
)

// rpgle.go
// ─────────────────────────────────────────────────────────────────
// Fixed-form RPG IV tokenizer, scoped to the F/D/C-spec subset this
// library's interpreter supports (see interpreter.go's header for the
// exact opcode list). This module only tokenizes columns into spec
// records in source order -- control-flow resolution (matching
// IF/ELSE/ENDIF, DOW/ENDDO, SELECT/WHEN/OTHER/ENDSL, BEGSR/ENDSR)
// happens in interpreter.go's Compile step.
//
// Column positions are IBM's documented fixed-form layout (verified
// against the ILE RPG Language Reference, SC09-2508) -- see
// docs/LANGUAGE.md for the narrative version.

// FileSpec is the single WORKSTN file an RPGLE program can declare.
type FileSpec struct {
	Name        string
	Type        string
	Designation string
	Format      string
	Device      string
}

// DSpec is one standalone (definition type 'S') D-spec field.
type DSpec struct {
	Name    string
	DefType string
	Length  int
	// Dec is nil for a character field (blank decimal-positions
	// column) and non-nil for a numeric field, same reasoning as
	// Field.Dec in dds.go: a real "0" and "blank" are different things.
	Dec  *int
	Init *string
}

// Statement is one C-spec line, tokenized but not yet control-flow
// resolved (that happens in Compile).
type Statement struct {
	Op      string
	Indic   string
	Factor1 string
	Expr    string // extended factor 2 (EVAL/IF/DOW/WHEN): free-form, cols 36-80
	Factor2 string // classic factor 2 (DIV/MVR-style ops): cols 36-49
	Result  string // classic result (DIV/MVR-style ops): cols 50-63
}

// Program is a fully-tokenized RPGLE source file.
type Program struct {
	File       FileSpec
	DSpecs     []DSpec
	Statements []Statement
}

var inzRe = regexp.MustCompile(`(?i)INZ\(([^)]*)\)`)

// ParseRPGLE tokenizes fixed-form RPG IV source into F-spec/D-spec/
// C-spec records, in source order.
func ParseRPGLE(source string) (*Program, error) {
	prog := &Program{}

	for _, raw := range strings.Split(source, "\n") {
		line := trimTrailingWhitespace(raw)
		r := []rune(line)
		if len(r) < 6 {
			continue
		}
		specType := r[5] // column 6
		if specType == '*' {
			continue // comment line
		}

		switch specType {
		case 'F':
			prog.File.Name = col(r, 7, 16)
			prog.File.Type = col(r, 17, 17)
			prog.File.Designation = col(r, 18, 18)
			prog.File.Format = col(r, 22, 22)
			prog.File.Device = col(r, 36, 42)

		case 'D':
			name := col(r, 7, 21)
			if name == "" {
				continue
			}
			deftype := col(r, 24, 25)
			lengthS := col(r, 33, 39)
			decS := col(r, 41, 42)
			var keywords string
			if len(r) >= 44 {
				keywords = strings.TrimSpace(string(r[43:]))
			}
			var init *string
			if m := inzRe.FindStringSubmatch(keywords); m != nil {
				v := strings.TrimSpace(m[1])
				init = &v
			}
			length := 0
			if lengthS != "" {
				if n, err := strconv.Atoi(lengthS); err == nil {
					length = n
				}
			}
			var dec *int
			if decS != "" {
				if d, err := strconv.Atoi(decS); err == nil {
					dec = &d
				}
			}
			prog.DSpecs = append(prog.DSpecs, DSpec{
				Name: name, DefType: deftype, Length: length, Dec: dec, Init: init,
			})

		case 'C':
			indic := col(r, 9, 11)
			factor1 := col(r, 12, 25)
			opcode := strings.ToUpper(col(r, 26, 35))
			if opcode == "" {
				continue
			}
			// Extended factor 2 (EVAL/IF/DOW/WHEN): free-form expression, 36-80.
			var expr string
			if len(r) >= 36 {
				expr = strings.TrimSpace(string(r[35:]))
			}
			// Classic factor 2 / result (DIV/MVR and any other
			// traditional fixed-column op): 36-49 and 50-63, read
			// separately since they aren't whitespace-delimited from
			// each other in a reliable way.
			factor2 := col(r, 36, 49)
			result := col(r, 50, 63)
			prog.Statements = append(prog.Statements, Statement{
				Op: opcode, Indic: indic, Factor1: factor1,
				Expr: expr, Factor2: factor2, Result: result,
			})

			// H-spec and other spec types: not needed for v1, ignored.
		}
	}

	return prog, nil
}
