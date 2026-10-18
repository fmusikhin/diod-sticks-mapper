"""Шаблон DAT колбэков компонента dsm. Скопируйте в свой DAT и укажите его в паре Callbacks.

Механика — пары компонента, политика — эти функции. Исключение в колбэке печатается в textport
и не прерывает загрузку патча.
"""


def onPatchLoaded(patch, issues):
    """Патч загружен. patch — dsm_core.model.Patch; issues — список dsm_core.validate_types.Issue."""
    pass


def onOutputError(message):
    """Ошибка загрузки или вывода. message — текст по-русски, при ошибке схемы — с путём к полю."""
    pass
