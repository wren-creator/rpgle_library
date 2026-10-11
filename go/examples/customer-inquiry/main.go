// customer-inquiry — rpgle_library example with a real DISK file.
//
// Drives programs/custinq.rpgle: type a customer number, the program CHAINs to
// the CUSTMAST file and shows the name and balance, or "Customer not found".
// The file lives in an in-memory SQLite database loaded from
// programs/custmast.csv, so there is nothing to set up.
//
// DISK files come from the sqlitedb package, which is the only part of the
// library with a dependency (modernc.org/sqlite, pure Go, no cgo). A program
// that doesn't use DISK files never compiles it.
//
//	cd go/examples/customer-inquiry && go run .     (try 20, then 99, then f3)
package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	rpgle "github.com/wren-creator/rpgle_library/go"
	"github.com/wren-creator/rpgle_library/go/sqlitedb"
)

var f3Re = regexp.MustCompile(`(?i)^f3$`)

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	progDir := filepath.Join("..", "..", "..", "programs")
	read := func(name string) string {
		b, err := os.ReadFile(filepath.Join(progDir, name))
		check(err)
		return string(b)
	}

	pf, err := rpgle.ParsePF("CUSTMAST", read("custmast.pf"))
	check(err)
	db, err := sqlitedb.Open(":memory:")
	check(err)
	defer db.Close()
	file, err := db.OpenFile(pf)
	check(err)

	// custmast.csv: a header row of field names, then one customer per line.
	lines := strings.Split(strings.TrimSpace(read("custmast.csv")), "\n")
	names := strings.Split(lines[0], ",")
	for _, line := range lines[1:] {
		rec := rpgle.Record{}
		for i, cell := range strings.Split(line, ",") {
			rec[strings.ToUpper(strings.TrimSpace(names[i]))] = rpgle.StringValue(cell)
		}
		check(file.Write(rec))
	}

	dds, err := rpgle.ParseDDS(read("custinq.dspf"))
	check(err)
	prog, err := rpgle.ParseRPGLE(read("custinq.rpgle"))
	check(err)

	r := rpgle.RunWith(prog, dds, rpgle.RunOptions{DB: db, PFs: map[string]*rpgle.PhysicalFile{"CUSTMAST": pf}})
	scanner := bufio.NewScanner(os.Stdin)

	screen, done, err := r.Next(nil)
	for {
		check(err)
		if done {
			fmt.Println("\n(program ended)")
			return
		}
		renderPlainText(screen)

		var inputName string
		for _, name := range screen.InputMap {
			inputName = name
			break // this program has exactly one input field per format
		}

		fmt.Print("> ")
		if !scanner.Scan() {
			return
		}
		line := strings.TrimSpace(scanner.Text())
		key := "ENTER"
		if f3Re.MatchString(line) {
			key = "F3"
		}
		values := map[string]string{}
		if inputName != "" && key == "ENTER" {
			values[inputName] = line
		}
		screen, done, err = r.Next(&rpgle.Resume{Key: key, Values: values})
	}
}

func renderPlainText(screen *rpgle.ScreenData) {
	fmt.Println("\n" + strings.Repeat("─", 40))
	for _, f := range screen.Fields {
		fmt.Println(f.Text)
	}
	var keys []string
	for k, v := range screen.Keys {
		keys = append(keys, fmt.Sprintf("%s->F%s", k, v))
	}
	if len(keys) > 0 {
		fmt.Println("Keys: " + strings.Join(keys, ", "))
	}
}
