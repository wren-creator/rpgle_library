"""
loader.py
─────────────────────────────────────────────────────────────────
Finds, parses and caches the programs a running program CALLs, and
the files that go with them (display file, physical-file DDS).

A program name maps to a source file by lookup, not registration:
`call 'CUSTNAME'` (or a prototype with extpgm('CUSTNAME')) looks for
custname.rpgle, then .rpg and .sqlrpgle, as written, in lower case and
in upper case, first in the folder of the program making the call and
then in each of the loader's folders (the main program's folder, then
any -I folders). A library prefix, 'MYLIB/CUSTNAME', is accepted and
ignored: there are no libraries here, only folders.

A name is an IBM i object name (letters, digits, _ $ # @), never a path,
so a call can't reach outside the folders it searches.
─────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .dds import DDS, parse_dds
from .errors import RpgleError
from .pf import PhysicalFile, parse_pf
from .rpgle import Program, parse_rpgle

_NAME = re.compile(r"^[A-Za-z_$#@][A-Za-z0-9_$#@]*$")
_PROGRAM_EXTENSIONS = (".rpgle", ".rpg", ".sqlrpgle")


class CallError(RpgleError):
    """A CALL that couldn't be made: program not found, parameters that
    don't fit, a program calling itself. A CALL's error indicator
    (columns 73-74) catches these, and only these: an error raised while
    the called program runs is that program's, and propagates.
    """


def program_key(target: str) -> str:
    """'MYLIB/custname ' -> 'CUSTNAME'. Raises CallError for anything that
    isn't an object name, including anything that looks like a path.
    """
    name = target.strip().strip("'\"").strip().replace("\\", "/")
    name = name.rsplit("/", 1)[-1] if "/" in name else name
    if not _NAME.match(name):
        raise CallError(f"{target.strip()!r} is not a program name")
    return name.upper()


def find_pf(name: str, directories: Iterable[Path]) -> Path:
    dirs = list(directories)
    for d in dirs:
        for candidate in (name.lower(), name.upper(), name):
            p = d / f"{candidate}.pf"
            if p.exists():
                return p
    raise RpgleError(f"No physical file DDS for {name}: expected {dirs[0] / (name.lower() + '.pf')}")


def load_pfs(prog: Program, directories: Iterable[Path]) -> dict[str, PhysicalFile]:
    """The physical-file DDS for every DISK file the program declares,
    found as <file>.pf in the first of directories that has it.
    """
    dirs = list(directories)
    return {
        fs.name.upper(): parse_pf(fs.name, find_pf(fs.name, dirs).read_text())
        for fs in prog.files
        if (fs.device or "").upper() == "DISK"
    }


def find_dds(prog: Program, path: Path, directories: Iterable[Path]) -> DDS:
    """The display file for a program: <workstn file>.dspf, else
    <program name>.dspf, in the program's folder or directories.
    """
    names = [prog.file.name] if prog.file.name else []
    names.append(path.stem)
    for d in [path.parent, *directories]:
        for n in names:
            for candidate in (n.lower(), n.upper(), n):
                p = d / f"{candidate}.dspf"
                if p.exists():
                    return parse_dds(p.read_text())
    return parse_dds("")


@dataclass
class LoadedProgram:
    key: str
    path: Path
    prog: Program
    dds: DDS
    pfs: dict[str, PhysicalFile]


class ProgramLoader:
    def __init__(
        self,
        dirs: Iterable[Path],
        include_dirs: Iterable[Path] = (),
        defines: Iterable[str] = (),
        pf_dirs: Iterable[Path] = (),
        main: Optional[Path] = None,
    ):
        self.dirs = [Path(d).resolve() for d in dirs]
        self.include_dirs = [Path(d) for d in include_dirs]
        self.defines = list(defines)
        self.pf_dirs = [Path(d) for d in pf_dirs]
        # The main program's own name, so calling it by name is caught as
        # recursion. A file name that isn't an object name (custinq-copy)
        # can't be called anyway, so it just has no name.
        self.main_name: Optional[str] = None
        if main is not None:
            try:
                self.main_name = program_key(main.stem)
            except CallError:
                pass
        self._cache: dict[Path, LoadedProgram] = {}

    def find(self, key: str, caller_dir: Optional[Path]) -> Path:
        search = [d for d in ([caller_dir] if caller_dir else []) + self.dirs]
        seen: list[Path] = []
        for d in search:
            if d in seen:
                continue
            seen.append(d)
            for variant in dict.fromkeys((key.lower(), key)):
                for ext in _PROGRAM_EXTENSIONS:
                    p = d / (variant + ext)
                    if p.is_file():
                        return p.resolve()
        raise CallError(f"program {key} not found (looked for {key.lower()}.rpgle in {', '.join(str(d) for d in seen)})")

    def load(self, key: str, caller_dir: Optional[Path]) -> LoadedProgram:
        path = self.find(key, caller_dir)
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            prog = parse_rpgle(
                path.read_text(encoding="utf-8", errors="replace"),
                path=path,
                include_dirs=[path.parent, *self.include_dirs],
                defines=self.defines,
            )
        except RpgleError as e:
            # The parser says "line 3: ..."; from inside another program that
            # needs to say which file.
            msg = str(e)
            m = re.match(r"^line (\d+): ", msg)
            raise RpgleError(f"{path.name}:{m.group(1)}: {msg[m.end():]}" if m else f"{path.name}: {msg}") from None
        dirs = [path.parent, *self.dirs, *self.pf_dirs]
        loaded = LoadedProgram(
            key=key,
            path=path,
            prog=prog,
            dds=find_dds(prog, path, self.dirs),
            pfs=load_pfs(prog, dirs),
        )
        self._cache[path] = loaded
        return loaded
