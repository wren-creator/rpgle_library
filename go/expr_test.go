package rpgle_test

import (
	"math"
	"strings"
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

// + joins two texts; text mixed with a number, - on text, and negating
// text are errors rather than a silent NaN. (Parity with the Python port.)
func TestPlusJoinsTwoCharacterValuesPaddingIncluded(t *testing.T) {
	ctx := &rpgle.Context{
		Vars:       map[string]*rpgle.Var{"name": {Type: 'A', Length: 6, Dec: 0, Value: rpgle.StringValue("BOB   ")}},
		Indicators: map[string]bool{},
	}
	if got := mustEval(t, "'ab' + 'cd'", ctx); got.Kind != 'S' || got.Str != "abcd" {
		t.Errorf("'ab' + 'cd' = %+v, want text abcd", got)
	}
	if got := mustEval(t, "name + 'x'", ctx); got.Str != "BOB   x" { // padding is part of the value
		t.Errorf("name + 'x' = %q, want %q", got.Str, "BOB   x")
	}
	if got := mustEval(t, "1 + 2", ctx); got.Kind != 'N' || got.Num != 3 {
		t.Errorf("1 + 2 = %+v", got)
	}
	if got := mustEval(t, "7 - 2", ctx); got.Num != 5 {
		t.Errorf("7 - 2 = %+v", got)
	}
}

func TestTextAndNumbersDoNotMixInPlusAndMinus(t *testing.T) {
	for _, src := range []string{"'a' + 1", "1 + 'a'", "'a' - 1", "1 - 'a'", "'a' - 'b'"} {
		if _, err := rpgle.EvalExpr(src, emptyCtx()); err == nil || !strings.Contains(strings.ToLower(err.Error()), "text") {
			t.Errorf("EvalExpr(%q): want an error mentioning text, got %v", src, err)
		}
	}
}

func TestNegatingTextIsAnErrorNegatingANumberStillWorks(t *testing.T) {
	if _, err := rpgle.EvalExpr("-'a'", emptyCtx()); err == nil || !strings.Contains(err.Error(), "negate text") {
		t.Errorf("-'a': want a 'negate text' error, got %v", err)
	}
	if got := mustEval(t, "-(2 + 3)", emptyCtx()); got.Num != -5 {
		t.Errorf("-(2 + 3) = %+v", got)
	}
	if got := mustEval(t, "- -4", emptyCtx()); got.Num != 4 {
		t.Errorf("- -4 = %+v", got)
	}
}

// Dividing by zero is an error, not +Inf. (Parity with the Python port.)
func TestDivisionByZeroIsAnErrorOrdinaryDivisionStillWorks(t *testing.T) {
	for _, src := range []string{"1 / 0", "5 / (2 - 2)"} {
		if _, err := rpgle.EvalExpr(src, emptyCtx()); err == nil || !strings.Contains(err.Error(), "by zero") {
			t.Errorf("EvalExpr(%q): want a division-by-zero error, got %v", src, err)
		}
	}
	if got := mustEval(t, "7 / 2", emptyCtx()); got.Num != 3.5 {
		t.Errorf("7 / 2 = %v", got.Num)
	}
	if got := mustEval(t, "0 / 5", emptyCtx()); got.Num != 0 {
		t.Errorf("0 / 5 = %v", got.Num)
	}
}

// ── built-in functions (parity with the Python port) ────────────────────
// Every case here is also in python/tests/test_expr.py and tools/parity.

func evalVars(t *testing.T, src string, vars map[string]*rpgle.Var) rpgle.Value {
	t.Helper()
	return mustEval(t, src, &rpgle.Context{Vars: vars, Indicators: map[string]bool{}})
}

type bifCase struct {
	src  string
	want any // float64, string or bool
}

func checkCases(t *testing.T, cases []bifCase, vars map[string]*rpgle.Var) {
	t.Helper()
	if vars == nil {
		vars = map[string]*rpgle.Var{}
	}
	for _, c := range cases {
		v := evalVars(t, c.src, vars)
		switch w := c.want.(type) {
		case float64:
			if v.Kind != 'N' || v.Num != w {
				t.Errorf("%s = %+v, want %v", c.src, v, w)
			}
		case string:
			if v.Kind != 'S' || v.Str != w {
				t.Errorf("%s = %+v, want %q", c.src, v, w)
			}
		case bool:
			if v.Kind != 'B' || v.Bool != w {
				t.Errorf("%s = %+v, want %v", c.src, v, w)
			}
		}
	}
}

func checkFails(t *testing.T, sources []string, fragment string) {
	t.Helper()
	for _, src := range sources {
		_, err := rpgle.EvalExpr(src, emptyCtx())
		if err == nil {
			t.Errorf("%s: want an error, got none", src)
		} else if fragment != "" && !strings.Contains(err.Error(), fragment) {
			t.Errorf("%s: error %q should mention %q", src, err, fragment)
		}
	}
}

func TestNumericBuiltIns(t *testing.T) {
	checkCases(t, []bifCase{
		{"%abs(-5)", 5.0}, {"%abs(3)", 3.0}, {"%int(7.9)", 7.0}, {"%int(-7.9)", -7.0},
		{"%inth(7.5)", 8.0}, {"%inth(-7.5)", -8.0}, {"%dec('12.345' : 5 : 2)", 12.34}, {"%dech('12.345' : 5 : 2)", 12.35},
		{"%rem(17 : 5)", 2.0}, {"%div(17 : 5)", 3.0}, {"%sqrt(16)", 4.0}, {"%min(4 : 2 : 9)", 2.0}, {"%max(4 : 2 : 9)", 9.0},
		{"%min('b' : 'a')", "a"}, {"%rem(-17 : 5)", -2.0}, {"%div(-17 : 5)", -3.0},
	}, nil)
}

func TestTextBuiltIns(t *testing.T) {
	checkCases(t, []bifCase{
		{"%upper('abC')", "ABC"}, {"%lower('abC')", "abc"}, {"%triml('  a  ')", "a  "}, {"%trimr('  a  ')", "  a"},
		{"%trim('  a  ')", "a"}, {"%scan('lo' : 'hello world')", 4.0}, {"%scan('o' : 'hello world' : 6)", 8.0},
		{"%scan('zz' : 'hello')", 0.0}, {"%replace('XY' : 'hello' : 2 : 3)", "hXYo"}, {"%replace('XY' : 'hello' : 3 : 0)", "heXYllo"},
		{"%replace('XY' : 'hello')", "XYllo"}, {"%subst('hello' : 2 : 3)", "ell"}, {"%len('abc')", 3.0},
	}, nil)
}

func TestCharUsesABareVariablesDeclaredDecimalsAnExpressionDoesNot(t *testing.T) {
	vars := map[string]*rpgle.Var{
		"money": {Type: 'N', Length: 9, Dec: 2, Value: rpgle.NumberValue(5)},
		"whole": {Type: 'N', Length: 5, Dec: 0, Value: rpgle.NumberValue(5)},
	}
	checkCases(t, []bifCase{
		{"%char(money)", "5.00"}, {"%char(whole)", "5"}, {"%char(money + 1)", "6"}, {"%char('x')", "x"},
		{"%char(*on)", "1"}, {"%char(*off)", "0"},
	}, vars)
}

func TestBuiltInsAgreeOnCharactersNotBytesOrUTF16Units(t *testing.T) {
	checkCases(t, []bifCase{
		{"%len('café')", 4.0}, {"%len('\U0001f600x')", 2.0}, {"%subst('cafés' : 4 : 2)", "és"},
		{"%scan('é' : 'café')", 4.0}, {"%replace('\U0001f600' : 'abc' : 2 : 1)", "a\U0001f600c"},
	}, nil)
}

func TestSubstClampsInsteadOfFailing(t *testing.T) {
	checkCases(t, []bifCase{
		{"%subst('hello' : 0 : 2)", "he"}, {"%subst('hello' : -3 : 2)", "he"}, {"%subst('hello' : 9)", ""},
		{"%subst('hello' : 3 : 99)", "llo"}, {"%subst('hello' : 3 : -1)", ""}, {"%subst('hello' : 2)", "ello"},
	}, nil)
}

func TestNumbersBecomeTextInPlainDecimalNeverExponentForm(t *testing.T) {
	checkCases(t, []bifCase{
		{"%char(0.0000001)", "0.0000001"}, {"%char(0.00001234)", "0.00001234"},
		{"%char(10000000000000000000000)", "10000000000000000000000"}, {"%char(123456789012345678)", "123456789012345680"},
		{"%char(0.1 + 0.2)", "0.30000000000000004"}, {"%char(-2.5)", "-2.5"}, {"%char(7)", "7"},
	}, nil)
}

func TestAPositionOrLengthThatIsNotAWholeNumberIsAnError(t *testing.T) {
	checkFails(t, []string{
		"%dec('5' : 5 : 'x')", "%scan('a' : 'abc' : 'x')", "%replace('a' : 'bcd' : 'x')", "%replace('a' : 'bcd' : 1 : 'x')",
		"%subst('abc' : 'x')", "%subst('abc' : 1 : 'x')",
	}, "not a whole number")
}

func TestDecimalPositionsAreLimitedToWhatRPGAllows(t *testing.T) {
	checkFails(t, []string{"%dec(1 : 5 : 64)", "%dec(1 : 5 : -1)", "%dech(1 : 5 : 400)"}, "0 to 63")
	checkCases(t, []bifCase{{"%dec('12.345' : 5 : 0)", 12.0}, {"%dec(1 : 63 : 63)", 1.0}}, nil)
}

func TestWrongArgumentCountsAndUnknownFunctionsAreErrors(t *testing.T) {
	checkFails(t, []string{"%len('a' : 'b')", "%subst('a')", "%scan('a')", "%replace('a')", "%min(1)"}, "argument")
	checkFails(t, []string{"%trim()", "%abs()"}, "") // no argument at all is a parse error
	checkFails(t, []string{"%nosuch(1)"}, "unsupported RPG built-in")
}

func TestBuiltInErrorsNotANumberDivideByZeroNegativeSquareRoot(t *testing.T) {
	checkFails(t, []string{"%dec('abc' : 5 : 2)", "%int('12x')", "%abs('')"}, "is not a number")
	checkFails(t, []string{"%rem(1 : 0)", "%div(1 : 0)"}, "division by zero")
	checkFails(t, []string{"%sqrt(-1)"}, "negative")
}

func TestNotBindsLooserThanComparisonAndTighterThanAnd(t *testing.T) {
	checkCases(t, []bifCase{
		{"not 1 = 2", true}, {"not 1 = 1 or 2 = 2", true}, {"not (1 = 1 and 2 = 2)", false}, {"not not 1 = 1", true},
		{"not 1 = 1 and 2 = 2", false},
	}, nil)
}

func TestBlanksAndZerosAreFigurativeConstants(t *testing.T) {
	checkCases(t, []bifCase{
		{"*blanks", ""}, {"*blank", ""}, {"*zeros", 0.0}, {"*zero", 0.0}, {"*blanks = *blanks", true},
	}, nil)
}

func TestOnlyPlainNumbersAreNumbersInArithmetic(t *testing.T) {
	// '0x10' * 1 is 16 in JS and a NaN in Python; strict everywhere now
	for _, src := range []string{"'0x10' * 1", "'1_000' * 1", "'inf' * 1"} {
		if v := mustEval(t, src, emptyCtx()); !math.IsNaN(v.Num) {
			t.Errorf("%s = %+v, want NaN (not a number)", src, v)
		}
	}
	checkCases(t, []bifCase{{"'5' * 2", 10.0}, {"' 7 ' * 1", 7.0}, {"'1e2' * 1", 100.0}}, nil)
}
