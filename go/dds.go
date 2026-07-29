// Package rpgle implements a zero-dependency RPG IV (RPGLE) and DDS
// parser/interpreter. See the root README and docs/ for the language
// subset supported and why this library exists.
package rpgle

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// dds.go
// ─────────────────────────────────────────────────────────────────
// Minimal DDS parser for WORKSTN display files: record formats,
// constants, and named input/output fields, plus record-level
// CFnn/CAnn (command key -> response indicator) keywords and
// field-level indicator conditioning.
//
// Column positions are IBM's documented DDS-for-display-files layout
// (verified against the DDS Reference: Display Files manual) -- see
// docs/DDS_REFERENCE.md for the full column table.
//
// Not supported (see docs/DDS_REFERENCE.md for the full list and
// why): OR'd/multi-line ANDed indicator conditions, subfiles, window/
// keyword continuation lines.

// IndicatorCond is one conditioning indicator on a DDS field/const line.
type IndicatorCond struct {
	Not bool
	Num string
}

// Field is one DDS field or constant line within a record format.
type Field struct {
	Kind       string // "field" | "const"
	Name       string
	Row, Col   int
	Length     int
	DType      string
	Dec        *int // nil = character field (blank decimal positions); non-nil = numeric with that many decimals
	Usage      string
	Text       string // constant literal text, for Kind == "const"
	Indicators []IndicatorCond
}

// Format is one DDS record format.
type Format struct {
	Name   string
	Fields []Field
	Keys   map[string]string // "F3" -> "03", from CFnn/CAnn keywords
}

// DDS is the result of parsing a display-file DDS source file.
type DDS struct {
	Formats map[string]*Format
	// FormatOrder preserves source order, since Go map iteration order
	// is randomized but BuildVars' "first definition wins" rule depends
	// on it -- same reasoning as JS relying on Object.keys() insertion
	// order.
	FormatOrder []string
}

type ddsKeyword struct {
	Name   string
	Params []string
}

var (
	indicatorRe  = regexp.MustCompile(`(N?)(\d{2})`)
	ddsKeywordRe = regexp.MustCompile(`([A-Z0-9]+)(\(([^)]*)\))?`)
	cfCaRe       = regexp.MustCompile(`^C[AF](\d{2})$`)
	quotedRe     = regexp.MustCompile(`'([^']*)'`)
	wsRe         = regexp.MustCompile(`\s+`)
)

func parseIndicators(line []rune) []IndicatorCond {
	n := len(line)
	start, end := 6, 16 // columns 7-16, 0-indexed slice
	if start > n {
		start = n
	}
	if end > n {
		end = n
	}
	raw := string(line[start:end])
	m := indicatorRe.FindStringSubmatch(raw)
	if m == nil {
		return nil
	}
	return []IndicatorCond{{Not: m[1] == "N", Num: m[2]}}
}

// parseDdsKeywords parses "CF03(03) DSPATR(HI)" into
// [{Name:"CF03", Params:["03"]}, ...].
func parseDdsKeywords(text string) []ddsKeyword {
	var out []ddsKeyword
	for _, m := range ddsKeywordRe.FindAllStringSubmatch(text, -1) {
		var params []string
		if m[3] != "" {
			params = wsRe.Split(m[3], -1)
		}
		out = append(out, ddsKeyword{Name: m[1], Params: params})
	}
	return out
}

// ParseDDS parses DDS display-file source into its record formats.
func ParseDDS(source string) (*DDS, error) {
	formats := map[string]*Format{}
	var order []string
	var current *Format

	for _, raw := range strings.Split(source, "\n") {
		line := trimTrailingWhitespace(raw)
		r := []rune(line)
		if len(r) < 6 {
			continue
		}
		if r[5] != 'A' { // column 6
			continue
		}
		if len(r) > 6 && r[6] == '*' { // column 7: comment
			continue
		}

		isRecord := col(r, 17, 17) == "R"
		name := col(r, 19, 28)
		var keywordArea string
		if len(r) >= 45 {
			keywordArea = string(r[44:])
		}

		if isRecord {
			current = &Format{Name: name, Keys: map[string]string{}}
			if _, exists := formats[name]; !exists {
				order = append(order, name)
			}
			formats[name] = current
			for _, kw := range parseDdsKeywords(keywordArea) {
				if m := cfCaRe.FindStringSubmatch(kw.Name); m != nil && len(kw.Params) > 0 && kw.Params[0] != "" {
					n, err := strconv.Atoi(m[1])
					if err != nil {
						return nil, fmt.Errorf("invalid CF/CA number in %q: %w", kw.Name, err)
					}
					current.Keys[fmt.Sprintf("F%d", n)] = kw.Params[0]
				}
			}
			continue
		}

		if current == nil {
			continue // stray field line before any R spec
		}

		lineNo := col(r, 39, 41)
		posNo := col(r, 42, 44)
		if lineNo == "" || posNo == "" {
			continue // not a locatable field/constant line
		}

		rowN, err := strconv.Atoi(lineNo)
		if err != nil {
			return nil, fmt.Errorf("invalid DDS line number %q: %w", lineNo, err)
		}
		colN, err := strconv.Atoi(posNo)
		if err != nil {
			return nil, fmt.Errorf("invalid DDS column number %q: %w", posNo, err)
		}
		row := rowN - 1 // convert to 0-indexed, matching screen renderers
		dcol := colN - 1
		indicators := parseIndicators(r)

		if name == "" {
			text := ""
			if m := quotedRe.FindStringSubmatch(keywordArea); m != nil {
				text = m[1]
			}
			current.Fields = append(current.Fields, Field{Kind: "const", Row: row, Col: dcol, Text: text, Indicators: indicators})
			continue
		}

		lengthS := col(r, 30, 34)
		dtype := col(r, 35, 35)
		decS := col(r, 36, 37)
		usage := col(r, 38, 38)

		length := 0
		if lengthS != "" {
			length, err = strconv.Atoi(lengthS)
			if err != nil {
				return nil, fmt.Errorf("invalid DDS field length %q: %w", lengthS, err)
			}
		}
		if dtype == "" {
			if decS != "" {
				dtype = "N"
			} else {
				dtype = "A"
			}
		}
		var dec *int
		if decS != "" {
			d, err := strconv.Atoi(decS)
			if err != nil {
				return nil, fmt.Errorf("invalid DDS decimal positions %q: %w", decS, err)
			}
			dec = &d
		}
		if usage == "" {
			usage = "O" // O=output, I=input, B=both
		}

		current.Fields = append(current.Fields, Field{
			Kind: "field", Name: name, Row: row, Col: dcol,
			Length: length, DType: dtype, Dec: dec, Usage: usage,
			Indicators: indicators,
		})
	}

	return &DDS{Formats: formats, FormatOrder: order}, nil
}
