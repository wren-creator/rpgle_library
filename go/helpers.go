package rpgle

import "strings"

// col mirrors the JS col(line, a, b) helper used throughout the JS port:
// a 1-indexed, inclusive column range, bounds-safe, trimmed.
func col(line []rune, a, b int) string {
	n := len(line)
	start := a - 1
	end := b
	if start < 0 {
		start = 0
	}
	if start > n {
		start = n
	}
	if end > n {
		end = n
	}
	if start > end {
		return ""
	}
	return strings.TrimSpace(string(line[start:end]))
}

// trimTrailingWhitespace mirrors JS's raw.replace(/\s+$/, ”), applied to
// each source line before column parsing.
func trimTrailingWhitespace(s string) string {
	return strings.TrimRight(s, " \t\r\n\v\f")
}

// padEndTrunc pads s with trailing spaces to length, or truncates it to
// length if it's already longer -- the fixed-width character-field
// semantics both DDS and RPG standalone fields use.
func padEndTrunc(s string, length int) string {
	if length < 0 {
		length = 0
	}
	if len(s) < length {
		return s + strings.Repeat(" ", length-len(s))
	}
	if len(s) > length {
		return s[:length]
	}
	return s
}

// padStartZero left-pads s with '0' to length -- RPG's default numeric
// display format (right-justified, zero-filled).
func padStartZero(s string, length int) string {
	if len(s) >= length {
		return s
	}
	return strings.Repeat("0", length-len(s)) + s
}
