package rpgle_test

import (
	"fmt"
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

// RPG is case-insensitive, so EXFMT main finds the format DDS calls MAIN, and
// the screen reports the DDS's own spelling. (Parity with the Python port.)
func TestExfmtFormatNamesAreCaseInsensitive(t *testing.T) {
	dds, err := rpgle.ParseDDS("     A          R RESULT\n     A                                  1  2'HELLO'\n")
	if err != nil {
		t.Fatal(err)
	}
	for _, written := range []string{"RESULT", "result", "Result"} {
		prog, err := rpgle.ParseRPGLE("     FRESULT    CF   E             WORKSTN\n     C                   EXFMT     " + written + "\n")
		if err != nil {
			t.Fatal(err)
		}
		screen, _, err := rpgle.Run(prog, dds).Next(nil)
		if err != nil {
			t.Fatalf("EXFMT %s: %v", written, err)
		}
		if screen.FormatName != "RESULT" {
			t.Errorf("EXFMT %s: FormatName = %q, want the DDS's own spelling RESULT", written, screen.FormatName)
		}
		if screen.Fields[0].Text != "HELLO" {
			t.Errorf("EXFMT %s: first field = %q", written, screen.Fields[0].Text)
		}
	}
}

func TestExfmtOfAFormatThatDoesNotExistIsStillAnError(t *testing.T) {
	dds, _ := rpgle.ParseDDS("     A          R RESULT\n     A                                  1  2'HELLO'\n")
	prog, _ := rpgle.ParseRPGLE("     FRESULT    CF   E             WORKSTN\n     C                   EXFMT     nosuch\n")
	if _, _, err := rpgle.Run(prog, dds).Next(nil); err == nil || !strings.Contains(err.Error(), "unknown DDS record format: nosuch") {
		t.Errorf("want an unknown-format error, got %v", err)
	}
}

// ── numbers: what happens when the data isn't one ──────────────────────
// Letters typed into a number field used to quietly become 0. It is an error
// that names the field, a blank field reads as zero, and a front end can ask
// which inputs are numeric. (Parity with the Python port.)

const numDDS = "     A          R INQ                       CF03(03)\n" +
	"     A            CUSTNO         5  0B  3 20\n" +
	"     A            NAME          10A  B  4 20\n"
const numProg = "     FINQ       CF   E             WORKSTN\n" +
	"     C                   EXFMT     INQ\n     C                   EXFMT     INQ\n"

func typeInto(t *testing.T, values map[string]string) (*rpgle.ScreenData, error) {
	t.Helper()
	dds, err := rpgle.ParseDDS(numDDS)
	if err != nil {
		t.Fatal(err)
	}
	prog, err := rpgle.ParseRPGLE(numProg)
	if err != nil {
		t.Fatal(err)
	}
	r := rpgle.Run(prog, dds)
	if _, _, err := r.Next(nil); err != nil {
		t.Fatal(err)
	}
	s, _, err := r.Next(&rpgle.Resume{Key: "ENTER", Values: values})
	return s, err
}

func TestScreenDataSaysWhichInputsAreNumeric(t *testing.T) {
	dds, _ := rpgle.ParseDDS(numDDS)
	prog, _ := rpgle.ParseRPGLE(numProg)
	screen, _, err := rpgle.Run(prog, dds).Next(nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(screen.NumericFields) != 1 || screen.NumericFields[0] != "custno" {
		t.Errorf("NumericFields = %v, want [custno]", screen.NumericFields)
	}
}

func TestLettersInANumericFieldAreAnErrorThatNamesTheField(t *testing.T) {
	for _, typed := range []string{"abc", "12x", "1.2.3"} {
		_, err := typeInto(t, map[string]string{"custno": typed})
		if err == nil || !strings.Contains(err.Error(), "'"+typed+"' is not a number") || !strings.Contains(err.Error(), "custno") {
			t.Errorf("typed %q: want a not-a-number error naming custno, got %v", typed, err)
		}
	}
}

func TestABlankNumericFieldReadsAsZeroAndPaddedNumbersStillWork(t *testing.T) {
	for typed, want := range map[string]string{"   ": "00000", "": "00000", " 42 ": "00042", "-7": "-0007"} {
		s, err := typeInto(t, map[string]string{"custno": typed})
		if err != nil {
			t.Fatalf("typed %q: %v", typed, err)
		}
		var got string
		for _, f := range s.Fields {
			if f.Input && f.Row == 2 {
				got = f.Text
			}
		}
		if got != want {
			t.Errorf("typed %q: field shows %q, want %q", typed, got, want)
		}
	}
}

func TestAssigningTextToANumberIsAnErrorANumericStringIsFine(t *testing.T) {
	run := func(expr string) error {
		prog, err := rpgle.ParseRPGLE("     Dx                S              5  0\n     C                   EVAL      " + expr + "\n")
		if err != nil {
			t.Fatal(err)
		}
		dds, _ := rpgle.ParseDDS("")
		_, _, err = rpgle.Run(prog, dds).Next(nil)
		return err
	}
	if err := run("x = 'abc'"); err == nil || !strings.Contains(err.Error(), "'abc' is not a number") {
		t.Errorf("x = 'abc': got %v", err)
	}
	if err := run("x = ''"); err == nil || !strings.Contains(err.Error(), "not a number") {
		t.Errorf("x = '': got %v", err)
	}
	if err := run("x = '5'"); err != nil {
		t.Errorf("x = '5': %v", err)
	}
}

func TestTheDivOpcodeRefusesToDivideByZero(t *testing.T) {
	src := "     Da                S              5  0 INZ(7)\n" +
		"     Db                S              5  0 INZ(0)\n" +
		"     Dq                S              5  0\n" +
		"     C     a             DIV       b             q\n"
	prog, err := rpgle.ParseRPGLE(src)
	if err != nil {
		t.Fatal(err)
	}
	dds, _ := rpgle.ParseDDS("")
	if _, _, err := rpgle.Run(prog, dds).Next(nil); err == nil || !strings.Contains(strings.ToLower(err.Error()), "division by zero") {
		t.Errorf("want a division-by-zero error, got %v", err)
	}
}

// ── block structure: a keyword with nothing to close, or a block left open ──
// A DOW or IF with no end compiled and crashed later on a missing jump
// address; it is an error up front. (Parity with the Python port.)

func cspecLine(op, factor2 string) string {
	return fmt.Sprintf("     C                   %-10s%s", op, factor2)
}

func compileLines(t *testing.T, lines ...string) error {
	t.Helper()
	prog, err := rpgle.ParseRPGLE(strings.Join(lines, "\n"))
	if err != nil {
		t.Fatal(err)
	}
	_, err = rpgle.Compile(prog.Statements)
	return err
}

func TestABlockKeywordWithNothingOpenIsAnErrorNamingTheProblem(t *testing.T) {
	cases := []struct{ op, f2, want string }{
		{"ELSE", "", "ELSE with no open IF"},
		{"ENDIF", "", "ENDIF with no open IF"},
		{"ENDDO", "", "ENDDO with no open DOW"},
		{"LEAVE", "", "LEAVE with no open DOW"},
		{"WHEN", "1 = 1", "WHEN with no open SELECT"},
		{"OTHER", "", "OTHER with no open SELECT"},
		{"ENDSL", "", "ENDSL with no open SELECT"},
	}
	for _, c := range cases {
		if err := compileLines(t, cspecLine(c.op, c.f2)); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: want %q, got %v", c.op, c.want, err)
		}
	}
}

func TestABlockLeftOpenIsAnErrorAtCompileTimeNotACrashAtRunTime(t *testing.T) {
	cases := []struct {
		lines []string
		want  string
	}{
		{[]string{cspecLine("DOW", "1 = 1")}, "DOW loop with no ENDDO"},
		{[]string{cspecLine("IF", "1 = 1")}, "IF with no ENDIF"},
		{[]string{cspecLine("IF", "1 = 1"), cspecLine("ELSE", "")}, "IF with no ENDIF"},
		{[]string{cspecLine("SELECT", ""), cspecLine("WHEN", "1 = 1")}, "SELECT with no ENDSL"},
		{[]string{cspecLine("IF", "1 = 1"), cspecLine("DOW", "1 = 1"), cspecLine("ENDDO", "")}, "IF with no ENDIF"}, // the inner one closed
	}
	for _, c := range cases {
		if err := compileLines(t, c.lines...); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%v: want %q, got %v", c.lines, c.want, err)
		}
	}
}

