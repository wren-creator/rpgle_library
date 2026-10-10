"""Builds fixed-form RPG lines by column, so tests don't hide a
misplaced space inside a hand-counted string literal.
"""


def _put(buf: list, start: int, text: str) -> None:
    """Writes text at 1-based column start."""
    for i, ch in enumerate(text):
        idx = start - 1 + i
        while len(buf) <= idx:
            buf.append(" ")
        buf[idx] = ch


def fspec(name, ftype="I", desig="F", addition="", fmt="E", keyed=False, device="DISK") -> str:
    b = [" "] * 5 + ["F"]
    _put(b, 7, name)
    _put(b, 17, ftype)
    _put(b, 18, desig)
    _put(b, 20, addition)
    _put(b, 22, fmt)
    if keyed:
        _put(b, 34, "K")
    _put(b, 36, device)
    return "".join(b).rstrip()


def cspec(opcode, factor1="", factor2="", result="", hi="", lo="", eq="", cond="") -> str:
    b = [" "] * 5 + ["C"]
    _put(b, 9, cond)
    _put(b, 12, factor1)
    _put(b, 26, opcode)
    _put(b, 36, factor2)
    _put(b, 50, result)
    _put(b, 71, hi)
    _put(b, 73, lo)
    _put(b, 75, eq)
    return "".join(b).rstrip()


def dds(*lines: str) -> str:
    """DDS lines: each is (kind, name, length, type, dec, keywords)
    tuples rendered by column. Use dds_line() for each entry.
    """
    return "\n".join(lines) + "\n"


def dds_line(kind="", name="", length="", dtype="", dec="", keywords="") -> str:
    b = [" "] * 5 + ["A"]
    _put(b, 17, kind)
    _put(b, 19, name)
    _put(b, 30, str(length).rjust(5) if length != "" else "")
    _put(b, 35, dtype)
    _put(b, 36, str(dec).rjust(2) if dec != "" else "")
    _put(b, 45, keywords)
    return "".join(b).rstrip()


def dspec(name, length, dec=None, init=None) -> str:
    b = [" "] * 5 + ["D"]
    _put(b, 7, name)
    _put(b, 24, "S")
    _put(b, 33, str(length).rjust(7))
    if dec is not None:
        _put(b, 41, str(dec).rjust(2))
    if init is not None:
        _put(b, 45, f"INZ({init})")
    return "".join(b).rstrip()
