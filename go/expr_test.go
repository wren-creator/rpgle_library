package rpgle_test

import (
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

func mustEval(t *testing.T, src string, ctx *rpgle.Context) rpgle.Value {
	t.Helper()
	v, err := rpgle.EvalExpr(src, ctx)
	if err != nil {
		t.Fatalf("EvalExpr(%q): %v", src, err)
	}
	return v
}

func emptyCtx() *rpgle.Context {
	return &rpgle.Context{Vars: map[string]*rpgle.Var{}, Indicators: map[string]bool{}}
}

func TestAndOrAlwaysConsumeBothOperands(t *testing.T) {
	// Regression test: parseAnd/parseOr must not short-circuit token
	// consumption, or the right side is left unparsed and the parser
	// throws a spurious trailing-tokens error.
	ctx := emptyCtx()
	cases := []struct {
		src  string
		want bool
	}{
		{"1 > 2 and 3 > 4", false},
		{"1 < 2 and 3 < 4", true},
		{"1 > 2 or 3 < 4", true},
		{"1 > 2 or 3 > 4", false},
	}
	for _, c := range cases {
		if got := mustEval(t, c.src, ctx).AsBool(); got != c.want {
			t.Errorf("evalExpr(%q) = %v, want %v", c.src, got, c.want)
		}
	}
}

func TestComparisonOperators(t *testing.T) {
	ctx := emptyCtx()
	checks := map[string]bool{
		"1 = 1":        true,
		"1 <> 2":       true,
		"1 <= 1":       true,
		"2 >= 3":       false,
		"1 < 2":        true,
		"2 > 1":        true,
		"'AB' = 'AB'":  true,
		"'AB' <> 'CD'": true,
	}
	for src, want := range checks {
		if got := mustEval(t, src, ctx).AsBool(); got != want {
			t.Errorf("evalExpr(%q) = %v, want %v", src, got, want)
		}
	}
}

func TestArithmeticPrecedence(t *testing.T) {
	ctx := emptyCtx()
	checks := map[string]float64{
		"2 + 3 * 4":   14,
		"(2 + 3) * 4": 20,
		"10 - 2 - 3":  5,
		"-5 + 2":      -3,
	}
	for src, want := range checks {
		if got := mustEval(t, src, ctx).AsFloat(); got != want {
			t.Errorf("evalExpr(%q) = %v, want %v", src, got, want)
		}
	}
}

func TestBuiltinFunctions(t *testing.T) {
	ctx := emptyCtx()
	if got := mustEval(t, "%TRIM('  hi  ')", ctx).AsString(); got != "hi" {
		t.Errorf("%%TRIM = %q, want %q", got, "hi")
	}
	if got := mustEval(t, "%LEN('hello')", ctx).AsFloat(); got != 5 {
		t.Errorf("%%LEN = %v, want 5", got)
	}
	if got := mustEval(t, "%SUBST('hello' : 2)", ctx).AsString(); got != "ello" {
		t.Errorf("%%SUBST/2 = %q, want %q", got, "ello")
	}
	if got := mustEval(t, "%SUBST('hello' : 2 : 3)", ctx).AsString(); got != "ell" {
		t.Errorf("%%SUBST/3 = %q, want %q", got, "ell")
	}
}

func TestFigurativeConstants(t *testing.T) {
	ctx := &rpgle.Context{
		Vars:       map[string]*rpgle.Var{},
		Indicators: map[string]bool{"90": true, "80": false, "LR": true, "RT": false},
	}
	if got := mustEval(t, "*ON", ctx).AsBool(); got != true {
		t.Errorf("*ON = %v, want true", got)
	}
	if got := mustEval(t, "*OFF", ctx).AsBool(); got != false {
		t.Errorf("*OFF = %v, want false", got)
	}
	if got := mustEval(t, "*IN90", ctx).AsBool(); got != true {
		t.Errorf("*IN90 = %v, want true", got)
	}
	if got := mustEval(t, "*IN80", ctx).AsBool(); got != false {
		t.Errorf("*IN80 = %v, want false", got)
	}
	if got := mustEval(t, "*INLR", ctx).AsBool(); got != true {
		t.Errorf("*INLR = %v, want true", got)
	}
	if got := mustEval(t, "*INRT", ctx).AsBool(); got != false {
		t.Errorf("*INRT = %v, want false", got)
	}
}

func TestVariableLookup(t *testing.T) {
	ctx := &rpgle.Context{
		Vars: map[string]*rpgle.Var{
			"plrhp": {Type: 'N', Length: 3, Dec: 0, Value: rpgle.NumberValue(20)},
		},
		Indicators: map[string]bool{},
	}
	if got := mustEval(t, "plrhp", ctx).AsFloat(); got != 20 {
		t.Errorf("plrhp = %v, want 20", got)
	}
	if got := mustEval(t, "plrhp > 10", ctx).AsBool(); got != true {
		t.Errorf("plrhp > 10 = %v, want true", got)
	}
}

func TestSplitAssignment(t *testing.T) {
	lvalue, rvalue, err := rpgle.SplitAssignment("plrhp = 20")
	if err != nil {
		t.Fatalf("SplitAssignment: %v", err)
	}
	if lvalue != "plrhp" || rvalue != "20" {
		t.Errorf("SplitAssignment = (%q, %q), want (%q, %q)", lvalue, rvalue, "plrhp", "20")
	}

	lvalue, rvalue, err = rpgle.SplitAssignment("loctxt = %TRIM(loctxt)")
	if err != nil {
		t.Fatalf("SplitAssignment: %v", err)
	}
	if lvalue != "loctxt" || rvalue != "%TRIM(loctxt)" {
		t.Errorf("SplitAssignment = (%q, %q), want (%q, %q)", lvalue, rvalue, "loctxt", "%TRIM(loctxt)")
	}
}

// Character values compare as if blank-padded, as in RPG: a 20-character
// field holding BOB is equal to 'BOB'. (Parity with the Python port.)
func TestCharacterComparisonIgnoresTrailingBlanks(t *testing.T) {
	ctx := &rpgle.Context{
		Vars: map[string]*rpgle.Var{
			"name":  {Type: 'A', Length: 20, Dec: 0, Value: rpgle.StringValue("BOB                 ")},
			"other": {Type: 'A', Length: 5, Dec: 0, Value: rpgle.StringValue("BOB  ")},
		},
		Indicators: map[string]bool{},
	}
	cases := []struct {
		src  string
		want bool
	}{
		{"name = 'BOB'", true},
		{"'BOB' = name", true},
		{"name <> 'BOB'", false},
		{"name = other", true},
		{"name = 'BOBB'", false},
		{"name < 'BOBB'", true}, // 'BOB' < 'BOBB', padding ignored
		{"name > 'BOBB'", false},
		{"name <= 'BOB'", true},
		{"name >= 'BOB'", true},
		{"'a' = 'A'", false}, // still case-sensitive
	}
	for _, c := range cases {
		if got := mustEval(t, c.src, ctx).AsBool(); got != c.want {
			t.Errorf("evalExpr(%q) = %v, want %v", c.src, got, c.want)
		}
	}
}

func TestComparingTextWithANumberIsStillNeverEqual(t *testing.T) {
	ctx := emptyCtx()
	if mustEval(t, "'1' = 1", ctx).AsBool() {
		t.Error("'1' = 1 should be false")
	}
	if !mustEval(t, "1 = 1", ctx).AsBool() {
		t.Error("1 = 1 should be true")
	}
}
