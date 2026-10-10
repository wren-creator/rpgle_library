"""
freeform.py
─────────────────────────────────────────────────────────────────
Free-form RPG IV (source that starts with **FREE). Parses into the
same Program/FileSpec/DSpec/Statement records the fixed-form parser
produces, so the compiler, interpreter and file runtime are shared:
a free-form `chain key custmast;` becomes the same CHAIN statement a
fixed-form C-spec does, minus the result indicators (free-form
programs use %FOUND and %EOF instead).

A statement is everything up to a semicolon, so unlike fixed-form it
can span lines and several can share one. `//` comments and /* */
blocks are stripped. Keywords are case-insensitive.

Supported:
  declarations   ctl-opt (ignored), dcl-s, dcl-c, dcl-f, dcl-ds ... end-ds,
                 dcl-proc ... end-proc with dcl-pi ... end-pi, dcl-pr (skipped)
                 (qualified, likeds, likerec, extname)
  calls          callp name(args);  or just  name(args);
  control flow   if/elseif/else/endif, dow, dou, enddo, for/endfor,
                 select/when/other/endsl, leave, iter, return
  subroutines    begsr, endsr, exsr
  assignment     x = expr;  x += expr;  eval x = expr;  *in03 = *on;
  files          chain setll setgt read reade readp readpe write
                 update delete exfmt, optionally into/from a data structure
  arrays         dim(n) on dcl-s and dcl-ds, sorta, clear

Not supported yet (each raises an error naming the statement, never
skips it silently): arrays inside data structures, pos/overlay,
external procedures,
date/time types, monitor, /copy, the (e) opcode extender.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from typing import Optional

from .errors import RpgleError
from .expr import split_top
from .rpgle import DSpec, DsSpec, FileSpec, ParamSpec, ProcSpec, Program, Statement

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_FOR = re.compile(
    r"^(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<start>.+?)\s+(?P<dir>to|downto)\s+(?P<limit>.+?)(?:\s+by\s+(?P<by>.+))?$",
    re.IGNORECASE | re.DOTALL,
)

_KEYED_OPS = {"chain", "setll", "setgt", "reade", "readpe"}  # key then file
_FILE_ONLY_OPS = {"read", "readp", "write", "update"}  # file or record format
_SIMPLE = {"else", "endif", "enddo", "endfor", "select", "other", "endsl", "endsr", "leave", "iter"}
_UNSUPPORTED = {
    "monitor": "MONITOR/ON-ERROR is not supported",
    "call": "CALL is not supported",
    "dsply": "DSPLY is not supported",
    "reset": "RESET is not supported",
    "eval-corr": "EVAL-CORR is not supported",
}


def is_free(source: str) -> bool:
    for raw in source.split("\n"):
        if raw.strip():
            return raw.strip().upper().startswith("**FREE")
    return False


def _split(source: str) -> list[tuple[str, int]]:
    """(statement text, starting line) for every semicolon-terminated
    statement. Quotes, // comments and /* */ blocks are respected.
    """
    lines = source.split("\n")
    # Blank out the **FREE marker and compiler directives (keeping the
    # line count so error messages still point at the right line).
    for i, raw in enumerate(lines):
        t = raw.strip()
        if t.upper().startswith("**FREE"):
            lines[i] = ""
        elif t.startswith("/") and t[1:2].isalpha():
            lines[i] = ""  # /FREE, /END-FREE, /EJECT ...: /COPY and friends were expanded already
    text = "\n".join(lines)

    out: list[tuple[str, int]] = []
    cur: list[str] = []
    line = 1
    start_line = 1
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            cur.append(" ")
        elif in_str:
            cur.append(c)
            if c == "'":
                if text[i + 1 : i + 2] == "'":
                    cur.append("'")
                    i += 1
                else:
                    in_str = False
        elif c == "'":
            if not "".join(cur).strip():
                start_line = line
            in_str = True
            cur.append(c)
        elif c == "/" and text[i + 1 : i + 2] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                raise RpgleError(f"line {line}: unterminated /* comment")
            line += text.count("\n", i, end)
            i = end + 2
            cur.append(" ")
            continue
        elif c == ";":
            stmt = "".join(cur).strip()
            if stmt:
                out.append((stmt, start_line))
            cur = []
        else:
            if not c.isspace() and not "".join(cur).strip():
                start_line = line
            cur.append(c)
        i += 1
    if in_str:
        raise RpgleError(f"line {start_line}: unterminated string")
    if "".join(cur).strip():
        raise RpgleError(f"line {start_line}: statement is missing its semicolon")
    return out


def _find_assignment(text: str) -> Optional[tuple[str, str, str]]:
    """(target, operator, value) if text is an assignment. operator is
    '' for plain = or one of + - * / for compound assignment.
    """
    depth = 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n and text[j] != "'":
                j += 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = text[i - 1] if i else ""
            if prev in "<>!":
                i += 1
                continue
            op = ""
            end = i
            if prev in "+-*/" and i >= 1:
                op, end = prev, i - 1
            target = text[:end].strip()
            value = text[i + 1 :].strip()
            if target and value and _is_lvalue(target):
                return target, op, value
            return None
        i += 1
    return None


def _is_lvalue(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z_*][\w.*]*(\s*\(.*\))?(\.[A-Za-z_]\w*)?$", text))


def _parse_type(type_name: str, params: list[str]) -> tuple[int, Optional[int], str]:
    """(length, decimals or None for character, kind) for a free-form type."""
    t = type_name.lower()

    def num(i: int, default: Optional[int] = None) -> int:
        if i < len(params) and params[i]:
            try:
                return int(params[i])
            except ValueError:
                raise RpgleError(f"{type_name}: {params[i]!r} is not a whole number") from None
        if default is None:
            raise RpgleError(f"{type_name} needs a length")
        return default

    if t == "char":
        return num(0, 1), None, ""
    if t == "varchar":
        return num(0), None, "varchar"
    if t == "ind":
        return 1, None, "ind"
    if t in ("int", "uns"):
        return {3: 3, 5: 5, 10: 10, 20: 20}.get(num(0), num(0)), 0, ""
    if t in ("packed", "zoned", "bindec"):
        return num(0), num(1, 0), ""
    if t == "float":
        return 20, 10, ""
    raise RpgleError(f"type {type_name} is not supported")


def _parse_dim(visible: list[DSpec], tail: str) -> Optional[int]:
    """The element count from dim(n), where n is a whole number or the
    name of an earlier numeric dcl-c.
    """
    dm = re.search(r"\bdim\s*\(\s*([^)]*?)\s*\)", tail, re.IGNORECASE)
    if not dm:
        return None
    arg = dm.group(1)
    if arg.lstrip("-").isdigit():
        n = int(arg)
    else:
        const = next((d for d in visible if d.name.lower() == arg.lower() and d.dec == 0 and d.init), None)
        if const is None:
            raise RpgleError(f"dim({arg}): use a whole number or a numeric dcl-c declared earlier")
        n = int(float(const.init))
    if n < 1:
        raise RpgleError(f"dim({arg}): an array needs at least one element")
    return n


def _make_dspec(visible: list[DSpec], rest: str, what: str) -> DSpec:
    """Builds the DSpec for `name type(params) keywords`, the shape shared
    by dcl-s and data structure subfields.
    """
    m = re.match(r"^(\w+)\s+(\w+)\s*(\(([^)]*)\))?\s*(.*)$", rest, re.DOTALL)
    if not m:
        raise RpgleError(f"can't read declaration: {what} {rest}")
    name, type_name, _, params_s, tail = m.groups()
    tail_l = tail.lower()
    dim = _parse_dim(visible, tail)
    if re.search(r"\b(pos|overlay|samepos)\s*\(", tail_l):
        raise RpgleError("POS/OVERLAY are not supported: subfields don't share storage")

    if type_name.lower() == "like":
        src = (params_s or "").strip()
        base = next((d for d in visible if d.name.lower() == src.lower()), None)
        if base is None:
            raise RpgleError(f"LIKE({src}): only fields declared earlier with dcl-s can be used")
        length, dec, kind = base.length, base.dec, base.kind
    else:
        params = [p.strip() for p in (params_s or "").split(":")] if params_s else []
        length, dec, kind = _parse_type(type_name, params)

    init: Optional[str] = None
    mi = re.search(r"\binz\b\s*(\((.*)\))?", tail, re.IGNORECASE | re.DOTALL)
    if mi:
        arg = (mi.group(2) or "").strip()
        low = arg.lower()
        if low in ("*blank", "*blanks"):
            init = ""
        elif low in ("*zero", "*zeros"):
            init = "0"
        elif low == "*on":
            init = "1"
        elif low == "*off":
            init = "0"
        elif arg:
            init = arg
    if kind == "ind" and init is None:
        init = "0"
    return DSpec(name=name, deftype="S", length=length, dec=dec, init=init, kind=kind, dim=dim)


def _parse_dcl_s(scope, visible: list[DSpec], rest: str) -> None:
    scope.dspecs.append(_make_dspec(visible, rest, "dcl-s"))


def _parse_dcl_ds(scope, visible: list[DSpec], rest: str) -> Optional[DsSpec]:
    """Starts a data structure. Returns it if it still needs subfields
    (and an end-ds), or None if its layout is borrowed (likeds, likerec,
    extname) and it's already complete.
    """
    tokens = [t for t in split_top(rest, " ") if t]
    if not tokens:
        raise RpgleError("dcl-ds needs a name")
    ds = DsSpec(name=tokens[0])
    for tok in tokens[1:]:
        low = tok.lower()
        arg = tok[tok.index("(") + 1 : tok.rindex(")")].strip() if "(" in tok and tok.endswith(")") else ""
        head = low.split("(")[0]
        if head == "qualified":
            ds.qualified = True
        elif head in ("inz", "template"):
            pass
        elif head == "likeds":
            ds.likeds = arg
        elif head == "likerec":
            ds.likerec = split_top(arg, ":")[0]
        elif head == "extname":
            ds.extname = split_top(arg, ":")[0].strip("'")
        elif head == "dim":
            ds.dim = _parse_dim(visible, tok)
        else:
            raise RpgleError(f"dcl-ds {ds.name}: {tok} is not supported yet")
    if (ds.likeds or ds.likerec or ds.extname) and not ds.qualified and ds.likeds:
        ds.qualified = True  # a likeds copy is always accessed qualified
    if ds.dim and not ds.qualified:
        raise RpgleError(f"data structure {ds.name}: an array of data structures must be qualified")
    if any(d.name.lower() == ds.name.lower() for d in scope.dss):
        raise RpgleError(f"data structure {ds.name} is declared twice")
    scope.dss.append(ds)
    return None if (ds.likeds or ds.likerec or ds.extname) else ds


def _parse_dcl_c(scope, rest: str) -> None:
    m = re.match(r"^(\w+)\s+(?:const\s*\((.*)\)|(.*))$", rest, re.IGNORECASE | re.DOTALL)
    if not m:
        raise RpgleError(f"can't read declaration: dcl-c {rest}")
    name = m.group(1)
    value = (m.group(2) if m.group(2) is not None else m.group(3)).strip()
    if value.startswith("'"):
        text = value[1:-1].replace("''", "'")
        scope.dspecs.append(DSpec(name=name, deftype="S", length=len(text), dec=None, init=text))
    elif re.match(r"^-?\d+(\.\d+)?$", value):
        digits = value.lstrip("-").replace(".", "")
        dec = len(value.split(".")[1]) if "." in value else 0
        scope.dspecs.append(DSpec(name=name, deftype="S", length=len(digits), dec=dec, init=value))
    else:
        raise RpgleError(f"dcl-c {name}: only literal constants are supported")


def _parse_dcl_f(prog: Program, rest: str) -> None:
    tokens = split_top(rest.replace("\t", " "), " ")
    tokens = [t for t in tokens if t]
    name, device, usage, keyed = tokens[0], None, [], False
    for tok in tokens[1:]:
        low = tok.lower()
        if low in ("disk", "workstn", "printer"):
            device = low.upper()
        elif low == "keyed":
            keyed = True
        elif low.startswith("usage("):
            usage = [u.strip().lower().lstrip("*") for u in tok[6:-1].split(":")]
        else:
            raise RpgleError(f"dcl-f {name}: {tok} is not supported yet")
    device = device or "DISK"
    if device == "WORKSTN":
        ftype = "C"
    elif "update" in usage or "delete" in usage:
        ftype = "U"
    elif "output" in usage and "input" in usage:
        ftype = "U"
    elif "output" in usage:
        ftype = "O"
    else:
        ftype = "I"
    fs = FileSpec(
        name=name,
        type=ftype,
        designation="F",
        format="E",
        device=device,
        addition="A" if "output" in usage and device == "DISK" else None,
        keyed=keyed,
    )
    prog.files.append(fs)
    if device == "WORKSTN" and not prog.file.name:
        prog.file = fs


class _State:
    """What the statement parser carries from one statement to the next."""

    def __init__(self) -> None:
        self.prog = Program()
        self.open_ds: Optional[DsSpec] = None  # between dcl-ds and end-ds
        self.proc: Optional[ProcSpec] = None  # between dcl-proc and end-proc
        self.in_pi = False  # between dcl-pi and end-pi
        self.in_pr = False  # between dcl-pr and end-pr (prototypes are skipped)

    @property
    def scope(self):
        """Where declarations and statements go: the open procedure, else the program."""
        return self.proc if self.proc is not None else self.prog

    def visible_dspecs(self) -> list[DSpec]:
        return [*(self.proc.dspecs if self.proc else []), *self.prog.dspecs]


def _statement(state: _State, text: str, line: int) -> None:
    prog = state.prog
    scope = state.scope
    m = _WORD.match(text)
    word = m.group(0).lower() if m else ""
    rest = text[m.end() :] if m else text

    if state.in_pr:  # prototypes describe procedures defined elsewhere; nothing to run
        if word == "end-pr":
            state.in_pr = False
        return
    if state.in_pi:
        if word == "end-pi":
            state.in_pi = False
        else:
            _add_param(state, text)
        return
    if state.open_ds is not None:
        if word == "end-ds":
            state.open_ds = None
        elif word.startswith("dcl-"):
            raise RpgleError(f"{word} found inside data structure {state.open_ds.name}; is its end-ds missing?")
        else:
            sub = _make_dspec(state.visible_dspecs(), text, "subfield")
            if sub.dim:
                raise RpgleError(f"{sub.name}: arrays inside data structures are not supported yet")
            state.open_ds.subfields.append(sub)
        return
    if word == "end-ds":
        return  # closes a DS whose layout was borrowed; nothing to do

    def add(op: str, **kw) -> None:
        scope.statements.append(Statement(op=op, line=line, **kw))

    # Opcode extenders, e.g. chain(n) or read(e): attached with no space.
    # Only letters from E/N/P/H count, so chain(custno) is still a key.
    if word == "sorta":
        sm = re.match(r"^\(\s*([adAD])\s*\)", rest)
        down = bool(sm) and sm.group(1).lower() == "d"
        name = rest[sm.end() :].strip() if sm else rest.strip()
        if not re.match(r"^[A-Za-z_]\w*$", name):
            raise RpgleError("sorta needs an array name: sorta arr;  or  sorta(d) arr;")
        return add("SORTA", expr=name, down=down)
    if word in _KEYED_OPS | _FILE_ONLY_OPS | {"delete", "callp"} and rest.startswith("("):
        em = re.match(r"^\(([a-zA-Z ]*)\)", rest)
        letters = set(em.group(1).lower().replace(" ", "")) if em else set()
        if em and letters and letters <= set("enph"):
            if "e" in letters:
                raise RpgleError(f"the (E) extender on {word} needs %ERROR, which is not supported yet")
            rest = rest[em.end() :]  # N, P and H (locking hints) don't matter here
    rest = rest.strip()

    if word in _UNSUPPORTED:
        raise RpgleError(f"{word}: {_UNSUPPORTED[word]}")
    if word == "ctl-opt":
        return
    if word == "dcl-proc":
        return _open_proc(state, rest.strip())
    if word == "end-proc":
        if state.proc is None:
            raise RpgleError("end-proc with no dcl-proc")
        prog.procs.append(state.proc)
        state.proc = None
        return
    if word == "dcl-pi":
        return _open_pi(state, rest.strip())
    if word == "dcl-pr":
        state.in_pr = True
        return
    if word == "dcl-s":
        return _parse_dcl_s(scope, state.visible_dspecs(), rest)
    if word == "dcl-ds":
        state.open_ds = _parse_dcl_ds(scope, state.visible_dspecs(), rest.strip())
        return
    if word == "clear":
        if not rest:
            raise RpgleError("clear needs a variable or data structure")
        return add("CLEAR", expr=rest)
    if word == "dcl-c":
        return _parse_dcl_c(scope, rest)
    if word == "dcl-f":
        if state.proc is not None:
            raise RpgleError("dcl-f inside a procedure is not supported: declare files at the top of the program")
        return _parse_dcl_f(prog, rest)

    if word in ("if", "elseif", "dow", "dou", "when"):
        if not rest:
            raise RpgleError(f"{word} needs a condition")
        return add(word.upper(), expr=rest)
    if word in _SIMPLE:
        if rest:
            raise RpgleError(f"unexpected text after {word}: {rest!r}")
        return add(word.upper())
    if word == "for":
        fm = _FOR.match(rest)
        if not fm:
            raise RpgleError("for needs: for i = start to|downto limit [by step]")
        return add(
            "FOR",
            factor1=fm.group("var"),
            expr=fm.group("start"),
            limit=fm.group("limit"),
            step=fm.group("by"),
            down=fm.group("dir").lower() == "downto",
        )
    if word == "begsr":
        return add("BEGSR", factor1=rest)
    if word == "exsr":
        return add("EXSR", expr=rest)
    if word == "return":
        return add("RETURN", expr=rest or None)
    if word == "exfmt":
        return add("EXFMT", expr=rest)

    declared = {f.name.lower() for f in prog.files}
    if word in _KEYED_OPS:
        km = re.match(r"^(.*\S)\s+(\w+)\s+(\w+)$", rest, re.DOTALL)
        if km and km.group(2).lower() in declared:  # key file datastructure
            return add(word.upper(), factor1=km.group(1).strip(), factor2=km.group(2), ds=km.group(3))
        km = re.match(r"^(.*\S)\s+([A-Za-z_]\w*)$", rest, re.DOTALL)
        if not km:
            raise RpgleError(f"{word} needs a key and a file name")
        return add(word.upper(), factor1=km.group(1).strip(), factor2=km.group(2))
    if word == "delete":
        km = re.match(r"^(?:(.*\S)\s+)?([A-Za-z_]\w*)$", rest, re.DOTALL)
        if not km:
            raise RpgleError("delete needs a file name")
        return add("DELETE", factor1=km.group(1), factor2=km.group(2))
    if word in _FILE_ONLY_OPS:
        fm = re.match(r"^([A-Za-z_]\w*)(?:\s+([A-Za-z_]\w*))?$", rest)
        if not fm:
            raise RpgleError(f"{word} takes a file or record format name, then optionally a data structure; got {rest!r}")
        return add(word.upper(), factor2=fm.group(1), ds=fm.group(2))

    if word == "callp":
        return _add_call(add, rest)
    body = rest if word == "eval" else text
    found = _find_assignment(body)
    if found is None:
        if re.match(r"^[A-Za-z_]\w*\s*\(.*\)$", text, re.DOTALL):
            return _add_call(add, text)  # a bare call: name(args);
        raise RpgleError(f"unrecognized statement: {text!r}")
    target, op, value = found
    expr = f"{target} = {value}" if not op else f"{target} = {target} {op} ({value})"
    add("EVAL", expr=expr)


def _add_call(add, text: str) -> None:
    cm = re.match(r"^\s*([A-Za-z_]\w*)\s*\((.*)\)\s*$", text, re.DOTALL)
    if not cm:
        raise RpgleError(f"a procedure call looks like name(arg : arg); got {text!r}")
    add("CALLP", expr=f"{cm.group(1)}({cm.group(2)})")


def _open_proc(state: _State, rest: str) -> None:
    if state.proc is not None:
        raise RpgleError(f"dcl-proc inside procedure {state.proc.name}; is its end-proc missing?")
    words = rest.split()
    if not words:
        raise RpgleError("dcl-proc needs a name")
    extra = [w.lower() for w in words[1:]]
    if any(w != "export" for w in extra):
        raise RpgleError(f"dcl-proc {words[0]}: {' '.join(extra)} is not supported")
    if any(p.name.lower() == words[0].lower() for p in state.prog.procs):
        raise RpgleError(f"procedure {words[0]} is declared twice")
    state.proc = ProcSpec(name=words[0], export="export" in extra)


def _open_pi(state: _State, rest: str) -> None:
    if state.proc is None:
        raise RpgleError("dcl-pi outside a procedure (parameters of the main program are not supported)")
    if state.proc.params or state.proc.returns:
        raise RpgleError(f"procedure {state.proc.name} has two dcl-pi blocks")
    parts = rest.split(None, 1)
    if not parts:
        raise RpgleError("dcl-pi needs a name (or *n)")
    if len(parts) == 2 and parts[1].strip():
        ret = _make_dspec(state.visible_dspecs(), f"_return {parts[1]}", "return type")
        state.proc.returns = ret
    state.in_pi = True


def _add_param(state: _State, text: str) -> None:
    proc = state.proc
    spec = _make_dspec(state.visible_dspecs(), text, "parameter")
    low = text.lower()
    mode = "value" if re.search(r"\bvalue\b", low) else "const" if re.search(r"\bconst\b", low) else "ref"
    nopass = False
    om = re.search(r"\boptions\s*\(([^)]*)\)", low)
    if om:
        opts = {o.strip() for o in om.group(1).split(":")}
        if opts - {"*nopass"}:
            raise RpgleError(f"parameter {spec.name}: options({', '.join(sorted(opts - {'*nopass'}))}) is not supported")
        nopass = True
    if any(p.spec.name.lower() == spec.name.lower() for p in proc.params):
        raise RpgleError(f"parameter {spec.name} is declared twice")
    proc.params.append(ParamSpec(spec=spec, mode=mode, nopass=nopass))


def parse_free(source: str) -> Program:
    """Parses free-form RPG IV source (starting with **FREE)."""
    state = _State()
    for text, line in _split(source):
        try:
            _statement(state, text, line)
        except RpgleError as e:
            if str(e).startswith("line "):
                raise
            raise RpgleError(f"line {line}: {e}") from None
    if state.open_ds is not None:
        raise RpgleError(f"data structure {state.open_ds.name} is missing its end-ds")
    if state.in_pi or state.in_pr:  # the innermost unclosed block is the likely culprit
        raise RpgleError("a dcl-pi or dcl-pr is missing its end-pi or end-pr")
    if state.proc is not None:
        raise RpgleError(f"procedure {state.proc.name} is missing its end-proc")
    return state.prog
