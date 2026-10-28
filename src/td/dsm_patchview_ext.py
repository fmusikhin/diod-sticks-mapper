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
        self._grab = None                      # (Hit, fixture_id, "map"|"zoom") — перетаскивание сегмента
        self._drop = None                      # (выход, юниверс, канал) под курсором при перетаскивании
        self.place_id: str | None = None       # фикстура из списка «не размещены», ждущая клика по карте (3.6)
        eng = ownerComp.ext.DsmEngineExt
        eng.AddListener(self.OnPatchLoaded)
        eng.AddInfoProvider(self._info_rows)
        self._rebuild()                        # таблицы по уже загруженному патчу; пишет и info со строками dmx_*

    # ------------------------------------------------------------------ окно и выбор (задачи 2.2, 2.6, 3.1)
    def Open(self) -> None:
        """Окно панели DMX (1.1): рендеры включаются, таблицы рендера пишутся заново."""
        ui = self._ui()
        if ui is None:
            self._fail("Open: нет панели ui_dmx", notify=False)
            return
        self._cooking(True)
        self._sync_pars(Dmxwindow=True)
        self._rebuild()
        ui.op("win").par.winopen.pulse()

    def Close(self) -> None:
        """Закрыть окно; при закрытом окне рендеры карты не кукаются (1.6)."""
        ui = self._ui()
        if ui is None:
            return
        ui.op("win").par.winclose.pulse()
        self._sync_pars(Dmxwindow=False)
        self._cooking(False)

    def _cooking(self, on: bool) -> None:
        ui = self._ui()
        for name in ("body_lr/left_col/map_area/map_render", "body_lr/left_col/zoom_area/zoom_render"):
            r = ui.op(name)
            if r is not None:
                r.allowCooking = on

    def PickUnassigned(self, fixture_id: str | None) -> None:
        """Фикстура из «не размещены» ждёт клика по карте: клик по строке и каналу даёт ей адрес (3.6)."""
        self.place_id = (fixture_id or "").strip() or None
        self.message = f"{self.place_id}: щёлкните по строке карты" if self.place_id else ""
        self._write_status()

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
        self._fill_editors()
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

    def Select(self, fixture_ids) -> None:
        """Подсветка фикстур снаружи (8.1): без колбэка onSelect."""
        self._set_selection(fixture_ids, notify=False)

    def _set_selection(self, fixture_ids, notify: bool) -> None:
        """Выбор → слой выделения карты, info; из панели — onSelect один раз на смену (8.2)."""
        raw = fixture_ids.replace(",", " ").split() if isinstance(fixture_ids, str) else (fixture_ids or [])
        patch = self._engine().patch
        known = {f.id for f in patch.fixtures} if patch is not None else set()
        sel = tuple(dict.fromkeys(str(i).strip() for i in raw if str(i).strip() in known))
        if sel == self.selection:
            return
        self.selection = sel
        self._rebuild()
        self._fill_editors()
        if notify:
            self._engine().CallUser("onSelect", list(sel))

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

    def SequentialFromSelection(self) -> bool:
        """Кнопка «Подряд»: выбранные фикстуры подряд от адреса первой из них (её адрес задают полями фикстуры)."""
        eng = self._engine()
        if eng.patch is None or not self.selection:
            return self._fail("Подряд: выберите фикстуры", notify=False)
        first = next((f for f in eng.patch.fixtures if f.id == self.selection[0]), None)
        if first is None or first.address.universe is None or first.address.channel is None:
            return self._fail(f"Подряд: у {self.selection[0]} нет адреса", notify=False)
        return self.Sequential(list(self.selection), first.address.universe, first.address.channel)

    def CurrentOutput(self) -> str | None:
        """Выход для команд без явного выхода: выбранный на карте, иначе выход первой выбранной фикстуры, иначе первый."""
        patch = self._engine().patch
        if patch is None:
            return None
        if self.output_id:
            return self.output_id
        fx = next((f for f in patch.fixtures if self.selection and f.id == self.selection[0]), None)
        if fx is not None and fx.address.output_id:
            return fx.address.output_id
        return patch.outputs[0].id if patch.outputs else None

    def AutoAssign(self, output_id: str) -> bool:
        """Раздать заново (5.4): все фикстуры выхода — в авто, ядро раскладывает их по цепочке от начала выхода."""
        eng = self._engine()
        if not self._can_edit("AutoAssign"):
            return False
        out = next((o for o in eng.patch.outputs if o.id == output_id), None)
        if out is None:
            return self._fail(f"AutoAssign: нет такого выхода: {output_id}")
        api = eng.api

        def edit(p):
            for fx in p.fixtures:
                if fx.address.output_id == output_id and fx.address.mode.value == "manual":
                    p = api.assign_to_output(p, fx.id, output_id, fx.address.port_id)
            return p
        return self._edit(edit, f"{output_id}: адреса розданы заново")

    def AddOutput(self) -> str | None:
        self._not_yet("AddOutput")
        return None

    def RemoveOutput(self, output_id: str) -> bool:
        return self._not_yet("RemoveOutput")

    OUTPUT_FIELDS = ("name", "ip", "start_universe", "universe_count", "rate_hz")

    def UpdateOutput(self, output_id: str, **fields) -> bool:
        """Параметры выхода (5.1): имя, IP, стартовый юниверс, число юниверсов, частота; проверка IP и диапазона."""
        eng = self._engine()
        if not self._can_edit("UpdateOutput"):
            return False
        out = next((o for o in eng.patch.outputs if o.id == output_id), None)
        if out is None:
            return self._fail(f"UpdateOutput: нет такого выхода: {output_id}")
        bad = [k for k in fields if k not in self.OUTPUT_FIELDS]
        if bad:
            return self._fail(f"UpdateOutput: поля не правятся здесь: {' '.join(bad)}")
        ch = {}
        try:
            if "name" in fields:
                ch["name"] = str(fields["name"]).strip() or out.name
            if "ip" in fields:
                ip = (str(fields["ip"] or "")).strip()
                if ip:
                    import ipaddress
                    ipaddress.IPv4Address(ip)
                ch["ip"] = ip or None
            if "start_universe" in fields:
                ch["start_universe"] = int(fields["start_universe"])
                from dsm_core.protocol import universe_range
                lo, hi = universe_range(out.protocol)
                if not lo <= ch["start_universe"] <= hi:
                    return self._fail(f"UpdateOutput: юниверс {ch['start_universe']} вне {lo}–{hi}")
            if "universe_count" in fields:
                v = fields["universe_count"]
                ch["universe_count"] = int(v) if v not in (None, "", 0, "0") else None
                if ch["universe_count"] is not None and ch["universe_count"] < 1:
                    return self._fail("UpdateOutput: число юниверсов — от 1")
            if "rate_hz" in fields:
                ch["rate_hz"] = float(fields["rate_hz"])
                if not 1.0 <= ch["rate_hz"] <= 120.0:
                    return self._fail("UpdateOutput: частота 1–120 Гц")
        except (TypeError, ValueError) as e:
            return self._fail(f"UpdateOutput: неверное значение: {e}")
        if not ch:
            return True
        api = eng.api
        return self._edit(lambda p: api.update_output(p, output_id, **ch),
                          f"{output_id}: " + ", ".join(f"{k}={v}" for k, v in ch.items()))

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
        """Лист адресов CSV (7.1): по умолчанию рядом с патчем ``<имя>_addresses.csv``; путь файла или "" при ошибке."""
        eng = self._engine()
        if eng.patch is None:
            self._fail("ExportSheet: патч не загружен")
            return ""
        full = eng.ResolvePath(path) if path else os.path.splitext(eng.ResolvePath(eng.patch_path))[0] + "_addresses.csv"
        try:
            text = self.logic.sheet_csv(self.logic.sheet_rows(eng.patch, self.output_id))
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as e:
            self._fail(f"ExportSheet: не записан: {e}")
            return ""
        self.message = f"лист адресов: {os.path.basename(full)}"
        self._write("pv_status", self._status_rows(eng, *self._counts))
        eng.WriteInfo()
        return full

    # ------------------------------------------------------------------ события
    def OnPatchLoaded(self, patch, issues) -> None:
        """Слушатель движка: перестройка таблиц карты; выбранный выход и крупный юниверс сохраняются, если есть (1.3).
        Отмена привязана к файлу и дате своей записи — загрузка другого или изменённого файла её снимает."""
        if self.undo_doc is not None and not self._undo_valid():
            self._drop_undo()
        self._grab, self._drop, self.place_id = None, None, None     # раскладка сменилась — жест и размещение сброшены
        self._write("pv_preview", [self.logic.RECT_COLUMNS])
        if self.output_id is not None and not any(o.id == self.output_id for o in patch.outputs):
            self.output_id = None
            self.zoom_row = None
            self._sync_pars(Dmxoutput="")
        self._rebuild()

    def OnMap(self, event: str, u: float, v: float, shift: bool = False) -> None:
        """Область карты (3.3, 3.4): hover — подсказка; down — выбор сегмента (захват) или строки (крупный юниверс),
        пусто — снять выбор; move — предпросмотр; up — правка общим путём, вне карты — отмена."""
        ui = self._ui()
        if self.layout is None or ui is None:
            return
        L = self.logic
        w, _h = self._map_size()
        mu = L.area_uv(u, w, float(ui.par.Gutterw))
        h = L.hit(self.layout, mu, v)
        self._pointer(event, h, shift, lambda: L.drop_address_from_grab(
            self.layout, self._engine().patch, self._grab[1], self._grab[0], mu, v, not shift and bool(
                self.ownerComp.par.Dmxsnap.eval())) if self._grab and self._grab[2] == "map" else None, "map", u, v)

    def OnZoom(self, event: str, u: float, v: float, shift: bool = False) -> None:
        """Крупный юниверс (2.4): то же, что карта, внутри строки ``zoom_row``; канал — по сетке 32×16."""
        if self.layout is None or self.zoom_row is None:
            return
        L = self.logic
        h = L.zoom_hit(self.layout, self.zoom_row, u, v)

        def drop():
            if not (self._grab and self._grab[2] == "zoom") or h.channel is None:
                return None
            grab, fid = self._grab[0], self._grab[1]
            first = L._first_segment(self.layout, fid)
            ch = h.channel if first is None or grab.channel is None else first.first_channel + (h.channel - grab.channel)
            row = self.zoom_row if first is None else first.row
            return L.drop_address(self.layout, self._engine().patch, fid, row, ch,
                                  not shift and bool(self.ownerComp.par.Dmxsnap.eval()))
        self._pointer(event, h, shift, drop, "zoom", u, v)

    def _pointer(self, event: str, h, shift: bool, drop, area: str, u: float, v: float) -> None:
        eng = self._engine()
        if event == "hover":
            self._hover(h, area, u, v)
            return
        if event == "down":
            self._grab, self._drop = None, None
            if self.place_id:                                                   # размещение из «не размещены»
                fid, self.place_id = self.place_id, None
                if h.row is None or h.channel is None:
                    self.message = f"размещение {fid} отменено"
                    self._write_status()
                    return
                try:
                    out, uni, ch = self.logic.drop_address(self.layout, eng.patch, fid, h.row, h.channel,
                                                           not shift and bool(self.ownerComp.par.Dmxsnap.eval()))
                except (ValueError, KeyError) as e:
                    self._fail(f"размещение: {e}", notify=False)
                    return
                self.ReAddress(fid, out, uni, ch)
                return
            if h.fixture_id:
                self._grab = (h, h.fixture_id, area)
                self._set_selection([h.fixture_id], notify=True)
            elif h.row is not None:
                self._grab = None
                if area == "map":
                    self.zoom_row = h.row
                self._set_selection([], notify=True)
                self._rebuild()
            return
        if self._grab is None:
            return
        if event == "move":
            try:
                addr = drop()
            except (ValueError, KeyError):
                addr = None
            if addr == self._drop:
                return
            self._drop = addr
            rows = [self.logic.RECT_COLUMNS]
            if addr is not None:
                pre = self.logic.preview(self.layout, eng.patch, self._grab[1], *addr)
                rows = pre.rects if not pre.error else rows
                self.message = (f"{self._grab[1]} → {addr[0]} {addr[1]}:{addr[2]}" + (f" — {pre.error}" if pre.error else "")
                                + (f"; конфликтов {len(pre.issues)}" if pre.issues else ""))
            self._write("pv_preview", rows)
            self._write_preview_render()
            self._write_status()
            return
        if event == "up":
            grab, addr = self._grab, self._drop
            self._grab, self._drop = None, None
            self._write("pv_preview", [self.logic.RECT_COLUMNS])
            self._write_preview_render()
            if addr is None:
                self.message = "перетаскивание отменено" if grab else ""
                self._write_status()
                return
            fx = next((f for f in eng.patch.fixtures if f.id == grab[1]), None)
            cur = (fx.address.output_id, fx.address.universe, fx.address.channel) if fx else None
            if addr == cur:
                self.message = ""
                self._write_status()
                return
            self.ReAddress(grab[1], *addr)

    def _hover(self, h, area: str, u: float, v: float) -> None:
        if h.row is None:
            text = ""
        else:
            r = self.layout.rows[h.row]
            text = f"{r.output_id} u{r.universe} ({r.dotted}) ch {h.channel}"
            if h.fixture_id:
                text = f"{h.fixture_id} · " + text
        self._write("pv_hover", [["key", "value"], ["text", text], ["area", area], ["u", f"{u:.4f}"], ["v", f"{v:.4f}"]])

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
        self._fill_editors()
        return True

    def _fill_editors(self) -> None:
        """Поля фикстуры и выхода панели (3.7) — по выбору: первая выбранная фикстура и текущий выход."""
        ui, patch = self._ui(), self._engine().patch
        if ui is None or patch is None:
            return
        fe, oe = ui.op("fixture_edit"), ui.op("output_edit")
        fx = next((f for f in patch.fixtures if self.selection and f.id == self.selection[0]), None)
        if fe is not None:
            a = fx.address if fx is not None else None
            fe.par.Fixture = fx.id if fx is not None else ""
            fe.par.Output = (a.output_id or "") if a is not None else ""
            fe.par.Universe = a.universe if a is not None and a.universe is not None else 0
            fe.par.Channel = a.channel if a is not None and a.channel is not None else 1
        out_id = self.CurrentOutput()
        out = next((o for o in patch.outputs if o.id == out_id), None)
        if oe is not None and out is not None:
            oe.par.Output = out.id
            oe.par.Ip = out.ip or ""
            oe.par.Start = out.start_universe
            oe.par.Count = out.universe_count or 0

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
            self._write_render()
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
        self._write_render()
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

    def _ui(self):
        return self.ownerComp.op("ui_dmx")

    def _map_size(self) -> tuple[float, float]:
        """Размер области карты в пикселях — из пар панели (не из геометрии панели: см. мануал, panel layout freeze)."""
        ui = self._ui()
        return (float(ui.par.w) - float(ui.par.Sidew),
                float(ui.par.h) - float(ui.par.Headerh) - float(ui.par.Zoomh))

    def _write_render(self) -> None:
        """Таблицы инстансов и подписей для рендеров панели (3.2) — пиксели области; без панели — ничего."""
        ui = self._ui()
        if ui is None:
            return
        L = self.logic
        w, h = self._map_size()
        g = float(ui.par.Gutterw)
        zw, zh = w, float(ui.par.Zoomh)
        rects, zoom, prev = self._rows("pv_rects"), self._rows("pv_zoom"), self._rows("pv_preview")
        self._write("rn_map_fill", L.render_rows(rects, g, 0, w - g, h, (0, 1)))
        self._write("rn_map_line", L.render_rows(rects, g, 0, w - g, h, (2, 3)))
        self._write("rn_map_prev", L.render_rows(prev, g, 0, w - g, h, (0, 1, 2, 3)))
        self._write("rn_zoom_fill", L.render_rows(zoom, 0, 0, zw, zh, (0, 1)))
        self._write("rn_zoom_line", L.render_rows(zoom, 0, 0, zw, zh, (2, 3)))
        self._write("rn_zoom_grid", L.render_rows(zoom, 0, 0, zw, zh, (0,)))     # сетка клеток поверх сегментов
        nrows = max(len(self.layout.rows), 1) if self.layout is not None else 1
        self._write("rn_map_labels", L.label_spec(self._rows("pv_labels"), w, h, g, float(ui.par.Labelsize),
                                                   row_h=h / nrows))

    def _write_preview_render(self) -> None:
        """При перетаскивании меняется только слой предпросмотра — остальные таблицы рендера не трогаем."""
        ui = self._ui()
        if ui is None:
            return
        w, h = self._map_size()
        g = float(ui.par.Gutterw)
        self._write("rn_map_prev", self.logic.render_rows(self._rows("pv_preview"), g, 0, w - g, h, (0, 1, 2, 3)))

    def _rows(self, name: str) -> list[list[str]]:
        d = self._table(name)
        return [[c.val for c in r] for r in d.rows()] if d is not None else []

    def _write_status(self) -> None:
        self._write("pv_status", self._status_rows(self._engine(), *self._counts))
        self._engine().WriteInfo()

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
