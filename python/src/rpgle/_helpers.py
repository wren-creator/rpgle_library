"""
Small helpers shared by dds.py, rpgle.py, and interpreter.py -- kept in
one private module (leading underscore, not part of the public API)
rather than duplicated per file, mirroring go/helpers.go's reasoning
for a single shared implementation.
"""

from __future__ import annotations

import re

from .errors import RpgleError


def col(line: str, a: int, b: int) -> str:
    """A 1-indexed, inclusive column range, bounds-safe, trimmed.

    Mirrors the col(line, a, b) helper duplicated across the JS port's
    dds.js/rpgle.js. Python slicing already clamps out-of-range indices
    without raising, so no manual bounds-checking is needed here the
    way go/helpers.go's version required.
    """
    return line[a - 1 : b].strip()


def pad_end_trunc(s: str, length: int) -> str:
    """Pad s with trailing spaces to length, or truncate it to length
    if it's already longer -- the fixed-width character-field
    semantics both DDS and RPG standalone fields use.
    """
    if length < 0:
        length = 0
    if len(s) < length:
        return s + " " * (length - len(s))
    return s[:length]


def pad_start_zero(s: str, length: int) -> str:
    """Left-pad s with '0' to length -- RPG's default numeric display
    format (right-justified, zero-filled).
    """
    if len(s) >= length:
        return s
    return "0" * (length - len(s)) + s


_OBJECT_NAME = re.compile(r"^[A-Za-z_$#@][A-Za-z0-9_$#@]*$")


def is_object_name(name: str) -> bool:
    """An IBM i object name: letters, digits and _ $ # @, not starting with a digit.

    File and program names become file names on disk (custmast.pf,
    custlookup.rpgle), so they must never contain a path separator or a dot.
    """
    return bool(_OBJECT_NAME.match(name))


def digits(text: str, what: str, owner: str) -> int:
    """A numeric column of a spec or DDS line: blank is 0, anything else must
    be whole digits. Malformed input is an RpgleError naming the field, never
    a ValueError out of int().
    """
    text = text.strip()
    if not text:
        return 0
    if not text.isdigit():
        raise RpgleError(f"{owner}: {what} {text!r} is not a number")
    return int(text)
