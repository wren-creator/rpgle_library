package rpgle_test

import (
	"os"
	"path/filepath"
	"sort"
	"testing"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

func readAdventureDDS(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "programs", "adventure.dspf"))
	if err != nil {
		t.Fatalf("reading adventure.dspf: %v", err)
	}
	return string(b)
}

func TestParseDDSFindsAllRecordFormats(t *testing.T) {
	dds, err := rpgle.ParseDDS(readAdventureDDS(t))
	if err != nil {
		t.Fatalf("ParseDDS: %v", err)
	}

	var names []string
	for name := range dds.Formats {
		names = append(names, name)
	}
	sort.Strings(names)
	want := []string{"COMBAT", "ENDGAME", "MAIN"}
	if len(names) != len(want) {
		t.Fatalf("format names = %v, want %v", names, want)
	}
	for i := range want {
		if names[i] != want[i] {
			t.Fatalf("format names = %v, want %v", names, want)
		}
	}

	if got := dds.Formats["MAIN"].Keys["F3"]; got != "03" {
		t.Errorf("MAIN.Keys[F3] = %q, want %q", got, "03")
	}
	if got := dds.Formats["COMBAT"].Keys["F3"]; got != "03" {
		t.Errorf("COMBAT.Keys[F3] = %q, want %q", got, "03")
	}

	var mainFieldNames []string
	for _, f := range dds.Formats["MAIN"].Fields {
		if f.Kind == "field" {
			mainFieldNames = append(mainFieldNames, f.Name)
		}
	}
	wantFields := []string{"LOCTXT", "PLRHP", "MSGTXT", "CMD"}
	if len(mainFieldNames) != len(wantFields) {
		t.Fatalf("MAIN field names = %v, want %v", mainFieldNames, wantFields)
	}
	for i := range wantFields {
		if mainFieldNames[i] != wantFields[i] {
			t.Fatalf("MAIN field names = %v, want %v", mainFieldNames, wantFields)
		}
	}

	var cmdField, msgField *rpgle.Field
	for i, f := range dds.Formats["MAIN"].Fields {
		if f.Kind == "field" && f.Name == "CMD" {
			cmdField = &dds.Formats["MAIN"].Fields[i]
		}
		if f.Kind == "field" && f.Name == "MSGTXT" {
			msgField = &dds.Formats["MAIN"].Fields[i]
		}
	}
	if cmdField == nil || cmdField.Usage != "B" {
		t.Errorf("CMD field usage = %+v, want usage B", cmdField)
	}
	if msgField == nil || len(msgField.Indicators) != 1 || msgField.Indicators[0] != (rpgle.IndicatorCond{Not: false, Num: "90"}) {
		t.Errorf("MSGTXT field indicators = %+v, want [{Not:false Num:90}]", msgField)
	}
}