func TestBalancedBlocksCompileAsBefore(t *testing.T) {
	err := compileLines(t,
		cspecLine("IF", "1 = 1"), cspecLine("ELSE", ""), cspecLine("ENDIF", ""),
		cspecLine("DOW", "1 = 2"), cspecLine("LEAVE", ""), cspecLine("ENDDO", ""),
		cspecLine("SELECT", ""), cspecLine("WHEN", "1 = 1"), cspecLine("OTHER", ""), cspecLine("ENDSL", ""),
	)
	if err != nil {
		t.Errorf("balanced blocks: %v", err)
	}
}

// The same accept/reject list is pinned in all three ports: only plain
// numbers are numbers. (Python's float() alone would take "1_000" and Arabic-
// Indic digits, so it was tightened to match.)
func TestOnlyPlainNumbersAreNumbersInANumericField(t *testing.T) {
	for _, typed := range []string{"1_000", "\u0663", "inf", "nan", "0x10", "1,5", "--5", "5-", "1 2"} {
		if _, err := typeInto(t, map[string]string{"custno": typed}); err == nil || !strings.Contains(err.Error(), "is not a number") {
			t.Errorf("typed %q: want a not-a-number error, got %v", typed, err)
		}
	}
	for _, typed := range []string{"+5", "-5", ".5", "5.", "1e2", " 42 ", "007"} {
		if _, err := typeInto(t, map[string]string{"custno": typed}); err != nil {
			t.Errorf("typed %q: %v", typed, err)
		}
	}
}
