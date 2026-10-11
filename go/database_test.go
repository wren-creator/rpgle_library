package rpgle_test

// Keyed file access on SQLite, through the rpgle.FileHandle interface.
// Mirrors python/tests/test_database.py case for case. (These live beside the
// core tests, not in sqlitedb/, so they can share the column helpers.)

import (
	"path/filepath"
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
	"github.com/wren-creator/rpgle_library/go/sqlitedb"
)

func mustPF(t testing.TB, name string, lines ...string) *rpgle.PhysicalFile {
	t.Helper()
	pf, err := rpgle.ParsePF(name, ddsText(lines...))
	if err != nil {
		t.Fatal(err)
	}
	return pf
}

var (
	custPFDef = []string{
		ddsLine("", "", "", "", "", "UNIQUE"),
		ddsLine("R", "CUSTREC", "", "", "", ""),
		ddsLine("", "CUSTNO", "5", "S", "0", ""),
		ddsLine("", "NAME", "10", "", "", ""),
		ddsLine("", "BAL", "9", "P", "2", ""),
		ddsLine("K", "CUSTNO", "", "", "", ""),
	}
	// Orders: key is (CUSTNO, ORDNO); duplicates of the full key are allowed.
	ordersPFDef = []string{
		ddsLine("R", "ORDREC", "", "", "", ""),
		ddsLine("", "CUSTNO", "5", "S", "0", ""),
		ddsLine("", "ORDNO", "5", "S", "0", ""),
		ddsLine("", "AMT", "7", "P", "2", ""),
		ddsLine("K", "CUSTNO", "", "", "", ""),
		ddsLine("K", "ORDNO", "", "", "", ""),
	}
	logPFDef = []string{ddsLine("R", "LOGREC", "", "", "", ""), ddsLine("", "MSG", "10", "", "", "")}
)

func num(n float64) rpgle.Value           { return rpgle.NumberValue(n) }
func str(s string) rpgle.Value            { return rpgle.StringValue(s) }
func key(vs ...rpgle.Value) []rpgle.Value { return vs }

func memDB(t testing.TB) *sqlitedb.DB {
	t.Helper()
	db, err := sqlitedb.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func openFile(t testing.TB, db *sqlitedb.DB, pf *rpgle.PhysicalFile) rpgle.FileHandle {
	t.Helper()
	h, err := db.OpenFile(pf)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func must[T any](t testing.TB) func(T, error) T {
	return func(v T, err error) T {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
		return v
	}
}

func custRec(no float64, name string, bal float64) rpgle.Record {
	return rpgle.Record{"CUSTNO": num(no), "NAME": str(name), "BAL": num(bal)}
}

func orderRec(c, o, amt float64) rpgle.Record {
	return rpgle.Record{"CUSTNO": num(c), "ORDNO": num(o), "AMT": num(amt)}
}

func customers(t *testing.T) rpgle.FileHandle {
	f := openFile(t, memDB(t), mustPF(t, "CUSTMAST", custPFDef...))
	// Written out of order on purpose: reads must come back in key order.
	for _, c := range []struct {
		no   float64
		name string
	}{{30, "CAROL"}, {10, "ALICE"}, {20, "BOB"}} {
		if err := f.Write(custRec(c.no, c.name, 0)); err != nil {
			t.Fatal(err)
		}
	}
	return f
}

func orders(t *testing.T) rpgle.FileHandle {
	f := openFile(t, memDB(t), mustPF(t, "ORDERS", ordersPFDef...))
	for _, o := range [][2]float64{{2, 101}, {1, 102}, {1, 100}, {3, 100}, {1, 101}} {
		if err := f.Write(orderRec(o[0], o[1], o[1])); err != nil {
			t.Fatal(err)
		}
	}
	return f
}

func nameOf(rec rpgle.Record) string {
	if rec == nil {
		return "<none>"
	}
	return rec["NAME"].Str
}

func eq[T comparable](t *testing.T, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestChainFoundAndNotFound(t *testing.T) {
	f := customers(t)
	eq(t, nameOf(must[rpgle.Record](t)(f.Chain(key(num(20))))), "BOB")
	eq(t, f.Found(), true)
	eq(t, nameOf(must[rpgle.Record](t)(f.Chain(key(num(25))))), "<none>")
	eq(t, f.Found(), false)
}

func TestCharKeysIgnoreTrailingBlanks(t *testing.T) {
	f := openFile(t, memDB(t), mustPF(t, "NAMES", ddsLine("R", "R", "", "", "", ""), ddsLine("", "N", "5", "", "", ""), ddsLine("K", "N", "", "", "", "")))
	if err := f.Write(rpgle.Record{"N": str("AB   ")}); err != nil {
		t.Fatal(err)
	}
	if rec := must[rpgle.Record](t)(f.Chain(key(str("AB")))); rec == nil {
		t.Error("AB should match AB with trailing blanks")
	}
}

func TestReadWalksInKeyOrderThenEOF(t *testing.T) {
	f := customers(t)
	if err := f.Setll(nil); err != nil {
		t.Fatal(err)
	}
	var names []string
	for rec := must[rpgle.Record](t)(f.Read()); rec != nil; rec = must[rpgle.Record](t)(f.Read()) {
		names = append(names, nameOf(rec))
	}
	eq(t, strings.Join(names, ","), "ALICE,BOB,CAROL")
	eq(t, f.EOF(), true)
}

func TestReadWithNoPositionStartsAtTheTop(t *testing.T) {
	eq(t, nameOf(must[rpgle.Record](t)(customers(t).Read())), "ALICE")
}

func TestSetllExactAndBetween(t *testing.T) {
	f := customers(t)
	f.Setll(key(num(20)))
	eq(t, f.Found() && f.Equal(), true)
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "BOB")

	f.Setll(key(num(15)))
	eq(t, f.Found(), true)
	eq(t, f.Equal(), false)
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "BOB") // first key >= 15
}

func TestSetllPastTheEndIsNotFound(t *testing.T) {
	f := customers(t)
	f.Setll(key(num(99)))
	eq(t, f.Found(), false)
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "<none>")
	eq(t, f.EOF(), true)
}

