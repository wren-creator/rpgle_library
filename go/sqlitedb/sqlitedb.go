// Package sqlitedb is the SQLite implementation of the rpgle package's
// Database and FileHandle interfaces: DISK file access, the part of RPG that
// makes it a database language. It lives in its own package so that the core
// library, which has no dependencies, never compiles SQLite unless a program
// asks for it. Mirrors python/src/rpgle/database.py and src/database.js.
//
// Each physical file (see rpgle.ParsePF) becomes one table, and a FileHandle
// gives it the keyed-access model RPG programs expect:
//
//	SETLL / SETGT   position the file relative to a key, read nothing
//	CHAIN           position at a key and read that record
//	READ / READE    read the next record / next record with this key
//	READP / READPE  read the previous record / previous with this key
//	WRITE / UPDATE / DELETE
//
// The position model is the thing to understand. A file always has a
// "current position" that the next READ moves from:
//
//	rec    sitting on a record we just read or chained to (its key and rrn)
//	setll  just before the first record whose key >= prefix
//	setgt  just after the last record whose key <= prefix
//
// Positions are stored as keys, never as SQL cursors, so deleting or
// updating records (including the current one) can't invalidate them. A
// partial key (fewer values than the file has key fields) compares on the
// leading key fields only, which is how RPG's KLIST partial keys and READE
// loops over a customer's orders work.
//
// Every record also has a relative record number, rrn (SQLite's own row id,
// kept in the _rrn column), which breaks ties between duplicate keys in
// arrival order and is the key for files with no K lines.
package sqlitedb

import (
	"database/sql"
	"errors"
	"fmt"
	"strings"

	rpgle "github.com/wren-creator/rpgle_library/go"
	"modernc.org/sqlite"
)

const rrnCol = "_rrn"

func q(name string) string { return `"` + strings.ReplaceAll(name, `"`, `""`) + `"` }

func marks(n int) string { return strings.TrimSuffix(strings.Repeat("?, ", n), ", ") }

// DB is a SQLite database holding DISK files.
type DB struct {
	conn *sql.DB
}

// Open opens (creating if needed) the database at path. ":memory:" is private
// to this process and vanishes on Close.
func Open(path string) (*DB, error) {
	conn, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// One connection: an in-memory database is per connection, and the file
	// opcodes are strictly sequential anyway.
	conn.SetMaxOpenConns(1)
	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, err
	}
	return &DB{conn: conn}, nil
}

// Close closes the database. Writes are already durable (each statement
// commits as it runs). Safe to call more than once.
func (d *DB) Close() error { return d.conn.Close() }

