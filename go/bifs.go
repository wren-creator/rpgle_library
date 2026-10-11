package rpgle

// Built-in functions (%TRIM, %CHAR, %SCAN ...). They mirror the Python port's
// table exactly, edge cases included; tools/parity checks that the two agree.
//
// Each function takes the name it was called by (for error messages), its
// evaluated arguments, and firstVar: the variable, if the first argument was a
// bare variable name, else nil. %CHAR needs it for the variable's declared
// decimals, which a value alone doesn't carry.

import (
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode"
)

type bifFunc func(name string, args []Value, firstVar *Var) (Value, error)

var bifs map[string]bifFunc

func init() {
	strFn := func(f func(string) string) bifFunc {
		return func(name string, args []Value, _ *Var) (Value, error) {
			if err := arity(name, args, 1, 1); err != nil {
				return Value{}, err
			}
			s, err := args[0].text()
			if err != nil {
				return Value{}, err
			}
			return StringValue(f(s)), nil
		}
	}
	bifs = map[string]bifFunc{
		"%TRIM":    strFn(strings.TrimSpace),
		"%TRIML":   strFn(func(s string) string { return strings.TrimLeftFunc(s, unicode.IsSpace) }),
		"%TRIMR":   strFn(func(s string) string { return strings.TrimRightFunc(s, unicode.IsSpace) }),
		"%UPPER":   strFn(strings.ToUpper),
		"%LOWER":   strFn(strings.ToLower),
		"%LEN":     bifLen,
		"%SUBST":   bifSubst,
		"%CHAR":    bifChar,
		"%DEC":     func(n string, a []Value, _ *Var) (Value, error) { return bifDec(n, a, truncTo) },
		"%DECH":    func(n string, a []Value, _ *Var) (Value, error) { return bifDec(n, a, halfAdjust) },
		"%INT":     bifInt(false),
		"%INTH":    bifInt(true),
		"%ABS":     bifAbs,
		"%REM":     bifRem,
		"%DIV":     bifDiv,
		"%SQRT":    bifSqrt,
		"%MIN":     bifMinMax,
		"%MAX":     bifMinMax,
		"%SCAN":    bifScan,
		"%REPLACE": bifReplace,
	}
}

func arity(name string, args []Value, lo, hi int) error {
	if len(args) < lo || len(args) > hi {
		want := strconv.Itoa(lo)
		if lo != hi {
			want = fmt.Sprintf("%d to %d", lo, hi)
		}
		return fmt.Errorf("%s takes %s argument(s), got %d", name, want, len(args))
	}
	return nil
}

// num is a number argument: text must look like one.
func num(name string, v Value) (float64, error) {
	n := v.AsFloat()
	if v.Kind == 'S' && math.IsNaN(n) {
		return 0, fmt.Errorf("%s: '%s' is not a number", name, v.Str)
	}
	return n, nil
}

// whole is a position, length or count: it must be a number, and any fraction
// is dropped.
func whole(name string, v Value) (int, error) {
	n := v.AsFloat()
	if math.IsNaN(n) || math.IsInf(n, 0) {
		what := "that value"
		if v.Kind == 'S' {
			what = "'" + v.Str + "'"
		}
		return 0, fmt.Errorf("%s: %s is not a whole number", name, what)
	}
	return int(math.Trunc(n)), nil
}

func truncTo(n float64, dec int) float64 {
	f := pow10(dec)
	return math.Trunc(n*f) / f
}

func halfAdjust(n float64, dec int) float64 {
	f := pow10(dec)
	r := math.Floor(math.Abs(n)*f + 0.5)
	if n < 0 {
		r = -r
	}
	return r / f
}

func bifLen(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 1, 1); err != nil {
		return Value{}, err
	}
	s, err := args[0].text()
	if err != nil {
		return Value{}, err
	}
	return NumberValue(float64(len([]rune(s)))), nil // characters, not bytes
}

func bifSubst(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 2, 3); err != nil {
		return Value{}, err
	}
	text, err := args[0].text()
	if err != nil {
		return Value{}, err
	}
	s := []rune(text)
	start, err := whole(name, args[1])
	if err != nil {
		return Value{}, err
	}
	start-- // RPG %SUBST start is 1-based
	length := len(s) - start
	if len(args) > 2 {
		if length, err = whole(name, args[2]); err != nil {
			return Value{}, err
		}
	}
	start = max(0, min(start, len(s)))
	end := max(start, min(start+length, len(s)))
	return StringValue(string(s[start:end])), nil
}

func bifChar(name string, args []Value, firstVar *Var) (Value, error) {
	if err := arity(name, args, 1, 1); err != nil {
		return Value{}, err
	}
	v := args[0]
	switch v.Kind {
	case 'S':
		return v, nil
	case 'B':
		if v.Bool {
			return StringValue("1"), nil
		}
		return StringValue("0"), nil
	}
	if firstVar != nil && firstVar.Type == 'N' && firstVar.Dec > 0 {
		return StringValue(strconv.FormatFloat(v.Num, 'f', firstVar.Dec, 64)), nil
	}
	s, err := v.text()
	return StringValue(s), err
}