func TestSetgtSkipsTheEqualRecord(t *testing.T) {
	f := customers(t)
	f.Setgt(key(num(20)))
	eq(t, f.Found(), true)
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "CAROL")
}

func TestHivalThenReadpGivesTheLastRecord(t *testing.T) {
	f := customers(t)
	f.Setgt(nil)
	eq(t, nameOf(must[rpgle.Record](t)(f.Readp())), "CAROL")
	eq(t, nameOf(must[rpgle.Record](t)(f.Readp())), "BOB")
}

func TestReadpAfterReadGoesBack(t *testing.T) {
	f := customers(t)
	f.Chain(key(num(20)))
	eq(t, nameOf(must[rpgle.Record](t)(f.Readp())), "ALICE")
	eq(t, nameOf(must[rpgle.Record](t)(f.Readp())), "<none>")
	eq(t, f.EOF(), true)
}

func TestUniqueKeyRejectsADuplicateWrite(t *testing.T) {
	err := customers(t).Write(custRec(20, "DUPE", 0))
	if err == nil || !strings.Contains(err.Error(), "duplicate key") {
		t.Errorf("got %v", err)
	}
}

func TestUpdateChangesTheCurrentRecord(t *testing.T) {
	f := customers(t)
	f.Chain(key(num(20)))
	if err := f.Update(custRec(20, "ROBERT", 5.5)); err != nil {
		t.Fatal(err)
	}
	rec := must[rpgle.Record](t)(f.Chain(key(num(20))))
	eq(t, rec["NAME"].Str, "ROBERT")
	eq(t, rec["BAL"].Num, 5.5)
}

func TestUpdateWithNoCurrentRecordIsAnError(t *testing.T) {
	if err := customers(t).Update(custRec(1, "X", 0)); err == nil || !strings.Contains(err.Error(), "no current record") {
		t.Errorf("got %v", err)
	}
}

func TestUpdateCanChangeTheKeyAndKeepPosition(t *testing.T) {
	f := customers(t)
	f.Chain(key(num(20)))
	if err := f.Update(custRec(25, "BOB", 0)); err != nil {
		t.Fatal(err)
	}
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "CAROL")
	eq(t, nameOf(must[rpgle.Record](t)(f.Chain(key(num(20))))), "<none>")
	eq(t, nameOf(must[rpgle.Record](t)(f.Chain(key(num(25))))), "BOB")
}

func TestDeleteCurrentAndByKey(t *testing.T) {
	f := customers(t)
	f.Chain(key(num(20)))
	eq(t, must[bool](t)(f.Delete(nil)), true)
	eq(t, nameOf(must[rpgle.Record](t)(f.Chain(key(num(20))))), "<none>")
	eq(t, must[bool](t)(f.Delete(key(num(10)))), true)
	eq(t, must[bool](t)(f.Delete(key(num(10)))), false)
	eq(t, f.Found(), false)
}

func TestDeleteWithNoCurrentRecordIsAnError(t *testing.T) {
	if _, err := customers(t).Delete(nil); err == nil || !strings.Contains(err.Error(), "no current record") {
		t.Errorf("got %v", err)
	}
}

func TestDeleteDuringAReadLoopKeepsThePosition(t *testing.T) {
	f := customers(t)
	f.Setll(nil)
	var seen []string
	for rec := must[rpgle.Record](t)(f.Read()); rec != nil; rec = must[rpgle.Record](t)(f.Read()) {
		seen = append(seen, nameOf(rec))
		must[bool](t)(f.Delete(nil))
	}
	eq(t, strings.Join(seen, ","), "ALICE,BOB,CAROL")
	f.Setll(nil)
	eq(t, nameOf(must[rpgle.Record](t)(f.Read())), "<none>")
}

func TestNumbersRoundTrip(t *testing.T) {
	f := customers(t)
	f.Write(custRec(40, "DAVE", 1234.56))
	rec := must[rpgle.Record](t)(f.Chain(key(num(40))))
	eq(t, rec["BAL"].Num, 1234.56)
	eq(t, rec["CUSTNO"].Num, 40.0)
}

