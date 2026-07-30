"""A single exception type for every parse/compile/runtime error this
library raises, so callers can catch one thing rather than guessing
which builtin exception a given failure happens to use. Subclasses
ValueError since every case is, at heart, malformed input (bad source,
bad column data, an unknown variable/format/subroutine name).
"""

from __future__ import annotations


class RpgleError(ValueError):
    pass