// OpenFile creates the file's table if it doesn't exist and returns a handle.
// Opening an existing table checks the columns match, so a changed DDS fails
// loudly instead of corrupting data.
func (d *DB) OpenFile(pf *rpgle.PhysicalFile) (rpgle.FileHandle, error) {
	table := q(pf.Name)
	var defs []string
	names := make([]string, len(pf.Fields))
	for i, f := range pf.Fields {
		typ := "TEXT"
		if f.DType != "A" {
			typ = "REAL"
			if f.Dec == 0 {
				typ = "INTEGER"
			}
		}
		defs = append(defs, q(f.Name)+" "+typ+" NOT NULL")
		names[i] = f.Name
	}
	if _, err := d.conn.Exec(fmt.Sprintf("CREATE TABLE IF NOT EXISTS %s (%s INTEGER PRIMARY KEY AUTOINCREMENT, %s)",
		table, q(rrnCol), strings.Join(defs, ", "))); err != nil {
		return nil, err
	}
	rows, err := d.conn.Query("PRAGMA table_info(" + table + ")")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var existing []string
	for rows.Next() {
		var cid, notnull, pk int
		var name, typ string
		var dflt any
		if err := rows.Scan(&cid, &name, &typ, &notnull, &dflt, &pk); err != nil {
			return nil, err
		}
		existing = append(existing, name)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	rows.Close()
	if len(existing) > 0 {
		existing = existing[1:]
	}
	if strings.Join(existing, "\x00") != strings.Join(names, "\x00") {
		return nil, fmt.Errorf("%s: table columns %v don't match the DDS fields %v", pf.Name, existing, names)
	}
	if len(pf.Keys) > 0 {
		unique := ""
		if pf.Unique {
			unique = "UNIQUE "
		}
		cols := make([]string, len(pf.Keys))
		for i, k := range pf.Keys {
			cols[i] = q(k)
		}
		if _, err := d.conn.Exec(fmt.Sprintf("CREATE %sINDEX IF NOT EXISTS %s ON %s (%s)",
			unique, q(pf.Name+"_KEY"), table, strings.Join(cols, ", "))); err != nil {
			return nil, err
		}
	}
	h := &handle{conn: d.conn, pf: pf, table: table, field: map[string]rpgle.PFField{}}
	for _, f := range pf.Fields {
		h.field[f.Name] = f
	}
	h.keyCols = append(h.keyCols, pf.Keys...)
	if len(h.keyCols) == 0 {
		h.keyCols = []string{rrnCol} // arrival sequence: keyed by relative record number
	}
	h.pos = position{kind: posSetll}
	return h, nil
}

type posKind int

const (
	posSetll posKind = iota
	posSetgt
	posRec
)

type position struct {
	kind   posKind
	prefix []any // setll / setgt: the key prefix
	key    []any // rec: the record's key
	rrn    int64 // rec: the record's relative record number
}

type handle struct {
	conn    *sql.DB
	pf      *rpgle.PhysicalFile
	table   string
	field   map[string]rpgle.PFField
	keyCols []string
	pos     position
	current int64 // rrn of the record last read or chained to; 0 for none
	found   bool
	eof     bool
	equal   bool
}

func (h *handle) PF() *rpgle.PhysicalFile { return h.pf }
func (h *handle) Found() bool             { return h.found }
func (h *handle) EOF() bool               { return h.eof }
func (h *handle) Equal() bool             { return h.equal }

func (h *handle) coerceKey(values []rpgle.Value) ([]any, error) {
	if len(values) > len(h.keyCols) {
		return nil, fmt.Errorf("%s: key has %d fields, file has %d", h.pf.Name, len(values), len(h.keyCols))
	}
	out := make([]any, len(values))
	for i, v := range values {
		f, ok := h.field[h.keyCols[i]]
		if !ok { // relative record number
			n, err := rpgle.RelativeRecordNumber(v)
			if err != nil {
				return nil, err
			}
			out[i] = n
			continue
		}
		s, err := rpgle.StoredValue(f, v)
		if err != nil {
			return nil, err
		}
		out[i] = s
	}
	return out, nil
}

// row is one fetched record: its fields, key and relative record number.
type row struct {
	rec rpgle.Record
	key []any
	rrn int64
}

func (h *handle) fetch(where string, params []any, forward bool) (*row, error) {
	dir := "DESC"
	if forward {
		dir = "ASC"
	}
	var order []string
	for _, c := range append(append([]string{}, h.keyCols...), rrnCol) {
		order = append(order, q(c)+" "+dir)
	}
	rs, err := h.conn.Query(fmt.Sprintf("SELECT * FROM %s WHERE %s ORDER BY %s LIMIT 1",
		h.table, where, strings.Join(order, ", ")), params...)
	if err != nil {
		return nil, err
	}
	defer rs.Close()
	if !rs.Next() {
		return nil, rs.Err()
	}
	cols, err := rs.Columns()
	if err != nil {
		return nil, err
	}
	vals := make([]any, len(cols))
	ptrs := make([]any, len(cols))
	for i := range vals {
		ptrs[i] = &vals[i]
	}
	if err := rs.Scan(ptrs...); err != nil {
		return nil, err
	}
	byName := map[string]any{}
	for i, c := range cols {
		byName[c] = vals[i]
	}
	r := &row{rec: rpgle.Record{}}
	for _, f := range h.pf.Fields {
		r.rec[f.Name] = fromStored(f, byName[f.Name])
	}
	for _, c := range h.keyCols {
		r.key = append(r.key, byName[c])
	}
	r.rrn, _ = byName[rrnCol].(int64)
	return r, nil
}

func fromStored(f rpgle.PFField, x any) rpgle.Value {
	switch v := x.(type) {
	case string:
		return rpgle.StringValue(v)
	case []byte:
		return rpgle.StringValue(string(v))
	case int64:
		return rpgle.NumberValue(float64(v))
	case float64:
		return rpgle.NumberValue(v)
	}
	if f.DType == "A" {
		return rpgle.StringValue("")
	}
	return rpgle.NumberValue(0)
}

func (h *handle) stepCondition(forward bool) (string, []any) {
	if h.pos.kind == posRec {
		names := make([]string, 0, len(h.keyCols)+1)
		for _, c := range append(append([]string{}, h.keyCols...), rrnCol) {
			names = append(names, q(c))
		}
		op := "<"
		if forward {
			op = ">"
		}
		params := append(append([]any{}, h.pos.key...), h.pos.rrn)
		return fmt.Sprintf("(%s) %s (%s)", strings.Join(names, ", "), op, marks(len(params))), params
	}
	prefix := h.pos.prefix
	if len(prefix) == 0 {
		// Positioned before everything (setll) or after everything (setgt).
		if (h.pos.kind == posSetll) == forward {
			return "1", nil
		}
		return "0", nil
	}
	names := make([]string, len(prefix))
	for i := range prefix {
		names[i] = q(h.keyCols[i])
	}
	var op string
	switch {
	case forward && h.pos.kind == posSetll:
		op = ">="
	case forward:
		op = ">"
	case h.pos.kind == posSetll:
		op = "<"
	default:
		op = "<="
	}
	return fmt.Sprintf("(%s) %s (%s)", strings.Join(names, ", "), op, marks(len(prefix))), prefix
}

func (h *handle) land(r *row) rpgle.Record {
	h.pos = position{kind: posRec, key: r.key, rrn: r.rrn}
	h.current = r.rrn
	h.eof = false
	return r.rec
}

func (h *handle) step(forward bool, match []any) (rpgle.Record, error) {
	where, params := h.stepCondition(forward)
	r, err := h.fetch(where, params, forward)
	if err != nil {
		return nil, err
	}
	if r == nil {
		h.eof = true
		return nil, nil
	}
	rec := h.land(r)
	for i, m := range match {
		if r.key[i] != m {
			// Positioned past the last match; nothing is current.
			h.eof = true
			h.current = 0
			return nil, nil
		}
	}
	return rec, nil
}

func (h *handle) Read() (rpgle.Record, error)  { return h.step(true, nil) }
func (h *handle) Readp() (rpgle.Record, error) { return h.step(false, nil) }

func (h *handle) Reade(key []rpgle.Value) (rpgle.Record, error) {
	k, err := h.coerceKey(key)
	if err != nil {
		return nil, err
	}
	return h.step(true, k)
}

func (h *handle) Readpe(key []rpgle.Value) (rpgle.Record, error) {
	k, err := h.coerceKey(key)
	if err != nil {
		return nil, err
	}
	return h.step(false, k)
}

func (h *handle) equalsClause(n int) string {
	parts := make([]string, n)
	for i := range parts {
		parts[i] = q(h.keyCols[i]) + " = ?"
	}
	if n == 0 {
		return "1"
	}
	return strings.Join(parts, " AND ")
}

func (h *handle) Chain(key []rpgle.Value) (rpgle.Record, error) {
	k, err := h.coerceKey(key)
	if err != nil {
		return nil, err
	}
	r, err := h.fetch(h.equalsClause(len(k)), k, true)
	if err != nil {
		return nil, err
	}
	h.found = r != nil
	if r == nil {
		return nil, nil
	}
	return h.land(r), nil
}

// Setll positions before the first record with key >= the given key; a nil
// key is *LOVAL, the very start of the file.
func (h *handle) Setll(key []rpgle.Value) error {
	if key == nil {
		return h.position(posSetll, nil)
	}
	k, err := h.coerceKey(key)
	if err != nil {
		return err
	}
	return h.position(posSetll, k)
}

// Setgt positions after the last record with key <= the given key; a nil key
// is *HIVAL, the very end of the file.
func (h *handle) Setgt(key []rpgle.Value) error {
	if key == nil {
		h.pos = position{kind: posSetgt}
		h.found, h.equal, h.eof = false, false, false
		return nil
	}
	k, err := h.coerceKey(key)
	if err != nil {
		return err
	}
	return h.position(posSetgt, k)
}

func (h *handle) position(kind posKind, prefix []any) error {
	h.pos = position{kind: kind, prefix: prefix}
	h.eof = false
	if len(prefix) == 0 {
		h.equal = false
		h.found = false
		if kind == posSetll {
			n, err := h.count("1", nil)
			if err != nil {
				return err
			}
			h.found = n > 0
		}
		return nil
	}
	names := make([]string, len(prefix))
	for i := range prefix {
		names[i] = q(h.keyCols[i])
	}
	op := ">="
	if kind == posSetgt {
		op = ">"
	}
	n, err := h.count(fmt.Sprintf("(%s) %s (%s)", strings.Join(names, ", "), op, marks(len(prefix))), prefix)
	if err != nil {
		return err
	}
	h.found = n > 0
	n, err = h.count(h.equalsClause(len(prefix)), prefix)
	if err != nil {
		return err
	}
	h.equal = n > 0
	return nil
}

func (h *handle) count(where string, params []any) (int, error) {
	var n int
	err := h.conn.QueryRow("SELECT COUNT(*) FROM "+h.table+" WHERE "+where, params...).Scan(&n)
	return n, err
}

func (h *handle) values(rec rpgle.Record) ([]any, error) {
	var missing []string
	out := make([]any, len(h.pf.Fields))
	for i, f := range h.pf.Fields {
		v, ok := rec[f.Name]
		if !ok {
			missing = append(missing, f.Name)
			continue
		}
		s, err := rpgle.StoredValue(f, v)
		if err != nil {
			return nil, err
		}
		out[i] = s
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("%s: record is missing fields %v", h.pf.Name, missing)
	}
	return out, nil
}

func (h *handle) fieldList(format func(string) string) string {
	parts := make([]string, len(h.pf.Fields))
	for i, f := range h.pf.Fields {
		parts[i] = format(q(f.Name))
	}
	return strings.Join(parts, ", ")
}

func isConstraint(err error) bool {
	var se *sqlite.Error
	// 19 is SQLITE_CONSTRAINT; the extended codes (unique, primary key, not
	// null ...) carry it in their low byte.
	return errors.As(err, &se) && se.Code()&0xff == 19
}

func (h *handle) Write(rec rpgle.Record) error {
	vals, err := h.values(rec)
	if err != nil {
		return err
	}
	_, err = h.conn.Exec(fmt.Sprintf("INSERT INTO %s (%s) VALUES (%s)",
		h.table, h.fieldList(func(s string) string { return s }), marks(len(vals))), vals...)
	if isConstraint(err) {
		return fmt.Errorf("%s: duplicate key", h.pf.Name)
	}
	return err
}

func (h *handle) Update(rec rpgle.Record) error {
	if h.current == 0 {
		return fmt.Errorf("%s: UPDATE with no current record (read or chain one first)", h.pf.Name)
	}
	vals, err := h.values(rec)
	if err != nil {
		return err
	}
	res, err := h.conn.Exec(fmt.Sprintf("UPDATE %s SET %s WHERE %s = ?",
		h.table, h.fieldList(func(s string) string { return s + " = ?" }), q(rrnCol)), append(vals, h.current)...)
	if isConstraint(err) {
		return fmt.Errorf("%s: duplicate key", h.pf.Name)
	}
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("%s: the current record no longer exists", h.pf.Name)
	}
	// The key may have changed; keep the position on this record.
	r, err := h.fetch(q(rrnCol)+" = ?", []any{h.current}, true)
	if err != nil {
		return err
	}
	if r == nil {
		return fmt.Errorf("%s: the current record no longer exists", h.pf.Name)
	}
	h.pos = position{kind: posRec, key: r.key, rrn: h.current}
	return nil
}

// Delete removes the record with the given key (first match), or the current
// record when key is nil. It reports whether one was deleted.
func (h *handle) Delete(key []rpgle.Value) (bool, error) {
	var rrn int64
	if key == nil {
		if h.current == 0 {
			return false, fmt.Errorf("%s: DELETE with no current record (read or chain one first)", h.pf.Name)
		}
		rrn = h.current
	} else {
		k, err := h.coerceKey(key)
		if err != nil {
			return false, err
		}
		r, err := h.fetch(h.equalsClause(len(k)), k, true)
		if err != nil {
			return false, err
		}
		if r == nil {
			h.found = false
			return false, nil
		}
		rrn = r.rrn
	}
	res, err := h.conn.Exec("DELETE FROM "+h.table+" WHERE "+q(rrnCol)+" = ?", rrn)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	h.found = n > 0
	if rrn == h.current {
		h.current = 0
	}
	return h.found, nil
}
