"""Shared fixture loading for the test suite -- mirrors the
readAdventureDDS/readAdventureRPGLE test helpers duplicated across the
Go port's dds_test.go/rpgle_test.go.
"""

from __future__ import annotations

import pathlib

_PROGRAMS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "programs"


def read_adventure_dds() -> str:
    return (_PROGRAMS_DIR / "adventure.dspf").read_text()


def read_adventure_rpgle() -> str:
    return (_PROGRAMS_DIR / "adventure.rpgle").read_text()
