package rpgle

import (
	"fmt"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

// expr.go
// ─────────────────────────────────────────────────────────────────
// A small recursive-descent evaluator for the RPG IV extended-factor-2
// expressions this interpreter supports: EVAL right-hand sides and
// IF/DOW/WHEN boolean conditions. See docs/LANGUAGE.md for the
// grammar and the worked reasoning behind each design choice noted
// below.
//
// Truncation-on-assignment (the classic RPG idiom used for modulo,
// since RPG has no %REM until well past V4R3) is applied by the
// caller (interpreter.go's assign()), not here -- this module only
// evaluates the right-hand side to a Value.

// Value is a small tagged union: RPG expressions can yield a number, a
// string, or a boolean (from a comparison/logical result or an
// indicator). Using a tagged struct instead of `any` keeps the hot
// comparison/arithmetic paths allocation-free and switch-based.
type Value struct {
	Kind byte // 'N' number, 'S' string, 'B' bool
	Num  float64
	Str  string
	Bool bool
}

func NumberValue(n float64) Value { return Value{Kind: 'N', Num: n} }
func StringValue(s string) Value  { return Value{Kind: 'S', Str: s} }
func BoolValue(b bool) Value      { return Value{Kind: 'B', Bool: b} }

// AsBool mirrors JS's Boolean(x) coercion used throughout the source
// interpreter (Boolean(0)/Boolean(”) are false, everything else true).
func (v Value) AsBool() bool {
	switch v.Kind {
	case 'B':
		return v.Bool
	case 'N':
		return v.Num != 0
	case 'S':
		return v.Str != ""
	}
	return false
}

// AsFloat coerces a Value to a number for arithmetic. Real RPG
// arithmetic operators only ever see numeric operands in the shipped
// content; this is a best-effort coercion for anything else, not a
// documented feature.
func (v Value) AsFloat() float64 {
	switch v.Kind {
	case 'N':
		return v.Num
	case 'B':
		if v.Bool {
			return 1
		}
		return 0
	case 'S':
		if f, ok := parseNumberText(v.Str); ok {
			return f
		}
		return math.NaN()
	}
	return 0
}

// numberText is what a number looks like as text: an optional sign, digits, an
// optional decimal point and an optional exponent. Anything else ("12x", "",
// "1,5", "0x10", "inf") is not a number, rather than being read as a prefix, 0
// or NaN. The Python and JS ports use the same rule.
var numberText = regexp.MustCompile(`^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$`)

// parseNumberText reads text as a number, reporting whether it was one.
func parseNumberText(s string) (float64, bool) {
	t := strings.TrimSpace(s)
	if !numberText.MatchString(t) {
		return 0, false
	}
	f, err := strconv.ParseFloat(t, 64)
	return f, err == nil && !math.IsInf(f, 0)
}

// pow10 is 10 to the power n, correctly rounded (math.Pow10 multiplies tables
// and is not, for larger n), matching Python's float(10 ** n).
func pow10(n int) float64 {
	f, _ := strconv.ParseFloat("1e"+strconv.Itoa(n), 64)
	return f
}

// text is the value as text, as the Python port's as_string does: a number
// that isn't one (NaN, infinity) is an error rather than the word "NaN".
func (v Value) text() (string, error) {
	if v.Kind == 'N' && (math.IsNaN(v.Num) || math.IsInf(v.Num, 0)) {
		return "", fmt.Errorf("that value is not a number, so it can't be turned into text")
	}
	return v.AsString(), nil
}

// AsString mirrors JS's String(x) coercion, used by %TRIM/%LEN/%SUBST.
func (v Value) AsString() string {
	switch v.Kind {
	case 'S':
		return v.Str
	case 'N':
		return formatNumberForString(v.Num)
	case 'B':
		if v.Bool {
			return "true"
		}
		return "false"
	}
	return ""
}

// formatNumberForString writes a number as text. A whole number prints every
// digit exactly; anything else prints the shortest digits that round-trip, and
// never in exponent form.
func formatNumberForString(n float64) string {
	if n == math.Trunc(n) && !math.IsInf(n, 0) {
		if math.Abs(n) < 1e15 {
			return strconv.FormatInt(int64(n), 10)
		}
		i, _ := new(big.Float).SetFloat64(n).Int(nil) // exact, beyond int64 too
		return i.String()
	}
	return strconv.FormatFloat(n, 'f', -1, 64)
}

// trimBlanks drops trailing whitespace. RPG compares character data as if
// both sides were blank-padded, so a 20-character field holding BOB equals
// 'BOB': trailing blanks never matter.
func trimBlanks(s string) string {
	return strings.TrimRightFunc(s, unicode.IsSpace)
}

func valuesEqual(a, b Value) bool {
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case 'N':
		return a.Num == b.Num
	case 'S':
		return trimBlanks(a.Str) == trimBlanks(b.Str)
	case 'B':
		return a.Bool == b.Bool
	}
	return false
}

