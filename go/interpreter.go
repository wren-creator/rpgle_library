package rpgle

import (
	"errors"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// interpreter.go
// ─────────────────────────────────────────────────────────────────
// Runs a parsed RPGLE program (see rpgle.go) against its WORKSTN
// display file (see dds.go). Real RPG's EXFMT ("write a format, then
// read it") maps onto Run()/Runner.Next(): the program runs in its
// own goroutine and blocks on a channel send at EXFMT, resuming once
// the caller calls Next() with the operator's response. This mirrors
// the JS port's generator .next()/.next(resume) contract as closely
// as a language without generators can -- see docs/LANGUAGE.md's
// "EXFMT Yield/Resume Contract" section.
//
// Supported opcode subset (see docs/LANGUAGE.md for the full RPGLE
// subset this library implements, and what's explicitly out of
// scope): EVAL, IF/ELSE/ENDIF, DOW/ENDDO, LEAVE, SELECT/WHEN/OTHER/
// ENDSL, EXSR/BEGSR/ENDSR, EXFMT, DIV/MVR, RETURN.

// Var is one program variable's current type/length/value.
type Var struct {
	Type   byte // 'A' (alpha) or 'N' (numeric)
	Length int
	Dec    int
	Value  Value
}

// BuildVars builds the program's variable table: every named DDS
// field across every record format of the externally-described
// WORKSTN file becomes a program variable (first definition in source
// order wins on a name collision), plus D-spec standalone fields
// (definition type 'S' only).
func BuildVars(prog *Program, dds *DDS) map[string]*Var {
	vars := map[string]*Var{}

	for _, fmtName := range dds.FormatOrder {
		format := dds.Formats[fmtName]
		for _, f := range format.Fields {
			if f.Kind != "field" {
				continue
			}
			key := strings.ToLower(f.Name)
			if _, exists := vars[key]; exists {
				continue
			}
			if f.DType == "A" {
				vars[key] = &Var{Type: 'A', Length: f.Length, Dec: 0, Value: StringValue(strings.Repeat(" ", f.Length))}
			} else {
				dec := 0
				if f.Dec != nil {
					dec = *f.Dec
				}
				vars[key] = &Var{Type: 'N', Length: f.Length, Dec: dec, Value: NumberValue(0)}
			}
		}
	}

	for _, d := range prog.DSpecs {
		if d.DefType != "S" {
			continue
		}
		key := strings.ToLower(d.Name)
		if d.Dec == nil {
			init := ""
			if d.Init != nil {
				init = strings.Trim(*d.Init, "'")
			}
			vars[key] = &Var{Type: 'A', Length: d.Length, Dec: 0, Value: StringValue(padEndTrunc(init, d.Length))}
		} else {
			val := 0.0
			if d.Init != nil {
				if f, err := strconv.ParseFloat(*d.Init, 64); err == nil {
					val = f
				}
			}
			vars[key] = &Var{Type: 'N', Length: d.Length, Dec: *d.Dec, Value: NumberValue(val)}
		}
	}

	return vars
}

// instr is one compiled, jump-resolved instruction. Fields are
// deliberately overloaded per-opcode (mirroring the JS port's loosely
// typed instruction objects) rather than split into a separate struct
// per opcode -- there are few enough opcodes that a flat struct reads
// more directly against the JS source it's ported from.
type instr struct {
	Op          string
	Expr        string // EVAL/IF/DOW/WHEN expression source
	FalseTarget int    // IF/DOW/WHEN false-branch target; -1 if unset
	Target      int    // JMP/LEAVE/ENDDO jump target; -1 if unset
	SubTarget   string // EXSR: subroutine name to call
	Format      string // EXFMT: record format name
	Factor1     string // DIV: factor1
	Factor2     string // DIV: factor2
	Result      string // DIV/MVR: result field name
}

// Compiled is a jump-resolved instruction array plus subroutine entry points.
type Compiled struct {
	Instrs      []instr
	Subroutines map[string]int
}

// Compile compiles a flat C-spec statement list into a jump-resolved
// instruction array (classic backpatch compilation: IF/DOW/SELECT
// frames on a stack, each closing keyword patches the jump targets it
// owns). See docs/LANGUAGE.md's "Compile Phases" section for a worked
// example.
func Compile(statements []Statement) (*Compiled, error) {
	var instrs []instr
	subroutines := map[string]int{}

	type ifFrame struct {
		ifIndex      int
		elseJmpIndex int
	}
	type dowFrame struct {
		dowIndex int
		leaves   []int
	}
	type armInfo struct {
		index   int
		isOther bool
	}
	type selFrame struct {
		arms         []armInfo
		pendingJumps []int
	}

	var ifStack []*ifFrame
	var dowStack []*dowFrame
	var selStack []*selFrame

	for _, stmt := range statements {
		switch stmt.Op {
		case "EVAL":
			instrs = append(instrs, instr{Op: "EVAL", Expr: stmt.Expr})

		case "IF":
			idx := len(instrs)
			instrs = append(instrs, instr{Op: "IF", Expr: stmt.Expr, FalseTarget: -1})
			ifStack = append(ifStack, &ifFrame{ifIndex: idx, elseJmpIndex: -1})

		case "ELSE":
			if len(ifStack) == 0 {
				return nil, errors.New("ELSE with no open IF")
			}
			frame := ifStack[len(ifStack)-1]
			jmpIdx := len(instrs)
			instrs = append(instrs, instr{Op: "JMP", Target: -1})
			frame.elseJmpIndex = jmpIdx
			instrs[frame.ifIndex].FalseTarget = jmpIdx + 1

		case "ENDIF":
			if len(ifStack) == 0 {
				return nil, errors.New("ENDIF with no open IF")
			}
			frame := ifStack[len(ifStack)-1]
			ifStack = ifStack[:len(ifStack)-1]
			idx := len(instrs)
			if frame.elseJmpIndex == -1 {
				instrs[frame.ifIndex].FalseTarget = idx
			} else {
				instrs[frame.elseJmpIndex].Target = idx
			}
			instrs = append(instrs, instr{Op: "NOP"})

		case "DOW":
			idx := len(instrs)
			instrs = append(instrs, instr{Op: "DOW", Expr: stmt.Expr, FalseTarget: -1})
			dowStack = append(dowStack, &dowFrame{dowIndex: idx})

		case "LEAVE":
			if len(dowStack) == 0 {
				return nil, errors.New("LEAVE with no open DOW")
			}
			frame := dowStack[len(dowStack)-1]
			frame.leaves = append(frame.leaves, len(instrs))
			instrs = append(instrs, instr{Op: "LEAVE", Target: -1})

		case "ENDDO":
			if len(dowStack) == 0 {
				return nil, errors.New("ENDDO with no open DOW")
			}
			frame := dowStack[len(dowStack)-1]
			dowStack = dowStack[:len(dowStack)-1]
			idx := len(instrs)
			instrs[frame.dowIndex].FalseTarget = idx + 1
			for _, li := range frame.leaves {
				instrs[li].Target = idx + 1
			}
			instrs = append(instrs, instr{Op: "ENDDO", Target: frame.dowIndex})

		case "SELECT":
			selStack = append(selStack, &selFrame{})
			instrs = append(instrs, instr{Op: "NOP"})

		case "WHEN", "OTHER":
			if len(selStack) == 0 {
				return nil, fmt.Errorf("%s with no open SELECT", stmt.Op)
			}
			sframe := selStack[len(selStack)-1]
			if len(sframe.arms) > 0 {
				sframe.pendingJumps = append(sframe.pendingJumps, len(instrs))
				instrs = append(instrs, instr{Op: "JMP", Target: -1})
			}
			idx := len(instrs)
			if stmt.Op == "WHEN" {
				instrs = append(instrs, instr{Op: "WHEN", Expr: stmt.Expr, FalseTarget: -1})
			} else {
				instrs = append(instrs, instr{Op: "OTHER"})
			}
			sframe.arms = append(sframe.arms, armInfo{index: idx, isOther: stmt.Op == "OTHER"})

		case "ENDSL":
			if len(selStack) == 0 {
				return nil, errors.New("ENDSL with no open SELECT")
			}
			sframe := selStack[len(selStack)-1]
			selStack = selStack[:len(selStack)-1]
			sframe.pendingJumps = append(sframe.pendingJumps, len(instrs))
			instrs = append(instrs, instr{Op: "JMP", Target: -1})
			endslIdx := len(instrs)
			instrs = append(instrs, instr{Op: "NOP"})
			for i, arm := range sframe.arms {
				if arm.isOther {
					continue
				}
				nextIdx := endslIdx
				if i+1 < len(sframe.arms) {
					nextIdx = sframe.arms[i+1].index
				}
				instrs[arm.index].FalseTarget = nextIdx
			}
			for _, j := range sframe.pendingJumps {
				instrs[j].Target = endslIdx
			}

		case "BEGSR":
			subroutines[stmt.Factor1] = len(instrs)

		case "ENDSR":
			instrs = append(instrs, instr{Op: "ENDSR"})

		case "EXSR":
			instrs = append(instrs, instr{Op: "EXSR", SubTarget: stmt.Expr})

		case "EXFMT":
			instrs = append(instrs, instr{Op: "EXFMT", Format: stmt.Expr})

		case "DIV":
			instrs = append(instrs, instr{Op: "DIV", Factor1: stmt.Factor1, Factor2: stmt.Factor2, Result: stmt.Result})

		case "MVR":
			instrs = append(instrs, instr{Op: "MVR", Result: stmt.Result})

		case "RETURN":
			instrs = append(instrs, instr{Op: "RETURN"})

		default:
			return nil, fmt.Errorf("unsupported RPG opcode: %s", stmt.Op)
		}
	}

	return &Compiled{Instrs: instrs, Subroutines: subroutines}, nil
}

var numLitRe = regexp.MustCompile(`^-?\d+(\.\d+)?$`)

func readValue(vars map[string]*Var, name string) (Value, error) {
	key := strings.ToLower(strings.TrimSpace(name))
	v, ok := vars[key]
	if !ok {
		return Value{}, fmt.Errorf("unknown RPG variable: %s", name)
	}
	return v.Value, nil
}

func numericOperand(vars map[string]*Var, operand string) (float64, error) {
	if numLitRe.MatchString(operand) {
		f, err := strconv.ParseFloat(operand, 64)
		if err != nil {
			return 0, err
		}
		return f, nil
	}
	v, err := readValue(vars, operand)
	if err != nil {
		return 0, err
	}
	return v.AsFloat(), nil
}

var (
	assignIndRe  = regexp.MustCompile(`(?i)^\*IN(\d\d)$`)
	assignInlrRe = regexp.MustCompile(`(?i)^\*INLR$`)
)

func assign(vars map[string]*Var, indicators map[string]bool, lvalue string, rvalue Value) error {
	if m := assignIndRe.FindStringSubmatch(lvalue); m != nil {
		indicators[m[1]] = rvalue.AsBool()
		return nil
	}
	if assignInlrRe.MatchString(lvalue) {
		indicators["LR"] = rvalue.AsBool()
		return nil
	}
	key := strings.ToLower(strings.TrimSpace(lvalue))
	v, ok := vars[key]
	if !ok {
		return fmt.Errorf("unknown RPG variable on assignment: %s", lvalue)
	}
	if v.Type == 'A' {
		v.Value = StringValue(padEndTrunc(rvalue.AsString(), v.Length))
	} else {
		// Truncate-on-assign (no rounding) -- the real RPG default, and
		// what DIV/MVR-based modulo relies on for boundedness.
		factor := math.Pow(10, float64(v.Dec))
		v.Value = NumberValue(math.Trunc(rvalue.AsFloat()*factor) / factor)
	}
	return nil
}

// formatFieldText is a field's display text. Numeric fields are zero-filled
// to their length. With decimals the decimal point is written out (so the
// field is one column wider than a real 5250 would draw it, which shows bare
// digits with an implied point): bare digits would not survive being typed
// back in, since "000004875" parses as 4875, not 48.75.
func formatFieldText(v *Var) string {
	if v.Type == 'A' {
		return v.Value.Str
	}
	n := v.Value.Num
	if v.Dec > 0 {
		body := fmt.Sprintf("%0*.*f", v.Length+1, v.Dec, math.Abs(n))
		if n < 0 {
			return "-" + body[1:]
		}
		return body
	}
	if n < 0 {
		width := v.Length - 1
		if width < 1 {
			width = 1
		}
		return "-" + padStartZero(strconv.FormatInt(int64(math.Round(-n)), 10), width)
	}
	return padStartZero(strconv.FormatInt(int64(math.Round(n)), 10), v.Length)
}

// RenderedField is one field or constant, positioned and formatted,
// ready to display -- protocol-agnostic (no 5250/3270 datastream
// concerns here; that's a presentation-layer concern for the caller).
type RenderedField struct {
	Row, Col int
	Text     string
	Input    bool
	Length   int // only meaningful when Input is true
}

// Cursor is a screen position.
type Cursor struct {
	Row, Col int
}

// RenderResult is a DDS record format rendered against the current
// variable/indicator state.
type RenderResult struct {
	Fields   []RenderedField
	InputMap map[int]string // screen row -> field name
	Cursor   Cursor
}

// RenderFormat renders a DDS record format against the current
// variable/indicator state. InputMap maps each input field's screen
// row to its field name, so the caller can read input back after the
// next AID key using whatever row/field convention its own renderer
// already uses.
func RenderFormat(format *Format, vars map[string]*Var, indicators map[string]bool) RenderResult {
	var fields []RenderedField
	inputMap := map[int]string{}
	var cursor *Cursor

	conditionOk := func(conds []IndicatorCond) bool {
		for _, c := range conds {
			on := indicators[c.Num]
			fail := on
			if !c.Not {
				fail = !on
			}
			if fail {
				return false
			}
		}
		return true
	}

	for _, f := range format.Fields {
		if !conditionOk(f.Indicators) {
			continue
		}
		if f.Kind == "const" {
			fields = append(fields, RenderedField{Row: f.Row, Col: f.Col, Text: f.Text, Input: false})
			continue
		}
		v := vars[strings.ToLower(f.Name)]
		text := formatFieldText(v)
		isInput := f.Usage == "I" || f.Usage == "B"
		rf := RenderedField{Row: f.Row, Col: f.Col, Text: text, Input: isInput}
		if isInput {
			rf.Length = f.Length
		}
		fields = append(fields, rf)
		if isInput {
			name := strings.ToLower(f.Name)
			inputMap[f.Row] = name
			if cursor == nil {
				cursor = &Cursor{Row: f.Row, Col: f.Col}
			}
		}
	}

	c := Cursor{Row: 0, Col: 0}
	if cursor != nil {
		c = *cursor
	}
	return RenderResult{Fields: fields, InputMap: inputMap, Cursor: c}
}

// ScreenData is what the interpreter sends when the program hits
// EXFMT -- equivalent to the JS generator's yielded value.
type ScreenData struct {
	FormatName string
	Fields     []RenderedField
	Cursor     Cursor
	Keys       map[string]string
	InputMap   map[int]string
}

// Resume is what the caller sends back to unblock the interpreter
// after EXFMT -- equivalent to the argument passed to the JS
// generator's .next(resume).
type Resume struct {
	Key    string
	Values map[string]string
}

// Runner drives one running program. Create with Run(); read the next
// screen (or completion) with Next().
type Runner struct {
	screenCh chan ScreenData
	resumeCh chan Resume
	doneCh   chan error
}

// Run starts the program in its own goroutine and returns a Runner
// for driving it. Equivalent to calling runProgram(rpgAst, ddsFormats)
// in the JS port and manually calling .next()/.next(resume) in a loop.
func Run(prog *Program, dds *DDS) *Runner {
	r := &Runner{
		screenCh: make(chan ScreenData),
		resumeCh: make(chan Resume),
		doneCh:   make(chan error, 1),
	}
	go r.execute(prog, dds)
	return r
}

// Next blocks until the next EXFMT yield, or the program finishes.
// Pass nil for resume on the first call (mirrors gen.next() with no
// argument); pass the operator's response on every call after that
// (mirrors gen.next(resume)).
func (r *Runner) Next(resume *Resume) (*ScreenData, bool, error) {
	if resume != nil {
		r.resumeCh <- *resume
	}
	select {
	case s := <-r.screenCh:
		return &s, false, nil
	case err := <-r.doneCh:
		return nil, true, err
	}
}

func (r *Runner) execute(prog *Program, dds *DDS) {
	defer func() {
		if rec := recover(); rec != nil {
			r.doneCh <- fmt.Errorf("rpgle: %v", rec)
		}
	}()

	vars := BuildVars(prog, dds)
	indicators := map[string]bool{}
	compiled, err := Compile(prog.Statements)
	if err != nil {
		r.doneCh <- err
		return
	}
	instrs := compiled.Instrs
	subroutines := compiled.Subroutines

	var callStack []int
	pc := 0
	var lastDivRemainder float64

	for pc < len(instrs) {
		in := instrs[pc]
		switch in.Op {
		case "NOP":
			pc++

		case "ENDSR":
			if len(callStack) == 0 {
				r.doneCh <- errors.New("ENDSR with no active EXSR call")
				return
			}
			pc = callStack[len(callStack)-1]
			callStack = callStack[:len(callStack)-1]

		case "EVAL":
			lvalue, rvalueSrc, err := SplitAssignment(in.Expr)
			if err != nil {
				r.doneCh <- err
				return
			}
			rvalue, err := EvalExpr(rvalueSrc, &Context{Vars: vars, Indicators: indicators})
			if err != nil {
				r.doneCh <- err
				return
			}
			if err := assign(vars, indicators, lvalue, rvalue); err != nil {
				r.doneCh <- err
				return
			}
			pc++

		case "IF", "WHEN":
			cond, err := EvalExpr(in.Expr, &Context{Vars: vars, Indicators: indicators})
			if err != nil {
				r.doneCh <- err
				return
			}
			if cond.AsBool() {
				pc++
			} else {
				pc = in.FalseTarget
			}

		case "OTHER":
			pc++

		case "JMP":
			pc = in.Target

		case "DOW":
			cond, err := EvalExpr(in.Expr, &Context{Vars: vars, Indicators: indicators})
			if err != nil {
				r.doneCh <- err
				return
			}
			if cond.AsBool() {
				pc++
			} else {
				pc = in.FalseTarget
			}

		case "ENDDO":
			pc = in.Target

		case "LEAVE":
			pc = in.Target

		case "EXSR":
			target, ok := subroutines[in.SubTarget]
			if !ok {
				r.doneCh <- fmt.Errorf("unknown RPG subroutine: %s", in.SubTarget)
				return
			}
			callStack = append(callStack, pc+1)
			pc = target

		case "DIV":
			f1, err := numericOperand(vars, in.Factor1)
			if err != nil {
				r.doneCh <- err
				return
			}
			f2, err := numericOperand(vars, in.Factor2)
			if err != nil {
				r.doneCh <- err
				return
			}
			quotient := math.Trunc(f1 / f2)
			lastDivRemainder = f1 - quotient*f2
			if err := assign(vars, indicators, in.Result, NumberValue(quotient)); err != nil {
				r.doneCh <- err
				return
			}
			pc++

		case "MVR":
			if err := assign(vars, indicators, in.Result, NumberValue(lastDivRemainder)); err != nil {
				r.doneCh <- err
				return
			}
			pc++

		case "EXFMT":
			format, ok := dds.Formats[in.Format]
			if !ok {
				r.doneCh <- fmt.Errorf("unknown DDS record format: %s", in.Format)
				return
			}
			rr := RenderFormat(format, vars, indicators)

			r.screenCh <- ScreenData{
				FormatName: in.Format,
				Fields:     rr.Fields,
				Cursor:     rr.Cursor,
				Keys:       format.Keys,
				InputMap:   rr.InputMap,
			}
			resume := <-r.resumeCh

			// Response indicators reset before applying this read's key,
			// scoped to only the indicators this format's own CF/CA
			// keywords own (real RPG behavior -- other formats' key
			// indicators are untouched).
			for _, num := range format.Keys {
				indicators[num] = false
			}
			if indForKey, ok := format.Keys[resume.Key]; ok {
				indicators[indForKey] = true
			}

			for _, fieldName := range rr.InputMap {
				if resume.Values != nil {
					if v, ok := resume.Values[fieldName]; ok {
						if err := assign(vars, indicators, fieldName, StringValue(v)); err != nil {
							r.doneCh <- err
							return
						}
					}
				}
			}
			pc++

		case "RETURN":
			r.doneCh <- nil
			return

		default:
			r.doneCh <- fmt.Errorf("unimplemented RPG instruction: %s", in.Op)
			return
		}
	}
	r.doneCh <- nil
}
