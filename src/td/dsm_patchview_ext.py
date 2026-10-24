"""Расширение patch-view компонента dsm: вкладка DMX — карта юниверсов, правки адресов, выходов и портов через ядро
и запись патча движка, отмена одной ступенью, выбор фикстур и колбэк onSelect, строки info `dmx_*`.

Третье расширение на COMP (``ext2``). Состояние — атрибуты экземпляра, не op.store; своего патча нет — источник правды
патч движка (`DsmEngineExt.patch`). Python — только по событиям (пара, метод, клик/перетаскивание, загрузка патча),
никогда в куке кадра. Движок про patch-view не знает: подписка на загрузку и строки info — через его публичные хуки.
"""
from __future__ import annotations

import traceback

NOT_YET = "не реализовано"


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
        self.message: str = ""
        self._in_par = False                   # защита от повторного входа: метод меняет пару → пара вызывает метод
        eng = ownerComp.ext.DsmEngineExt
        eng.AddListener(self.OnPatchLoaded)
        eng.AddInfoProvider(self._info_rows)
        eng.WriteInfo()                        # движок уже записал info при своей инициализации — дописать строки dmx_*

    # ------------------------------------------------------------------ окно и выбор (задачи 2.2, 2.6, 3.1)
    def Open(self) -> None:
        self._not_yet("Open")

    def Close(self) -> None:
        self._not_yet("Close")

    def SelectOutput(self, output_id: str | None) -> None:
        self._not_yet("SelectOutput")

    def ShowIssue(self, index: int) -> None:
        self._not_yet("ShowIssue")

    def Select(self, fixture_ids: list[str]) -> None:
        self._not_yet("Select")

    def Selection(self) -> list[str]:
        return list(self.selection)

    # ------------------------------------------------------------------ правки (задачи 2.3–2.5)
    def ReAddress(self, fixture_id: str, output_id: str, universe: int, channel: int) -> bool:
        return self._not_yet("ReAddress")

    def ToAuto(self, fixture_id: str, output_id: str | None = None) -> bool:
        return self._not_yet("ToAuto")

    def Sequential(self, fixture_ids: list[str], universe: int, channel: int) -> bool:
        return self._not_yet("Sequential")

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
        return self._not_yet("Undo")

    def ExportSheet(self, path: str | None = None) -> str:
        self._not_yet("ExportSheet")
        return ""

    # ------------------------------------------------------------------ события
    def OnPatchLoaded(self, patch, issues) -> None:
        """Слушатель движка: перестройка таблиц карты (задача 2.2). Движок уже записал info с нашими строками."""
        return

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

    # ------------------------------------------------------------------ служебное
    def _engine(self):
        return self.ownerComp.ext.DsmEngineExt

    def _not_yet(self, name: str) -> bool:
        self.message = f"{name}: {NOT_YET}"
        self._engine().WriteInfo()
        return False

    def _info_rows(self) -> list[list[str]]:
        return [["dmx_output", self.output_id or "all"], ["dmx_selection", " ".join(self.selection)],
                ["dmx_message", self.message]]
