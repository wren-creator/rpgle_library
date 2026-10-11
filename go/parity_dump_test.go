package rpgle_test

// Runs the cross-port parity cases (tools/parity/cases.json) through the Go
// port and writes the results to a file. It does nothing unless PARITY_IN and
// PARITY_OUT are set, so a plain `go test` is unaffected; tools/parity/check.py
// sets them. See "Checking port parity" in docs/PUBLISHING.md.

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

type parityCases struct {
	Exprs   []string
	Render  [][]float64
	Dspecs  [][]string
	Specs   []string
	Compile [][]string
	Typed   []string
	Assign  []string
}

var parityErr = map[string]any{"error": true}

func parityOK(v any) map[string]any { return map[string]any{"ok": v} }

func TestParityDump(t *testing.T) {
	in, out := os.Getenv("PARITY_IN"), os.Getenv("PARITY_OUT")
	if in == "" || out == "" {
		t.Skip("set PARITY_IN and PARITY_OUT (tools/parity/check.py does)")
	}
	raw, err := os.ReadFile(in)
	if err != nil {
		t.Fatal(err)
	}
	var c parityCases
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	res := map[string]any{
		"exprs":   parityExprs(c.Exprs),
		"render":  parityRender(c.Render),
		"dspecs":  parityDSpecs(c.Dspecs),
		"specs":   paritySpecs(c.Specs),
		"compile": parityCompile(c.Compile),
		"typed":   parityTyped(c.Typed),
		"assign":  parityAssign(c.Assign),
	}
	b, err := json.Marshal(res)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, b, 0o644); err != nil {
		t.Fatal(err)
	}
}

func parityExprs(exprs []string) []any {
	ctx := &rpgle.Context{
		Vars: map[string]*rpgle.Var{
			"name":  {Type: 'A', Length: 20, Dec: 0, Value: rpgle.StringValue(fmt.Sprintf("%-20s", "BOB"))},
			"other": {Type: 'A', Length: 5, Dec: 0, Value: rpgle.StringValue("BOB  ")},
		},
		Indicators: map[string]bool{"03": true},
	}
	var out []any
	for _, e := range exprs {
		v, err := rpgle.EvalExpr(e, ctx)
		switch {
		case err != nil:
			out = append(out, parityErr)
		case v.Kind == 'B':
			out = append(out, map[string]any{"t": "b", "v": v.Bool})
		case v.Kind == 'N':
			if math.IsInf(v.Num, 0) || math.IsNaN(v.Num) {
				out = append(out, map[string]any{"error": true, "nonfinite": true})
			} else {
				out = append(out, map[string]any{"t": "n", "v": v.Num})
			}
		default:
			out = append(out, map[string]any{"t": "s", "v": v.Str})
		}
	}
	return out
}

func parityRender(cases [][]float64) []string {
	dds, _ := rpgle.ParseDDS("     A          R MAIN\n     A            AMT            9  2O  1  1\n")
	var out []string
	for _, r := range cases {
		vars := map[string]*rpgle.Var{"amt": {Type: 'N', Length: int(r[2]), Dec: int(r[1]), Value: rpgle.NumberValue(r[0])}}
		out = append(out, rpgle.RenderFormat(dds.Formats["MAIN"], vars, map[string]bool{}).Fields[0].Text)
	}
	return out
}

func parityDSpecs(cases [][]string) []any {
	line := func(kw, name, length, dec string) string {
		d := "   "
		if dec != "" {
			d = fmt.Sprintf("%3s", dec)
		}
		return fmt.Sprintf("     D%-17sS%15s%s %s", name, length, d, kw)
	}
	var out []any
	for _, d := range cases {
		p, err := rpgle.ParseRPGLE(line(d[0], d[1], d[2], d[3]))
		if err != nil {
			out = append(out, parityErr)
			continue
		}
		s := p.DSpecs[0]
		var dec, init any
		if s.Dec != nil {
			dec = *s.Dec
		}
		if s.Init != nil {
			init = *s.Init
		}
		out = append(out, parityOK([]any{s.Name, s.Length, dec, init}))
	}
	return out
}

func parityOKOrErr(err error) any {
	if err != nil {
		return parityErr
	}
	return parityOK(true)
}

func paritySpecs(cases []string) []any {
	var out []any
	for _, s := range cases {
		_, err := rpgle.ParseRPGLE(s)
		out = append(out, parityOKOrErr(err))
	}
	return out
}

func parityCompile(cases [][]string) []any {
	cs := func(op string) string {
		parts := strings.SplitN(op, " ", 2)
		rest := ""
		if len(parts) > 1 {
			rest = parts[1]
		}
		return fmt.Sprintf("     C                   %-10s%s", parts[0], rest)
	}
	var out []any
	for _, lines := range cases {
		var ls []string
		for _, l := range lines {
			ls = append(ls, cs(l))
		}
		p, err := rpgle.ParseRPGLE(strings.Join(ls, "\n"))
		if err == nil {
			_, err = rpgle.Compile(p.Statements)
		}
		out = append(out, parityOKOrErr(err))
	}
	return out
}

func parityTyped(cases []string) []any {
	const dds = "     A          R INQ                       CF03(03)\n     A            CUSTNO         5  0B  3 20\n"
	const prog = "     FINQ       CF   E             WORKSTN\n     C                   EXFMT     INQ\n     C                   EXFMT     INQ\n"
	var out []any
	for _, typed := range cases {
		d, _ := rpgle.ParseDDS(dds)
		p, _ := rpgle.ParseRPGLE(prog)
		r := rpgle.Run(p, d)
		r.Next(nil)
		s, _, err := r.Next(&rpgle.Resume{Key: "ENTER", Values: map[string]string{"custno": typed}})
		if err != nil || s == nil {
			out = append(out, parityErr)
			continue
		}
		for _, f := range s.Fields {
			if f.Input {
				out = append(out, parityOK(f.Text))
				break
			}
		}
	}
	return out
}

func parityAssign(cases []string) []any {
	var out []any
	for _, a := range cases {
		d, _ := rpgle.ParseDDS("     A          R X\n     A            X              5  2O  1  1\n")
		p, err := rpgle.ParseRPGLE("     Dx                S              5  2\n     C                   EVAL      x = " + a + "\n     C                   EXFMT     X\n")
		if err != nil {
			out = append(out, parityErr)
			continue
		}
		s, _, err := rpgle.Run(p, d).Next(nil)
		if err != nil || s == nil {
			out = append(out, parityErr)
		} else {
			out = append(out, parityOK(s.Fields[0].Text))
		}
	}
	return out
}
