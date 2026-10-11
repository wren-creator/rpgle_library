package rpgle

// Minimal DDS parser for DISK (physical) files: the record format, its
// fields, and the key. This is what makes a DISK file "externally
// described": the RPG program declares only the file name (F-spec,
// column 22 = E) and every field here becomes a program variable, the same
// way a display file's fields do. Mirrors python/src/rpgle/pf.py and
// src/pf.js.
//
// Column layout is the DDS-for-physical-files one:
//   6      Form type ('A')
//   7      Comment marker ('*')
//   17     'R' starts the record format, 'K' is a key field line
//   19-28  Name
//   30-34  Length
//   35     Data type (blank = character, or zoned if decimals given;
//          P packed, S zoned, B binary, F float are all numeric here)
//   36-37  Decimal positions
//   45-80  Keywords (UNIQUE before the R line makes the key unique;
//          DESCEND on a key line is parsed but rejected)
//
// Not supported: DESCEND keys, join and select/omit logical files, REF,
// more than one record format per file.

import (
	"fmt"
	"strings"
)

// PFField is one field of a physical file's record format.
type PFField struct {
	Name   string
	Length int
	DType  string // "N" numeric, "A" character
	Dec    int
}

// PhysicalFile is a parsed physical-file DDS.
type PhysicalFile struct {
	Name   string
	Format string
	Fields []PFField
	Keys   []string
	Unique bool
}

// ParsePF parses physical-file DDS source for the file called fileName.
func ParsePF(fileName, source string) (*PhysicalFile, error) {
	pf := &PhysicalFile{Name: strings.ToUpper(fileName)}

	for _, raw := range strings.Split(source, "\n") {
		r := []rune(trimTrailingWhitespace(raw))
		if len(r) < 6 || r[5] != 'A' {
			continue
		}
		if len(r) > 6 && r[6] == '*' {
			continue
		}

		kind := col(r, 17, 17)
		name := strings.ToUpper(col(r, 19, 28))
		keywords := ""
		if len(r) >= 45 {
			keywords = strings.ToUpper(string(r[44:]))
		}

		switch {
		case kind == "R":
			if pf.Format != "" {
				return nil, fmt.Errorf("%s: only one record format per DISK file is supported", pf.Name)
			}
			pf.Format = name
		case kind == "K":
			if strings.Contains(keywords, "DESCEND") {
				return nil, fmt.Errorf("%s: DESCEND keys are not supported", pf.Name)
			}
			pf.Keys = append(pf.Keys, name)
		case pf.Format == "":
			// File-level keywords before the R line.
			for _, w := range strings.Fields(keywords) {
				if w == "UNIQUE" {
					pf.Unique = true
				}
			}
		case name == "":
		default:
			length, err := digits(col(r, 30, 34), "length", name)
			if err != nil {
				return nil, err
			}
			dtype := strings.ToUpper(col(r, 35, 35))
			decS := col(r, 36, 37)
			dec, err := digits(decS, "decimal positions", name)
			if err != nil {
				return nil, err
			}
			t := "A"
			if strings.Contains("PSBF", dtype) && dtype != "" || decS != "" {
				t = "N"
			}
			pf.Fields = append(pf.Fields, PFField{Name: name, Length: length, DType: t, Dec: dec})
		}
	}

	if pf.Format == "" {
		return nil, fmt.Errorf("%s: no record format (R line) found in DDS", pf.Name)
	}
	for _, k := range pf.Keys {
		found := false
		for _, f := range pf.Fields {
			if f.Name == k {
				found = true
			}
		}
		if !found {
			return nil, fmt.Errorf("%s: key field %s is not in record format %s", pf.Name, k, pf.Format)
		}
	}
	return pf, nil
}
