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

    def Next(self) -> None:
        self._not_yet("Next")

    def Prev(self) -> None:
        self._not_yet("Prev")

    def Walk(self, on: bool) -> None:
        self._not_yet("Walk")

    def Test(self, pattern: str) -> None:
        self._not_yet("Test")

    def Raw(self, output_id: str, universe: int, first: int, last: int, value: int) -> bool:
        return self._not_yet("Raw")

    def RawClear(self) -> None:
        self._not_yet("RawClear")

    def SetAcceptance(self, fixture_id: str, status: str) -> bool:
        return self._not_yet("SetAcceptance")

    def FixReversed(self, fixture_id: str) -> bool:
        return self._not_yet("FixReversed")

    def ExportSheet(self, path: str | None = None) -> str:
        self._not_yet("ExportSheet")
        return ""

    # ------------------------------------------------------------------ события
    def OnPatchLoaded(self, patch, issues) -> None:
        """Слушатель движка: пока Locate после загрузки снимается (сохранение цели — задача 4.2)."""
        self._set(target=None, pattern="off", message="")
        self._sync_pars(Target="", Testpattern="off")
        self._apply(notify=False)
        self._engine().WriteInfo()      # движок пишет info до слушателей — строки Locate обновить сейчас

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
