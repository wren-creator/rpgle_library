package rpgle

// DISK file opcodes (CHAIN, SETLL, READ, WRITE ...). This file is the
// language side: it resolves keys (literals, variables, KLISTs), moves
// records between a file and the program's variables, and sets the
// resulting indicators in columns 71-76. The database side is behind the
// Database and FileHandle interfaces below, so this package never compiles
// SQLite; the sqlitedb subpackage supplies one. Mirrors
// python/src/rpgle/fileops.py and src/fileops.js.
//
// Which indicator means what depends on the opcode, and it's a classic
// source of confusion, so it's all here in one table:
//
//	opcode                      hi (71-72)   lo (73-74)   eq (75-76)
//	CHAIN                       not found    error        -
//	SETLL                       not found    error        equal
//	SETGT                       not found    error        -
//	READ READE READP READPE     -            error        end of file
//	WRITE UPDATE                -            error        -
//	DELETE                      not found    error        -
//
// Every indicator the line names is set explicitly, on or off, on every
// execution, so a stale "on" from an earlier iteration can't leak. An error
// with no error indicator on the line is raised, which is what a real
// program with no *PSSR does too (it halts).

import (
	"errors"
	"fmt"
	"math"
	"strings"
)

// Record is one record's fields by name: text for character fields, numbers
// for numeric ones.
type Record map[string]Value

// FileHandle is one open DISK file. A nil Record from a read or chain means
// no record (end of file, or not found). Keys are lists of program values;
// a key with fewer values than the file has key fields is a partial key.
type FileHandle interface {
	PF() *PhysicalFile
	Chain(key []Value) (Record, error)
	Setll(key []Value) error // a nil key is *LOVAL
	Setgt(key []Value) error // a nil key is *HIVAL
	Read() (Record, error)
	Readp() (Record, error)
	Reade(key []Value) (Record, error)
	Readpe(key []Value) (Record, error)
	Write(rec Record) error
	Update(rec Record) error
	// Delete removes the record with the given key, or the current record
	// when key is nil. It reports whether one was deleted.
	Delete(key []Value) (bool, error)
	Found() bool
	EOF() bool
	Equal() bool
}

// Database opens DISK files. See the sqlitedb package for one on SQLite.
type Database interface {
	OpenFile(pf *PhysicalFile) (FileHandle, error)
}

// StoredValue turns a program value into what a file stores for field f.
// Character data is stored without trailing blanks so 'A' and 'A    ' are
// the same key, as in RPG; a numeric field with no decimals stores a whole
// number. The result is a string, an int64 or a float64.
func StoredValue(f PFField, v Value) (any, error) {
	if f.DType == "A" {
		s, err := v.text()
		if err != nil {
			return nil, fmt.Errorf("%s: %w", f.Name, err)
		}
		return strings.TrimRight(s, " \t\r\n\v\f"), nil
	}
	if v.Kind == 'S' && strings.TrimSpace(v.Str) == "" {
		v = NumberValue(0) // a blank reads as zero
	}
	n, ok := toNumber(v)
	if !ok {
		what := "that value"
		if v.Kind == 'S' {
			what = "'" + strings.TrimSpace(v.Str) + "'"
		}
		return nil, fmt.Errorf("%s: %s is not a number", f.Name, what)
	}
	if f.Dec == 0 {
		return int64(math.Trunc(n)), nil
	}
	return n, nil
}

// RelativeRecordNumber turns a key value for an arrival-sequence file (which
// is keyed by record number) into a whole number.
func RelativeRecordNumber(v Value) (int64, error) {
	n, ok := toNumber(v)
	if !ok {
		return 0, fmt.Errorf("relative record number: that value is not a number")
	}
	return int64(math.Trunc(n)), nil
}

type fileSentinel int

const (
	keyLoval fileSentinel = iota + 1
	keyHival
)