func TestNumericFieldsRefuseTextThatIsNotANumber(t *testing.T) {
	if _, err := customers(t).Chain(key(str("abc"))); err == nil || !strings.Contains(err.Error(), "not a number") {
		t.Errorf("got %v", err)
	}
}

func TestReadeLoopsOverOneCustomersOrders(t *testing.T) {
	f := orders(t)
	f.Setll(key(num(1)))
	eq(t, f.Equal(), true)
	var got []float64
	for rec := must[rpgle.Record](t)(f.Reade(key(num(1)))); rec != nil; rec = must[rpgle.Record](t)(f.Reade(key(num(1)))) {
		got = append(got, rec["ORDNO"].Num)
	}
	eq(t, len(got), 3)
	eq(t, got[0]+got[1]+got[2], 303.0)
	eq(t, got[0], 100.0)
	eq(t, f.EOF(), true)
}

func TestReadeWithNoMatchIsAnImmediateEOF(t *testing.T) {
	f := orders(t)
	f.Setll(key(num(9)))
	eq(t, must[rpgle.Record](t)(f.Reade(key(num(9)))) == nil, true)
	eq(t, f.EOF(), true)
}

func TestChainOnAPartialKeyFindsTheFirstMatch(t *testing.T) {
	eq(t, must[rpgle.Record](t)(orders(t).Chain(key(num(1))))["ORDNO"].Num, 100.0)
}

func TestChainOnTheFullKey(t *testing.T) {
	f := orders(t)
	eq(t, must[rpgle.Record](t)(f.Chain(key(num(1), num(102))))["AMT"].Num, 102.0)
	eq(t, must[rpgle.Record](t)(f.Chain(key(num(1), num(999)))) == nil, true)
}

func TestReadpeWalksBackwardWithinAKey(t *testing.T) {
	f := orders(t)
	f.Setgt(key(num(1)))
	var got []float64
	for rec := must[rpgle.Record](t)(f.Readpe(key(num(1)))); rec != nil; rec = must[rpgle.Record](t)(f.Readpe(key(num(1)))) {
		got = append(got, rec["ORDNO"].Num)
	}
	eq(t, len(got), 3)
	eq(t, got[0], 102.0)
	eq(t, got[2], 100.0)
}

func TestTooManyKeyValuesIsAnError(t *testing.T) {
	if _, err := orders(t).Chain(key(num(1), num(2), num(3))); err == nil || !strings.Contains(err.Error(), "key has 3 fields") {
		t.Errorf("got %v", err)
	}
}

func TestDuplicateFullKeysAreAllowedAndReadInArrivalOrder(t *testing.T) {
	f := orders(t)
	f.Write(orderRec(1, 100, 7))
	f.Setll(key(num(1), num(100)))
	a := must[rpgle.Record](t)(f.Read())
	b := must[rpgle.Record](t)(f.Read())
	eq(t, a["AMT"].Num, 100.0)
	eq(t, b["AMT"].Num, 7.0)
}

func TestArrivalSequenceFilesReadInArrivalOrderAndChainByRecordNumber(t *testing.T) {
	f := openFile(t, memDB(t), mustPF(t, "LOG", logPFDef...))
	for _, m := range []string{"one", "two", "three"} {
		f.Write(rpgle.Record{"MSG": str(m)})
	}
	eq(t, must[rpgle.Record](t)(f.Read())["MSG"].Str, "one")
	eq(t, must[rpgle.Record](t)(f.Read())["MSG"].Str, "two")
	eq(t, must[rpgle.Record](t)(f.Read())["MSG"].Str, "three")
	eq(t, must[rpgle.Record](t)(f.Read()) == nil, true)
	eq(t, must[rpgle.Record](t)(f.Chain(key(num(2))))["MSG"].Str, "two")
}

func TestDataSurvivesAReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.db")
	db, err := sqlitedb.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	pf := mustPF(t, "CUSTMAST", custPFDef...)
	openFile(t, db, pf).Write(custRec(1, "ALICE", 0))
	db.Close()

	db2, err := sqlitedb.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer db2.Close()
	eq(t, nameOf(must[rpgle.Record](t)(openFile(t, db2, pf).Chain(key(num(1))))), "ALICE")
}

func TestAChangedDDSFailsLoudly(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.db")
	db, _ := sqlitedb.Open(path)
	openFile(t, db, mustPF(t, "CUSTMAST", custPFDef...))
	db.Close()

	other := mustPF(t, "CUSTMAST", ddsLine("R", "R", "", "", "", ""), ddsLine("", "ZZZ", "3", "", "", ""))
	db3, _ := sqlitedb.Open(path)
	defer db3.Close()
	if _, err := db3.OpenFile(other); err == nil || !strings.Contains(err.Error(), "don't match") {
		t.Errorf("got %v", err)
	}
}

func TestCloseTwiceIsHarmless(t *testing.T) {
	db, _ := sqlitedb.Open(":memory:")
	db.Close()
	db.Close()
}
