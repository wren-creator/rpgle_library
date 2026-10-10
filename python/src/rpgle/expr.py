"""
expr.py
─────────────────────────────────────────────────────────────────
A small recursive-descent evaluator for the RPG IV extended-factor-2
expressions this interpreter supports: EVAL right-hand sides and
IF/DOW/WHEN boolean conditions.

Grammar (case-insensitive keywords):
  expr       := or_expr
  or_expr    := and_expr (OR and_expr)*
  and_expr   := not_expr (AND not_expr)*
  not_expr   := NOT not_expr | compare
  compare    := additive ((= | <> | <= | >= | < | >) additive)?
  additive   := term ((+ | -) term)*
  term       := unary ((* | /) unary)*
  unary      := '-' unary | primary
  primary    := NUMBER | STRING | '*ON' | '*OFF'
              | '*IN' DIGIT DIGIT | '*INLR'
              | '%' NAME '(' expr (':' expr)* ')'
              | '%EOF' | '%FOUND' | '%EQUAL' ('(' FILENAME ')')?
              | NAME | '(' expr ')'

Values are plain Python float/str/bool -- Python is dynamically typed
like the original JS, so (unlike the Go port) no tagged Value union is
needed to hold "a number, a string, or a bool" in one variable. The
as_bool/as_float/as_string coercion helpers below exist for the same
reason the Go port added them despite Go not strictly needing a helper
either: real RPG arithmetic/comparison operators only ever see the
"right" operand kind in the shipped content, so these are a best-effort
coercion path for anything else, not a documented feature -- matching
the Go port's behavior exactly rather than JS's looser automatic
+/- coercion, since docs/PUBLISHING.md requires ports to agree on
behavior and Go's stricter, explicit coercion is the spec of record for
non-JS ports.

Truncation-on-assignment (the classic RPG idiom used for modulo, since
RPG has no %REM until well past V4R3) is applied by the caller
(interpreter.py's _assign), not here -- this module only evaluates the
right-hand side to a value.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .errors import RpgleError

_IN_NUM_RE = re.compile(r"^\*IN(\d\d)$")


def _kind(v: Any) -> str:
    if isinstance(v, bool):
        return "B"
    if isinstance(v, (int, float)):
        return "N"
    if isinstance(v, str):
        return "S"
    raise RpgleError(f"Unsupported RPG expression value: {v!r}")


def as_bool(v: Any) -> bool:
    """Mirrors JS's Boolean(x) coercion used throughout the source
    interpreter (Boolean(0)/Boolean('') are false, everything else true).
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return False


def as_float(v: Any) -> float:
    """Coerces a value to a number for arithmetic."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return math.nan
    return 0.0


def _format_number_for_string(n: float) -> str:
    if n == math.trunc(n) and not math.isinf(n):
        return str(int(n))
    return repr(n)


def as_string(v: Any) -> str:
    """Mirrors JS's String(x) coercion, used by %TRIM/%LEN/%SUBST."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _format_number_for_string(v)
    return ""


def _values_equal(a: Any, b: Any) -> bool:
    if _kind(a) != _kind(b):
        return False
    if isinstance(a, str):
        return a.rstrip() == b.rstrip()  # RPG compares character data as if blank-padded
    return a == b