type fileRuntime struct {
	klists   map[string][]string
	files    map[string]FileHandle
	byFormat map[string]string
	last     FileHandle
}

func newFileRuntime(prog *Program, db Database, pfs map[string]*PhysicalFile, klists map[string][]string) (*fileRuntime, error) {
	fr := &fileRuntime{klists: klists, files: map[string]FileHandle{}, byFormat: map[string]string{}}
	var disk []FileSpec
	for _, f := range prog.Files {
		if strings.EqualFold(f.Device, "DISK") {
			disk = append(disk, f)
		}
	}
	if len(disk) > 0 && db == nil {
		return nil, errors.New("program declares DISK files but no database was given")
	}
	for _, fs := range disk {
		name := strings.ToUpper(fs.Name)
		if !strings.EqualFold(fs.Format, "E") {
			return nil, fmt.Errorf("%s: only externally described DISK files (F-spec column 22 = E) are supported", name)
		}
		pf := pfs[name]
		if pf == nil {
			return nil, fmt.Errorf("%s: no physical file DDS was provided", name)
		}
		h, err := db.OpenFile(pf)
		if err != nil {
			return nil, err
		}
		fr.files[name] = h
		fr.byFormat[pf.Format] = name
	}
	return fr, nil
}

func (fr *fileRuntime) resolve(name string) (FileHandle, error) {
	if name == "" {
		return nil, errors.New("file opcode is missing its file name (factor 2)")
	}
	up := strings.ToUpper(name)
	if f, ok := fr.byFormat[up]; ok {
		up = f
	}
	h, ok := fr.files[up]
	if !ok {
		return nil, fmt.Errorf("%s is not a DISK file or record format in this program", name)
	}
	return h, nil
}

// closesAtEnd reports whether the '(' at the start of text is closed by the
// ')' at its end.
func closesAtEnd(text string) bool {
	depth := 0
	inStr := false
	for i, c := range text {
		switch {
		case c == '\'':
			inStr = !inStr
		case inStr:
		case c == '(':
			depth++
		case c == ')':
			depth--
			if depth == 0 {
				return i == len(text)-1
			}
		}
	}
	return false
}

// key resolves factor 1: a KLIST name, *LOVAL/*HIVAL, or an expression, or
// several in parentheses: custno, 'ABC', (custno : ordno), (a + 1). The
// sentinel is returned in place of a key for *LOVAL and *HIVAL.
func (fr *fileRuntime) key(factor1 string, eval func(string) (Value, error)) ([]Value, fileSentinel, error) {
	if factor1 == "" {
		return nil, 0, errors.New("this file opcode needs a key in factor 1")
	}
	up := strings.ToUpper(factor1)
	if up == "*LOVAL" {
		return nil, keyLoval, nil
	}
	if up == "*HIVAL" {
		return nil, keyHival, nil
	}
	var parts []string
	if names, ok := fr.klists[up]; ok {
		parts = names
	} else {
		text := strings.TrimSpace(factor1)
		if strings.HasPrefix(text, "(") && closesAtEnd(text) {
			text = text[1 : len(text)-1]
		}
		parts = SplitTop(text, ':')
	}
	out := make([]Value, 0, len(parts))
	for _, p := range parts {
		v, err := eval(p)
		if err != nil {
			return nil, 0, err
		}
		out = append(out, v)
	}
	return out, 0, nil
}

func (fr *fileRuntime) needKey(in *instr, eval func(string) (Value, error)) ([]Value, error) {
	key, sentinel, err := fr.key(in.Factor1, eval)
	if err != nil {
		return nil, err
	}
	if sentinel != 0 {
		return nil, fmt.Errorf("%s does not accept *LOVAL or *HIVAL", in.Op)
	}
	return key, nil
}

func readVarValue(vars map[string]*Var, name string) (Value, error) {
	v, ok := vars[strings.ToLower(strings.TrimSpace(name))]
	if !ok {
		return Value{}, fmt.Errorf("unknown RPG variable: %s", name)
	}
	return v.Value, nil
}

