"""Расширение компонента dsm (pixel-engine): загрузка патча, таблицы, статус, колбэки.

Единственный модуль слоя TD с TD-API (``project``, ``op``, ``tableDAT`` — глобалы TouchDesigner, импорт не нужен).
Состояние — в атрибутах экземпляра, не в op.store. Python исполняется только по событию (пульс, смена
файла, таймер счётчиков) — никогда в куке кадра.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Literal

Status = Literal["no patch", "ok", "error"]

TABLE_NAMES = ("geo_cells", "geo_prims", "geo_raw", "raw_prims", "fx_arrows", "profiles", "delay_groups", "out_groups",
               "arrow_pts", "arrow_prims", "fixture_sets", "raw_sets", "map_sets", "info")
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
    """Публичные методы (с заглавной — промоутятся на COMP): Reload, LoadPatch, ReportError, ApplyActive, CheckFile,
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
        self.counters: dict = {}            # fps, пакеты, куки — обновляет UpdateCounters раз в секунду
        self._last_tick: tuple[float, int, dict] | None = None   # (время, кадр, totalCooks узлов)
        self._last_node_message: str = ""
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
            self.ApplyActive()
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
        self.ApplyActive()
        self._call("onPatchLoaded", patch, list(issues))
        return True

    def ApplyActive(self) -> None:
        """Политика отправки: узлы вывода активны, только если пара Active включена и патч загружен (status ok).
        Константа на узле, а не выражение — в кадре Python не исполняется."""
        on = bool(self.ownerComp.par.Active.eval()) and self.status == "ok"
        out = self.ownerComp.op("output")
        if out is None:
            return
        for comp in out.children:
            o = comp.op("out") if comp.isCOMP else None
            if o is not None and o.OPType == "dmxoutPOP":
                o.par.active = on

    def TuneBypass(self) -> None:
        """Обход лишних узлов при простом патче (флаги bypass — константы, ставятся при загрузке):
        одна группа задержки → keep_c/keep_r и cache (0 кадров) не нужны; один набор фикстур → keep не нужен."""
        dl, fx = self.ownerComp.op("delay"), self.ownerComp.op("fixtures")
        groups = [c for c in dl.children if c.isCOMP and c.name.startswith("grp")] if dl else []
        for g in groups:
            frames = int(g.op("cache_c").par.outputindex.eval() != 0)
            for n in ("cache_c", "cache_r"):
                g.op(n).bypass = not frames
            for n in ("keep_c", "keep_r"):
                g.op(n).bypass = len(groups) == 1
        if dl is not None:
            for n in ("merge_c", "merge_r"):          # одна реплика → слияние лишнее
                dl.op(n).bypass = len(groups) == 1
        sets = [c for c in fx.children if c.isCOMP and c.op("keep")] if fx else []
        cell_sets = [c for c in sets if c.name.startswith("p")]
        raw_sets = [c for c in sets if c.name.startswith("raw_")]
        for c in cell_sets:
            c.op("keep").bypass = len(cell_sets) == 1
        for c in raw_sets:
            c.op("keep").bypass = len(raw_sets) == 1

    def RefreshMaps(self, on: bool = True, frames: int = 40) -> None:
        """Карты DMX (dmxmapDAT реплик) пересчитываются только по запросу: при on=True включаем их и через ``frames``
        кадров выключаем — постоянно активная карта на 50 000 ячеек стоит ~170 мс CPU на кук."""
        fx = self.ownerComp.op("fixtures")
        if fx is None:
            return
        for comp in fx.children:
            m = comp.op("map") if comp.isCOMP else None
            if m is not None:
                m.par.active = on
                if on:
                    m.par.updatevalpulse.pulse()
        if on:
            run(f"op('{self.ownerComp.path}').RefreshMaps(False)", delayFrames=frames)  # noqa: F821 — отложенное выключение

    def ReportError(self, message: str) -> None:
        """Статус error, текст в info, колбэк onOutputError. Геометрия и вывод не меняются."""
        full = self.ResolvePath(self.ownerComp.par.Patchfile.eval())
        self.loaded_mtime = os.path.getmtime(full) if full and os.path.exists(full) else None  # не дёргать Reload по кругу
        self._set_status("error", message)
        self._write_info()
        self.ApplyActive()
        self._call("onOutputError", message)

    # ------------------------------------------------------------------ счётчики (5.1)
    def _output_nodes(self) -> list:
        out = self.ownerComp.op("output")
        if out is None:
            return []
        return [c.op("out") for c in out.children if c.isCOMP and c.op("out") is not None]

    def UpdateCounters(self) -> None:
        """Раз в секунду (timerCHOP → колбэк): fps проекта, частота кук узлов вывода, предупреждения и ошибки
        операторов, состояние входа. Новое сообщение узла → onOutputError один раз. Пишет только таблицу info."""
        now, frame = time.perf_counter(), int(absTime.frame)  # noqa: F821 — глобал TD
        nodes = self._output_nodes()
        cooks = {n.parent().name: n.totalCooks for n in nodes}
        c: dict = {"fps": "", "rate": {}, "cooks": {}}
        if self._last_tick is not None:
            t0, f0, cooks0 = self._last_tick
            dt = max(now - t0, 1e-6)
            c["fps"] = f"{(frame - f0) / dt:.1f}"
            c["cooks"] = {k: f"{(cooks.get(k, 0) - cooks0.get(k, 0)) / dt:.1f}" for k in cooks}
        c["rate"] = {n.parent().name: str(n.par.rate.eval()) for n in nodes}
        self._last_tick = (now, frame, cooks)
        c["input"] = "connected" if self.ownerComp.inputs else "none"
        msgs = []
        for n in nodes + [self.ownerComp.op("engine/px_process")]:
            if n is None:
                continue
            for text in (n.errors(), n.warnings()):
                text = (text or "").strip()
                if text:
                    msgs.append(f"{n.path.split('/dsm/')[-1]}: {text.splitlines()[0]}")
        c["node_messages"] = msgs
        self.counters = c
        self._write_info()
        joined = " | ".join(msgs)
        if joined and joined != self._last_node_message:
            self._call("onOutputError", joined)
        self._last_node_message = joined

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

    @staticmethod
    def _arrow_geometry(arrows_rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
        """Стрелки направления для превью: две точки на фикстуру (начало — зелёная, конец — красная) и линия."""
        pts = [["P(0)", "P(1)", "P(2)", "Color(0)", "Color(1)", "Color(2)", "Color(3)"]]
        prims = [["vertices"]]
        for k, r in enumerate(arrows_rows[1:]):
            x1, y1, x2, y2 = r[2], r[3], r[4], r[5]
            pts.append([x1, y1, "0", "0", "1", "0", "1"])
            pts.append([x2, y2, "0", "1", "0", "0", "1"])
            prims.append([f"{2 * k} {2 * k + 1}"])
        return pts, prims

    def _write_tables(self, t) -> None:
        arrow_pts, arrow_prims = self._arrow_geometry(t.arrows_rows)
        map_sets = [["set"]] + [[r[0]] for r in t.fixture_sets[1:]] + [[r[0]] for r in t.raw_sets[1:]]   # карты DMX реплик
        pairs = {"geo_cells": t.cells_rows, "geo_prims": t.prims_rows, "geo_raw": t.raw_rows, "raw_prims": t.raw_prims_rows,
                 "fx_arrows": t.arrows_rows, "arrow_pts": arrow_pts, "arrow_prims": arrow_prims, "map_sets": map_sets,
                 "profiles": t.profiles_rows, "delay_groups": t.delay_groups,
                 "out_groups": t.out_groups, "fixture_sets": t.fixture_sets, "raw_sets": t.raw_sets}
        for name, rows in pairs.items():
            self._table(name).text = rows_to_text(rows)
        px_top = self.ownerComp.op("diag/pixels_top")
        if px_top is not None:
            px_top.par.layout = self.pixels_layout
        for grp, rows in t.routing_by_group.items():
            self._table(f"routing_{grp}").text = rows_to_text(rows)
        # устаревшие таблицы маршрутизации (групп стало меньше) — очищаем, не удаляем
        for d in self.ownerComp.op("tables").children:
            if d.name.startswith("routing_") and d.name[8:] not in t.routing_by_group:
                d.text = rows_to_text([self.geometry.ROUTING_COLUMNS])
        # реплики по таблицам пересоздаются явно: репликатор не всегда замечает смену содержимого
        for path in ("delay/rep", "fixtures/rep", "fixtures/rep_raw", "output/rep", "diag/rep_maps"):
            rep = self.ownerComp.op(path)
            if rep is not None:
                rep.par.recreateall.pulse()
        run(f"op('{self.ownerComp.path}').TuneBypass(); op('{self.ownerComp.path}').RefreshMaps(True)", delayFrames=12)  # noqa: F821

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
                ["fps", self.counters.get("fps", "")], ["project_rate", str(float(self.ownerComp.time.rate))],
                ["input", self.counters.get("input", "connected" if self.ownerComp.inputs else "none")],
                ["canvas_w", str(p.canvas.width) if p is not None else "1280"], ["canvas_h", str(p.canvas.height) if p is not None else "720"]]
        for grp, rate in self.counters.get("rate", {}).items():
            rows.append([f"rate_{grp}", rate])
        for grp, cps in self.counters.get("cooks", {}).items():
            rows.append([f"cooks_{grp}", cps])
        for m in self.counters.get("node_messages", []):
            rows.append(["node_message", m])
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