def _value_less(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.rstrip() < b.rstrip()
    return as_float(a) < as_float(b)


@dataclass
class Var:
    """One program variable's current type/length/value.

    Defined here (rather than interpreter.py) since expr.py's Context
    needs it and interpreter.py already imports from expr.py -- keeping
    it here avoids a circular import.
    """

    type: str  # 'A' (alpha) or 'N' (numeric)
    length: int
    dec: int
    value: Any
    # "ind" for a free-form indicator variable: stored as the character
    # '1' or '0' (which is what RPG does), but read and assigned as a boolean.
    kind: str = ""


@dataclass
class Context:
    """The variable/indicator state an expression is evaluated against."""

    vars: dict[str, Var] = field(default_factory=dict)
    indicators: dict[str, bool] = field(default_factory=dict)
    # Answers %EOF/%FOUND/%EQUAL: (kind, file name or None for "the
    # last file used") -> bool. None when the program has no DISK files.
    file_status: Optional[Callable[[str, Optional[str]], bool]] = None


@dataclass
class _Token:
    type: str  # "number" | "string" | "indicator_or_const" | "func" | "name" | "logic" | "op"
    value: Any


def _tokenize(src: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "'":
            j = i + 1
            s = []
            while j < n and src[j] != "'":
                s.append(src[j])
                j += 1
            tokens.append(_Token("string", "".join(s)))
            i = j + 1
            continue
        if c.isdigit():
            j = i
            s = []
            while j < n and (src[j].isdigit() or src[j] == "."):
                s.append(src[j])
                j += 1
            tokens.append(_Token("number", float("".join(s))))
            i = j
            continue
        if c == "*" and i + 1 < n and src[i + 1].isalpha():
            j = i + 1
            s = ["*"]
            while j < n and src[j].isalnum():
                s.append(src[j])
                j += 1
            tokens.append(_Token("indicator_or_const", "".join(s).upper()))
            i = j
            continue
        if c == "%":
            j = i + 1
            s = ["%"]
            while j < n and src[j].isalnum():
                s.append(src[j])
                j += 1
            tokens.append(_Token("func", "".join(s).upper()))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            s = []
            while j < n and (src[j].isalnum() or src[j] == "_"):
                s.append(src[j])
                j += 1
            name = "".join(s)
            upper = name.upper()
            if upper in ("AND", "OR", "NOT"):
                tokens.append(_Token("logic", upper))
            else:
                tokens.append(_Token("name", name))
            i = j
            continue
        if c == "<" and src[i + 1 : i + 2] == ">":
            tokens.append(_Token("op", "<>"))
            i += 2
            continue
        if c == "<" and src[i + 1 : i + 2] == "=":
            tokens.append(_Token("op", "<="))
            i += 2
            continue
        if c == ">" and src[i + 1 : i + 2] == "=":
            tokens.append(_Token("op", ">="))
            i += 2
            continue
        if c in "=<>+-*/():":
            tokens.append(_Token("op", c))
            i += 1
            continue
        raise RpgleError(f"Unexpected character in RPG expression: {c!r} in {src!r}")
    return tokens


# ── built-in functions ───────────────────────────────────────────
# Each takes (name, args, first_var) where first_var is the Var if the
# first argument was a bare variable name (else None).


def _arity(name: str, args: list, lo: int, hi: Optional[int] = None) -> None:
    hi = lo if hi is None else hi
    if not lo <= len(args) <= hi:
        want = str(lo) if lo == hi else f"{lo} to {hi}"
        raise RpgleError(f"{name} takes {want} argument(s), got {len(args)}")


def _num(name: str, v: Any) -> float:
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            raise RpgleError(f"{name}: {v!r} is not a number") from None
    return as_float(v)


def _trunc(n: float, dec: int) -> float:
    f = 10**dec
    return math.trunc(n * f) / f


def _half_adjust(n: float, dec: int) -> float:
    f = 10**dec
    return math.copysign(math.floor(abs(n) * f + 0.5), n) / f


def _bif_char(name, args, var):
    _arity(name, args, 1)
    v = args[0]
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "1" if v else "0"
    if var is not None and var.type == "N" and var.dec:
        return f"{float(v):.{var.dec}f}"
    return _format_number_for_string(float(v))


def _bif_dec(name, args, var, half=False):
    _arity(name, args, 3)
    n, dec = _num(name, args[0]), int(as_float(args[2]))
    return _half_adjust(n, dec) if half else _trunc(n, dec)


def _bif_int(name, args, var, half=False):
    _arity(name, args, 1)
    n = _num(name, args[0])
    return float(_half_adjust(n, 0) if half else math.trunc(n))


def _bif_rem(name, args, var):
    _arity(name, args, 2)
    a, b = _num(name, args[0]), _num(name, args[1])
    if b == 0:
        raise RpgleError(f"{name}: division by zero")
    return a - math.trunc(a / b) * b


def _bif_div(name, args, var):
    _arity(name, args, 2)
    a, b = _num(name, args[0]), _num(name, args[1])
    if b == 0:
        raise RpgleError(f"{name}: division by zero")
    return float(math.trunc(a / b))


def _bif_sqrt(name, args, var):
    _arity(name, args, 1)
    n = _num(name, args[0])
    if n < 0:
        raise RpgleError("%SQRT of a negative number")
    return math.sqrt(n)


def _bif_minmax(name, args, var):
    _arity(name, args, 2, 99)
    pick = min if name == "%MIN" else max
    if all(isinstance(a, str) for a in args):
        return pick(args, key=lambda a: a.rstrip())
    return pick(_num(name, a) for a in args)


def _bif_scan(name, args, var):
    _arity(name, args, 2, 3)
    needle, hay = as_string(args[0]), as_string(args[1])
    start = int(as_float(args[2])) if len(args) == 3 else 1
    if needle == "" or start < 1:
        return 0.0
    return float(hay.find(needle, start - 1) + 1)


def _bif_replace(name, args, var):
    _arity(name, args, 2, 4)
    repl, src = as_string(args[0]), as_string(args[1])
    start = int(as_float(args[2])) if len(args) >= 3 else 1
    length = int(as_float(args[3])) if len(args) == 4 else len(repl)
    if start < 1:
        raise RpgleError("%REPLACE: start must be 1 or more")
    return src[: start - 1] + repl + src[start - 1 + length :]


def _bif_subst(name, args, var):
    _arity(name, args, 2, 3)
    s = as_string(args[0])
    start = int(as_float(args[1])) - 1  # RPG %SUBST start is 1-based
    length = len(s) - start if len(args) <= 2 else int(as_float(args[2]))
    start = max(0, min(start, len(s)))
    end = max(start, min(start + length, len(s)))
    return s[start:end]


def _bif_len(name, args, var):
    _arity(name, args, 1)
    return float(len(as_string(args[0])))


def _bif_abs(name, args, var):
    _arity(name, args, 1)
    return abs(_num(name, args[0]))


def _str_fn(fn):
    def run(name, args, var):
        _arity(name, args, 1)
        return fn(as_string(args[0]))

    return run


_BIFS = {
    "%TRIM": _str_fn(str.strip),
    "%TRIML": _str_fn(str.lstrip),
    "%TRIMR": _str_fn(str.rstrip),
    "%UPPER": _str_fn(str.upper),
    "%LOWER": _str_fn(str.lower),
    "%LEN": _bif_len,
    "%SUBST": _bif_subst,
    "%CHAR": _bif_char,
    "%DEC": _bif_dec,
    "%DECH": lambda n, a, v: _bif_dec(n, a, v, half=True),
    "%INT": _bif_int,
    "%INTH": lambda n, a, v: _bif_int(n, a, v, half=True),
    "%ABS": _bif_abs,
    "%REM": _bif_rem,
    "%DIV": _bif_div,
    "%SQRT": _bif_sqrt,
    "%MIN": _bif_minmax,
    "%MAX": _bif_minmax,
    "%SCAN": _bif_scan,
    "%REPLACE": _bif_replace,
}


class _Parser:
    def __init__(self, tokens: list[_Token], ctx: Context):
        self.tokens = tokens
        self.pos = 0
        self.ctx = ctx

    def _peek(self) -> Optional[_Token]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> Optional[_Token]:
        t = self._peek()
        self.pos += 1
        return t

    def _expect_op(self, v: str) -> None:
        t = self._next()
        if t is None or t.type != "op" or t.value != v:
            raise RpgleError(f"Expected '{v}' in RPG expression")

    def parse_expr(self) -> Any:
        return self._parse_or()

    def _parse_or(self) -> Any:
        v = self._parse_and()
        while (t := self._peek()) and t.type == "logic" and t.value == "OR":
            self._next()
            # Parse (consume tokens for) the right side unconditionally --
            # Python's `or` would short-circuit and skip parsing it,
            # leaving trailing tokens whenever the left side is already
            # true.
            rhs = self._parse_and()
            v = as_bool(v) or as_bool(rhs)
        return v

    def _parse_not(self) -> Any:
        t = self._peek()
        if t and t.type == "logic" and t.value == "NOT":
            self._next()
            return not as_bool(self._parse_not())
        return self._parse_compare()

    def _parse_and(self) -> Any:
        v = self._parse_not()
        while (t := self._peek()) and t.type == "logic" and t.value == "AND":
            self._next()
            # Same reasoning as _parse_or: always parse the right side,
            # even if the left side is already false.
            rhs = self._parse_not()
            v = as_bool(v) and as_bool(rhs)
        return v

    def _parse_compare(self) -> Any:
        left = self._parse_additive()
        t = self._peek()
        if t and t.type == "op" and t.value in ("=", "<>", "<=", ">=", "<", ">"):
            self._next()
            right = self._parse_additive()
            if t.value == "=":
                return _values_equal(left, right)
            if t.value == "<>":
                return not _values_equal(left, right)
            if t.value == "<":
                return _value_less(left, right)
            if t.value == ">":
                return _value_less(right, left)
            if t.value == "<=":
                return not _value_less(right, left)
            if t.value == ">=":
                return not _value_less(left, right)
        return left

    def _parse_additive(self) -> Any:
        v = self._parse_term()
        while (t := self._peek()) and t.type == "op" and t.value in ("+", "-"):
            op = self._next().value
            rhs = self._parse_term()
            if isinstance(v, str) or isinstance(rhs, str):
                if op == "+" and isinstance(v, str) and isinstance(rhs, str):
                    v = v + rhs  # + joins character values, padding and all (use %TRIM)
                else:
                    raise RpgleError(f"Can't use {op} between text and a number, or - between two texts; convert with %CHAR or %DEC")
            else:
                v = as_float(v) + as_float(rhs) if op == "+" else as_float(v) - as_float(rhs)
        return v

    def _parse_term(self) -> Any:
        v = self._parse_unary()
        while (t := self._peek()) and t.type == "op" and t.value in ("*", "/"):
            op = self._next().value
            rhs = self._parse_unary()
            v = as_float(v) * as_float(rhs) if op == "*" else as_float(v) / as_float(rhs)
        return v

    def _parse_unary(self) -> Any:
        t = self._peek()
        if t and t.type == "op" and t.value == "-":
            self._next()
            return -as_float(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Any:
        t = self._next()
        if t is None:
            raise RpgleError("Unexpected end of RPG expression")
        if t.type == "number":
            return t.value
        if t.type == "string":
            return t.value
        if t.type == "indicator_or_const":
            return self._resolve_indicator_or_const(t.value)
        if t.type == "func":
            return self._parse_func_call(t.value)
        if t.type == "name":
            return self._read_var(t.value)
        if t.type == "op" and t.value == "(":
            v = self.parse_expr()
            self._expect_op(")")
            return v
        raise RpgleError(f"Unexpected token in RPG expression: {t!r}")

    def _resolve_indicator_or_const(self, name: str) -> Any:
        if name == "*ON":
            return True
        if name == "*OFF":
            return False
        if name in ("*INLR", "*INRT"):
            return bool(self.ctx.indicators.get(name[3:], False))
        m = _IN_NUM_RE.match(name)
        if m:
            return bool(self.ctx.indicators.get(m.group(1), False))
        raise RpgleError(f"Unsupported RPG figurative constant: {name}")

    def _parse_file_status(self, name: str) -> bool:
        """%EOF, %FOUND, %EQUAL: the argument is a file name, not an
        expression, and may be left off to mean the last file used.
        """
        fname = None
        t = self._peek()
        if t and t.type == "op" and t.value == "(":
            self._next()
            nt = self._next()
            if nt is None or nt.type != "name":
                raise RpgleError(f"{name} takes a file name")
            fname = nt.value
            self._expect_op(")")
        if self.ctx.file_status is None:
            raise RpgleError(f"{name} used but the program has no DISK files")
        return self.ctx.file_status(name[1:].lower(), fname)

    def _parse_func_call(self, name: str) -> Any:
        if name in ("%EOF", "%FOUND", "%EQUAL"):
            return self._parse_file_status(name)
        fn = _BIFS.get(name)
        if fn is None:
            raise RpgleError(f"Unsupported RPG built-in: {name}")
        self._expect_op("(")
        # Remember if the first argument is a bare variable: %CHAR needs
        # its declared decimals, which the value alone doesn't carry.
        first = self._peek()
        before = self.pos
        args = [self.parse_expr()]
        first_var = None
        if self.pos - before == 1 and first is not None and first.type == "name":
            first_var = self.ctx.vars.get(first.value.lower())
        while (t := self._peek()) and t.type == "op" and t.value == ":":
            self._next()
            args.append(self.parse_expr())
        self._expect_op(")")
        return fn(name, args, first_var)

    def _read_var(self, name: str) -> Any:
        key = name.lower()
        v = self.ctx.vars.get(key)
        if v is None:
            raise RpgleError(f"Unknown RPG variable: {name}")
        return v.value == "1" if v.kind == "ind" else v.value


def eval_expr(src: str, ctx: Context) -> Any:
    """Evaluates a full extended-factor-2 expression string (a
    condition, or the right-hand side of an EVAL) against ctx.
    """
    tokens = _tokenize(src)
    parser = _Parser(tokens, ctx)
    v = parser.parse_expr()
    if parser.pos < len(tokens):
        raise RpgleError(f"Trailing tokens in RPG expression: {src!r}")
    return v


def split_top(text: str, sep: str) -> list[str]:
    """Splits text on sep, ignoring any sep inside quotes or
    parentheses. split_top("a : f(b:c) : 'x:y'", ":") is three parts.
    """
    parts, depth, cur, i, n = [], 0, [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 1
            cur.append(text[i : j + 1])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur).strip())
    return parts


def split_assignment(src: str) -> tuple[str, str]:
    """Splits an EVAL statement's extended-factor-2 text into its
    lvalue name and rvalue expression source, on the first top-level
    '='. RPG EVAL statements never nest a bare assignment inside the
    right-hand side, so the first '=' is always the assignment
    operator.
    """
    eq = src.find("=")
    if eq == -1:
        raise RpgleError(f"EVAL statement missing '=': {src!r}")
    return src[:eq].strip(), src[eq + 1 :].strip()
