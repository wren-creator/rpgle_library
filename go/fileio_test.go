package rpgle_test

// DISK file opcodes running inside a program. Mirrors
// python/tests/test_fileio.py and test/fileio.test.js.

import (
	"path/filepath"
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
	"github.com/wren-creator/rpgle_library/go/sqlitedb"
)

var (
	ioCustDef   = custPFDef
	ioOrdersDef = []string{
		ddsLine("R", "ORDREC", "", "", "", ""),
		ddsLine("", "OCUST", "5", "S", "0", ""),
		ddsLine("", "ORDNO", "5", "S", "0", ""),
		ddsLine("", "AMT", "7", "P", "2", ""),
		ddsLine("K", "OCUST", "", "", "", ""),
		ddsLine("K", "ORDNO", "", "", "", ""),
	}
	ioOutDef = []string{
		ddsLine("R", "OUTREC", "", "", "", ""),
		ddsLine("", "MSG", "20", "", "", ""),
		ddsLine("", "NUM", "9", "P", "2", ""),
	}
)

var (
	fCust = fspec("CUSTMAST", fspecOpts{typ: "U", addition: "A", keyed: true})
	fOrd  = fspec("ORDERS", fspecOpts{keyed: true})
	fOut  = fspec("OUT", fspecOpts{typ: "O"})
)

func cs(op string, o cspecOpts) string { return cspec(op, o) }
func f2(expr string) cspecOpts         { return cspecOpts{factor2: expr} }

type ioFixture struct {
	t   *testing.T
	db  *sqlitedb.DB
	pfs map[string]*rpgle.PhysicalFile
}

func newIO(t *testing.T) *ioFixture {
	db := memDB(t)
	fx := &ioFixture{t: t, db: db, pfs: map[string]*rpgle.PhysicalFile{
		"CUSTMAST": mustPF(t, "CUSTMAST", ioCustDef...),
		"ORDERS":   mustPF(t, "ORDERS", ioOrdersDef...),
		"OUT":      mustPF(t, "OUT", ioOutDef...),
	}}
	cust := openFile(t, db, fx.pfs["CUSTMAST"])
	for _, c := range []struct {
		no   float64
		name string
		bal  float64
	}{{10, "ALICE", 100}, {20, "BOB", 50.5}, {30, "CAROL", 0}} {
		if err := cust.Write(custRec(c.no, c.name, c.bal)); err != nil {
			t.Fatal(err)
		}
	}
	ord := openFile(t, db, fx.pfs["ORDERS"])
	for _, o := range [][3]float64{{1, 100, 10}, {1, 101, 20}, {2, 100, 5}, {1, 102, 30}} {
		if err := ord.Write(rpgle.Record{"OCUST": num(o[0]), "ORDNO": num(o[1]), "AMT": num(o[2])}); err != nil {
			t.Fatal(err)
		}
	}
	openFile(t, db, fx.pfs["OUT"])
	return fx
}

func (fx *ioFixture) runFiles(files []string, lines ...string) error {
	fx.t.Helper()
	prog, err := rpgle.ParseRPGLE(strings.Join(append(append([]string{}, files...), lines...), "\n"))
	if err != nil {
		return err
	}
	dds, _ := rpgle.ParseDDS("")
	r := rpgle.RunWith(prog, dds, rpgle.RunOptions{DB: fx.db, PFs: fx.pfs})
	for {
		_, done, err := r.Next(nil)
		if err != nil || done {
			return err
		}
	}
}

func (fx *ioFixture) run(lines ...string) {
	fx.t.Helper()
	if err := fx.runFiles([]string{fCust, fOrd, fOut}, lines...); err != nil {
		fx.t.Fatal(err)
	}
}

// out reads back every record the program wrote to OUT.
func (fx *ioFixture) out() [][2]any {
	h := openFile(fx.t, fx.db, fx.pfs["OUT"])
	h.Setll(nil)
	var rows [][2]any
	for r := must[rpgle.Record](fx.t)(h.Read()); r != nil; r = must[rpgle.Record](fx.t)(h.Read()) {
		rows = append(rows, [2]any{r["MSG"].Str, r["NUM"].Num})
	}
	return rows
}

func (fx *ioFixture) expectOut(want ...[2]any) {
	fx.t.Helper()
	got := fx.out()
	if len(got) != len(want) {
		fx.t.Fatalf("OUT = %v, want %v", got, want)
	}
	for i := range got {
		if got[i] != want[i] {
			fx.t.Fatalf("OUT = %v, want %v", got, want)
		}
	}
}

