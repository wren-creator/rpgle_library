package rpgle_test

import (
	"regexp"
	"strconv"
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

func parseAdventure(t *testing.T) (*rpgle.Program, *rpgle.DDS) {
	t.Helper()
	dds, err := rpgle.ParseDDS(readAdventureDDS(t))
	if err != nil {
		t.Fatalf("ParseDDS: %v", err)
	}
	prog, err := rpgle.ParseRPGLE(readAdventureRPGLE(t))
	if err != nil {
		t.Fatalf("ParseRPGLE: %v", err)
	}
	return prog, dds
}

func TestBuildVarsBindsDDSAndDSpecFields(t *testing.T) {
	prog, dds := parseAdventure(t)
	vars := rpgle.BuildVars(prog, dds)

	plrhp, ok := vars["plrhp"]
	if !ok || plrhp.Type != 'N' || plrhp.Length != 3 || plrhp.Value.AsFloat() != 0 {
		t.Errorf("plrhp = %+v, want DDS-bound N/3/0", plrhp)
	}
	loctxt, ok := vars["loctxt"]
	if !ok || loctxt.Type != 'A' || loctxt.Length != 30 {
		t.Errorf("loctxt = %+v, want DDS-bound A/30", loctxt)
	}
	seed, ok := vars["seed"]
	if !ok || seed.Type != 'N' || seed.Value.AsFloat() != 17 {
		t.Errorf("seed = %+v, want D-spec N with INZ 17", seed)
	}
	loc, ok := vars["loc"]
	if !ok || loc.Value.AsFloat() != 1 {
		t.Errorf("loc = %+v, want D-spec N with INZ 1", loc)
	}
	killcnt, ok := vars["killcnt"]
	if !ok || killcnt.Value.AsFloat() != 0 {
		t.Errorf("killcnt = %+v, want D-spec N with INZ 0", killcnt)
	}
}

func TestCompileBackpatchesIfElseEndif(t *testing.T) {
	statements := []rpgle.Statement{
		{Op: "IF", Expr: "1 = 1"},
		{Op: "EVAL", Expr: "a = 1"},
		{Op: "ELSE"},
		{Op: "EVAL", Expr: "a = 2"},
		{Op: "ENDIF"},
	}
	compiled, err := rpgle.Compile(statements)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	if compiled.Instrs[0].Op != "IF" {
		t.Fatalf("instrs[0].Op = %q, want IF", compiled.Instrs[0].Op)
	}
	if compiled.Instrs[0].FalseTarget != 3 {
		t.Errorf("instrs[0].FalseTarget = %d, want 3", compiled.Instrs[0].FalseTarget)
	}
	if compiled.Instrs[2].Op != "JMP" {
		t.Fatalf("instrs[2].Op = %q, want JMP", compiled.Instrs[2].Op)
	}
	if compiled.Instrs[2].Target != 4 {
		t.Errorf("instrs[2].Target = %d, want 4", compiled.Instrs[2].Target)
	}
	if compiled.Instrs[4].Op != "NOP" {
		t.Errorf("instrs[4].Op = %q, want NOP", compiled.Instrs[4].Op)
	}
}

func TestCompileBackpatchesDowEnddoLeave(t *testing.T) {
	statements := []rpgle.Statement{
		{Op: "DOW", Expr: "1 = 1"},
		{Op: "LEAVE"},
		{Op: "ENDDO"},
	}
	compiled, err := rpgle.Compile(statements)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	if compiled.Instrs[0].FalseTarget != 3 {
		t.Errorf("instrs[0].FalseTarget = %d, want 3", compiled.Instrs[0].FalseTarget)
	}
	if compiled.Instrs[1].Target != 3 {
		t.Errorf("instrs[1].Target = %d, want 3", compiled.Instrs[1].Target)
	}
	if compiled.Instrs[2].Target != 0 {
		t.Errorf("instrs[2].Target = %d, want 0", compiled.Instrs[2].Target)
	}
}

func TestCompileRecordsSubroutineEntryPoints(t *testing.T) {
	statements := []rpgle.Statement{
		{Op: "BEGSR", Factor1: "MYSUB"},
		{Op: "EVAL", Expr: "a = 1"},
		{Op: "ENDSR"},
	}
	compiled, err := rpgle.Compile(statements)
	if err != nil {
		t.Fatalf("Compile: %v", err)
	}
	if compiled.Subroutines["MYSUB"] != 0 {
		t.Errorf("Subroutines[MYSUB] = %d, want 0", compiled.Subroutines["MYSUB"])
	}
}

func TestAdventureFirstScreen(t *testing.T) {
	prog, dds := parseAdventure(t)
	r := rpgle.Run(prog, dds)
	screen, done, err := r.Next(nil)
	if err != nil {
		t.Fatalf("Next: %v", err)
	}
	if done {
		t.Fatal("program ended before first EXFMT")
	}
	if screen.FormatName != "MAIN" {
		t.Errorf("FormatName = %q, want MAIN", screen.FormatName)
	}
	byRow := map[int]string{}
	for _, f := range screen.Fields {
		byRow[f.Row] = f.Text
	}
	if got := strings.TrimSpace(byRow[2]); got != "a dark forest" {
		t.Errorf("row 2 = %q, want %q", got, "a dark forest")
	}
	if byRow[4] != "020" {
		t.Errorf("row 4 (HP) = %q, want 020", byRow[4])
	}
	if _, ok := byRow[6]; ok {
		t.Errorf("row 6 (MSGTXT) should be hidden until *IN90 is set, got %q", byRow[6])
	}
}

func TestAdventureF3AtMainMenuEndsImmediately(t *testing.T) {
	prog, dds := parseAdventure(t)
	r := rpgle.Run(prog, dds)
	if _, done, err := r.Next(nil); err != nil || done {
		t.Fatalf("first Next: done=%v err=%v", done, err)
	}
	_, done, err := r.Next(&rpgle.Resume{Key: "F3", Values: map[string]string{"cmd": ""}})
	if err != nil {
		t.Fatalf("Next: %v", err)
	}
	if !done {
		t.Error("expected program to end after F3, but it did not")
	}
}

var numericRe = regexp.MustCompile(`^[0-9]+$`)

func TestAdventureAlwaysFightPlaythroughStaysInBoundsAndEnds(t *testing.T) {
	prog, dds := parseAdventure(t)
	r := rpgle.Run(prog, dds)
	screen, done, err := r.Next(nil)
	if err != nil || done {
		t.Fatalf("first Next: done=%v err=%v", done, err)
	}

	sawCombat, sawEndgame := false, false
	steps := 0
	for ; steps < 500; steps++ {
		if screen.FormatName != "MAIN" && screen.FormatName != "COMBAT" && screen.FormatName != "ENDGAME" {
			t.Fatalf("unexpected format %q", screen.FormatName)
		}
		if screen.FormatName == "COMBAT" {
			sawCombat = true
		}
		if screen.FormatName == "ENDGAME" {
			sawEndgame = true
		}
		cmd := "F"
		if screen.FormatName == "MAIN" {
			cmd = "M"
		}
		next, isDone, err := r.Next(&rpgle.Resume{Key: "ENTER", Values: map[string]string{"cmd": cmd}})
		if err != nil {
			t.Fatalf("Next: %v", err)
		}
		if isDone {
			break
		}
		screen = next
		for _, f := range screen.Fields {
			trimmed := strings.TrimSpace(f.Text)
			if numericRe.MatchString(trimmed) && len(f.Text) > 6 {
				t.Errorf("numeric field text %q exceeds declared width", f.Text)
			}
		}
	}
	if !sawCombat {
		t.Error("expected at least one encounter over 500 steps")
	}
	if !sawEndgame {
		t.Error("expected the playthrough to reach an ending over 500 steps")
	}
	if steps >= 500 {
		t.Error("playthrough should terminate well within the step budget")
	}
}

// A numeric field with decimals is shown with its decimal point (one column
// wider than a real 5250 draws it), so what's on screen survives being typed
// back in: "000004875" would parse as 4875, not 48.75. (Parity with Python.)
func TestNumericFieldWithDecimalsShowsItsDecimalPoint(t *testing.T) {
	dds, err := rpgle.ParseDDS("     A          R MAIN\n     A            AMT            9  2O  1  1\n")
	if err != nil {
		t.Fatal(err)
	}
	show := func(value float64, dec, length int) string {
		vars := map[string]*rpgle.Var{"amt": {Type: 'N', Length: length, Dec: dec, Value: rpgle.NumberValue(value)}}
		return rpgle.RenderFormat(dds.Formats["MAIN"], vars, map[string]bool{}).Fields[0].Text
	}
	cases := []struct {
		value       float64
		dec, length int
		want        string
	}{
		{48.75, 2, 9, "0000048.75"},
		{-48.75, 2, 9, "-000048.75"},
		{0, 2, 9, "0000000.00"},
		{1250, 2, 9, "0001250.00"},
		{42, 0, 5, "00042"}, // whole-number fields are unchanged
		{-5, 0, 5, "-0005"},
	}
	for _, c := range cases {
		if got := show(c.value, c.dec, c.length); got != c.want {
			t.Errorf("show(%v, dec %d, len %d) = %q, want %q", c.value, c.dec, c.length, got, c.want)
		}
	}
	// it round-trips: the text parses back to the same number
	if f, err := strconv.ParseFloat(show(48.75, 2, 9), 64); err != nil || f != 48.75 {
		t.Errorf("round trip of 48.75 = %v, %v", f, err)
	}
}
