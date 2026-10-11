"""
include.py
─────────────────────────────────────────────────────────────────
The source preprocessor: /COPY and /INCLUDE, plus the conditional
directives copybooks use to guard against being included twice.

On IBM i a copy member is named `library/file,member`. Here that maps to
paths on disk, searched in this order in the including file's folder,
then each include folder (-I):

  /copy custdefs               custdefs
  /copy qrpglesrc,custdefs     qrpglesrc/custdefs, then custdefs
  /copy lib/qrpglesrc,custdefs lib/qrpglesrc/custdefs, qrpglesrc/custdefs,
                               then custdefs
  /copy 'inc/custdefs.rpgleinc' that exact path

Each candidate is tried as written, in lower case and in upper case, with
no extension and with .rpgleinc .rpgle .copy .inc .rpg .sqlrpgle.

Directives handled (case-insensitive):
  /COPY, /INCLUDE   insert another file
  /DEFINE name, /UNDEFINE name
  /IF DEFINED(name), /IF NOT DEFINED(name), /ELSEIF ..., /ELSE, /ENDIF
  /EOF              stop reading this file (the standard include guard:
                    /IF DEFINED(X) / EOF / ENDIF / DEFINE X)

Other /IF conditions (*VERSION, *CRTBNDRPG ...) are an error. Other
slash lines (/FREE, /END-FREE, /EJECT, /SPACE, /TITLE) pass through.

An include may only resolve inside the program's own folder or an
include folder, so running someone else's source can't make it read
arbitrary files. Includes nest up to 32 deep; a file including itself,
directly or not, is an error.

The result is one merged source text plus a map from each of its lines
back to the file and line it came from, so an error in a copybook says
`custdefs.rpgleinc:3`, not a line number in text nobody wrote.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from .errors import RpgleError

_DIRECTIVE = re.compile(r"^\s*/([A-Za-z][A-Za-z-]*)\b\s*(.*)$")
_HANDLED = {"copy", "include", "define", "undefine", "if", "elseif", "else", "endif", "eof"}
_DETECT = re.compile(r"^\s*/(copy|include|define|undefine|if|elseif|else|endif|eof)\b", re.IGNORECASE | re.MULTILINE)
_COND = re.compile(r"^(NOT\s+)?DEFINED\s*\(\s*([A-Za-z_][\w]*)\s*\)$", re.IGNORECASE)
_EXTENSIONS = ("", ".rpgleinc", ".rpgle", ".copy", ".inc", ".rpg", ".sqlrpgle")
_MAX_DEPTH = 32

Origin = tuple[str, int]  # (file label, 1-based line)


def has_directives(source: str) -> bool:
    return _DETECT.search(source) is not None


class _Frame:
    """One open /IF ... /ENDIF."""

    def __init__(self, parent_active: bool):
        self.parent_active = parent_active
        self.taken = False  # has some branch of this /IF already been used?
        self.active = False


class _Expander:
    def __init__(self, roots: list[Path], include_dirs: list[Path], defines: Iterable[str]):
        self.roots = roots  # an include may resolve anywhere under one of these
        self.include_dirs = include_dirs
        self.defines = {d.upper() for d in defines}
        self.lines: list[str] = []
        self.origin: list[Origin] = []
        self.stack: list[Path] = []  # files being read, for cycle detection

    def expand(self, text: str, label: str, here: Optional[Path], depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            raise RpgleError(f"{label}: /COPY nested more than {_MAX_DEPTH} deep")
        frames: list[_Frame] = []

        def active() -> bool:
            return frames[-1].active if frames else True

        hit_eof = False
        for n, raw in enumerate(text.split("\n"), start=1):
            line = raw.rstrip("\r")
            if n == 1 and depth > 0 and line.strip().upper().startswith("**FREE"):
                continue  # a copy member can't change the source's form; drop its marker
            m = _DIRECTIVE.match(line)
            word = m.group(1).lower() if m and m.group(1).lower() in _HANDLED else None
            where = f"{label}:{n}"

            if word == "if":
                parent = active()
                f = _Frame(parent)
                f.active = parent and self._condition(m.group(2), where)
                f.taken = f.active
                frames.append(f)
            elif word == "elseif":
                f = self._top(frames, "/ELSEIF", where)
                if f.taken or not f.parent_active:
                    f.active = False
                else:
                    f.active = self._condition(m.group(2), where)
                    f.taken = f.active
            elif word == "else":
                f = self._top(frames, "/ELSE", where)
                f.active = f.parent_active and not f.taken
                f.taken = True
            elif word == "endif":
                self._top(frames, "/ENDIF", where)
                frames.pop()
            elif not active():
                continue  # inside a branch that isn't taken: not even directives count
            elif word == "eof":
                hit_eof = True  # ends the member even inside an open /IF: that is the include guard
                break
            elif word == "define":
                self.defines.add(self._name(m.group(2), "/DEFINE", where))
            elif word == "undefine":
                self.defines.discard(self._name(m.group(2), "/UNDEFINE", where))
            elif word in ("copy", "include"):
                self._include(m.group(2), here, where, depth)
            else:
                self.lines.append(line)
                self.origin.append((label, n))
        if frames and not hit_eof:
            raise RpgleError(f"{label}: /IF with no /ENDIF")

    # ── directives ───────────────────────────────────────────────

    @staticmethod
    def _top(frames: list[_Frame], what: str, where: str) -> _Frame:
        if not frames:
            raise RpgleError(f"{where}: {what} with no /IF")
        return frames[-1]

    @staticmethod
    def _name(arg: str, what: str, where: str) -> str:
        name = arg.strip().split()[0] if arg.strip() else ""
        if not re.match(r"^[A-Za-z_]\w*$", name):
            raise RpgleError(f"{where}: {what} needs a name")
        return name.upper()

    def _condition(self, arg: str, where: str) -> bool:
        m = _COND.match(arg.strip())
        if not m:
            raise RpgleError(
                f"{where}: only /IF DEFINED(name) and /IF NOT DEFINED(name) are supported, not {arg.strip()!r}"
            )
        is_defined = m.group(2).upper() in self.defines
        return (not is_defined) if m.group(1) else is_defined

    def _include(self, arg: str, here: Optional[Path], where: str, depth: int) -> None:
        spec = arg.strip()
        if not spec:
            raise RpgleError(f"{where}: /COPY needs a member name")
        quoted = re.match(r"^(['\"])(.+?)\1", spec)
        spec = quoted.group(2) if quoted else spec.split()[0]
        if any(ord(c) < 32 for c in spec):
            raise RpgleError(f"{where}: /COPY member name has a control character in it")
        found = self._find(spec, bool(quoted), here)
        if found is None:
            tried = ", ".join(str(d) for d in self._search_dirs(here))
            raise RpgleError(f"{where}: can't find /COPY member {spec!r} (searched {tried})")
        if found in self.stack:
            chain = " -> ".join(p.name for p in [*self.stack, found])
            raise RpgleError(f"{where}: /COPY cycle: {chain}")
        try:
            text = found.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise RpgleError(f"{where}: can't read {found}: {e.strerror or e}") from None
        self.stack.append(found)
        try:
            self.expand(text, found.name, found.parent, depth + 1)
        finally:
            self.stack.pop()

    # ── finding members ──────────────────────────────────────────

    def _search_dirs(self, here: Optional[Path]) -> list[Path]:
        dirs = [here] if here is not None else []
        dirs += self.include_dirs
        seen, out = set(), []
        for d in dirs:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def _candidates(self, spec: str, quoted: bool) -> list[str]:
        spec = spec.replace("\\", "/")
        if quoted:
            return [spec]
        if "," in spec:
            lhs, member = spec.rsplit(",", 1)
            parts = [p for p in lhs.split("/") if p]
            bases = ["/".join([*parts[i:], member]) for i in range(len(parts))] + [member]
        else:
            bases = [spec]
        return bases

    def _find(self, spec: str, quoted: bool, here: Optional[Path]) -> Optional[Path]:
        for directory in self._search_dirs(here):
            for base in self._candidates(spec, quoted):
                for variant in dict.fromkeys((base, base.lower(), base.upper())):
                    for ext in ([""] if quoted else _EXTENSIONS):
                        try:
                            p = (directory / (variant + ext)).resolve()
                            if not p.is_file():
                                continue
                        except (OSError, ValueError):
                            continue  # a name the filesystem won't take (too long, odd bytes) is just not found
                        if not any(p == r or r in p.parents for r in self.roots):
                            raise RpgleError(
                                f"/COPY {spec!r} resolves to {p}, outside the program's folder and the "
                                "include folders (-I); refusing to read it"
                            )
                        return p
        return None


def expand_includes(
    source: str,
    path: Optional[Path] = None,
    include_dirs: Optional[Iterable[Path]] = None,
    defines: Iterable[str] = (),
) -> tuple[str, list[Origin]]:
    """Expands /COPY and the conditional directives. Returns the merged
    source and, for each of its lines, (file, line) it came from.
    path is the file the source was read from (None for a bare string,
    in which case includes resolve against the include folders and the
    current directory).
    """
    here = path.resolve().parent if path is not None else Path.cwd().resolve()
    dirs = [Path(d).resolve() for d in (include_dirs or [])]
    ex = _Expander(roots=[here, *dirs], include_dirs=dirs, defines=defines)
    if path is not None:
        ex.stack.append(path.resolve())
    ex.expand(source, path.name if path is not None else "<source>", here)
    return "\n".join(ex.lines), ex.origin


_LINE_PREFIX = re.compile(r"^line (\d+): ")


def remap_error(message: str, origin: list[Origin]) -> str:
    """Rewrites a parser's 'line N: ...' to the file and line that N came from."""
    m = _LINE_PREFIX.match(message)
    if m and 1 <= int(m.group(1)) <= len(origin):
        label, n = origin[int(m.group(1)) - 1]
        return f"{label}:{n}: {message[m.end():]}"
    return message
