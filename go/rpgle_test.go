package rpgle_test

import (
	"os"
	"path/filepath"
	"sort"
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
