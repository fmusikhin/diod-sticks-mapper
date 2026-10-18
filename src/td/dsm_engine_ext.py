"""Расширение компонента dsm (pixel-engine): загрузка патча, таблицы, статус, колбэки.

Единственный модуль слоя TD с TD-API (``project``, ``op``, ``tableDAT`` — глобалы TouchDesigner, импорт не нужен).
Состояние — в атрибутах экземпляра, не в op.store. Python исполняется только по событию (пульс, смена
файла, таймер счётчиков) — никогда в куке кадра.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Literal

Status = Literal["no patch", "ok", "error"]

TABLE_NAMES = ("geo_cells", "geo_prims", "geo_raw", "raw_prims", "fx_arrows", "profiles", "delay_groups", "out_groups",
               "info")
INFO_KEYS = ("status", "patch_path", "fixtures", "cells", "universes", "outputs", "errors", "warnings", "skipped",
             "pixels_layout", "message", "fps", "input")


def _repo_root() -> str:
    """Корень репозитория относительно папки проекта (``td/dsm_dev.toe`` → родитель ``td``).

    В чужом проекте ядро подключает хозяин (``src/core`` в ``sys.path``) — тогда поиск не нужен.
    """
    folder = project.folder  # noqa: F821 — глобал TouchDesigner
    return os.path.dirname(folder.rstrip("/\\"))


def ensure_core_on_path() -> None:
    """Делает ``dsm_core`` импортируемым относительно папки проекта, без абсолютных путей.

    Модуль геометрии берётся не из ``sys.path``, а из DAT ``geometry_mod`` (``.module``): TD сам
    перезагружает его при смене текста, поэтому правка ``src/td/dsm_geometry.py`` подхватывается без перезапуска.
    """
    try:
        import dsm_core  # noqa: F401
        return
    except ImportError:
        pass
    path = os.path.join(_repo_root(), "src", "core")
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


def rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(str(c) for c in r) for r in rows)


class DsmEngineExt:
    """Публичные методы (с заглавной — промоутятся на COMP): Reload, LoadPatch, ReportError, CheckFile,
    PatchDir, PatchName, ResolvePath, UpdateCounters."""

    def __init__(self, ownerComp) -> None:
        self.ownerComp = ownerComp
        self.patch = None
        self.issues: list = []
        self.status: Status = "no patch"
        self.message: str = ""
        self.patch_path: str = ""
        self.tables = None
        self.pixels_layout: str = "onerow"
        self.loaded_mtime: float | None = None
        ensure_core_on_path()
        from dsm_core import api  # после ensure_core_on_path
        self.api = api
        self.geometry = ownerComp.op("geometry_mod").module   # dsm_geometry из синхронизированного DAT
        self._set_status("no patch", "")
        if ownerComp.par.Patchfile.eval():        # проект открыт с заданным патчем — загрузить сразу
            self.Reload()

    # ------------------------------------------------------------------ загрузка
    def ResolvePath(self, path: str) -> str:
        """Относительный путь — от папки проекта; абсолютный — как есть."""
        path = (path or "").strip()
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(project.folder, path))  # noqa: F821

    def PatchDir(self) -> str:
        """Папка файла патча (для folderDAT автоперезагрузки); пусто — патч не задан."""
        full = self.ResolvePath(self.ownerComp.par.Patchfile.eval())
        return os.path.dirname(full) if full else ""

    def PatchName(self) -> str:
        """Имя файла патча без расширения — фильтр ``names`` у folderDAT сравнивает имя без расширения."""
        full = self.ResolvePath(self.ownerComp.par.Patchfile.eval())
        return os.path.splitext(os.path.basename(full))[0] if full else ""

    def CheckFile(self) -> bool:
        """Автоперезагрузка: файл патча изменился на диске (mtime) → Reload. True — перезагружали."""
        if not self.ownerComp.par.Autoreload.eval():
            return False
        full = self.ResolvePath(self.ownerComp.par.Patchfile.eval())
        if not full or not os.path.exists(full):
            return False
        mtime = os.path.getmtime(full)
        if self.loaded_mtime is not None and mtime == self.loaded_mtime:
            return False
        self.Reload()
        return True

    def Reload(self) -> bool:
        """Загрузить патч из пары Patchfile. True — успех. При ошибке таблицы не трогаются."""
        raw = self.ownerComp.par.Patchfile.eval()
        if not raw:
            self.patch, self.issues, self.tables, self.loaded_mtime = None, [], None, None
            self._set_status("no patch", "")
            self._write_info()
            return False
        return self.LoadPatch(raw)

    def LoadPatch(self, path: str) -> bool:
        """Загрузить патч по пути (пара Patchfile не меняется). True — успех."""
        api = self.api
        full = self.ResolvePath(path)
        try:
            patch, notes = api.load(full)
            patch, assign_issues = api.auto_assign_all(patch)
            issues = api.validate(patch)
            segments = api.segments_by_fixture(patch)
            routing = api.routing_table(patch)
            fps = float(self.ownerComp.time.rate)
            tables = self.geometry.build_tables(patch, issues, segments, routing, fps)
        except (OSError, ValueError, api.PatchError) as e:  # ValueError — битый JSON
            self.ReportError(self._error_text(e, full))
            return False
        except Exception as e:  # сбой расчёта геометрии — тоже в info и колбэк, а не молча в textport
            print(traceback.format_exc())
            self.ReportError(f"ошибка сборки геометрии: {type(e).__name__}: {e}")
            return False
        # всё посчитано — только теперь меняем состояние и таблицы
        self.patch, self.issues, self.tables, self.patch_path = patch, list(issues), tables, path
        self.loaded_mtime = os.path.getmtime(full)
        self.pixels_layout = "onerow" if len(tables.cells_rows) - 1 <= 16384 else "square"
        self._write_tables(tables)
        self._set_status("ok", "; ".join(notes))
        self._write_info()
        self._call("onPatchLoaded", patch, list(issues))
        return True

    def ReportError(self, message: str) -> None:
        """Статус error, текст в info, колбэк onOutputError. Геометрия и вывод не меняются."""
        full = self.ResolvePath(self.ownerComp.par.Patchfile.eval())
        self.loaded_mtime = os.path.getmtime(full) if full and os.path.exists(full) else None  # не дёргать Reload по кругу
        self._set_status("error", message)
        self._write_info()
        self._call("onOutputError", message)

    # ------------------------------------------------------------------ задача 5.1
    def UpdateCounters(self) -> None:
        raise NotImplementedError("задача 5.1")

    # ------------------------------------------------------------------ служебное
    @staticmethod
    def _error_text(e: Exception, full: str) -> str:
        if isinstance(e, FileNotFoundError):
            return f"файл патча не найден: {full}"
        if isinstance(e, OSError):
            return f"файл патча не читается: {full} ({e})"
        if isinstance(e, json.JSONDecodeError):
            return f"файл патча — не JSON: {full}: строка {e.lineno}, {e.msg}"
        errors = getattr(e, "errors", None)
        if errors:
            return "ошибка схемы: " + "; ".join(f"{x.path}: {x.reason}" for x in errors[:5]) + (
                f" (ещё {len(errors) - 5})" if len(errors) > 5 else "")
        return f"ошибка ядра: {e}"

    def _set_status(self, status: Status, message: str) -> None:
        self.status, self.message = status, message
        self.ownerComp.par.Status.val = status

    def _table(self, name: str):
        tables = self.ownerComp.op("tables")
        d = tables.op(name)
        if d is None:
            d = tables.create(tableDAT, name)  # noqa: F821
            k = len(tables.children) - 1
            d.nodeX, d.nodeY = (k % 5) * 200, -(k // 5) * 130
            d.viewer = True
        return d

    def _write_tables(self, t) -> None:
        pairs = {"geo_cells": t.cells_rows, "geo_prims": t.prims_rows, "geo_raw": t.raw_rows, "raw_prims": t.raw_prims_rows,
                 "fx_arrows": t.arrows_rows, "profiles": t.profiles_rows, "delay_groups": t.delay_groups,
                 "out_groups": t.out_groups}
        for name, rows in pairs.items():
            self._table(name).text = rows_to_text(rows)
        for grp, rows in t.routing_by_group.items():
            self._table(f"routing_{grp}").text = rows_to_text(rows)
        # устаревшие таблицы маршрутизации (групп стало меньше) — очищаем, не удаляем
        for d in self.ownerComp.op("tables").children:
            if d.name.startswith("routing_") and d.name[8:] not in t.routing_by_group:
                d.text = rows_to_text([self.geometry.ROUTING_COLUMNS])

    def _write_info(self) -> None:
        p, t = self.patch, self.tables
        summ = self.api.summary(p) if p is not None else {}
        warn = list(t.warnings) if t is not None else []
        rows = [["key", "value"],
                ["status", self.status], ["patch_path", self.patch_path],
                ["fixtures", str(summ.get("fixtures", 0))], ["cells", str(summ.get("cells", 0))],
                ["universes", str(summ.get("universes", 0))], ["outputs", str(summ.get("outputs", 0))],
                ["errors", str(sum(1 for i in self.issues if i.level.value == "error"))],
                ["warnings", str(sum(1 for i in self.issues if i.level.value == "warning") + len(warn))],
                ["skipped", " | ".join(w for w in warn if "не выводится" in w)],
                ["pixels_layout", self.pixels_layout], ["message", self.message],
                ["fps", str(float(self.ownerComp.time.rate))], ["input", "connected" if self.ownerComp.inputs else "none"]]
        for w in warn:
            rows.append(["warning", w])
        for i in self.issues:
            rows.append([f"issue_{i.level.value}", f"{i.code.value}: {i.message}"])
        self._table("info").text = rows_to_text(rows)

    def _call(self, name: str, *args) -> None:
        dat = self.ownerComp.par.Callbacks.eval()
        if dat is None:
            return
        fn = getattr(dat.module, name, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # колбэк пользователя — показываем, не глушим и не прерываем загрузку
            print(f"dsm: ошибка в колбэке {name}:\n{traceback.format_exc()}")
