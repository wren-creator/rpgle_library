package rpgle

import (
	"fmt"
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

// FileSpec is one F-spec: a WORKSTN display file or a DISK file.
type FileSpec struct {
	Name        string
	Type        string
	Designation string
	Format      string
	Device      string
	Addition    string // column 20: "A" allows adding records
	Keyed       bool   // "K" in column 34
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

	// Resulting indicators, columns 71-76: high, low, equal.
	IndHi, IndLo, IndEq string
}

// Program is a fully-tokenized RPGLE source file.
type Program struct {
	File       FileSpec   // the first WORKSTN file
	Files      []FileSpec // every F-spec, in source order
	DSpecs     []DSpec
	Statements []Statement
}

var objectNameRe = regexp.MustCompile(`^[A-Za-z_$#@][A-Za-z0-9_$#@]*$`)

// isObjectName: a file name can never contain a path separator or dot.
func isObjectName(s string) bool { return len(s) <= 100 && objectNameRe.MatchString(s) }

var inzRe = regexp.MustCompile(`(?i)INZ\(([^)]*)\)`)

var (
	dimRe     = regexp.MustCompile(`(?i)\bDIM\s*\(`)
	literalRe = regexp.MustCompile(`'[^']*'`)
)

// digits reads a numeric column of a spec line: blank is 0, anything else
// must be whole digits. Quietly treating junk as 0 would hide a mistake.
func digits(text, what, owner string) (int, error) {
	if text == "" {
		return 0, nil
	}
	for _, c := range text {
		if c < '0' || c > '9' {
			return 0, fmt.Errorf("%s: %s '%s' is not a number", owner, what, text)
		}
	}
	return strconv.Atoi(text)
}

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

		// Output specifications describe printed output. This port can't print,
		// and ignoring them would run the program and quietly print nothing.
		if specType == 'O' {
			return nil, fmt.Errorf("output specifications (O-specs) are not supported")
		}

		switch specType {
		case 'F':
			fs := FileSpec{
				Name:        col(r, 7, 16),
				Type:        col(r, 17, 17),
				Designation: col(r, 18, 18),
				Addition:    col(r, 20, 20),
				Format:      col(r, 22, 22),
				Keyed:       col(r, 34, 34) == "K",
				Device:      col(r, 36, 42),
			}
			if fs.Name != "" && !isObjectName(fs.Name) {
				return nil, fmt.Errorf("'%s' is not a valid file name (letters, digits, _ $ # @ only)", fs.Name)
			}
			prog.Files = append(prog.Files, fs)
			if strings.EqualFold(fs.Device, "WORKSTN") && prog.File.Name == "" {
				prog.File = fs
			}

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
			// An array would silently become one value, so refuse it. (Text
			// literals are skipped: INZ('see DIM(3)') is not an array.)
			if dimRe.MatchString(literalRe.ReplaceAllString(keywords, "''")) {
				return nil, fmt.Errorf("%s: arrays (DIM) are not supported", name)
			}
			var init *string
			if m := inzRe.FindStringSubmatch(keywords); m != nil {
				v := strings.TrimSpace(m[1])
				init = &v
			}
			length, err := digits(lengthS, "length", name)
			if err != nil {
				return nil, err
			}
			var dec *int
			if decS != "" {
				d, err := digits(decS, "decimal positions", name)
				if err != nil {
					return nil, err
				}
				dec = &d
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
				IndHi: col(r, 71, 72), IndLo: col(r, 73, 74), IndEq: col(r, 75, 76),
			})

			// H-spec and other spec types: not needed for v1, ignored.
		}
	}

	return prog, nil
}
