"""Исключения ядра. Тексты — по-русски, с id объектов."""
from __future__ import annotations


class PatchError(Exception):
    """Базовая ошибка ядра."""


class SchemaError(PatchError):
    """Нарушение схемы документа: путь к полю и причина.

    При загрузке собираются все ошибки; бросается первая, остальные — в ``errors``.
    """

    def __init__(self, path: str, reason: str, errors: list["SchemaError"] | None = None) -> None:
        self.path = path
        self.reason = reason
        self.errors: list[SchemaError] = errors if errors is not None else [self]
        super().__init__(f"{path}: {reason}")


class OperationError(PatchError):
    """Операция правки отклонена, патч не изменён."""


class LayoutError(PatchError):
    """Фикстура не помещается по правилам выхода."""


class CsvError(PatchError):
    """Ошибка файла CSV целиком (не отдельной строки)."""