// exec runs one file opcode. An error is returned only when the program has
// no error indicator on the line for it to land in.
func (fr *fileRuntime) exec(in *instr, vars map[string]*Var, assign func(string, Value) error, setInd func(string, bool), eval func(string) (Value, error)) error {
	h, err := fr.resolve(in.Factor2)
	if err != nil {
		return err
	}
	fr.last = h
	var hi, lo, eq bool // what each indicator column will be set to

	load := func(rec Record) error {
		if rec == nil {
			return nil
		}
		for _, f := range h.PF().Fields {
			if err := assign(f.Name, rec[f.Name]); err != nil {
				return err
			}
		}
		return nil
	}
	run := func() error {
		switch in.Op {
		case "CHAIN":
			key, err := fr.needKey(in, eval)
			if err != nil {
				return err
			}
			rec, err := h.Chain(key)
			if err != nil {
				return err
			}
			if err := load(rec); err != nil {
				return err
			}
			hi = !h.Found()
		case "SETLL", "SETGT":
			key, sentinel, err := fr.key(in.Factor1, eval)
			if err != nil {
				return err
			}
			switch {
			case sentinel == keyLoval:
				err = h.Setll(nil)
			case sentinel == keyHival:
				err = h.Setgt(nil)
			case in.Op == "SETLL":
				err = h.Setll(key)
			default:
				err = h.Setgt(key)
			}
			if err != nil {
				return err
			}
			hi = !h.Found()
			eq = h.Equal()
		case "READ", "READP":
			var rec Record
			var err error
			if in.Op == "READ" {
				rec, err = h.Read()
			} else {
				rec, err = h.Readp()
			}
			if err != nil {
				return err
			}
			if err := load(rec); err != nil {
				return err
			}
			eq = h.EOF()
		case "READE", "READPE":
			key, err := fr.needKey(in, eval)
			if err != nil {
				return err
			}
			var rec Record
			if in.Op == "READE" {
				rec, err = h.Reade(key)
			} else {
				rec, err = h.Readpe(key)
			}
			if err != nil {
				return err
			}
			if err := load(rec); err != nil {
				return err
			}
			eq = h.EOF()
		case "WRITE", "UPDATE":
			rec := Record{}
			for _, f := range h.PF().Fields {
				v, err := readVarValue(vars, f.Name)
				if err != nil {
					return err
				}
				rec[f.Name] = v
			}
			if in.Op == "WRITE" {
				return h.Write(rec)
			}
			return h.Update(rec)
		case "DELETE":
			if in.Factor1 != "" {
				key, err := fr.needKey(in, eval)
				if err != nil {
					return err
				}
				if _, err := h.Delete(key); err != nil {
					return err
				}
				hi = !h.Found()
			} else if _, err := h.Delete(nil); err != nil {
				return err
			}
		}
		return nil
	}
	if err := run(); err != nil {
		if in.IndLo == "" {
			return err
		}
		hi, lo, eq = false, true, false
	}
	for _, x := range []struct {
		ind string
		on  bool
	}{{in.IndHi, hi}, {in.IndLo, lo}, {in.IndEq, eq}} {
		if x.ind != "" {
			setInd(x.ind, x.on)
		}
	}
	return nil
}

// status answers %EOF, %FOUND and %EQUAL. With no file name it means the
// last file used.
func (fr *fileRuntime) status(kind, name string) (bool, error) {
	var h FileHandle
	switch {
	case name != "":
		var err error
		if h, err = fr.resolve(name); err != nil {
			return false, err
		}
	case fr.last != nil:
		h = fr.last
	default:
		return false, fmt.Errorf("%%%s with no file operation before it", strings.ToUpper(kind))
	}
	switch kind {
	case "eof":
		return h.EOF(), nil
	case "found":
		return h.Found(), nil
	}
	return h.Equal(), nil
}
