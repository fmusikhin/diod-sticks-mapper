"""Расширение Locate компонента dsm (locate-test): пары страницы Locate и методы → состояние → таблица на фикстуру,
униформы шейдера hook/locate, обход, сырой адрес, лист приёмки, колбэк onLocate, строки info.

Второе расширение на COMP (``ext1``). Состояние — атрибуты экземпляра, не op.store. Python — только по команде
(пара, метод, шаг таймера обхода), никогда в куке кадра. Движок про Locate не знает: Locate подписывается на загрузку
патча и дописывает свои строки в info через публичные хуки DsmEngineExt.
"""
from __future__ import annotations

import traceback

NOT_YET = "не реализовано"


class DsmLocateExt:
    """Публичные методы (с заглавной — промоутятся на COMP): Locate, Clear, Next, Prev, Walk, Test, Raw, RawClear,
    SetAcceptance, FixReversed, ExportSheet, OnPatchLoaded, OnLocatePar."""

    def __init__(self, ownerComp) -> None:
        self.ownerComp = ownerComp
        self.logic = ownerComp.op("locate_mod").module      # dsm_locate из синхронизированного DAT
        self.message: str = ""
        self.target = None          # Target | None — Locate выключен при старте (9.4)
        self.mode: str = "blink"
        self.pattern: str = "off"
        self.raw = None
        self._in_par = False        # защита от повторного входа: метод меняет пару → пара вызывает метод
        eng = ownerComp.ext.DsmEngineExt
        eng.AddListener(self.OnPatchLoaded)
        eng.AddInfoProvider(self._info_rows)

    # ------------------------------------------------------------------ команды (задачи 4.x)
    def Locate(self, target: str, mode: str | None = None) -> bool:
        return self._not_yet("Locate")

    def Clear(self) -> None:
        self.target, self.pattern = None, "off"
        self.message = ""
        self._engine().WriteInfo()

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
        """Слушатель движка: в каркасе Locate после загрузки выключен (цель сохраняется — задача 4.2)."""
        self.target, self.pattern = None, "off"

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
                if par.eval():
                    self.Locate(par.eval())
                else:
                    self.Clear()
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
            # Mode, Others, Level, Dim, Blinkhz, Blinksmooth, Runsec, Testcolor — униформы (задачи 3.x, 4.1)
        except Exception:  # команда пары — в textport и info, компонент работает дальше
            print(traceback.format_exc())
            self.message = "ошибка команды Locate: см. textport"
            self._engine().WriteInfo()
        finally:
            self._in_par = False

    # ------------------------------------------------------------------ служебное
    def _engine(self):
        return self.ownerComp.ext.DsmEngineExt

    def _not_yet(self, name: str) -> bool:
        self.message = f"{name}: {NOT_YET}"
        self._engine().WriteInfo()
        return False

    def _info_rows(self) -> list[list[str]]:
        tgt = self.target.spec if self.target is not None else "off"
        return [["locate_target", tgt], ["locate_mode", self.mode], ["locate_pattern", self.pattern],
                ["locate_raw", "off" if self.raw is None else str(self.raw)], ["locate_message", self.message]]
