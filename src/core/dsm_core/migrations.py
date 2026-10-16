"""Миграции документа между версиями схемы.

``MIGRATIONS`` — список ``(from_version, fn)``: ``fn(doc) -> doc`` поднимает документ на одну версию.
Пока схема одна (версия 1), список пуст.
"""
from __future__ import annotations

from typing import Callable

from .errors import PatchError
from .schema import SCHEMA_VERSION

MIGRATIONS: list[tuple[int, Callable[[dict], dict]]] = []


def migrate(doc: dict) -> tuple[dict, list[str]]:
    """Поднять документ до ``SCHEMA_VERSION``. Новее текущей — ``PatchError``."""
    notes: list[str] = []
    ver = doc.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        raise PatchError("schema_version: отсутствует или не целое")
    if ver > SCHEMA_VERSION:
        raise PatchError(f"schema_version {ver} новее поддерживаемой {SCHEMA_VERSION}: обновите ядро")
    steps = {v: fn for v, fn in MIGRATIONS}
    while ver < SCHEMA_VERSION:
        fn = steps.get(ver)
        if fn is None:
            raise PatchError(f"нет миграции с версии {ver}")
        doc = fn(doc)
        notes.append(f"миграция схемы {ver} → {ver + 1}")
        ver += 1
        doc["schema_version"] = ver
    return doc, notes
