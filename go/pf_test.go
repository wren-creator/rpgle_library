package rpgle_test

import (
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

var custPF = ddsText(
	ddsLine("", "", "", "", "", "UNIQUE"),
	ddsLine("R", "CUSTREC", "", "", "", ""),
	ddsLine("", "CUSTNO", "5", "S", "0", ""),
	ddsLine("", "NAME", "20", "", "", ""),
	ddsLine("", "BAL", "9", "P", "2", ""),
	ddsLine("K", "CUSTNO", "", "", "", ""),
)

// ── physical file DDS (parity with python/tests/test_pf.py) ─────────────

func TestParsePFReadsTheFormatFieldsAndKey(t *testing.T) {
	pf, err := rpgle.ParsePF("custmast", custPF)
	if err != nil {
		t.Fatal(err)
	}
	if pf.Name != "CUSTMAST" || pf.Format != "CUSTREC" || !pf.Unique {
		t.Errorf("got %+v", pf)
	}
	var got []string
	for _, f := range pf.Fields {
		got = append(got, f.Name+":"+string(f.DType)+":"+string(rune('0'+f.Dec)))
	}
	if strings.Join(got, ",") != "CUSTNO:N:0,NAME:A:0,BAL:N:2" {
		t.Errorf("fields = %v", got)
	}
	if len(pf.Keys) != 1 || pf.Keys[0] != "CUSTNO" {
		t.Errorf("keys = %v", pf.Keys)
	}
}

func TestNoKLineMeansArrivalSequence(t *testing.T) {
	pf, err := rpgle.ParsePF("log", ddsText(ddsLine("R", "LOGREC", "", "", "", ""), ddsLine("", "MSG", "40", "", "", "")))
	if err != nil || len(pf.Keys) != 0 || pf.Unique {
		t.Errorf("got %+v, %v", pf, err)
	}
}

func TestParsePFRefusesWhatItCannotHonourLoudly(t *testing.T) {
	cases := []struct{ src, want string }{
		{ddsText(ddsLine("R", "R1", "", "", "", ""), ddsLine("", "A", "3", "", "", ""), ddsLine("K", "A", "", "", "", "DESCEND")), "DESCEND"},
		{ddsText(ddsLine("R", "R1", "", "", "", ""), ddsLine("", "A", "3", "", "", ""), ddsLine("K", "B", "", "", "", "")), "key field B is not in"},
		{"", "no record format"},
		{ddsText(ddsLine("R", "R1", "", "", "", ""), ddsLine("R", "R2", "", "", "", "")), "only one record format"},
		{ddsText(ddsLine("R", "R1", "", "", "", ""), ddsLine("", "A", "3X", "", "", "")), "length '3X' is not a number"},
		{ddsText(ddsLine("R", "R1", "", "", "", ""), ddsLine("", "A", "3", "S", "q", "")), "decimal positions 'q' is not a number"},
	}
	for _, c := range cases {
		if _, err := rpgle.ParsePF("F", c.src); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("want error containing %q, got %v", c.want, err)
		}
	}
}

func TestCommentLinesAreIgnored(t *testing.T) {
	pf, err := rpgle.ParsePF("f", strings.Join([]string{"     A* a comment", ddsLine("R", "R1", "", "", "", ""), ddsLine("", "X", "1", "", "", "")}, "\n"))
	if err != nil || len(pf.Fields) != 1 || pf.Fields[0].Name != "X" {
		t.Errorf("got %+v, %v", pf, err)
	}
}

// ── F-specs and resulting indicators ────────────────────────────────────

func TestEveryFSpecIsCollectedAndFileStaysTheWorkstnOne(t *testing.T) {
	prog, err := rpgle.ParseRPGLE(strings.Join([]string{
		fspec("CUSTMAST", fspecOpts{typ: "U", addition: "A", keyed: true}),
		fspec("INQ", fspecOpts{typ: "C", device: "WORKSTN"}),
		fspec("ORDERS", fspecOpts{}),
	}, "\n"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, f := range prog.Files {
		names = append(names, f.Name)
	}
	if strings.Join(names, ",") != "CUSTMAST,INQ,ORDERS" || prog.File.Name != "INQ" {
		t.Errorf("files %v, file %q", names, prog.File.Name)
	}
	cm := prog.Files[0]
	if cm.Type != "U" || cm.Addition != "A" || !cm.Keyed || cm.Device != "DISK" {
		t.Errorf("CUSTMAST: %+v", cm)
	}
}

func TestTheFirstWorkstnFileWinsNotTheLastFSpec(t *testing.T) {
	prog, _ := rpgle.ParseRPGLE(strings.Join([]string{fspec("A", fspecOpts{typ: "C", device: "WORKSTN"}), fspec("B", fspecOpts{typ: "C", device: "WORKSTN"})}, "\n"))
	if prog.File.Name != "A" {
		t.Errorf("File.Name = %q, want A", prog.File.Name)
	}
}

func TestResultIndicatorsAreReadFromColumns71To76(t *testing.T) {
	prog, err := rpgle.ParseRPGLE(cspec("CHAIN", cspecOpts{factor1: "custno", factor2: "CUSTMAST", hi: "71", lo: "72", eq: "73"}))
	if err != nil {
		t.Fatal(err)
	}
	s := prog.Statements[0]
	if s.Op != "CHAIN" || s.Factor1 != "custno" || s.Factor2 != "CUSTMAST" || s.IndHi != "71" || s.IndLo != "72" || s.IndEq != "73" {
		t.Errorf("got %+v", s)
	}
}

func TestAFileNameMustBeAnObjectNameSoItCanNeverBeAPath(t *testing.T) {
	for _, bad := range []string{"../x", "a/b", "a.b"} {
		if _, err := rpgle.ParseRPGLE("     F" + bad + strings.Repeat(" ", 10-len(bad)) + " O    E             PRINTER"); err == nil || !strings.Contains(err.Error(), "not a valid file name") {
			t.Errorf("%q: got %v", bad, err)
		}
	}
	prog, err := rpgle.ParseRPGLE(fspec("CUST$1", fspecOpts{}))
	if err != nil || prog.Files[0].Name != "CUST$1" {
		t.Errorf("CUST$1: %v, %v", prog, err)
	}
}
