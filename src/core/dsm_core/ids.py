"""Генерация идентификаторов: префикс раздела + (максимальный номер + 1)."""
from __future__ import annotations

import re
from typing import Iterable

PREFIX_FIXTURE_TYPE = "ft"
PREFIX_FIXTURE = "fx"
PREFIX_OUTPUT = "out"
PREFIX_PORT = "port"
PREFIX_GROUP = "grp"


def new_id(existing: Iterable[str], prefix: str) -> str:
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    top = 0
    for s in existing:
        m = pat.match(s)
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}{top + 1}"


def new_ids(existing: Iterable[str], prefix: str, count: int) -> list[str]:
    taken = set(existing)
    out: list[str] = []
    for _ in range(count):
        nid = new_id(taken, prefix)
        taken.add(nid)
        out.append(nid)
    return out
