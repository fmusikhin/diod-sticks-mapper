"""Расширение Locate компонента dsm (locate-test): пары страницы Locate и методы → состояние → таблица на фикстуру,
униформы шейдера hook/locate, обход, сырой адрес, лист приёмки, колбэк onLocate, строки info.

Второе расширение на COMP (``ext1``). Состояние — ``LocateState`` (frozen dataclass из ``dsm_locate``) в атрибуте
экземпляра, не op.store. Python — только по команде (пара, метод, шаг таймера обхода), никогда в куке кадра.
Движок про Locate не знает: Locate подписывается на загрузку патча и дописывает свои строки в info через публичные
хуки DsmEngineExt. Каждая команда: разбор → одна запись ``tables/locate_fx`` → униформы → bypass шейдера → info → onLocate.
"""
from __future__ import annotations

import dataclasses
import traceback

NOT_YET = "не реализовано"
MODE_INDEX = {"blink": 0, "direction": 1, "number": 2, "address": 3}       # uMode шейдера
PATTERN_INDEX = {"fill": 4, "rgbw": 5, "chase": 6}
OTHERS_INDEX = {"solo": 0, "dim": 1, "over": 2}                             # uOthers


class DsmLocateExt:
    """Публичные методы (с заглавной — промоутятся на COMP): Locate, Clear, Next, Prev, Walk, Test, Raw, RawClear,
    SetAcceptance, FixReversed, ExportSheet, OnPatchLoaded, OnLocatePar."""

    def __init__(self, ownerComp) -> None:
        self.ownerComp = ownerComp
        self.logic = ownerComp.op("locate_mod").module      # dsm_locate из синхронизированного DAT
        self.state = self.logic.LocateState()   # Locate выключен при старте (9.4); dataclass из dsm_locate
        self.rows = []                          # строки locate_fx текущего патча (FxRow), для сообщений и обхода
        self._in_par = False        # защита от повторного входа: метод меняет пару → пара вызывает метод
        self._last_notified: tuple | None = None    # (spec, mode) последнего onLocate — звать один раз на смену (1.7)
        eng = ownerComp.ext.DsmEngineExt
        eng.AddListener(self.OnPatchLoaded)
        eng.AddInfoProvider(self._info_rows)
        self._apply()                           # шейдер в обходе, таблица пустая

    # ------------------------------------------------------------------ цель и режимы (4.1)
    def Locate(self, target: str, mode: str | None = None) -> bool:
        """Подсветить цель (fx7, #12, name:…, group:…, output:…, port:…; через запятую) в режиме ``mode``
        (по умолчанию — пара Mode). False — цель не разобрана: состояние и вывод прежние, причина в info и onOutputError."""
        eng = self._engine()
        if eng.patch is None:
            return self._fail(f"Locate: патч не загружен")
        try:
            tgt = self.logic.resolve_target(eng.patch, eng.issues, target)
        except self.logic.TargetError as e:
            return self._fail(f"Locate: {e.reason}")
        if mode is not None and mode not in MODE_INDEX:
            return self._fail(f"Locate: неизвестный режим {mode!r}")
        changes = dict(target=tgt, pattern="off", message="")
        if mode is not None:
            changes["mode"] = mode
        self._set(**changes)
        self._sync_pars(Target=target, Mode=self.state.mode, Testpattern="off")
        self._apply()
        return True

    def Clear(self) -> None:
        """Снять Locate и тест-паттерн: контент на все фикстуры (1.4)."""
        self._set(target=None, pattern="off", message="")
        self._sync_pars(Target="", Testpattern="off")
        self._apply()

    # ------------------------------------------------------------------ обход (4.3)
    def Next(self) -> None:
        """Следующая фикстура по порядку патча (выводимые; «только непроверенные» — пара Walkunchecked)."""
        self._step(1)

    def Prev(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> bool:
        eng, s = self._engine(), self.state
        if eng.patch is None:
            if s.walk:
                self.Walk(False)
            return self._fail("обход: патч не загружен")
        p = self.ownerComp.par
        unchecked = bool(p.Walkunchecked.eval()) if p["Walkunchecked"] is not None else s.walk_unchecked
        loop = bool(p.Walkloop.eval()) if p["Walkloop"] is not None else s.walk_loop
        order = self.logic.walk_order(eng.patch, eng.issues, unchecked)
        cur = s.target.fixtures[0] if s.target is not None and len(s.target.fixtures) == 1 else None
        k = self.logic.step(order, cur, delta, loop)
        if k is None:
            if s.walk:                                          # обходить нечего — автошаг выключается
                self.Walk(False)
            return self._fail("обход: нет выводимых фикстур" + (" без статуса" if unchecked else ""))
        if not loop and cur is not None and k == cur:          # край без круга — автошаг останавливается
            if s.walk:
                self.Walk(False)
            return False
        return self.Locate(eng.patch.fixtures[k].id)

    def Walk(self, on: bool) -> None:
        """Автошаг: каждые Walkstep секунд — следующая; Python только на шаге таймера (run с задержкой)."""
        self._walk_token = getattr(self, "_walk_token", 0) + 1
        self._set(walk=bool(on))
        self._sync_pars(Walk=bool(on))
        if on:
            if self.state.target is None:
                self._step(1)
            self._schedule_walk(self._walk_token)
        self._engine().WriteInfo()

    def _schedule_walk(self, token: int) -> None:
        p = self.ownerComp.par
        sec = float(p.Walkstep.eval()) if p["Walkstep"] is not None else self.state.walk_step_s
        run("args[0]._walk_tick(args[1])", self, token, delayMilliSeconds=max(0.2, sec) * 1000.0)  # noqa: F821

    def _walk_tick(self, token: int) -> None:
        if token != getattr(self, "_walk_token", 0) or not self.state.walk:
            return
        self._step(1)
        if self.state.walk:                       # на краю без круга _step сам выключает автошаг
            self._schedule_walk(token)

    # ------------------------------------------------------------------ тест-паттерны (4.3)
    def Test(self, pattern: str) -> None:
        """Тест-паттерн по всему ригу (fill / rgbw / chase; off — снять); включение снимает цель и наоборот."""
        pattern = (pattern or "off").strip()
        if pattern not in self.logic.PATTERNS:
            self._fail(f"тест: неизвестный паттерн {pattern!r}")
            return
        if pattern == "off":
            self._set(pattern="off", message="")
        else:
            self._set(target=None, pattern=pattern, message="")
            self._sync_pars(Target="")
        self._sync_pars(Testpattern=pattern)
        self._apply()

    # ------------------------------------------------------------------ сырой адрес (4.4)
    def Raw(self, output_id: str, universe: int, first: int, last: int, value: int) -> bool:
        """Значение ``value`` на каналы first–last юниверса выхода в обход патча; остальное как у движка."""
        eng = self._engine()
        if eng.patch is None:
            return self._fail("сырой адрес: патч не загружен")
        try:
            rr = self.logic.raw_rows(eng.patch, output_id, int(universe), int(first), int(last), int(value))
        except (self.logic.RawError, ValueError, TypeError) as e:
            return self._fail(f"сырой адрес: {getattr(e, 'reason', e)}")
        self._write_rows("locate_raw_pts", rr.pts_rows)
        self._write_rows("locate_raw_prims", rr.prims_rows)
        grp = self._group_of(output_id)
        self._raw_enable(grp)
        eng.SetCounterFilter("overlap")
        self._raw_args = (output_id, int(universe), int(first), int(last), int(value))
        self._set(raw=rr, message=rr.warning)
        self._sync_pars(Rawactive=True)
        eng.WriteInfo()
        return True

    def RawClear(self) -> None:
        self._raw_enable(None)
        self._write_rows("locate_raw_pts", [self.logic.RAW_PTS_COLUMNS])
        self._write_rows("locate_raw_prims", [self.logic.RAW_PRIM_COLUMNS])
        self._engine().SetCounterFilter(None)
        self._raw_args = None
        self._set(raw=None)
        self._sync_pars(Rawactive=False)
        self._engine().WriteInfo()

    def _group_of(self, output_id: str) -> str | None:
        t = self._table("out_groups")
        if t is None:
            return None
        for r in range(1, t.numRows):
            if output_id in t[r, "output_ids"].val.split():
                return t[r, "grp"].val
        return None

    def _raw_enable(self, grp: str | None) -> None:
        """Сырая фикстура — последняя в списке каждой группы вывода; включена только у группы нужного выхода."""
        out = self.ownerComp.op("output")
        for c in out.findChildren(name="out_g*", depth=1):
            o = c.op("out")
            if o is None:
                continue
            n = o.seq.fixture.numBlocks
            for i in range(n):
                v = o.par[f"fixture{i}pop"].val
                if v.endswith("raw/raw_fix"):
                    o.par[f"fixture{i}active"] = (c.name == f"out_{grp}")

    def _write_rows(self, name: str, rows) -> None:
        self._table(name).text = "\n".join("\t".join(str(x) for x in r) for r in rows)

    # ------------------------------------------------------------------ лист приёмки (4.5)
    STATUSES = ("none", "ok", "reversed", "dead", "misplaced", "wrong_address")

    def SetAcceptance(self, fixture_id: str, status: str) -> bool:
        """Статус приёмки фикстуры — в файл патча (без пересборки, меняется только acceptance)."""
        eng = self._engine()
        if eng.patch is None:
            return self._fail("приёмка: патч не загружен")
        if status not in self.STATUSES:
            return self._fail(f"приёмка: неизвестный статус {status!r}")
        if not any(f.id == fixture_id for f in eng.patch.fixtures):
            return self._fail(f"приёмка: нет фикстуры {fixture_id}")
        api = eng.api
        st = api.Acceptance(status) if hasattr(api, "Acceptance") else status
        if not eng.SavePatch(lambda p: api.set_acceptance(p, fixture_id, st), False):
            return self._fail(f"приёмка: не записано: {eng.message}")
        self._write_sheet()
        self._set(message=f"{fixture_id}: {status}")
        eng.WriteInfo()
        return True

    def FixReversed(self, fixture_id: str | list[str]) -> bool:
        """«Исправить перевёрнутую»: направление наоборот и статус сброшен — одной записью и одной пересборкой;
        цель и режим сохраняются. Список id — только со статусом reversed (8.2)."""
        eng = self._engine()
        if eng.patch is None:
            return self._fail("приёмка: патч не загружен")
        api = eng.api
        if isinstance(fixture_id, str):
            ids = [fixture_id]
        else:
            ids = [f.id for f in eng.patch.fixtures if f.id in set(fixture_id) and f.acceptance.value == "reversed"]
            if not ids:
                return self._fail("исправить: в цели нет фикстур со статусом reversed")

        def edit(p):
            for fid in ids:
                p = api.fix_reversed(p, fid)
            return p
        if not eng.SavePatch(edit, True):
            return self._fail(f"исправить: не записано: {eng.message}")
        self._write_sheet()
        self._set(message=f"{', '.join(ids)}: направление исправлено")
        eng.WriteInfo()
        return True

    def ExportSheet(self, path: str | None = None) -> str:
        """Лист приёмки CSV рядом с патчем ``<имя>_acceptance.csv``; путь файла или "" при ошибке."""
        import os
        eng = self._engine()
        if eng.patch is None:
            self._fail("лист: патч не загружен")
            return ""
        full = eng.ResolvePath(path) if path else os.path.splitext(eng.ResolvePath(eng.patch_path))[0] + "_acceptance.csv"
        try:
            rows = self.logic.sheet_rows(eng.patch, eng.api.segments_by_fixture(eng.patch))
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(self.logic.sheet_csv(rows))
        except OSError as e:
            self._fail(f"лист: не записан: {e}")
            return ""
        self._set(message=f"лист приёмки: {os.path.basename(full)}")
        eng.WriteInfo()
        return full

    def _target_ids(self) -> list[str]:
        p, s = self._engine().patch, self.state
        if p is None or s.target is None:
            return []
        return [p.fixtures[k].id for k in s.target.fixtures]

    def _write_sheet(self) -> None:
        eng = self._engine()
        if eng.patch is None:
            return
        rows = self.logic.sheet_rows(eng.patch, eng.api.segments_by_fixture(eng.patch))
        tables = self.ownerComp.op("tables")
        d = tables.op("locate_sheet")
        if d is None:
            d = tables.create(tableDAT, "locate_sheet")  # noqa: F821
            d.viewer = True
        d.text = "\n".join("\t".join(r) for r in rows)

    # ------------------------------------------------------------------ события
    def OnPatchLoaded(self, patch, issues) -> None:
        """Слушатель движка (4.2): цель сохраняется, если её фикстуры есть в новом патче, иначе Locate снимается
        с сообщением; сырой адрес включается заново после пересоздания реплик вывода."""
        s = self.state
        if s.target is not None:
            try:
                tgt = self.logic.resolve_target(patch, list(issues), s.target.spec)
                self._set(target=tgt)
            except self.logic.TargetError as e:
                self._set(target=None, message=f"Locate снят: {e.reason}")
                self._sync_pars(Target="")
        self._apply(notify=False)
        self._write_sheet()
        if getattr(self, "_raw_args", None):
            run("args[0].ReapplyRaw()", self, delayFrames=3)  # noqa: F821 — и из колбэка репликатора вывода
        self._engine().WriteInfo()      # движок пишет info до слушателей — строки Locate обновить сейчас

    def ReapplyRaw(self) -> None:
        """Включённый сырой адрес — заново после пересборки реплик вывода (их колбэк ставит сырую фикстуру выключенной)."""
        args = getattr(self, "_raw_args", None)
        if not args:
            return
        if self._engine().patch is None or not any(o.id == args[0] for o in self._engine().patch.outputs):
            self.RawClear()
            self._set(message=f"сырой адрес снят: нет выхода {args[0]}")
            self._engine().WriteInfo()
            return
        self.Raw(*args)

    def OnLocatePar(self, par) -> None:
        """Исполнитель пар страницы Locate (parexecuteDAT) → методы; повторный вход из метода игнорируется."""
        if self._in_par:
            return
        self._in_par = True
        try:
            name = par.name
            if name == "Clear":
                self.Clear()
            elif name == "Next":
                self.Next()
            elif name == "Prev":
                self.Prev()
            elif name == "Target":
                if par.eval().strip():
                    self.Locate(par.eval().strip())
                else:
                    self.Clear()
            elif name in ("Mode", "Others"):
                self._set(**{name.lower(): par.eval()})
                self._apply()
            elif name in ("Level", "Dim", "Blinkhz", "Blinksmooth", "Runsec") or name.startswith("Testcolor"):
                self._set(**{self._state_field(name): self._par_value(name)})   # униформы читают пары сами
                if self.state.active():
                    self._refresh_message()
                self._engine().WriteInfo()
            elif name == "Walk":
                self.Walk(bool(par.eval()))
            elif name == "Testpattern":
                self.Test(par.eval())
            elif name == "Rawactive":
                if par.eval():
                    p = self.ownerComp.par
                    self.Raw(p.Rawoutput.eval(), int(p.Rawuniverse), int(p.Rawfirst), int(p.Rawlast), int(p.Rawvalue))
                else:
                    self.RawClear()
            elif name in ("Acceptset", "Fixreversed"):
                ids = self._target_ids()
                if not ids:
                    self._fail("приёмка: сначала Locate фикстуры")
                elif name == "Acceptset":
                    for fid in ids:
                        self.SetAcceptance(fid, self.ownerComp.par.Accept.eval())
                else:
                    self.FixReversed(ids)
            elif name == "Exportsheet":
                self.ExportSheet()
        except Exception:  # команда пары — в textport и info, компонент работает дальше
            print(traceback.format_exc())
            self._set(message="ошибка команды Locate: см. textport")
            self._engine().WriteInfo()
        finally:
            self._in_par = False

    # ------------------------------------------------------------------ применение состояния
    def _apply(self, notify: bool = True) -> None:
        """Состояние → таблица на фикстуру, униформы, обход шейдера, info, onLocate. Геометрия и узлы движка не трогаются (10.4)."""
        eng, s = self._engine(), self.state
        shader = self.ownerComp.op("hook/locate")
        if eng.patch is not None:
            segs = eng.api.segments_by_fixture(eng.patch)
            self.rows = self.logic.fixture_rows(eng.patch, segs, s.target, eng.issues)
        else:
            self.rows = []
        active = s.active()
        if active:                                                  # таблица — до снятия обхода (texelFetch без проверки границ)
            self._table("locate_fx").text = "\n".join(["\t".join(self.logic.FX_COLUMNS)] + ["\t".join(r.as_row()) for r in self.rows])
            total, nfx = self.logic.fixture_totals(self.rows)
            self._uniform(shader, "uTotal", total); self._uniform(shader, "uNfx", nfx)
            self._uniform(shader, "uMode", PATTERN_INDEX[s.pattern] if s.pattern != "off" else MODE_INDEX[s.mode])
            self._uniform(shader, "uOthers", OTHERS_INDEX[s.others])
        if shader is not None:
            shader.bypass = not active
        if not active:
            self._table("locate_fx").text = "\t".join(self.logic.FX_COLUMNS)
        self._refresh_message()
        eng.WriteInfo()
        if notify:
            key = (s.target.spec if s.target is not None else "", s.pattern if s.pattern != "off" else s.mode)
            if key != self._last_notified:
                self._last_notified = key
                eng.CallUser("onLocate", key[0], key[1])

    def _refresh_message(self) -> None:
        """Сообщения info: вывод выключен (9.3), пропущенные фикстуры цели (1.6), номер не помещается (4.3)."""
        s, msgs = self.state, []
        if s.active() and not self.ownerComp.par.Active.eval():
            msgs.append("вывод выключен (Active)")
        if s.target is not None:
            for k, reason in s.target.skipped:
                msgs.append(f"{self._fx_id(k)} не выводится: {reason}")
            if not s.target.fixtures:
                msgs.append("в цели нет выводимых фикстур")
            if s.mode in ("number", "address") and s.pattern == "off":
                for k in s.target.fixtures:
                    r = self.rows[k]
                    fits = (self.logic.number_layout(r.number, r.ncells) if s.mode == "number"
                            else self.logic.address_layout(r.uni, r.ch, r.ncells))[1]
                    if not fits:
                        msgs.append(f"{self._fx_id(k)}: {s.mode} не помещается в {r.ncells} ячеек")
        self._set(message=" | ".join(msgs))

    # ------------------------------------------------------------------ служебное
    def _engine(self):
        return self.ownerComp.ext.DsmEngineExt

    def _table(self, name: str):
        return self.ownerComp.op("tables").op(name)

    @staticmethod
    def _uniform(shader, name: str, value) -> None:
        """Константа униформы по имени блока vec (не выражение — в кадре Python не исполняется)."""
        if shader is None:
            return
        for i in range(shader.seq.vec.numBlocks):
            if shader.par[f"vec{i}name"].eval() == name:
                shader.par[f"vec{i}valuex"] = float(value)
                return

    def _fx_id(self, k: int) -> str:
        p = self._engine().patch
        return p.fixtures[k].id if p is not None and k < len(p.fixtures) else f"#{k + 1}"

    _FIELDS = {"Level": "level", "Dim": "dim", "Blinkhz": "blink_hz", "Blinksmooth": "smooth", "Runsec": "run_sec"}

    def _state_field(self, par_name: str) -> str:
        return "test_color" if par_name.startswith("Testcolor") else self._FIELDS[par_name]

    def _par_value(self, par_name: str):
        p = self.ownerComp.par
        if par_name.startswith("Testcolor"):
            return tuple(float(x) for x in self.ownerComp.parGroup.Testcolor.eval())
        if par_name == "Blinksmooth":
            return bool(p[par_name].eval())
        return float(p[par_name].eval())

    def _sync_pars(self, **values) -> None:
        """Пары страницы Locate отражают состояние; запись из метода не должна снова вызвать метод."""
        was, self._in_par = self._in_par, True
        try:
            for name, v in values.items():
                if self.ownerComp.par[name].eval() != v:
                    self.ownerComp.par[name] = v
        finally:
            self._in_par = was

    def _fail(self, message: str) -> bool:
        """Команда отклонена: состояние и вывод прежние, причина в info и onOutputError (1.5, 7.4)."""
        self._set(message=message)
        eng = self._engine()
        eng.WriteInfo()
        eng.CallUser("onOutputError", message)
        return False

    def _not_yet(self, name: str) -> bool:
        self._set(message=f"{name}: {NOT_YET}")
        self._engine().WriteInfo()
        return False

    def _info_rows(self) -> list[list[str]]:
        s = self.state
        tgt = s.target.spec if s.target is not None else "off"
        return [["locate_target", tgt], ["locate_mode", s.mode], ["locate_others", s.others],
                ["locate_pattern", s.pattern], ["locate_raw", "off" if s.raw is None else "on"],
                ["locate_message", s.message]]

    def _set(self, **changes) -> None:
        """Новое состояние Locate (frozen dataclass) — только через замену."""
        self.state = dataclasses.replace(self.state, **changes)
