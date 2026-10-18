"""Расширение компонента dsm (pixel-engine): загрузка патча, таблицы, статус, колбэки.

Единственный модуль слоя TD с TD-API (``project``, ``op`` — глобалы TouchDesigner, импорт не нужен).
Состояние — в атрибутах экземпляра, не в op.store. Задача 1.2 — каркас: поиск ядра и статус;
загрузка патча — задача 3.1.
"""
from __future__ import annotations

import os
import sys
from typing import Literal

Status = Literal["no patch", "ok", "error"]


def _repo_root() -> str:
    """Корень репозитория относительно папки проекта (``td/dsm_dev.toe`` → родитель ``td``).

    В чужом проекте компонент лежит рядом с ядром только если хозяин сам положил ``src/core`` в ``sys.path``;
    тогда поиск не нужен.
    """
    folder = project.folder  # noqa: F821 — глобал TouchDesigner
    return os.path.dirname(folder.rstrip("/\\"))


def ensure_core_on_path() -> None:
    """Делает ``dsm_core`` и ``dsm_geometry`` импортируемыми: относительно папки проекта, без абсолютных путей."""
    try:
        import dsm_core  # noqa: F401
        import dsm_geometry  # noqa: F401
        return
    except ImportError:
        pass
    root = _repo_root()
    for sub in ("src/core", "src/td"):
        path = os.path.join(root, *sub.split("/"))
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


class DsmEngineExt:
    """Публичные методы (с заглавной — промоутятся на COMP): Reload, LoadPatch, ReportError, UpdateCounters."""

    def __init__(self, ownerComp) -> None:
        self.ownerComp = ownerComp
        self.patch = None
        self.issues: list = []
        self.status: Status = "no patch"
        self.message: str = ""
        ensure_core_on_path()
        from dsm_core import api  # после ensure_core_on_path
        self.api = api

    # --- задача 3.1 ---
    def Reload(self) -> bool:
        raise NotImplementedError("задача 3.1")

    def LoadPatch(self, path: str) -> bool:
        raise NotImplementedError("задача 3.1")

    def ReportError(self, message: str) -> None:
        raise NotImplementedError("задача 3.1")

    # --- задача 5.1 ---
    def UpdateCounters(self) -> None:
        raise NotImplementedError("задача 5.1")
