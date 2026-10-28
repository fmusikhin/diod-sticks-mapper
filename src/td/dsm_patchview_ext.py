"""Расширение patch-view компонента dsm: вкладка DMX — карта юниверсов, правки адресов, выходов и портов через ядро
и запись патча движка, отмена одной ступенью, выбор фикстур и колбэк onSelect, строки info `dmx_*`.

Третье расширение на COMP (``ext2``). Состояние — атрибуты экземпляра, не op.store; своего патча нет — источник правды
патч движка (`DsmEngineExt.patch`). Python — только по событиям (пара, метод, клик/перетаскивание, загрузка патча),
никогда в куке кадра. Движок про patch-view не знает: подписка на загрузку и строки info — через его публичные хуки.
"""
from __future__ import annotations

import os
import time
import traceback

NOT_YET = "не реализовано"
ROW_COLUMNS = ["index", "output", "universe", "dotted", "declared", "used"]
TABLES = ("pv_rects", "pv_preview", "pv_zoom", "pv_labels", "pv_rows", "pv_issues", "pv_unassigned", "pv_outputs",
          "pv_ports", "pv_sheet", "pv_status")


class DsmPatchViewExt:
    """Публичные методы (с заглавной — промоутятся на COMP): Open, Close, SelectOutput, ShowIssue, Select, Selection,
    ReAddress, ToAuto, Sequential, AutoAssign, AddOutput, RemoveOutput, UpdateOutput, AddPort,
    RemovePort, UpdatePort, ReorderPort, Undo, ExportSheet, OnPatchLoaded, OnMap, OnDmxPar."""

    def __init__(self, ownerComp) -> None:
        self.ownerComp = ownerComp
        self.logic = ownerComp.op("patchview_mod").module     # dsm_patchview из синхронизированного DAT
        self.output_id: str | None = None      # выбранный выход карты; None — все
        self.zoom_row: int | None = None       # строка крупного юниверса
        self.selection: tuple[str, ...] = ()   # выбранные фикстуры
        self.undo_doc = None                   # документ файла до последней правки (одна ступень отмены)
        self._undo_file: tuple[str, float] = ("", 0.0)   # (файл, дата нашей записи) — к чему применима отмена
        self.message: str = ""
        self.layout = None                     # MapLayout текущего патча и выхода (для попадания и предпросмотра)
        self.last_rebuild_ms: float = 0.0      # время последней перестройки таблиц (9.1)
        self._seen: tuple | None = None        # (патч движка, статус) последней перестройки — ссылка, не id()
        self._counts = (0, 0)                  # (ошибок, предупреждений) текущего патча — строки info dmx_*
        self._pending = False                  # перестройка запланирована на следующий кадр
        self._in_par = False                   # защита от повторного входа: метод меняет пару → пара вызывает метод
        eng = ownerComp.ext.DsmEngineExt
        eng.AddListener(self.OnPatchLoaded)
        eng.AddInfoProvider(self._info_rows)
        self._rebuild()                        # таблицы по уже загруженному патчу; пишет и info со строками dmx_*

    # ------------------------------------------------------------------ окно и выбор (задачи 2.2, 2.6, 3.1)
    def Open(self) -> None:
        self._not_yet("Open")

    def Close(self) -> None:
        self._not_yet("Close")

    def SelectOutput(self, output_id: str | None) -> bool:
        """Выход карты (``None`` или пусто — все выходы, 2.5). Неизвестный выход — False и сообщение, выбор прежний."""
        output_id = (output_id or "").strip() or None
        patch = self._engine().patch
        if output_id is not None and (patch is None or not any(o.id == output_id for o in patch.outputs)):
            self.message = f"SelectOutput: нет такого выхода: {output_id}"
            self._sync_pars(Dmxoutput=self.output_id or "")
            self._engine().WriteInfo()
            return False
        if output_id != self.output_id:
            self.zoom_row = None
        self.output_id = output_id
        self.message = ""
        self._sync_pars(Dmxoutput=output_id or "")
        self._rebuild()
        return True

    def ShowIssue(self, index: int) -> bool:
        """Показать проблему ``index`` (номер строки ``pv_issues`` с 1, 3.2): выход и крупный юниверс её места."""
        t = self._table("pv_issues")
        if t is None or not 1 <= index < t.numRows:
            self.message = f"ShowIssue: нет проблемы {index}"
            self._engine().WriteInfo()
            return False
        out, uni = t[index, "output"].val, t[index, "universe"].val
        if not out:                                       # проблема по фикстурам — выход первой фикстуры
            patch = self._engine().patch
            fids = t[index, "fixture_ids"].val.split()
            fx = next((f for f in patch.fixtures if fids and f.id == fids[0]), None) if patch else None
            out = fx.address.output_id if fx is not None and fx.address.output_id else ""
        if not out and not uni:
            self.message = f"ShowIssue: у проблемы {index} нет места на карте ({t[index, 'code'].val})"
            self._rebuild()
            return True
        if out and not self.SelectOutput(out):
            return False
        if uni and self.layout is not None:
            self.zoom_row = next((r.index for r in self.layout.rows if str(r.universe) == uni
                                  and (not out or r.output_id == out)), None)
            self._rebuild()
        return True

    def Select(self, fixture_ids: list[str]) -> None:
        self._not_yet("Select")

    def Selection(self) -> list[str]:
        return list(self.selection)

    # ------------------------------------------------------------------ правки (задачи 2.3–2.5)
    def ReAddress(self, fixture_id: str, output_id: str, universe: int, channel: int) -> bool:
        """Ручной адрес фикстуры (4.1, 4.2): начало в (выход, юниверс, канал). Невозможный адрес (ошибка ядра, канал вне
        1–512, юниверс вне протокола) — отказ без правки; конфликты правку не блокируют, они на карте (4.7)."""
        eng = self._engine()
        if not self._can_edit("ReAddress"):
            return False
        try:
            universe, channel = int(universe), int(channel)
        except (TypeError, ValueError):
            return self._fail(f"ReAddress: юниверс и канал — целые числа ({universe!r}, {channel!r})")
        if not 1 <= channel <= 512:
            return self._fail(f"ReAddress: канал {channel} вне 1–512")
        out = next((o for o in eng.patch.outputs if o.id == output_id), None)
        if out is None:
            return self._fail(f"ReAddress: нет такого выхода: {output_id}")
        from dsm_core.protocol import universe_range     # ядро в sys.path (движок подключает его при старте)
        lo, hi = universe_range(out.protocol)
        if not lo <= universe <= hi:
            return self._fail(f"ReAddress: юниверс {universe} вне диапазона {out.protocol.value} {lo}–{hi}")
        cur = next((f for f in eng.patch.fixtures if f.id == fixture_id), None)
        port = cur.address.port_id if cur is not None and cur.address.output_id == output_id else None   # в своём выходе — порт сохраняется
        L = self.logic
        pre = L.preview(self.layout or L.map_layout(eng.patch, {}, [], None), eng.patch, fixture_id, output_id,
                        universe, channel, L.Geom(), port)              # проверка с тем же портом, что и правка
        if pre.error:
            return self._fail(f"ReAddress: {pre.error}")
        api = eng.api
        return self._edit(lambda p: api.set_manual_address(p, fixture_id, output_id, universe, channel, port),
                          f"{fixture_id} → {output_id} {universe}:{channel}", subject=fixture_id)

    def ToAuto(self, fixture_id: str, output_id: str | None = None) -> bool:
        """«В авто» и перенос на выход (4.2, 4.4): адрес в режим auto на выходе ``output_id`` (по умолчанию — текущий
        выход фикстуры); номер раздаёт ядро при записи. В своём выходе порт сохраняется, на другом — без порта."""
        eng = self._engine()
        if not self._can_edit("ToAuto"):
            return False
        fx = next((f for f in eng.patch.fixtures if f.id == fixture_id), None)
        if fx is None:
            return self._fail(f"ToAuto: нет такой фикстуры: {fixture_id}")
        output_id = (output_id or "").strip() or fx.address.output_id
        if not output_id:
            return self._fail(f"ToAuto: {fixture_id} не на выходе — укажите выход")
        if not any(o.id == output_id for o in eng.patch.outputs):
            return self._fail(f"ToAuto: нет такого выхода: {output_id}")
        port = fx.address.port_id if fx.address.output_id == output_id else None
        api = eng.api
        # assign_to_output даёт тот же адрес, что clear_address → assign_to_output, но в своём выходе и порту
        # не переставляет фикстуру в конец цепочки (порядок цепочки — порядок автоадресации)
        return self._edit(lambda p: api.assign_to_output(p, fixture_id, output_id, port),
                          f"{fixture_id} → {output_id} авто", subject=fixture_id)

    def Sequential(self, fixture_ids: list[str], universe: int, channel: int) -> bool:
        """Подряд для списка (4.5): всем выбранным — ручные адреса подряд от (юниверс, канал), по правилам выхода
        (``assign_sequential`` раскладывает, затем каждой фикстуре — ``set_manual_address`` с её адресом). Ручные, потому что
        авто-адреса ядро при загрузке раздаёт заново от начала выхода. Все фикстуры — на одном выходе."""
        eng = self._engine()
        if not self._can_edit("Sequential"):
            return False
        raw = fixture_ids.replace(",", " ").split() if isinstance(fixture_ids, str) else (fixture_ids or [])
        ids = list(dict.fromkeys(str(i).strip() for i in raw if str(i).strip()))   # пробел или запятая, без дублей
        if not ids:
            return self._fail("Sequential: пустой список фикстур")
        try:
            universe, channel = int(universe), int(channel)
        except (TypeError, ValueError):
            return self._fail(f"Sequential: юниверс и канал — целые числа ({universe!r}, {channel!r})")
        api = eng.api
        def edit(p):
            laid, _issues = api.assign_sequential(p, ids, universe, channel)
            for fid in ids:
                a = next(f for f in laid.fixtures if f.id == fid).address
                if a.universe is None or a.channel is None:
                    raise api.OperationError(f"{fid} не помещается в выход от {universe}:{channel}")
                p = api.set_manual_address(p, fid, a.output_id, a.universe, a.channel, a.port_id)
            return p
        try:                                                         # отказ ядра — до записи, без трассировки в textport
            edit(eng.patch)
        except (api.PatchError, KeyError, ValueError) as e:
            return self._fail(f"Sequential: {e}")
        return self._edit(edit,
                          f"подряд {len(ids)} от {universe}:{channel}", subject=None, skip=set(ids))

    def AutoAssign(self, output_id: str) -> bool:
        return self._not_yet("AutoAssign")

    def AddOutput(self) -> str | None:
        self._not_yet("AddOutput")
        return None

    def RemoveOutput(self, output_id: str) -> bool:
        return self._not_yet("RemoveOutput")

    def UpdateOutput(self, output_id: str, **fields) -> bool:
        return self._not_yet("UpdateOutput")

    def AddPort(self, output_id: str) -> str | None:
        self._not_yet("AddPort")
        return None

    def RemovePort(self, output_id: str, port_id: str) -> bool:
        return self._not_yet("RemovePort")

    def UpdatePort(self, output_id: str, port_id: str, **fields) -> bool:
        return self._not_yet("UpdatePort")

    def ReorderPort(self, output_id: str, port_id: str, fixture_ids: list[str]) -> bool:
        return self._not_yet("ReorderPort")

    def Undo(self) -> bool:
        """Отменить последнюю правку (4.9): вернуть документ файла до неё — одна ступень. Отмена действует, только пока
        файл — тот же и не менялся после правки (иначе затёрла бы чужое); работает и при ошибке загрузки после записи."""
        if self.undo_doc is None:
            return self._fail("Undo: нечего отменять", notify=False)
        eng = self._engine()
        if eng.patch is None:
            return self._fail("Undo: патч не загружен")
        if not self._undo_valid():
            self._drop_undo()
            return self._fail("Undo: файл патча сменился или изменён после правки — отмена недоступна")
        doc = self.undo_doc
        return self._edit(lambda p: doc, "отмена", keep_undo=False)   # запись отмены снимает undo_doc

    def ExportSheet(self, path: str | None = None) -> str:
        self._not_yet("ExportSheet")
        return ""

    # ------------------------------------------------------------------ события
    def OnPatchLoaded(self, patch, issues) -> None:
        """Слушатель движка: перестройка таблиц карты; выбранный выход и крупный юниверс сохраняются, если есть (1.3).
        Отмена привязана к файлу и дате своей записи — загрузка другого или изменённого файла её снимает."""
        if self.undo_doc is not None and not self._undo_valid():
            self._drop_undo()
        if self.output_id is not None and not any(o.id == self.output_id for o in patch.outputs):
            self.output_id = None
            self.zoom_row = None
            self._sync_pars(Dmxoutput="")
        self._rebuild()

    def OnMap(self, event: str, u: float, v: float, shift: bool = False) -> None:
        """Обработчик области карты (задачи 3.3–3.4): down / move / up / hover."""
        self._not_yet("OnMap")

    def OnDmxPar(self, par) -> None:
        """Исполнитель пар страницы Dmx (parexecuteDAT) → методы; повторный вход из метода игнорируется."""
        if self._in_par:
            return
        self._in_par = True
        try:
            name = par.name
            if name == "Dmxwindow":
                self.Open() if par.eval() else self.Close()
            elif name == "Dmxoutput":
                self.SelectOutput(par.eval().strip() or None)
            elif name == "Dmxundo" and par.isPulse:
                self.Undo()
            elif name == "Dmxexport" and par.isPulse:
                self.ExportSheet()
            # Dmxsnap читается при перетаскивании (задача 3.4)
        except Exception:  # команда пары — в textport и info, компонент работает дальше
            print(traceback.format_exc())
            self.message = "ошибка команды DMX: см. textport"
            self._engine().WriteInfo()
        finally:
            self._in_par = False

    # ------------------------------------------------------------------ общий путь правки (2.3)
    def _can_edit(self, name: str) -> bool:
        """Без патча или при ошибке загрузки правки неактивны (1.4)."""
        eng = self._engine()
        if eng.patch is None or eng.status != "ok":
            self._fail(f"{name}: патч не загружен (статус {eng.status})")
            return False
        return True

    def _edit(self, edit, label: str, keep_undo: bool = True, subject: str | None = None,
              skip: set[str] | None = None) -> bool:
        """Правка ядра → запись файла движком с пересборкой → перестройка по слушателю → «сдвинулось N» (4.8).
        Отказ ядра или записи: файл, патч и карта прежние, причина в статусе и onOutputError; undo_doc не трогается.
        Файл записан, но не загрузился (ошибка раскладки): отмена сохраняется — Undo вернёт прежний файл (4.7, 6.3)."""
        eng = self._engine()
        before = eng.patch
        full = eng.ResolvePath(eng.patch_path)
        try:
            snapshot, _notes = eng.api.load(full)                    # документ файла до правки — для отмены
            mtime0 = os.path.getmtime(full)
        except Exception as e:  # файл исчез или не читается — правку не начинаем
            return self._fail(f"правка: файл патча не читается: {e}")
        ok = eng.SavePatch(edit, True)
        try:
            mtime1 = os.path.getmtime(full)
        except OSError:
            mtime1 = mtime0
        written = mtime1 != mtime0
        if written:                                                  # файл переписан (загрузка могла и не пройти)
            if keep_undo:
                self.undo_doc, self._undo_file = snapshot, (full, mtime1)
            else:
                self._drop_undo()
        if not ok:                                                   # движок уже вызвал onOutputError — второй раз не зовём
            if not written:
                return self._fail(f"правка не записана: {eng.message}", notify=False)
            hint = "; Undo вернёт прежний файл" if keep_undo else ""
            return self._fail(f"правка записана, но патч не загрузился: {eng.message}{hint}", notify=False)
        skip = set(skip or ()) | ({subject} if subject else set())
        moved = [f for f in self.logic.shifted(before, eng.patch) if f not in skip]   # кроме самих правленных фикстур
        self.message = f"{label}" + (f"; сдвинулось {len(moved)}: {' '.join(moved[:8])}" if moved else "")
        self._write("pv_status", self._status_rows(eng, *self._counts))   # таблицы уже перестроены слушателем загрузки
        eng.WriteInfo()
        return True

    def _undo_valid(self) -> bool:
        """Отмена применима: загружен тот же файл, что правили, и его дата — дата нашей записи."""
        eng = self._engine()
        path, mtime = self._undo_file
        try:
            return bool(path) and eng.ResolvePath(eng.patch_path) == path and os.path.getmtime(path) == mtime
        except OSError:
            return False

    def _drop_undo(self) -> None:
        self.undo_doc, self._undo_file = None, ("", 0.0)

    def _fail(self, message: str, notify: bool = True) -> bool:
        """Команда отклонена: причина в статусе панели, info и onOutputError (4.7); ``notify=False`` — колбэк уже вызван."""
        self.message = message
        eng = self._engine()
        if self._seen is not None:
            self._write("pv_status", self._status_rows(eng, *self._counts))
        eng.WriteInfo()
        if notify:
            eng.CallUser("onOutputError", message)
        return False

    # ------------------------------------------------------------------ таблицы (2.2)
    def _rebuild(self) -> None:
        """Патч движка → таблицы pv_* (каждая одной записью) → info. Нет патча или ошибка — пустые таблицы (1.4)."""
        t0 = time.perf_counter()
        eng, L = self._engine(), self.logic
        self._pending = False
        self._seen = (eng.patch, eng.status)
        patch = eng.patch if eng.status == "ok" else None
        if patch is None:
            self.layout = None
            self._counts = (0, 0)
            for name, header in self._headers().items():      # пустая таблица = только заголовок (листеры, инстансинг)
                self._write(name, [header])
            self._write("pv_status", self._status_rows(eng, 0, 0))
            self.last_rebuild_ms = (time.perf_counter() - t0) * 1000.0
            eng.WriteInfo()
            return
        segs = eng.api.segments_by_fixture(patch)
        issues = list(eng.issues)
        geom = L.Geom()
        self.layout = layout = L.map_layout(patch, segs, issues, self.output_id)
        if self.zoom_row is not None and not 0 <= self.zoom_row < len(layout.rows):
            self.zoom_row = None
        sel = set(self.selection)
        self._write("pv_rects", L.map_rects(layout, sel, geom))
        self._write("pv_preview", [L.RECT_COLUMNS])
        self._write("pv_zoom", L.zoom_rects(layout, self.zoom_row, geom, sel) if self.zoom_row is not None else [L.RECT_COLUMNS])
        self._write("pv_labels", L.label_rows(layout, geom))
        self._write("pv_rows", [ROW_COLUMNS] + [[str(r.index), r.output_id, str(r.universe), r.dotted,
                                                  "1" if r.declared else "0", str(r.used_channels)] for r in layout.rows])
        self._write("pv_issues", L.issue_rows(issues))
        self._write("pv_unassigned", L.unassigned_rows(patch, segs))
        self._write("pv_outputs", L.output_rows(patch, segs, issues))
        port_out = self.output_id or (patch.outputs[0].id if patch.outputs else None)
        self._write("pv_ports", L.port_rows(patch, port_out) if port_out else [L.PORT_COLUMNS])
        self._write("pv_sheet", L.sheet_rows(patch, self.output_id))
        errors = sum(1 for i in issues if i.level.value == "error")
        self._counts = (errors, len(issues) - errors)
        self._write("pv_status", self._status_rows(eng, errors, len(issues) - errors))
        self.last_rebuild_ms = (time.perf_counter() - t0) * 1000.0
        eng.WriteInfo()

    def _headers(self) -> dict[str, list[str]]:
        L = self.logic
        rect = L.RECT_COLUMNS
        sheet = self._engine().api.address_table(self._engine().api.new_patch())[0]
        return {"pv_rects": rect, "pv_preview": rect, "pv_zoom": rect, "pv_labels": L.LABEL_COLUMNS, "pv_rows": ROW_COLUMNS,
                "pv_issues": L.ISSUE_COLUMNS, "pv_unassigned": L.UNASSIGNED_COLUMNS, "pv_outputs": L.OUTPUT_COLUMNS,
                "pv_ports": L.PORT_COLUMNS, "pv_sheet": sheet}

    def _deferred_rebuild(self) -> None:
        """Отложенная перестройка из _info_rows: лишняя, если слушатель загрузки уже перестроил в этом кадре."""
        eng = self._engine()
        if self._pending and self._seen != (eng.patch, eng.status):
            self._rebuild()
        self._pending = False

    def _status_rows(self, eng, errors: int, warnings: int) -> list[list[str]]:
        return [["key", "value"], ["status", eng.status], ["output", self.output_id or "all"],
                ["zoom_row", "" if self.zoom_row is None else str(self.zoom_row)], ["errors", str(errors)],
                ["warnings", str(warnings)], ["selection", " ".join(self.selection)], ["message", self.message or eng.message]]

    def _table(self, name: str):
        return self.ownerComp.op("tables").op(name)

    def _write(self, name: str, rows: list[list[str]]) -> None:
        """Таблица tables/<name> одной записью; создаётся при первом обращении (сетка как у таблиц движка)."""
        tables = self.ownerComp.op("tables")
        d = tables.op(name)
        if d is None:
            d = tables.create(tableDAT, name)  # noqa: F821 — глобал TouchDesigner
            k = len(tables.children) - 1
            d.nodeX, d.nodeY = (k % 5) * 200, -(k // 5) * 130
            d.viewer = True
        d.text = "\n".join("\t".join(str(c) for c in r) for r in rows)

    def _sync_pars(self, **values) -> None:
        """Пары страницы Dmx отражают состояние; запись из метода не должна снова вызвать метод."""
        was, self._in_par = self._in_par, True
        try:
            for name, v in values.items():
                if self.ownerComp.par[name].eval() != v:
                    self.ownerComp.par[name] = v
        finally:
            self._in_par = was

    # ------------------------------------------------------------------ служебное
    def _engine(self):
        return self.ownerComp.ext.DsmEngineExt

    def _not_yet(self, name: str) -> bool:
        self.message = f"{name}: {NOT_YET}"
        self._engine().WriteInfo()
        return False

    def _info_rows(self) -> list[list[str]]:
        # Движок зовёт слушателей только после успешной загрузки; пустой Patchfile и ошибка загрузки меняют только статус.
        # Info пишется на каждое такое событие — по нему и замечаем смену и перестраиваем таблицы следующим кадром (1.4).
        eng = self._engine()
        if self._seen is not None and (self._seen[0] is not eng.patch or self._seen[1] != eng.status) and not self._pending:
            self._pending = True
            run("args[0]._deferred_rebuild()", self, delayFrames=1)  # noqa: F821 — глобал TD; без строки пути
        return [["dmx_output", self.output_id or "all"], ["dmx_selection", " ".join(self.selection)],
                ["dmx_message", self.message], ["dmx_errors", str(self._counts[0])], ["dmx_warnings", str(self._counts[1])],
                ["dmx_rebuild_ms", f"{self.last_rebuild_ms:.1f}"]]