func valueLess(a, b Value) bool {
	if a.Kind == 'S' && b.Kind == 'S' {
		return trimBlanks(a.Str) < trimBlanks(b.Str)
	}
	return a.AsFloat() < b.AsFloat()
}

// Context is the variable/indicator state an expression is evaluated
// against.
type Context struct {
	Vars       map[string]*Var
	Indicators map[string]bool
}

type tokenType int

const (
	tokNumber tokenType = iota
	tokString
	tokIndicatorOrConst
	tokFunc
	tokName
	tokLogic
	tokOp
)

type token struct {
	typ tokenType
	str string
	num float64
}

func isSpaceRune(c rune) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'
}
func isDigitRune(c rune) bool { return c >= '0' && c <= '9' }
func isAlphaRune(c rune) bool { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') }
func isAlnumRune(c rune) bool { return isAlphaRune(c) || isDigitRune(c) }

func tokenize(src string) ([]token, error) {
	var tokens []token
	r := []rune(src)
	n := len(r)
	i := 0
	for i < n {
		c := r[i]
		if isSpaceRune(c) {
			i++
			continue
		}
		if c == '\'' {
			j := i + 1
			var sb strings.Builder
			for j < n && r[j] != '\'' {
				sb.WriteRune(r[j])
				j++
			}
			tokens = append(tokens, token{typ: tokString, str: sb.String()})
			i = j + 1
			continue
		}
		if isDigitRune(c) {
			j := i
			var sb strings.Builder
			for j < n && (isDigitRune(r[j]) || r[j] == '.') {
				sb.WriteRune(r[j])
				j++
			}
			f, err := strconv.ParseFloat(sb.String(), 64)
			if err != nil {
				return nil, fmt.Errorf("invalid number in RPG expression: %q", sb.String())
			}
			tokens = append(tokens, token{typ: tokNumber, num: f})
			i = j
			continue
		}
		if c == '*' && i+1 < n && isAlphaRune(r[i+1]) {
			j := i + 1
			var sb strings.Builder
			sb.WriteRune('*')
			for j < n && isAlnumRune(r[j]) {
				sb.WriteRune(r[j])
				j++
			}
			tokens = append(tokens, token{typ: tokIndicatorOrConst, str: strings.ToUpper(sb.String())})
			i = j
			continue
		}
		if c == '%' {
			j := i + 1
			var sb strings.Builder
			sb.WriteRune('%')
			for j < n && isAlnumRune(r[j]) {
				sb.WriteRune(r[j])
				j++
			}
			tokens = append(tokens, token{typ: tokFunc, str: strings.ToUpper(sb.String())})
			i = j
			continue
		}
		if isAlphaRune(c) || c == '_' {
			j := i
			var sb strings.Builder
			for j < n && (isAlnumRune(r[j]) || r[j] == '_') {
				sb.WriteRune(r[j])
				j++
			}
			s := sb.String()
			upper := strings.ToUpper(s)
			if upper == "AND" || upper == "OR" || upper == "NOT" {
				tokens = append(tokens, token{typ: tokLogic, str: upper})
			} else {
				tokens = append(tokens, token{typ: tokName, str: s})
			}
			i = j
			continue
		}
		if c == '<' && i+1 < n && r[i+1] == '>' {
			tokens = append(tokens, token{typ: tokOp, str: "<>"})
			i += 2
			continue
		}
		if c == '<' && i+1 < n && r[i+1] == '=' {
			tokens = append(tokens, token{typ: tokOp, str: "<="})
			i += 2
			continue
		}
		if c == '>' && i+1 < n && r[i+1] == '=' {
			tokens = append(tokens, token{typ: tokOp, str: ">="})
			i += 2
			continue
		}
		if strings.ContainsRune("=<>+-*/():", c) {
			tokens = append(tokens, token{typ: tokOp, str: string(c)})
			i++
			continue
		}
		return nil, fmt.Errorf("unexpected character in RPG expression: %q in %q", string(c), src)
	}
	return tokens, nil
}

type exprParser struct {
	tokens []token
	pos    int
	ctx    *Context
}

func (p *exprParser) peek() *token {
	if p.pos < len(p.tokens) {
		return &p.tokens[p.pos]
	}
	return nil
}
func (p *exprParser) next() *token {
	t := p.peek()
	p.pos++
	return t
}
func (p *exprParser) expectOp(v string) error {
	t := p.next()
	if t == nil || t.typ != tokOp || t.str != v {
		return fmt.Errorf("expected '%s' in RPG expression", v)
	}
	return nil
}

func (p *exprParser) parseExpr() (Value, error) { return p.parseOr() }

func (p *exprParser) parseOr() (Value, error) {
	v, err := p.parseAnd()
	if err != nil {
		return Value{}, err
	}
	for {
		t := p.peek()
		if t == nil || t.typ != tokLogic || t.str != "OR" {
			break
		}
		p.next()
		// Parse (consume tokens for) the right side unconditionally --
		// Go's || would short-circuit and skip parsing it, leaving
		// trailing tokens whenever the left side is already true.
		rhs, err := p.parseAnd()
		if err != nil {
			return Value{}, err
		}
		v = BoolValue(v.AsBool() || rhs.AsBool())
	}
	return v, nil
}

// parseNot: NOT binds looser than a comparison and tighter than AND.
func (p *exprParser) parseNot() (Value, error) {
	if t := p.peek(); t != nil && t.typ == tokLogic && t.str == "NOT" {
		p.next()
		v, err := p.parseNot()
		if err != nil {
			return Value{}, err
		}
		return BoolValue(!v.AsBool()), nil
	}
	return p.parseCompare()
}

func (p *exprParser) parseAnd() (Value, error) {
	v, err := p.parseNot()
	if err != nil {
		return Value{}, err
	}
	for {
		t := p.peek()
		if t == nil || t.typ != tokLogic || t.str != "AND" {
			break
		}
		p.next()
		// Same reasoning as parseOr: always parse the right side, even
		// if the left side is already false.
		rhs, err := p.parseNot()
		if err != nil {
			return Value{}, err
		}
		v = BoolValue(v.AsBool() && rhs.AsBool())
	}
	return v, nil
}

func (p *exprParser) parseCompare() (Value, error) {
	left, err := p.parseAdditive()
	if err != nil {
		return Value{}, err
	}
	t := p.peek()
	if t != nil && t.typ == tokOp {
		switch t.str {
		case "=", "<>", "<=", ">=", "<", ">":
			op := t.str
			p.next()
			right, err := p.parseAdditive()
			if err != nil {
				return Value{}, err
			}
			switch op {
			case "=":
				return BoolValue(valuesEqual(left, right)), nil
			case "<>":
				return BoolValue(!valuesEqual(left, right)), nil
			case "<":
				return BoolValue(valueLess(left, right)), nil
			case ">":
				return BoolValue(valueLess(right, left)), nil
			case "<=":
				return BoolValue(!valueLess(right, left)), nil
			case ">=":
				return BoolValue(!valueLess(left, right)), nil
			}
		}
	}
	return left, nil
}

func (p *exprParser) parseAdditive() (Value, error) {
	v, err := p.parseTerm()
	if err != nil {
		return Value{}, err
	}
	for {
		t := p.peek()
		if t == nil || t.typ != tokOp || (t.str != "+" && t.str != "-") {
			break
		}
		op := p.next().str
		rhs, err := p.parseTerm()
		if err != nil {
			return Value{}, err
		}
		if v.Kind == 'S' || rhs.Kind == 'S' {
			// + joins two character values (padding and all). Anything else
			// involving text is a mistake that would otherwise be a silent
			// NaN, so it's an error.
			if op == "+" && v.Kind == 'S' && rhs.Kind == 'S' {
				v = StringValue(v.Str + rhs.Str)
				continue
			}
			return Value{}, fmt.Errorf("can't use %s between text and a number, or - between two texts", op)
		}
		if op == "+" {
			v = NumberValue(v.AsFloat() + rhs.AsFloat())
		} else {
			v = NumberValue(v.AsFloat() - rhs.AsFloat())
		}
	}
	return v, nil
}

func (p *exprParser) parseTerm() (Value, error) {
	v, err := p.parseUnary()
	if err != nil {
		return Value{}, err
	}
	for {
		t := p.peek()
		if t == nil || t.typ != tokOp || (t.str != "*" && t.str != "/") {
			break
		}
		op := p.next().str
		rhs, err := p.parseUnary()
		if err != nil {
			return Value{}, err
		}
		if op == "/" && rhs.AsFloat() == 0 {
			return Value{}, fmt.Errorf("division by zero")
		}
		if op == "*" {
			v = NumberValue(v.AsFloat() * rhs.AsFloat())
		} else {
			v = NumberValue(v.AsFloat() / rhs.AsFloat())
		}
	}
	return v, nil
}

func (p *exprParser) parseUnary() (Value, error) {
	t := p.peek()
	if t != nil && t.typ == tokOp && t.str == "-" {
		p.next()
		v, err := p.parseUnary()
		if err != nil {
			return Value{}, err
		}
		if v.Kind == 'S' {
			return Value{}, fmt.Errorf("can't negate text")
		}
		return NumberValue(-v.AsFloat()), nil
	}
	return p.parsePrimary()
}

var (
	inNumRe = regexp.MustCompile(`^\*IN(\d\d)$`)
)

func (p *exprParser) parsePrimary() (Value, error) {
	t := p.next()
	if t == nil {
		return Value{}, fmt.Errorf("unexpected end of RPG expression")
	}
	switch t.typ {
	case tokNumber:
		return NumberValue(t.num), nil
	case tokString:
		return StringValue(t.str), nil
	case tokIndicatorOrConst:
		return p.resolveIndicatorOrConst(t.str)
	case tokFunc:
		return p.parseFuncCall(t.str)
	case tokName:
		return p.readVar(t.str)
	case tokOp:
		if t.str == "(" {
			v, err := p.parseExpr()
			if err != nil {
				return Value{}, err
			}
			if err := p.expectOp(")"); err != nil {
				return Value{}, err
			}
			return v, nil
		}
	}
	return Value{}, fmt.Errorf("unexpected token in RPG expression: %+v", t)
}

func (p *exprParser) resolveIndicatorOrConst(name string) (Value, error) {
	if name == "*ON" {
		return BoolValue(true), nil
	}
	if name == "*OFF" {
		return BoolValue(false), nil
	}
	if name == "*BLANK" || name == "*BLANKS" {
		return StringValue(""), nil
	}
	if name == "*ZERO" || name == "*ZEROS" {
		return NumberValue(0), nil
	}
	if name == "*INLR" || name == "*INRT" {
		return BoolValue(p.ctx.Indicators[name[3:]]), nil
	}
	if m := inNumRe.FindStringSubmatch(name); m != nil {
		return BoolValue(p.ctx.Indicators[m[1]]), nil
	}
	return Value{}, fmt.Errorf("unsupported RPG figurative constant: %s", name)
}

func (p *exprParser) parseFuncCall(name string) (Value, error) {
	fn, ok := bifs[name]
	if !ok {
		return Value{}, fmt.Errorf("unsupported RPG built-in: %s", name)
	}
	if err := p.expectOp("("); err != nil {
		return Value{}, err
	}
	// Remember if the first argument was a bare variable: %CHAR needs its
	// declared decimals.
	before := p.pos
	var firstTok *token
	if before < len(p.tokens) {
		firstTok = &p.tokens[before]
	}
	first, err := p.parseExpr()
	if err != nil {
		return Value{}, err
	}
	var firstVar *Var
	if p.pos-before == 1 && firstTok != nil && firstTok.typ == tokName {
		firstVar = p.ctx.Vars[strings.ToLower(firstTok.str)]
	}
	args := []Value{first}
	for {
		t := p.peek()
		if t == nil || t.typ != tokOp || t.str != ":" {
			break
		}
		p.next()
		v, err := p.parseExpr()
		if err != nil {
			return Value{}, err
		}
		args = append(args, v)
	}
	if err := p.expectOp(")"); err != nil {
		return Value{}, err
	}
	return fn(name, args, firstVar)
}

func (p *exprParser) readVar(name string) (Value, error) {
	key := strings.ToLower(name)
	v, ok := p.ctx.Vars[key]
	if !ok {
		return Value{}, fmt.Errorf("unknown RPG variable: %s", name)
	}
	return v.Value, nil
}

// EvalExpr evaluates a full extended-factor-2 expression string (a
// condition, or the right-hand side of an EVAL) against ctx.
func EvalExpr(src string, ctx *Context) (Value, error) {
	tokens, err := tokenize(src)
	if err != nil {
		return Value{}, err
	}
	p := &exprParser{tokens: tokens, ctx: ctx}
	v, err := p.parseExpr()
	if err != nil {
		return Value{}, err
	}
	if p.pos < len(p.tokens) {
		return Value{}, fmt.Errorf("trailing tokens in RPG expression: %q", src)
	}
	return v, nil
}

// SplitAssignment splits an EVAL statement's extended-factor-2 text
// into its lvalue name and rvalue expression source, on the first
// top-level '='. RPG EVAL statements never nest a bare assignment
// inside the right-hand side, so the first '=' is always the
// assignment operator.
func SplitAssignment(src string) (lvalue, rvalueSrc string, err error) {
	eq := strings.Index(src, "=")
	if eq == -1 {
		return "", "", fmt.Errorf("EVAL statement missing '=': %q", src)
	}
	return strings.TrimSpace(src[:eq]), strings.TrimSpace(src[eq+1:]), nil
}