func bifDec(name string, args []Value, round func(float64, int) float64) (Value, error) {
	if err := arity(name, args, 3, 3); err != nil {
		return Value{}, err
	}
	n, err := num(name, args[0])
	if err != nil {
		return Value{}, err
	}
	dec, err := whole(name, args[2])
	if err != nil {
		return Value{}, err
	}
	if dec < 0 || dec > 63 {
		return Value{}, fmt.Errorf("%s: decimal positions must be 0 to 63, got %d", name, dec)
	}
	return NumberValue(round(n, dec)), nil
}

func bifInt(half bool) bifFunc {
	return func(name string, args []Value, _ *Var) (Value, error) {
		if err := arity(name, args, 1, 1); err != nil {
			return Value{}, err
		}
		n, err := num(name, args[0])
		if err != nil {
			return Value{}, err
		}
		if half {
			return NumberValue(halfAdjust(n, 0)), nil
		}
		return NumberValue(math.Trunc(n)), nil
	}
}

func bifAbs(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 1, 1); err != nil {
		return Value{}, err
	}
	n, err := num(name, args[0])
	return NumberValue(math.Abs(n)), err
}

func twoNumbers(name string, args []Value) (a, b float64, err error) {
	if err = arity(name, args, 2, 2); err != nil {
		return
	}
	if a, err = num(name, args[0]); err != nil {
		return
	}
	if b, err = num(name, args[1]); err != nil {
		return
	}
	if b == 0 {
		err = fmt.Errorf("%s: division by zero", name)
	}
	return
}

func bifRem(name string, args []Value, _ *Var) (Value, error) {
	a, b, err := twoNumbers(name, args)
	if err != nil {
		return Value{}, err
	}
	return NumberValue(a - math.Trunc(a/b)*b), nil
}

func bifDiv(name string, args []Value, _ *Var) (Value, error) {
	a, b, err := twoNumbers(name, args)
	if err != nil {
		return Value{}, err
	}
	return NumberValue(math.Trunc(a / b)), nil
}

func bifSqrt(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 1, 1); err != nil {
		return Value{}, err
	}
	n, err := num(name, args[0])
	if err != nil {
		return Value{}, err
	}
	if n < 0 {
		return Value{}, fmt.Errorf("%%SQRT of a negative number")
	}
	return NumberValue(math.Sqrt(n)), nil
}

func bifMinMax(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 2, 99); err != nil {
		return Value{}, err
	}
	isMin := name == "%MIN"
	allText := true
	for _, a := range args {
		if a.Kind != 'S' {
			allText = false
		}
	}
	if allText {
		// compared without trailing blanks, but the original value comes back
		best := args[0]
		for _, a := range args[1:] {
			x, y := trimBlanks(a.Str), trimBlanks(best.Str)
			if (isMin && x < y) || (!isMin && x > y) {
				best = a
			}
		}
		return best, nil
	}
	best, err := num(name, args[0])
	if err != nil {
		return Value{}, err
	}
	for _, a := range args[1:] {
		n, err := num(name, a)
		if err != nil {
			return Value{}, err
		}
		if (isMin && n < best) || (!isMin && n > best) {
			best = n
		}
	}
	return NumberValue(best), nil
}

func bifScan(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 2, 3); err != nil {
		return Value{}, err
	}
	nt, err := args[0].text()
	if err != nil {
		return Value{}, err
	}
	ht, err := args[1].text()
	if err != nil {
		return Value{}, err
	}
	needle, hay := []rune(nt), []rune(ht)
	start := 1
	if len(args) == 3 {
		if start, err = whole(name, args[2]); err != nil {
			return Value{}, err
		}
	}
	if len(needle) == 0 || start < 1 {
		return NumberValue(0), nil
	}
	for i := start - 1; i+len(needle) <= len(hay); i++ {
		if string(hay[i:i+len(needle)]) == string(needle) {
			return NumberValue(float64(i + 1)), nil
		}
	}
	return NumberValue(0), nil
}

func bifReplace(name string, args []Value, _ *Var) (Value, error) {
	if err := arity(name, args, 2, 4); err != nil {
		return Value{}, err
	}
	rt, err := args[0].text()
	if err != nil {
		return Value{}, err
	}
	st, err := args[1].text()
	if err != nil {
		return Value{}, err
	}
	repl, src := []rune(rt), []rune(st)
	start := 1
	if len(args) >= 3 {
		if start, err = whole(name, args[2]); err != nil {
			return Value{}, err
		}
	}
	length := len(repl)
	if len(args) == 4 {
		if length, err = whole(name, args[3]); err != nil {
			return Value{}, err
		}
	}
	if start < 1 {
		return Value{}, fmt.Errorf("%%REPLACE: start must be 1 or more")
	}
	// Python slicing semantics: a negative index counts from the end, and
	// everything clamps instead of failing.
	tailFrom := start - 1 + length
	if tailFrom < 0 {
		tailFrom = max(0, len(src)+tailFrom)
	}
	head := src[:min(start-1, len(src))]
	var tail []rune
	if tailFrom < len(src) {
		tail = src[tailFrom:]
	}
	return StringValue(string(head) + string(repl) + string(tail)), nil
}
