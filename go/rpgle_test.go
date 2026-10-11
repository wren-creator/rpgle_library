package rpgle_test

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

func readAdventureRPGLE(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "programs", "adventure.rpgle"))
	if err != nil {
		t.Fatalf("reading adventure.rpgle: %v", err)
	}
	return string(b)
}

func TestParseRPGLEReadsFAndDAndCSpecs(t *testing.T) {
	prog, err := rpgle.ParseRPGLE(readAdventureRPGLE(t))
	if err != nil {
		t.Fatalf("ParseRPGLE: %v", err)
	}

	if prog.File.Name != "SCREEN" {
		t.Errorf("File.Name = %q, want SCREEN", prog.File.Name)
	}
	if prog.File.Device != "WORKSTN" {
		t.Errorf("File.Device = %q, want WORKSTN", prog.File.Device)
	}
	if prog.File.Format != "E" {
		t.Errorf("File.Format = %q, want E", prog.File.Format)
	}

	var dnames []string
	for _, d := range prog.DSpecs {
		dnames = append(dnames, d.Name)
	}
	sort.Strings(dnames)
	want := []string{"dmg", "killcnt", "loc", "quot", "roll", "seed"}
	if len(dnames) != len(want) {
		t.Fatalf("dspec names = %v, want %v", dnames, want)
	}
	for i := range want {
		if dnames[i] != want[i] {
			t.Fatalf("dspec names = %v, want %v", dnames, want)
		}
	}

	if len(prog.Statements) <= 100 {
		t.Errorf("expected a substantial C-spec statement count, got %d", len(prog.Statements))
	}
	var sawDiv, sawMvr bool
	for _, s := range prog.Statements {
		if s.Op == "DIV" {
			sawDiv = true
		}
		if s.Op == "MVR" {
			sawMvr = true
		}
	}
	if !sawDiv {
		t.Error("expected at least one DIV statement")
	}
	if !sawMvr {
		t.Error("expected at least one MVR statement")
	}
}

// dline builds a fixed-form D-spec line by column: name from col 7, S in
// col 24, length ending col 39, decimals in 41-42, keywords from col 44.
func dline(name string, length string, dec string, keywords string) string {
	return fmt.Sprintf("     D%-17sS%15s%3s %s", name, length, dec, keywords)
}

// A source line the parser can't honour must be an error. Silently ignoring
// it lets a program run and quietly do the wrong thing. (Parity with Python.)
func TestADimKeywordIsAnErrorNotASilentlyScalarVariable(t *testing.T) {
	for _, kw := range []string{"DIM(10)", "dim(10)", "Dim (10)"} {
		if _, err := rpgle.ParseRPGLE(dline("ARR", "5", "0", kw)); err == nil || !strings.Contains(strings.ToUpper(err.Error()), "DIM") {
			t.Errorf("%q: want a DIM error, got %v", kw, err)
		}
	}
}

func TestATextLiteralThatMerelyMentionsDimIsNotAnArray(t *testing.T) {
	prog, err := rpgle.ParseRPGLE(dline("NOTE", "20", "", "INZ('see DIM(3) below')"))
	if err != nil || prog.DSpecs[0].Name != "NOTE" {
		t.Errorf("got %v, %v", prog, err)
	}
}

func TestAnOSpecIsAnErrorNotSilentlyIgnored(t *testing.T) {
	if _, err := rpgle.ParseRPGLE("     OQPRINT    D    01"); err == nil || !strings.Contains(err.Error(), "O-spec") {
		t.Errorf("want an O-spec error, got %v", err)
	}
}

func TestANonNumericLengthOrDecimalsIsAnErrorNamingTheField(t *testing.T) {
	if _, err := rpgle.ParseRPGLE(dline("PRICE", "5A", "0", "")); err == nil || !strings.Contains(err.Error(), "PRICE") || !strings.Contains(err.Error(), "not a number") {
		t.Errorf("length: got %v", err)
	}
	if _, err := rpgle.ParseRPGLE(dline("PRICE", "5", "x", "")); err == nil || !strings.Contains(err.Error(), "decimal positions") {
		t.Errorf("decimals: got %v", err)
	}
}

func TestOrdinaryDSpecsStillParseBlankDecimalsMeaningACharacterField(t *testing.T) {
	prog, err := rpgle.ParseRPGLE(dline("NAME", "20", "", "") + "\n" + dline("QTY", "5", "0", "INZ(42)"))
	if err != nil {
		t.Fatal(err)
	}
	if len(prog.DSpecs) != 2 || prog.DSpecs[0].Dec != nil || prog.DSpecs[0].Length != 20 {
		t.Errorf("NAME: %+v", prog.DSpecs[0])
	}
	if prog.DSpecs[1].Dec == nil || *prog.DSpecs[1].Dec != 0 || *prog.DSpecs[1].Init != "42" {
		t.Errorf("QTY: %+v", prog.DSpecs[1])
	}
}
