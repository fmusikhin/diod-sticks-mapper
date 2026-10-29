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
        self._undo: list = []                  # документы файла до правок (стек отмены, до 50 шагов)
        self._redo: list = []                  # документы, снятые отменой (стек повтора)
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
        self._ed: dict | None = None           # жест редактора маппинга: пан, рамка, сдвиг, масштаб, поворот, линия
        self.view = None                       # вид холста (x, y, ширина) в пикселях холста; None — вписать
        self._space = False                    # пробел зажат — левая кнопка панорамирует
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
        self._sync_pars(Dmxwindow=True)
        if self._main() is not None:
            self.MainOpen("dmx")
            return
        self._cooking(True)
        self._rebuild()
        ui.op("win").par.winopen.pulse()

    def Close(self) -> None:
        """Закрыть окно; при закрытом окне рендеры карты не кукаются (1.6)."""
        ui = self._ui()
        if ui is None:
            return
        if self._main() is not None:
            self.MainClose()
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
        self._fill_insp()
        self._sync_list_selection()
        ui = self._mui()
        if ui is not None and bool(ui.par.Locatesel):
            self.MapLocate(bool(sel))
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

    @property
    def undo_doc(self):
        return self._undo[-1] if self._undo else None

    def Undo(self) -> bool:
        """Отменить правку (Ctrl+Z): вернуть документ файла до неё; многошагово (до 50). Действует, только пока файл —
        тот же и не менялся снаружи после нашей записи (иначе затёрла бы чужое); работает и при ошибке загрузки."""
        return self._step_history(self._undo, "undo", "отмена")

    def Redo(self) -> bool:
        """Повторить отменённое (Ctrl+Shift+Z / Ctrl+Y)."""
        return self._step_history(self._redo, "redo", "повтор")

    def _step_history(self, stack: list, kind: str, label: str) -> bool:
        name = "Undo" if kind == "undo" else "Redo"
        if not stack:
            return self._fail(f"{name}: {'нечего отменять' if kind == 'undo' else 'нечего повторять'}", notify=False)
        eng = self._engine()
        if eng.patch is None:
            return self._fail(f"{name}: патч не загружен")
        if not self._undo_valid():
            self._drop_undo()
            return self._fail(f"{name}: файл патча сменился или изменён после правки — история недоступна")
        doc = stack.pop()
        ok = self._edit(lambda p: doc, f"{label} ({len(self._undo) if kind == 'undo' else len(self._redo)} в запасе)",
                        history=kind)
        if not ok:
            stack.append(doc)
        return ok

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
        if self.undo_doc is not None and not getattr(self, "_writing", False) and not self._undo_valid():
            self._drop_undo()                  # чужая загрузка или внешняя правка файла — история недействительна
        self._grab, self._drop, self.place_id = None, None, None     # раскладка сменилась — жест и размещение сброшены
        self._ed = None
        ids = {f.id for f in patch.fixtures}
        if any(i not in ids for i in self.selection):                 # другой патч — выбор только из существующих
            self.selection = tuple(i for i in self.selection if i in ids)
        self._write("pv_preview", [self.logic.RECT_COLUMNS])
        if self.output_id is not None and not any(o.id == self.output_id for o in patch.outputs):
            self.output_id = None
            self.zoom_row = None
            self._sync_pars(Dmxoutput="")
        self._rebuild()
        self._fill_insp()

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
            elif name == "Mapwindow":
                self.MapOpen() if par.eval() else self.MapClose()
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

    def _edit(self, edit, label: str, history: str = "push", subject: str | None = None,
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
        self._writing = True                   # своя запись: слушатель загрузки не должен сбрасывать историю
        try:
            ok = eng.SavePatch(edit, True)
        finally:
            self._writing = False
        try:
            mtime1 = os.path.getmtime(full)
        except OSError:
            mtime1 = mtime0
        written = mtime1 != mtime0
        if written:                                                  # файл переписан (загрузка могла и не пройти)
            self._undo_file = (full, mtime1)
            if history == "push":
                self._undo = (self._undo + [snapshot])[-50:]
                self._redo = []
            elif history == "undo":
                self._redo.append(snapshot)
            else:
                self._undo.append(snapshot)
        if not ok:                                                   # движок уже вызвал onOutputError — второй раз не зовём
            if not written:
                return self._fail(f"правка не записана: {eng.message}", notify=False)
            hint = "; Undo вернёт прежний файл"
            return self._fail(f"правка записана, но патч не загрузился: {eng.message}{hint}", notify=False)
        skip = set(skip or ()) | ({subject} if subject else set())
        both = {f.id for f in before.fixtures} & {f.id for f in eng.patch.fixtures}   # новые и удалённые — не «сдвиг»
        moved = [f for f in self.logic.shifted(before, eng.patch) if f not in skip and f in both]   # кроме самих правленных фикстур
        self.message = f"{label}" + (f"; сдвинулось {len(moved)}: {' '.join(moved[:8])}" if moved else "")
        self._write("pv_status", self._status_rows(eng, *self._counts))   # таблицы уже перестроены слушателем загрузки
        eng.WriteInfo()
        self._fill_editors()
        self._fill_insp()
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
        self._undo, self._redo, self._undo_file = [], [], ("", 0.0)

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
            self._write_overlay()
            self._write_list()
            self._write_cells_out()
            self._sync_types()
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
        self._write_overlay()
        self._write_list()
        self._write_cells_out()
        self._sync_types()
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

    # ------------------------------------------------------------------ общее окно: INPUT / OUTPUT / DMX
    def _main(self):
        return self.ownerComp.op("ui_main")

    def MainOpen(self, mode: str | None = None) -> None:
        """Открыть общее окно на вкладке ``mode`` (input / output / dmx; None — текущая)."""
        main = self._main()
        if main is None:
            return
        self.MainMode(mode or main.par.Mode.eval())
        main.op("win").par.winopen.pulse()

    def MainClose(self) -> None:
        main = self._main()
        if main is None:
            return
        if self._mui() is not None and bool(self._mui().par.Locating):
            self.MapLocate(False)
        main.op("win").par.winclose.pulse()
        self._cooking(False)
        self._sync_pars(Dmxwindow=False, Mapwindow=False)

    def MainMode(self, mode: str) -> None:
        """Вкладка общего окна: input / output — холст маппинга, dmx — карта юниверсов."""
        main = self._main()
        if main is None:
            return
        main.par.Mode = mode
        self._cooking(mode == "dmx")
        if mode == "dmx":
            self._rebuild()
        else:
            self._sync_types()
            self.MapMode(mode)

    # ------------------------------------------------------------------ редактор маппинга (холст ui_map)
    def _mui(self):
        return self.ownerComp.op("ui_map")

    def _me(self):
        return self.ownerComp.op("mapedit_mod").module

    def _mode(self) -> str:
        """Холст окна маппинга: input — выборка цвета поверх контента, output — физическое расположение."""
        ui = self._mui()
        return ui.par.Mode.eval() if ui is not None and ui.par["Mode"] is not None else "input"

    def _P(self):
        """Патч холста текущего режима: input — патч движка, output — вид с output_shape (кэш по патчу)."""
        eng = self._engine()
        if eng.patch is None or self._mode() != "output":
            return eng.patch
        key = id(eng.patch)
        if getattr(self, "_pview", (None, None))[0] != key:
            self._pview = (key, self._me().to_output_view(eng.patch))
        return self._pview[1]

    def _medit(self, edit, label: str, **kw) -> bool:
        """Правка холста: на OUTPUT — через вид и обратно (входная фигура не меняется, пишется output_shape)."""
        me = self._me()
        if self._mode() != "output":                    # INPUT: двигается только выборка, OUTPUT остаётся на месте
            return self._edit(lambda p: me.pin_outputs(edit(p), p), label, **kw)
        return self._edit(lambda p: me.from_output_view(edit(me.to_output_view(p)), p), "OUTPUT: " + label, **kw)

    def MapMode(self, mode: str) -> None:
        """Переключить холст INPUT / OUTPUT (вид, фон и правки)."""
        ui = self._mui()
        if ui is None:
            return
        ui.par.Mode = mode
        self._ed, self._poly = None, None
        self.view = None
        self._write_overlay()
        self._fill_insp()

    def _area(self) -> tuple[float, float]:
        """Размер области холста в пикселях экрана — из пар окна (геометрию панели не читаем)."""
        c = self._engine().patch.canvas
        ui = self._mui()
        aw = float(ui.par.w) - float(ui.par.Sidew) - float(ui.par.Listw) - float(ui.par.Splitw)
        return aw, float(ui.par.h) - float(ui.par.Headerh)          # холст на всю высоту под шапкой

    def _canvas_scale(self) -> tuple[float, float, float]:
        """(ширина, высота холста патча, пикселей холста на пиксель экрана при текущем виде)."""
        c = self._engine().patch.canvas
        aw, _ah = self._area()
        return float(c.width), float(c.height), self._get_view()[2] / max(aw, 1.0)

    def _get_view(self):
        if self.view is None:
            c = self._engine().patch.canvas
            self._set_view(self._me().fit_view(c.width, c.height, *self._area()))
        return self.view

    def _set_view(self, view) -> None:
        """Вид холста (x, y левого нижнего угла, ширина — пиксели холста) → пары окна, камера читает их выражениями."""
        self.view = tuple(float(x) for x in view)
        ui = self._mui()
        ui.par.Viewx, ui.par.Viewy, ui.par.Vieww = self.view

    def MapFit(self) -> None:
        if self._engine().patch is None or self._mui() is None:
            return
        c = self._engine().patch.canvas
        self._set_view(self._me().fit_view(c.width, c.height, *self._area()))
        self._write_overlay()

    def MapOneToOne(self) -> None:
        """Масштаб 1:1 (пиксель холста = пиксель экрана) вокруг центра вида."""
        if self._engine().patch is None or self._mui() is None:
            return
        aw, ah = self._area()
        vx, vy, vw = self._get_view()
        cx, cy = vx + vw / 2, vy + vw * ah / aw / 2
        self._set_view((cx - aw / 2, cy - ah / 2, aw))
        self._write_overlay()

    def OnCanvasWheel(self, u: float, v: float, delta: float) -> None:
        """Колесо — масштаб к курсору."""
        if self._engine().patch is None or self._mui() is None or not delta:
            return
        self._set_view(self._me().zoom_at(self._get_view(), u, v, 1.15 if delta > 0 else 1 / 1.15, *self._area()))
        self._write_overlay()

    def OnCanvas(self, event: str, u: float, v: float, shift: bool = False, ctrl: bool = False, alt: bool = False) -> None:
        """Мышь на холсте (как в Resolume). Средняя кнопка или пробел+левая — пан. «Выбор»: клик — выбрать,
        Shift+клик — добавить, Ctrl+клик — переключить; протяжка по пустому — рамка; тело/концы — сдвиг с привязкой
        к концам соседей и сетке (Shift — ось, Alt — без привязок); углы рамки — масштаб (Shift — пропорционально,
        Alt — от центра), верхняя ручка — поворот (Shift — шаг 15°). «Линия»: тянуть от начала до конца."""
        eng, ui = self._engine(), self._mui()
        if self._P() is None or eng.status != "ok" or ui is None:
            return
        me = self._me()
        aw, ah = self._area()
        _cw, _ch, scale = self._canvas_scale()
        x, y = me.to_canvas(self._get_view(), u, v, aw, ah)
        step = 0.0 if alt else float(ui.par.Snap)
        ed = self._ed
        if event in ("mdown", "down") and (event == "mdown" or self._space):
            self._ed = {"mode": "pan", "u0": u, "v0": v, "view0": self._get_view()}
            return
        if ed is not None and ed["mode"] == "pan":
            if event in ("move", "mmove"):
                vx, vy, vw = ed["view0"]
                self._set_view((vx - (u - ed["u0"]) * vw, vy - (v - ed["v0"]) * vw * ah / aw, vw))
                self._write_overlay()
            elif event in ("up", "mup"):
                self._ed = None
            return
        tool = ui.par.Tool.eval()
        self._shift = shift
        if tool == "poly":
            self._poly_event(event, x, y, step, scale)
            return
        if event == "hover":
            return
        if event == "down":
            self._ed = None
            if tool in ("line", "rect"):
                p0 = (me.snap(x, step), me.snap(y, step))
                self._ed = {"mode": "draw" if tool == "line" else "rect", "p1": p0, "p2": p0}
                self._write_overlay()
                return
            bbox = me.sel_bbox(self._P(), self.selection) if self.selection else None
            fid0, h0 = me.hit(self._P(), x, y, 6.0 * scale, self.selection)
            fh = None if h0 in ("p1", "p2") else (me.hit_frame(bbox, x, y, scale) if bbox is not None else None)  # концы линии важнее углов рамки
            if fh == "rot":
                self._ed = {"mode": "rotate", "ids": list(self.selection), "bbox": bbox, "x0": x, "y0": y, "deg": 0.0}
                return
            if fh is not None:
                self._ed = {"mode": "scale", "ids": list(self.selection), "bbox": bbox, "handle": fh,
                            "pivot": (0.0, 0.0), "sx": 1.0, "sy": 1.0}
                return
            fid, handle = me.hit(self._P(), x, y, 6.0 * scale, self.selection)
            if fid is None:
                if not (ctrl or shift):
                    self._set_selection([], notify=True)
                self._ed = {"mode": "box", "x0": x, "y0": y, "x1": x, "y1": y, "add": shift, "toggle": ctrl}
                self._write_overlay()
                return
            if ctrl:
                sel = [i for i in self.selection if i != fid] if fid in self.selection else [*self.selection, fid]
                self._set_selection(sel, notify=True)
                return
            if shift and fid not in self.selection:
                self._set_selection([*self.selection, fid], notify=True)
            elif fid not in self.selection:
                self._set_selection([fid], notify=True)
            ids = list(self.selection) if handle == "body" else [fid]
            self._ed = {"mode": "drag", "ids": ids, "handle": handle, "x0": x, "y0": y, "dx": 0.0, "dy": 0.0, "mark": None}
            self._write_overlay()
            return
        if ed is None:
            return
        if event == "move":
            m = ed["mode"]
            if m in ("draw", "rect"):
                p1 = ed["p1"]
                q = (me.snap(x, step), me.snap(y, step))
                if shift and m == "draw":
                    q = me.snap_angle(p1, (x, y), 15.0)               # угол кратен 15°
                ed["p2"], ed["square"] = q, shift
            elif m == "box":
                ed["x1"], ed["y1"] = x, y
            elif m == "drag":
                dx, dy = x - ed["x0"], y - ed["y0"]
                ed["mark"] = None
                if shift and ed["handle"] in ("p1", "p2"):             # конец линии: угол к другому концу кратен 15°
                    fx = next(f for f in self._P().fixtures if f.id == ed["ids"][0])
                    other = fx.shape.p2 if ed["handle"] == "p1" else fx.shape.p1
                    cur = getattr(fx.shape, ed["handle"])
                    q = me.snap_angle(other, (cur[0] + dx, cur[1] + dy), 15.0)
                    ed["dx"], ed["dy"] = q[0] - cur[0], q[1] - cur[1]
                    self._write_overlay()
                    return
                if shift:
                    dx, dy = (dx, 0.0) if abs(dx) >= abs(dy) else (0.0, dy)
                if not alt:
                    if ed["handle"] in ("p1", "p2"):
                        fx = next(f for f in self._P().fixtures if f.id == ed["ids"][0])
                        moving = [getattr(fx.shape, ed["handle"])]
                    else:
                        moving = [q for f in self._P().fixtures if f.id in set(ed["ids"]) for q in me.outline(f.shape)[0]]
                    targets = me.endpoints(self._P(), ed["ids"])
                    sdx, sdy, mark = me.snap_delta(moving, targets, dx, dy, 10.0 * scale)
                    if mark is not None:
                        dx, dy, ed["mark"] = sdx, sdy, mark
                    elif step > 0 and moving:
                        ox, oy = moving[0]
                        dx, dy = me.snap(ox + dx, step) - ox, me.snap(oy + dy, step) - oy
                ed["dx"], ed["dy"] = dx, dy
            elif m == "scale":
                ed["pivot"], ed["sx"], ed["sy"] = me.scale_params(ed["bbox"], ed["handle"], x, y, shift, alt)
            elif m == "rotate":
                _piv, ed["deg"] = me.rotate_angle(ed["bbox"], ed["x0"], ed["y0"], x, y, 15.0 if shift else 0.0)
            self._write_overlay()
            return
        if event == "up":
            self._ed = None
            m = ed["mode"]
            if m == "box":
                hit_ids = me.box_select(self._P(), ed["x0"], ed["y0"], ed["x1"], ed["y1"])
                if abs(ed["x1"] - ed["x0"]) + abs(ed["y1"] - ed["y0"]) > 3 * scale:
                    if ed["toggle"]:
                        cur = list(self.selection)
                        sel = [i for i in cur if i not in hit_ids] + [i for i in hit_ids if i not in cur]
                    elif ed["add"]:
                        sel = list(dict.fromkeys([*self.selection, *hit_ids]))
                    else:
                        sel = hit_ids
                    self._set_selection(sel, notify=True)
            elif m == "rect":
                p1, p2 = ed["p1"], ed["p2"]
                if abs(p2[0] - p1[0]) >= 8 * scale and (abs(p2[1] - p1[1]) >= 8 * scale or ed.get("square")):
                    shape = me.rect_shape(p1, p2, ed.get("square", False))
                    if not me.is_matrix(self._P(), ui.par.Addtype.eval()):
                        # профиль в один ряд — палка по длинной стороне прямоугольника
                        from dsm_core.model import LineShape
                        (cx, cy), (w, h) = shape.center, shape.size
                        shape = (LineShape(p1=(cx - w / 2, cy), p2=(cx + w / 2, cy)) if w >= h
                                 else LineShape(p1=(cx, cy - h / 2), p2=(cx, cy + h / 2)))
                    self._create(shape, "прямоугольник")
            elif m == "draw":
                p1, p2 = ed["p1"], ed["p2"]
                if (p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2 >= (8 * scale) ** 2:
                    type_id = ui.par.Addtype.eval()
                    ft = next((x for x in self._P().fixture_types if x.id == type_id), None)
                    if ft is not None and me.is_matrix(self._P(), type_id):         # матрица вдоль линии: W вдоль, H поперёк
                        self._create(me.line_to_rect(p1, p2, ft.width, ft.height), f"матрица {ft.width}×{ft.height}")
                    else:
                        from dsm_core.model import LineShape
                        self._create(LineShape(p1=p1, p2=p2), "линия")
            elif m == "drag" and abs(ed["dx"]) + abs(ed["dy"]) > 0.01:
                ids, handle, dx, dy = ed["ids"], ed["handle"], ed["dx"], ed["dy"]
                if handle in ("p1", "p2"):
                    edit = lambda p: me.apply_drag(p, ids, handle, dx, dy, 0.0)  # noqa: E731
                else:
                    edit = lambda p: me.apply_shift(p, ids, dx, dy)  # noqa: E731
                self._medit(edit, f"{' '.join(ids[:4])}: {'конец ' + handle if handle != 'body' else 'сдвиг'} {dx:+.0f} {dy:+.0f}",
                           subject=ids[0] if len(ids) == 1 else None)
            elif m == "scale" and (abs(ed["sx"] - 1) > 1e-4 or abs(ed["sy"] - 1) > 1e-4):
                ids, piv, sx, sy = ed["ids"], ed["pivot"], ed["sx"], ed["sy"]
                self._medit(lambda p: me.apply_transform(p, ids, piv, sx, sy, 0.0), f"масштаб {sx:.2f} × {sy:.2f}")
            elif m == "rotate" and abs(ed["deg"]) > 1e-3:
                ids, deg = ed["ids"], ed["deg"]
                piv, _a = me.rotate_angle(ed["bbox"], 0, 1, 0, 1)
                self._medit(lambda p: me.apply_transform(p, ids, piv, 1.0, 1.0, deg), f"поворот {deg:+.1f}°")
            self._write_overlay()

    def _create(self, shape, what: str) -> bool:
        """Новая фикстура профиля из окна (Addtype) на текущем выходе в авто; выбирается."""
        eng, ui, me = self._engine(), self._mui(), self._me()
        type_id, out = ui.par.Addtype.eval(), self.CurrentOutput()
        new_id = eng.api.next_ids(self._P(), "fixture")[0]
        ok = self._medit(lambda p: me.new_fixture(p, type_id, shape, out)[0], f"новая {new_id}: {what} ({type_id})")
        if ok:
            self._set_selection([new_id], notify=True)
        return ok

    def _poly_event(self, event: str, x: float, y: float, step: float, scale: float) -> None:
        """Ломаная: клик — точка, двойной клик или клик по первой точке / Enter — готово, Backspace — убрать точку,
        Esc — отмена; между кликами — резинка до курсора."""
        import time as _t
        me = self._me()
        q = (me.snap(x, step), me.snap(y, step))
        poly = getattr(self, "_poly", None)
        if poly and getattr(self, "_shift", False):
            q = me.snap_angle(poly["pts"][-1], (x, y), 15.0)
        if event in ("hover", "move"):
            if poly:
                poly["cursor"] = q
                self._write_overlay()
            return
        if event != "down":
            return
        now = _t.perf_counter()
        if poly is None:
            self._poly = {"pts": [q], "cursor": q, "t": now}
        else:
            last, first = poly["pts"][-1], poly["pts"][0]
            near = lambda a: abs(a[0] - q[0]) + abs(a[1] - q[1]) <= 8 * scale  # noqa: E731
            if (now - poly["t"] < 0.35 and near(last)) or (len(poly["pts"]) > 2 and near(first)):
                self.MapPolyFinish()
                return
            poly["pts"].append(q)
            poly["t"] = now
        self._write_overlay()

    def MapPolyFinish(self) -> bool:
        poly, self._poly = getattr(self, "_poly", None), None
        if not poly or len(poly["pts"]) < 2:
            self._write_overlay()
            return False
        me = self._me()
        pts = poly["pts"]
        from dsm_core.model import LineShape
        shape = LineShape(p1=pts[0], p2=pts[1]) if len(pts) == 2 else me.poly_shape(pts)
        ok = self._create(shape, f"ломаная {len(pts)} точек")
        self._write_overlay()
        return ok

    def MapBetween(self, count: int) -> bool:
        """Раскладка N линий между двумя выбранными линиями."""
        if len(self.selection) != 2:
            return self._fail("Между: выберите ровно две линии", notify=False)
        if not self._can_edit("Между"):
            return False
        eng, me = self._engine(), self._me()
        a, b = self.selection
        count = max(1, int(count))
        new = eng.api.next_ids(self._P(), "fixture", count)
        try:
            me.between(self._P(), a, b, count)
        except (ValueError, KeyError) as e:
            return self._fail(f"Между: {e}", notify=False)
        ok = self._medit(lambda p: me.between(p, a, b, count)[0], f"между {a} и {b}: {count} шт.", skip=set(new))
        if ok:
            self._set_selection(new, notify=True)
        return ok

    def _ghost(self):
        """Патч «как будет после жеста» для оверлея; без жеста — патч движка."""
        eng, me, ed = self._engine(), self._me(), self._ed
        p = self._P()
        if ed is None:
            return p
        m = ed["mode"]
        if m == "drag" and (ed["dx"] or ed["dy"]):
            if ed["handle"] in ("p1", "p2"):
                return me.apply_drag(p, ed["ids"], ed["handle"], ed["dx"], ed["dy"], 0.0)
            return me.apply_shift(p, ed["ids"], ed["dx"], ed["dy"])
        if m == "scale":
            return me.apply_transform(p, ed["ids"], ed["pivot"], ed["sx"], ed["sy"], 0.0)
        if m == "rotate" and ed["deg"]:
            piv, _a = me.rotate_angle(ed["bbox"], 0, 1, 0, 1)
            return me.apply_transform(p, ed["ids"], piv, 1.0, 1.0, ed["deg"])
        return p


    # ------------------------------------------------------------------ инспектор и список окна маппинга
    INSP_GEOM = ("X1", "Y1", "X2", "Y2", "Length", "Angle")

    def _insp(self):
        ui = self._mui()
        return ui.op("insp") if ui is not None else None

    def _fill_insp(self) -> None:
        """Инспектор — по первой выбранной фикстуре; меню профилей и выходов — из патча."""
        insp, patch = self._insp(), self._engine().patch
        if insp is None or patch is None:
            return
        was, self._in_insp = getattr(self, "_in_insp", False), True
        try:
            insp.par.Type.menuNames = [t.id for t in patch.fixture_types] or ["none"]
            insp.par.Type.menuLabels = [t.name for t in patch.fixture_types] or ["—"]
            insp.par.Output.menuNames = ["none"] + [o.id for o in patch.outputs]   # пустое имя пункта TD не принимает
            insp.par.Output.menuLabels = ["— no output"] + [o.name for o in patch.outputs]
            fx = next((f for f in patch.fixtures if self.selection and f.id == self.selection[0]), None)
            insp.par.Fixture = (fx.id + (f"  (+{len(self.selection) - 1})" if len(self.selection) > 1 else "")) if fx else ""
            if fx is None:
                return
            me = self._me()
            s = fx.shape
            insp.par.Name = fx.name
            insp.par.Type = fx.type_id
            if hasattr(s, "p1"):
                (x1, y1), (x2, y2) = s.p1, s.p2
            else:
                bb = me.sel_bbox(patch, [fx.id])
                x1, y1, x2, y2 = bb
            insp.par.X1, insp.par.Y1, insp.par.X2, insp.par.Y2 = round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)
            insp.par.Length = round(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 2)
            import math
            insp.par.Angle = round(math.degrees(math.atan2(y2 - y1, x2 - x1)), 2)
            insp.par.Reverse = fx.direction.value == "reverse"
            insp.par.Mute = bool(fx.mute)
            insp.par.Output = fx.address.output_id or "none"
            insp.par.Universe = fx.address.universe if fx.address.universe is not None else 0
            insp.par.Channel = fx.address.channel if fx.address.channel is not None else 1
            self._insp_filled = {n: insp.par[n].eval() for n in
                                 ("Name", "Type", "X1", "Y1", "X2", "Y2", "Length", "Angle", "Reverse", "Mute")}
        finally:
            self._in_insp = was

    def MapInspector(self, par) -> None:
        """Правка из инспектора: значение пары → правка ядра общим путём (геометрия, имя, профиль, направление, mute,
        адрес). Пульсы: Apply — ручной адрес, Toauto — в авто на выбранном выходе."""
        if getattr(self, "_in_insp", False):
            return
        eng, insp = self._engine(), self._insp()
        if self._P() is None or insp is None or not self.selection:
            return
        fid = self.selection[0]
        fx = next((f for f in self._P().fixtures if f.id == fid), None)
        if fx is None:
            return
        filled = getattr(self, "_insp_filled", {})
        if par.name in filled and not par.isPulse and par.eval() == filled[par.name]:
            return        # отложенное эхо заполнения инспектора (в т. ч. после смены INPUT/OUTPUT) — не правка
        name, api, me = par.name, eng.api, self._me()
        import math
        from dataclasses import replace as _replace
        if name in self.INSP_GEOM:
            x1, y1, x2, y2 = (float(insp.par[n]) for n in ("X1", "Y1", "X2", "Y2"))
            if name in ("Length", "Angle"):
                if hasattr(fx.shape, "p1"):
                    (ax, ay), (bx, by) = fx.shape.p1, fx.shape.p2
                    cur_l, cur_a = math.hypot(bx - ax, by - ay), math.degrees(math.atan2(by - ay, bx - ax))
                    if abs(cur_l - float(insp.par.Length)) < 0.01 and abs(cur_a - float(insp.par.Angle)) < 0.01:
                        return
                L, a = float(insp.par.Length), math.radians(float(insp.par.Angle))
                x2, y2 = x1 + L * math.cos(a), y1 + L * math.sin(a)
            if hasattr(fx.shape, "p1"):
                shape = _replace(fx.shape, p1=(x1, y1), p2=(x2, y2))
                same = max(abs(x1 - fx.shape.p1[0]), abs(y1 - fx.shape.p1[1]), abs(x2 - fx.shape.p2[0]),
                           abs(y2 - fx.shape.p2[1])) < 0.01
            else:
                bb = me.sel_bbox(self._P(), [fid])
                shape = me.shift_shape(fx.shape, x1 - bb[0], y1 - bb[1])
                same = abs(x1 - bb[0]) + abs(y1 - bb[1]) < 0.01
            if same:          # отложенное эхо заполнения инспектора (или округление) — не правка
                return
            self._medit(lambda p: api.update_fixture(p, fid, shape=shape), f"{fid}: геометрия", subject=fid)
        elif name == "Name":
            v = str(par.eval()).strip()
            if v and v != fx.name:
                self._medit(lambda p: api.update_fixture(p, fid, name=v), f"{fid}: имя «{v}»", subject=fid)
        elif name == "Type":
            v = str(par.eval())
            if v and v != "none" and v != fx.type_id:
                ft = next((x for x in self._P().fixture_types if x.id == v), None)
                changes = {"type_id": v}
                if ft is not None and me.is_matrix(self._P(), v) and hasattr(fx.shape, "p1"):
                    changes["shape"] = me.line_to_rect(fx.shape.p1, fx.shape.p2, ft.width, ft.height)  # линия → матрица
                self._medit(lambda p: api.update_fixture(p, fid, **changes), f"{fid}: профиль {v}", subject=fid)
        elif name == "Reverse":
            want = "reverse" if par.eval() else "forward"
            if fx.direction.value != want:
                self._medit(lambda p: me.reverse(p, [fid]), f"{fid}: направление {want}", subject=fid)
        elif name == "Mute":
            v = bool(par.eval())
            if v != bool(fx.mute):
                self._medit(lambda p: api.update_fixture(p, fid, mute=v), f"{fid}: mute {'вкл' if v else 'выкл'}", subject=fid)
        elif name == "Apply":
            out = insp.par.Output.eval()
            if out in ("", "none"):
                self._fail("адрес: выберите выход", notify=False)
            else:
                self.ReAddress(fid, out, insp.par.Universe.eval(), insp.par.Channel.eval())
        elif name == "Toauto":
            out = insp.par.Output.eval()
            self.ToAuto(fid, None if out in ("", "none") else out)
        self._fill_insp()

    def _write_list(self) -> None:
        """Таблица me_list для списка фикстур окна маппинга: порядок патча, адрес, состояние вывода."""
        if self._mui() is None:
            return
        eng = self._engine()
        rows = [["id", "name", "type", "addr", "state"]]
        if eng.patch is not None:
            bad = {f for i in eng.issues if i.level.value == "error" for f in i.fixture_ids}
            types = {t.id: t for t in eng.patch.fixture_types}
            for f in eng.patch.fixtures:
                a = f.address
                addr = f"{a.universe}:{a.channel}" if a.output_id and a.universe is not None else (a.output_id or "—")
                rows.append([f.id, f.name, self._type_short(types.get(f.type_id)), addr,
                             "✕" if f.id in bad else ("m" if f.mute else "")])
        self._write("me_list", rows)

    def MapRename(self, fixture_id: str, name: str) -> bool:
        """Переименовать фикстуру (список — двойной клик по имени, инспектор — поле «Имя»)."""
        name = (name or "").strip()
        eng = self._engine()
        fx = next((f for f in eng.patch.fixtures if f.id == fixture_id), None) if eng.patch is not None else None
        if fx is None or not name or name == fx.name or not self._can_edit("Имя"):
            return False
        api = eng.api
        return self._edit(lambda p: api.update_fixture(p, fixture_id, name=name), f"{fixture_id}: имя «{name}»",
                          subject=fixture_id)

    @staticmethod
    def _type_short(ft) -> str:
        """Профиль коротко: матрица — «W×H», остальные — «N px»."""
        if ft is None:
            return "?"
        return f"{ft.width}×{ft.height}" if ft.kind.value == "matrix" else f"{ft.cells} px"

    def ListSelect(self, ids) -> None:
        """Выбор строками списка → выбор на холсте (без обратной записи в список)."""
        if getattr(self, "_list_sync", False):
            return
        self._from_list = True
        try:
            self._set_selection(list(ids), notify=True)
        finally:
            self._from_list = False

    def _sync_list_selection(self) -> None:
        ui = self._mui()
        if ui is None or getattr(self, "_from_list", False):
            return
        ls = ui.op("body_lr/list_col/lister")
        if ls is None:
            return
        try:
            ext = ls.ext.ListerExt
            sel = set(self.selection)
            self._list_sync = True
            ext.SelectRow(None)
            first = True
            for r, d in enumerate(ext.Data):
                obj = d.get("rowObject")
                if isinstance(obj, dict) and obj.get("id") in sel:
                    ext.SelectRow(r, addRow=not first)
                    first = False
        except Exception:  # список ещё не отрисован (лист без панели) — выбор на холсте важнее
            pass
        finally:
            self._list_sync = False

    def _write_cells_out(self) -> None:
        """me_cells_out — позиции ячеек на OUTPUT (по output_shape) в порядке строк geo_cells движка: GPU-превью
        живого света берёт цвет точки движка и ставит её сюда. Python — только при загрузке патча."""
        if self._mui() is None:
            return
        eng = self._engine()
        cells = self.ownerComp.op("tables/geo_cells")
        rows = [["P(0)", "P(1)", "P(2)"]]
        if eng.patch is not None and cells is not None and cells.numRows > 1:
            geo, patch = eng.geometry, eng.patch
            types = {t.id: t for t in patch.fixture_types}
            out_pos: dict[int, list] = {}
            for k, fx in enumerate(patch.fixtures):
                if fx.output_shape is None or fx.type_id not in types:
                    continue
                try:
                    from dataclasses import replace as _r
                    fo = _r(fx, shape=fx.output_shape)
                    ft = types[fx.type_id]
                    order = geo.cell_order(ft, fo)
                    by_grid = geo._positions_by_grid(patch.canvas, ft, fo, len(order))
                    out_pos[k] = [by_grid[c.grid_index] for c in order]
                except Exception:  # форма выхода не поддержана — точки остаются на входе
                    continue
            ci = {cells[0, c].val: c for c in range(cells.numCols)}
            for r in range(1, cells.numRows):
                fx_i, di = int(cells[r, ci["fx"]]), int(cells[r, ci["cell"]])
                pos = out_pos.get(fx_i)
                if pos is not None and di < len(pos):
                    rows.append([f"{pos[di][0]:.3f}", f"{pos[di][1]:.3f}", "0"])
                else:
                    rows.append([cells[r, ci["P(0)"]].val, cells[r, ci["P(1)"]].val, "0"])
        self._write("me_cells_out", rows)

    def _write_overlay(self) -> None:
        ui, eng = self._mui(), self._engine()
        if ui is None:
            return
        me = self._me()
        if self._P() is None:
            self._write("me_overlay", [me.OVERLAY_COLUMNS])
            return
        _cw, _ch, scale = self._canvas_scale()
        ed = self._ed
        try:
            ghost = self._ghost()
        except Exception:  # жест дал недопустимую фигуру (ядро) — показываем без призрака
            ghost = self._P()
        bad = {f for i in eng.issues if i.level.value == "error" for f in i.fixture_ids}   # не выводятся движком
        draw = (ed["p1"], ed["p2"]) if ed and ed["mode"] == "draw" else None
        if ed and ed["mode"] == "rect":
            r = me.rect_shape(ed["p1"], ed["p2"], ed.get("square", False))
            c = me.outline(r)[0]
            draw = None
            extra_rect = [me._line_row(a, b, me.LINE_PX * 1.5 * scale, me.DRAG_RGB) for a, b in zip(c, c[1:] + c[:1])]
        else:
            extra_rect = []
        colors = {f.id: self.logic.fixture_color(f.id) for f in ghost.fixtures} if self._mode() == "output" else None
        rows = me.overlay_rows(ghost, self.selection, scale, None, draw, bad, colors)
        if self.selection and ui.par.Tool.eval() == "select":
            bb = me.sel_bbox(ghost, self.selection)
            if bb is not None:
                rows += me.frame_rows(bb, scale)
        rows += extra_rect
        rows += me.matrix_rows(ghost, self.selection, scale)
        if bool(ui.par.Locating):                                    # мигающие на живом свете — жёлтой обводкой на холсте
            for fx in ghost.fixtures:
                if fx.id in self.selection:
                    pts, closed = me.outline(fx.shape)
                    rows += [me._line_row(a, b, me.LINE_PX * 3 * scale, (1.0, 0.9, 0.1), 0.6)
                             for a, b in zip(pts, pts[1:] + (pts[:1] if closed else []))]
        if getattr(self, "_poly", None):
            rows += me.poly_rows(self._poly["pts"], self._poly.get("cursor"), scale)
        if ed and ed["mode"] == "box":
            rows += me.box_rows(ed["x0"], ed["y0"], ed["x1"], ed["y1"], scale)
        if ed and ed["mode"] == "drag" and ed.get("mark") is not None:
            rows += me.mark_rows(ed["mark"], scale)
        self._write("me_overlay", rows)
        self._write("me_labels", me.label_rows(ghost, scale, bad=bad) if bool(ui.par.Showlabels) else [me.LABEL_SPEC_COLUMNS])

    def MapDuplicate(self, dx: float, dy: float, count: int = 1) -> bool:
        """Массив: копии выбранных со сдвигом (dx, dy) × count; новые выбираются."""
        eng = self._engine()
        if not self._can_edit("Дублировать"):
            return False
        ids = list(self.selection)
        if not ids:
            return self._fail("Дублировать: ничего не выбрано", notify=False)
        count = max(1, int(count))
        new = eng.api.next_ids(self._P(), "fixture", len(ids) * count)
        api = eng.api
        me = self._me()
        ok = self._medit(lambda p: me.renumber(api.duplicate_fixtures(p, ids, float(dx), float(dy), count)[0], new),
                        f"копии: {len(new)} шт. шаг {dx:+g} {dy:+g}", skip=set(new))
        if ok:
            self._set_selection(new, notify=True)
        return ok

    def MapNudge(self, dx: float, dy: float) -> bool:
        """Сдвиг выбранных стрелками (пиксели холста, без сетки)."""
        if not self.selection or not self._can_edit("Сдвиг"):
            return False
        ids, me = list(self.selection), self._me()
        return self._medit(lambda p: me.apply_drag(p, ids, "body", dx, dy, 0.0), f"сдвиг {dx:+g} {dy:+g}",
                          subject=ids[0] if len(ids) == 1 else None)

    def MapKeyState(self, key: str, down: bool) -> None:
        """Удерживаемые клавиши окна маппинга: пробел — пан левой кнопкой."""
        if key.lower() == "space":
            self._space = bool(down)

    def MapKey(self, key: str, shift: bool = False, ctrl: bool = False) -> None:
        """Клавиши окна маппинга (как в Resolume): Delete/Backspace — удалить, стрелки — сдвиг 1 px (Shift — 10),
        Ctrl+D — массив, Ctrl+A — выбрать все, Ctrl+Z — отмена, Esc — снять выбор, V — выбор, L — линия."""
        ui, eng = self._mui(), self._engine()
        if ui is None or eng.patch is None:
            return
        k = key.lower()
        step = 10.0 if shift else 1.0
        if k == "delete" or (k == "backspace" and not getattr(self, "_poly", None)):
            self.MapDelete()
        elif k in ("left", "right", "up", "down"):
            dx, dy = {"left": (-step, 0), "right": (step, 0), "up": (0, step), "down": (0, -step)}[k]
            self.MapNudge(dx, dy)
        elif ctrl and k == "d":
            self.MapDuplicate(ui.par.Arrdx.eval(), ui.par.Arrdy.eval(), ui.par.Arrcount.eval())
        elif ctrl and k == "a":
            self._set_selection([f.id for f in eng.patch.fixtures], notify=True)
        elif ctrl and k == "z":
            self.Redo() if shift else self.Undo()
        elif ctrl and k == "y":
            self.Redo()
        elif ctrl and k == "c":
            self._clip = list(self.selection)
            self.message = f"скопировано: {len(self._clip)}"
            self._write_status()
        elif ctrl and k == "v":
            self.MapPaste()
        elif k in ("enter", "return") and getattr(self, "_poly", None):
            self.MapPolyFinish()
        elif k == "backspace" and getattr(self, "_poly", None):
            self._poly["pts"] = self._poly["pts"][:-1]
            if not self._poly["pts"]:
                self._poly = None
            self._write_overlay()
        elif k == "esc" and getattr(self, "_poly", None):
            self._poly = None
            self._write_overlay()
        elif k == "esc":
            self._ed = None
            if bool(ui.par.Locating):
                self.MapLocate(False)
            self._set_selection([], notify=True)
            self._write_overlay()
        elif not ctrl and k == "f":
            self.MapFit()
        elif not ctrl and k == "1":
            self.MapOneToOne()
        elif not ctrl and k == "v":
            ui.par.Tool = "select"
        elif not ctrl and k == "l":
            ui.par.Tool = "line"
        elif not ctrl and k == "b":
            self.MapLocate()
        elif not ctrl and k == "p":
            ui.par.Tool = "poly"
        elif not ctrl and k == "r":
            ui.par.Tool = "rect"

    def MapLocate(self, on: bool | None = None) -> bool:
        """Locate выбранных (как «мигнуть» фикстурой на живом свете): включить — цель = выбор, режим пары Mode
        страницы Locate; выключить — снять. ``on=None`` — переключить."""
        loc = self.ownerComp.ext.DsmLocateExt
        if getattr(self, "_in_locate", False):
            return False
        active = loc.state.target is not None and bool(loc.state.target.fixtures)
        on = (not active) if on is None else on
        ui = self._mui()
        if not on or not self.selection:
            loc.Clear()
            if ui is not None:
                self._in_locate = True
                try:
                    ui.par.Locating = False
                finally:
                    self._in_locate = False
            self.message = "Locate снят"
            self._write_status()
            return False
        ok = loc.Locate(",".join(self.selection))
        if ui is not None:
            self._in_locate = True            # подсветка кнопки и переключатель — по паре окна (ext-атрибут TD не отслеживает)
            try:
                ui.par.Locating = ok
            finally:
                self._in_locate = False
        self.message = f"Locate: {' '.join(self.selection[:6])}" + ("" if ok else " — не разобран")
        self._write_status()
        return ok

    def MapNewMatrixType(self) -> bool:
        """Новый матричный профиль W×H из панели инструментов; становится профилем новых фикстур."""
        ui, eng = self._mui(), self._engine()
        if ui is None or not self._can_edit("Профиль"):
            return False
        w, h, lay, order = int(ui.par.Mtxw), int(ui.par.Mtxh), ui.par.Mtxlayout.eval(), ui.par.Mtxorder.eval()
        me = self._me()
        tid = eng.api.next_ids(eng.patch, "fixture_type")[0]
        ok = self._edit(lambda p: me.new_matrix_type(p, w, h, lay, order)[0], f"профиль {tid}: матрица {w}×{h} {lay}")
        if ok:
            self._sync_types()
            ui.par.Addtype = tid
        return ok

    LIB_PARS = ("Libname", "Libw", "Libh", "Libdist", "Liborder")

    def _fill_lib(self) -> None:
        """Блок Fixture library — по выбранному профилю (Addtype)."""
        ui, patch = self._mui(), self._engine().patch
        if ui is None or patch is None or ui.par["Libw"] is None:
            return
        ft = next((t for t in patch.fixture_types if t.id == ui.par.Addtype.eval()), None)
        if ft is None:
            return
        me = self._me()
        w, h = me.profile_dims(ft)
        was, self._in_lib = getattr(self, "_in_lib", False), True
        try:
            ui.par.Libname = ft.name
            ui.par.Libw, ui.par.Libh = w, h
            ui.par.Libdist = ft.layout.value
            ui.par.Liborder = ft.color_order
            self._lib_filled = {n: ui.par[n].eval() for n in self.LIB_PARS}
        finally:
            self._in_lib = was

    def MapLibrary(self, par) -> None:
        """Правка профиля из библиотеки — сразу для всех фикстур профиля (как в Resolume); эхо заполнения игнорируется."""
        if getattr(self, "_in_lib", False):
            return
        ui, eng = self._mui(), self._engine()
        if ui is None or eng.patch is None:
            return
        if par.name == "Addtype":
            self._fill_lib()
            return
        if par.name == "Newprofile":
            self.MapLibNew()
            return
        if par.name == "Addfixture":
            self.MapLibAdd()
            return
        filled = getattr(self, "_lib_filled", {})
        if par.name in filled and par.eval() == filled[par.name]:
            return
        tid = ui.par.Addtype.eval()
        if not any(t.id == tid for t in eng.patch.fixture_types) or not self._can_edit("Профиль"):
            return
        me = self._me()
        w, h, lay, order, name = int(ui.par.Libw), int(ui.par.Libh), ui.par.Libdist.eval(), ui.par.Liborder.eval(), ui.par.Libname.eval()
        if self._edit(lambda p: me.update_profile(p, tid, w, h, lay, order, name), f"профиль {tid}: {w}×{h} {lay} {order}"):
            self._sync_types()
            self._fill_lib()

    def MapLibNew(self) -> bool:
        """Новый профиль библиотеки (16×1) — сразу выбран, дальше правится полями."""
        eng, ui, me = self._engine(), self._mui(), self._me()
        if ui is None or not self._can_edit("Профиль"):
            return False
        tid = eng.api.next_ids(eng.patch, "fixture_type")[0]
        ok = self._edit(lambda p: me.new_profile(p, 16, 1)[0], f"профиль {tid}: новый 16×1")
        if ok:
            self._sync_types()
            ui.par.Addtype = tid
            self._fill_lib()
        return ok

    def MapLibAdd(self) -> bool:
        """Add to canvas: фикстура выбранного профиля в центре вида (как добавление из библиотеки в Resolume)."""
        ui = self._mui()
        if ui is None or self._P() is None:
            return False
        vx, vy, vw = self._get_view()
        aw, ah = self._area()
        center = (vx + vw / 2, vy + vw * ah / aw / 2)
        return self._create(self._me().default_shape(self._P(), ui.par.Addtype.eval(), center), "из библиотеки")

    def MapPaste(self) -> bool:
        """Вставка скопированного (Ctrl+V): копии со сдвигом на шаг сетки (минимум 20 px холста); новые выбираются."""
        ids = [i for i in getattr(self, "_clip", []) if any(f.id == i for f in self._engine().patch.fixtures)]
        if not ids:
            return self._fail("Вставка: буфер пуст", notify=False)
        step = max(20.0, float(self._mui().par.Snap)) if self._mui() is not None else 20.0
        sel = self.selection
        self.selection = tuple(ids)
        ok = self.MapDuplicate(step, -step, 1)
        if not ok:
            self.selection = sel
        return ok

    def MapDelete(self) -> bool:
        eng = self._engine()
        if not self._can_edit("Удалить"):
            return False
        ids = list(self.selection)
        if not ids:
            return self._fail("Удалить: ничего не выбрано", notify=False)
        api = eng.api

        def edit(p):
            for fid in ids:
                p = api.remove_fixture(p, fid)
            return p
        self.selection = ()
        return self._medit(edit, f"удалено: {' '.join(ids[:8])}")

    def MapReverse(self) -> bool:
        if not self._can_edit("Развернуть"):
            return False
        ids = list(self.selection)
        if not ids:
            return self._fail("Развернуть: ничего не выбрано", notify=False)
        me = self._me()
        return self._medit(lambda p: me.reverse(p, ids), f"развёрнуто: {' '.join(ids[:8])}")

    def MapOpen(self) -> None:
        ui = self._mui()
        if ui is None:
            return
        self._sync_types()
        self._write_overlay()
        if self._main() is not None:
            self.MainOpen("input" if self._main().par.Mode.eval() == "dmx" else None)
            return
        ui.op("win").par.winopen.pulse()

    def MapClose(self) -> None:
        """Закрыть окно маппинга; Locate снимается — после закрытия на площадке ничего не мигает."""
        ui = self._mui()
        if ui is not None and self._main() is not None:
            self.MainClose()
            return
        if ui is not None:
            if bool(ui.par.Locating):
                self.MapLocate(False)
            ui.op("win").par.winclose.pulse()

    def _sync_types(self) -> None:
        """Меню профилей для инструмента «линия» — из патча."""
        ui, patch = self._mui(), self._engine().patch
        if ui is None or patch is None or not patch.fixture_types:
            return
        types = [t for t in patch.fixture_types]
        par = ui.par.Addtype
        cur = par.eval()
        par.menuNames = [t.id for t in types] or ["none"]
        par.menuLabels = [f"{t.name}  ·  {self._type_short(t)}" for t in types] or ["—"]
        if cur not in par.menuNames:
            par.val = par.menuNames[0]
        self._fill_lib()

    def _map_size(self) -> tuple[float, float]:
        """Размер области карты в пикселях — из пар панели (не из геометрии панели: см. мануал, panel layout freeze)."""
        ui = self._ui()
        return (float(ui.par.w) - float(ui.par.Sidew) - float(ui.par.Splitw),
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
