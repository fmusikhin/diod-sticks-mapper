"""Загрузка и сохранение патча. Запись детерминирована: одинаковый патч → байт в байт одинаковый файл."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .migrations import migrate
from .model import Canvas, Patch
from .schema import SCHEMA_VERSION, from_dict, to_dict


def new_patch() -> Patch:
    """Пустой патч: холст 1280×720 px, без выходов и фикстур."""
    return Patch(schema_version=SCHEMA_VERSION, canvas=Canvas())


def _extra_notes(patch: Patch) -> list[str]:
    notes = []
    if patch.extra:
        notes.append(f"неизвестные поля документа сохранены: {', '.join(sorted(patch.extra))}")
    for sect, items in (("fixture_types", patch.fixture_types), ("fixtures", patch.fixtures),
                        ("outputs", patch.outputs), ("groups", patch.groups)):
        for it in items:
            if it.extra:
                notes.append(f"{sect}/{it.id}: неизвестные поля сохранены: {', '.join(sorted(it.extra))}")
    return notes


def loads(text: str) -> tuple[Patch, list[str]]:
    """Текст JSON → (патч, заметки: миграции и неизвестные поля). Ошибки схемы — ``SchemaError``."""
    doc = json.loads(text)
    doc, notes = migrate(doc) if isinstance(doc, dict) else (doc, [])
    patch = from_dict(doc)
    return patch, notes + _extra_notes(patch)


def dumps(patch: Patch) -> str:
    return json.dumps(to_dict(patch), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load(path: str | Path) -> tuple[Patch, list[str]]:
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def save(patch: Patch, path: str | Path) -> None:
    """UTF-8 без BOM, LF; запись во временный файл и замена, чтобы не испортить патч при сбое."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(patch))
    os.replace(tmp, p)
