// adventure-cli — realistic-workflow rpgle_library example.
//
// Drives programs/adventure.rpgle (a small text-adventure) as a plain
// stdin/stdout loop: read the ScreenData the interpreter sends at
// each EXFMT, render it as plain text, read a line of input, and
// resume. There's no 5250/3270 protocol code here at all -- the
// library's job ends at producing/consuming plain data; how you
// present that data (a real terminal, plain text, a GUI) is entirely
// up to the caller.
package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	rpgle "github.com/wren-creator/rpgle_library/go"
)

var f3Re = regexp.MustCompile(`(?i)^f3$`)

func main() {
	progDir := filepath.Join("..", "..", "..", "programs")
	ddsSource, err := os.ReadFile(filepath.Join(progDir, "adventure.dspf"))
	if err != nil {
		panic(err)
	}
	rpgSource, err := os.ReadFile(filepath.Join(progDir, "adventure.rpgle"))
	if err != nil {
		panic(err)
	}

	dds, err := rpgle.ParseDDS(string(ddsSource))
	if err != nil {
		panic(err)
	}
	prog, err := rpgle.ParseRPGLE(string(rpgSource))
	if err != nil {
		panic(err)
	}

	r := rpgle.Run(prog, dds)
	scanner := bufio.NewScanner(os.Stdin)

	screen, done, err := r.Next(nil)
	for {
		if err != nil {
			panic(err)
		}
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
		if inputName != "" {
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