func row(msg string, n float64) [2]any { return [2]any{msg, n} }

func TestIOChainFoundLoadsTheRecordIntoVariables(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("WANT", 5, "0", "20"),
		cs("CHAIN", cspecOpts{factor1: "want", factor2: "CUSTMAST", hi: "71"}),
		cs("EVAL", f2("msg = name")),
		cs("EVAL", f2("num = bal")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("BOB", 50.5))
}

func TestIOChainNotFoundSetsTheIndicatorAndConditionedOpsFollowIt(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("WANT", 5, "0", "99"),
		cs("CHAIN", cspecOpts{factor1: "want", factor2: "CUSTMAST", hi: "71"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'NOT FOUND'", cond: "71"}),
		cs("EVAL", cspecOpts{factor2: "msg = name", cond: "N71"}),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("NOT FOUND", 0))
}

func TestIOChainWithALiteralKey(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("CHAIN", cspecOpts{factor1: "30", factor2: "CUSTMAST"}),
		cs("EVAL", f2("msg = name")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("CAROL", 0))
}

func TestIOAResultIndicatorIsResetEachTime(t *testing.T) {
	// 99 is not found (indicator on), then 10 is found (must go off).
	fx := newIO(t)
	fx.run(
		dspec("WANT", 5, "0", "99"),
		cs("CHAIN", cspecOpts{factor1: "want", factor2: "CUSTMAST", hi: "71"}),
		cs("EVAL", f2("want = 10")),
		cs("CHAIN", cspecOpts{factor1: "want", factor2: "CUSTMAST", hi: "71"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'STILL ON'", cond: "71"}),
		cs("EVAL", cspecOpts{factor2: "msg = name", cond: "N71"}),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("ALICE", 0))
}

func TestIOFoundAfterChain(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("CHAIN", cspecOpts{factor1: "20", factor2: "CUSTMAST"}),
		cs("IF", f2("%FOUND(CUSTMAST)")),
		cs("EVAL", f2("msg = 'YES'")),
		cs("ELSE", cspecOpts{}),
		cs("EVAL", f2("msg = 'NO'")),
		cs("ENDIF", cspecOpts{}),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("YES", 0))
}

func TestIOAReadLoopUntilEOFSumsEveryRecord(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("TOTAL", 9, "2", "0"),
		cs("SETLL", cspecOpts{factor1: "*LOVAL", factor2: "CUSTMAST"}),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("DOW", f2("%EOF(CUSTMAST) = *OFF")),
		cs("EVAL", f2("total = total + bal")),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("ENDDO", cspecOpts{}),
		cs("EVAL", f2("msg = 'TOTAL'")),
		cs("EVAL", f2("num = total")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("TOTAL", 150.5))
}

func TestIOTheEOFIndicatorFormOfTheSameLoop(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("N", 3, "0", "0"),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("DOW", f2("*IN75 = *OFF")),
		cs("EVAL", f2("n = n + 1")),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("ENDDO", cspecOpts{}),
		cs("EVAL", f2("num = n")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("", 3))
}

func TestIOLeaveInsideAnIfOnACounter(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("N", 3, "0", "0"),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("DOW", f2("*IN75 = *OFF")),
		cs("EVAL", f2("n = n + 1")),
		cs("IF", f2("n = 2")),
		cs("LEAVE", cspecOpts{}),
		cs("ENDIF", cspecOpts{}),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("ENDDO", cspecOpts{}),
		cs("EVAL", f2("num = n")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("", 2))
}

func TestIOLeaveConditionedOnTheEOFIndicator(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("N", 3, "0", "0"),
		cs("DOW", f2("1 = 1")),
		cs("READ", cspecOpts{factor2: "CUSTMAST", eq: "75"}),
		cs("LEAVE", cspecOpts{cond: "75"}),
		cs("EVAL", f2("n = n + 1")),
		cs("ENDDO", cspecOpts{}),
		cs("EVAL", f2("num = n")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("", 3))
}

func TestIOReadeOverAKlistPartialKey(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("TOTAL", 9, "2", "0"),
		dspec("WANT", 5, "0", "1"),
		cs("KLIST", cspecOpts{factor1: "ordk"}),
		cs("KFLD", cspecOpts{result: "want"}),
		cs("SETLL", cspecOpts{factor1: "ordk", factor2: "ORDERS"}),
		cs("READE", cspecOpts{factor1: "ordk", factor2: "ORDERS", eq: "75"}),
		cs("DOW", f2("%EOF = *OFF")),
		cs("EVAL", f2("total = total + amt")),
		cs("READE", cspecOpts{factor1: "ordk", factor2: "ORDERS", eq: "75"}),
		cs("ENDDO", cspecOpts{}),
		cs("EVAL", f2("num = total")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("", 60)) // customer 1: 10 + 20 + 30
}

func TestIOSetllIndicatorsNotFoundAndEqual(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("SETLL", cspecOpts{factor1: "20", factor2: "CUSTMAST", hi: "71", eq: "73"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'EQ'", cond: "73"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'NOEQ'", cond: "N73"}),
		cs("WRITE", f2("OUTREC")),
		cs("SETLL", cspecOpts{factor1: "99", factor2: "CUSTMAST", hi: "71", eq: "73"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'NF'", cond: "71"}),
		cs("WRITE", f2("OUTREC")),
	)
	got := fx.out()
	if len(got) != 2 || got[0][0] != "EQ" || got[1][0] != "NF" {
		t.Errorf("got %v", got)
	}
}

func TestIOAKeyCanBeAListOfExpressionsInParentheses(t *testing.T) {
	fx := newIO(t)
	fx.run(
		dspec("N", 5, "0", "1"),
		cs("CHAIN", cspecOpts{factor1: "(n : 100 + 1)", factor2: "ORDERS", hi: "71"}),
		cs("EVAL", f2("num = amt")),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("", 20))
}

func TestIOUpdateTheRecordJustChained(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("CHAIN", cspecOpts{factor1: "20", factor2: "CUSTMAST"}),
		cs("EVAL", f2("bal = bal + 10")),
		cs("UPDATE", f2("CUSTREC")),
	)
	rec := must[rpgle.Record](t)(openFile(t, fx.db, fx.pfs["CUSTMAST"]).Chain(key(num(20))))
	eq(t, rec["BAL"].Num, 60.5)
}

func TestIOWriteANewRecordFromVariables(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("EVAL", f2("custno = 40")),
		cs("EVAL", f2("name = 'DAVE'")),
		cs("EVAL", f2("bal = 12.5")),
		cs("WRITE", f2("CUSTREC")),
	)
	rec := must[rpgle.Record](t)(openFile(t, fx.db, fx.pfs["CUSTMAST"]).Chain(key(num(40))))
	eq(t, rec["NAME"].Str, "DAVE")
	eq(t, rec["BAL"].Num, 12.5)
}

func TestIODeleteTheCurrentRecord(t *testing.T) {
	fx := newIO(t)
	fx.run(cs("CHAIN", cspecOpts{factor1: "10", factor2: "CUSTMAST"}), cs("DELETE", f2("CUSTREC")))
	eq(t, must[rpgle.Record](t)(openFile(t, fx.db, fx.pfs["CUSTMAST"]).Chain(key(num(10)))) == nil, true)
}

func TestIODeleteByKeyReportsNotFound(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("DELETE", cspecOpts{factor1: "99", factor2: "CUSTMAST", hi: "71"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'NR'", cond: "71"}),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("NR", 0))
}

func TestIOADuplicateKeyWithAnErrorIndicatorDoesNotHalt(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("EVAL", f2("custno = 10")),
		cs("WRITE", cspecOpts{factor2: "CUSTREC", lo: "72"}),
		cs("EVAL", cspecOpts{factor2: "msg = 'ERR'", cond: "72"}),
		cs("WRITE", f2("OUTREC")),
	)
	fx.expectOut(row("ERR", 0))
}

func TestIOADuplicateKeyWithoutAnErrorIndicatorHalts(t *testing.T) {
	fx := newIO(t)
	err := fx.runFiles([]string{fCust, fOrd, fOut}, cs("EVAL", f2("custno = 10")), cs("WRITE", f2("CUSTREC")))
	if err == nil || !strings.Contains(err.Error(), "duplicate key") {
		t.Errorf("got %v", err)
	}
}

func TestIOAnUnknownFileIsAnErrorEvenWithAnErrorIndicator(t *testing.T) {
	fx := newIO(t)
	err := fx.runFiles([]string{fCust, fOrd, fOut}, cs("READ", cspecOpts{factor2: "NOPE", lo: "72"}))
	if err == nil || !strings.Contains(err.Error(), "not a DISK file") {
		t.Errorf("got %v", err)
	}
}

func TestIOFileAndFormatNamesAreMatchedIgnoringCase(t *testing.T) {
	fx := newIO(t)
	fx.run(
		cs("CHAIN", cspecOpts{factor1: "10", factor2: "custmast"}),
		cs("EVAL", f2("msg = name")),
		cs("WRITE", f2("outrec")),
	)
	fx.expectOut(row("ALICE", 0))
}

func TestIODiskFilesNeedADatabase(t *testing.T) {
	prog, _ := rpgle.ParseRPGLE(strings.Join([]string{fCust, cs("READ", f2("CUSTMAST"))}, "\n"))
	dds, _ := rpgle.ParseDDS("")
	_, _, err := rpgle.Run(prog, dds).Next(nil)
	if err == nil || !strings.Contains(err.Error(), "no database was given") {
		t.Errorf("got %v", err)
	}
}

func TestIOAMissingDDSForAFileIsAnError(t *testing.T) {
	fx := newIO(t)
	fx.pfs = map[string]*rpgle.PhysicalFile{}
	err := fx.runFiles([]string{fCust}, cs("READ", f2("CUSTMAST")))
	if err == nil || !strings.Contains(err.Error(), "no physical file DDS") {
		t.Errorf("got %v", err)
	}
}

func TestIOADiskFileMustBeExternallyDescribed(t *testing.T) {
	fx := newIO(t)
	err := fx.runFiles([]string{fspec("CUSTMAST", fspecOpts{fmt: "F"})})
	if err == nil || !strings.Contains(err.Error(), "externally described") {
		t.Errorf("got %v", err)
	}
}

func TestIOConditioningAControlOpIsRejectedLoudly(t *testing.T) {
	fx := newIO(t)
	err := fx.runFiles([]string{fCust}, cs("IF", cspecOpts{factor2: "1 = 1", cond: "71"}), cs("ENDIF", cspecOpts{}))
	if err == nil || !strings.Contains(err.Error(), "conditioning indicators on IF") {
		t.Errorf("got %v", err)
	}
}

func TestIOAnUnusableConditioningIndicatorIsRejectedLoudly(t *testing.T) {
	fx := newIO(t)
	err := fx.runFiles([]string{fCust}, cs("EVAL", cspecOpts{factor2: "x = 1", cond: "XX"}))
	if err == nil || !strings.Contains(err.Error(), "conditioning indicator") {
		t.Errorf("got %v", err)
	}
}

func TestIOBuildVarsIncludesDiskRecordFields(t *testing.T) {
	prog, _ := rpgle.ParseRPGLE(fCust)
	dds, _ := rpgle.ParseDDS("")
	v := rpgle.BuildVarsWith(prog, dds, map[string]*rpgle.PhysicalFile{"CUSTMAST": mustPF(t, "CUSTMAST", ioCustDef...)})
	eq(t, string(rune(v["custno"].Type))+string(rune(v["name"].Type)), "NA")
	eq(t, v["bal"].Dec, 2)
}

func TestIOChangesAreDurableWhenTheProgramEnds(t *testing.T) {
	path := filepath.Join(t.TempDir(), "t.db")
	db, err := sqlitedb.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	pf := mustPF(t, "CUSTMAST", ioCustDef...)
	openFile(t, db, pf)
	prog, _ := rpgle.ParseRPGLE(strings.Join([]string{
		fCust,
		cs("EVAL", f2("custno = 7")),
		cs("EVAL", f2("name = 'GRACE'")),
		cs("WRITE", f2("CUSTREC")),
	}, "\n"))
	dds, _ := rpgle.ParseDDS("")
	r := rpgle.RunWith(prog, dds, rpgle.RunOptions{DB: db, PFs: map[string]*rpgle.PhysicalFile{"CUSTMAST": pf}})
	if _, done, err := r.Next(nil); err != nil || !done {
		t.Fatalf("done=%v err=%v", done, err)
	}
	other, err := sqlitedb.Open(path) // a second connection sees it only if committed
	if err != nil {
		t.Fatal(err)
	}
	defer other.Close()
	eq(t, nameOf(must[rpgle.Record](t)(openFile(t, other, pf).Chain(key(num(7))))), "GRACE")
	db.Close()
}
