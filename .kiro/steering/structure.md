# Структура проекта

## Принцип организации

Слои по зависимости: ядро (`src/core`) ничего не знает о TouchDesigner; слой TD (`src/td`, `src/glsl`)
зависит от ядра; dev-проект `td/` собирает всё в `dsm.tox`. Текст — источник правды, бинарники — производные.

## Каталоги

### Ядро
**Где**: `src/core/`
**Что**: модель патча, схема, адресация, проверки, импорт/экспорт — чистый Python, только stdlib
**Пример**: `src/core/dsm_core/addressing.py`, `src/core/dsm_core/csv_io.py`; публичный вход — `dsm_core.api`;
в `sys.path` добавляется `src/core` (pytest — через `pyproject.toml`)

### Слой TouchDesigner
**Где**: `src/td/`
**Что**: расширения компонента, строитель таблиц для POP-геометрии (чистый модуль, pytest), шаблоны колбэков;
файлы синхронизируются с DAT компонента (`par.file` относительно папки проекта)
**Пример**: `src/td/dsm_engine_ext.py` (единственный модуль с TD-API), `src/td/dsm_geometry.py`, `src/td/dsm_callbacks_default.py`

### Шейдеры
**Где**: `src/glsl/`
**Что**: GLSL для POP и TOP, один файл = один шейдер
**Пример**: `src/glsl/px_process.glsl` (glslPOP: `TDIn_<Attr>(вход, id)` → `<Attr>[id]`)

### Тесты
**Где**: `tests/`
**Что**: pytest для ядра и слоя TD без TouchDesigner; синтетические образцы (без данных заказчиков)
**Пример**: `tests/test_addressing.py`, `tests/test_td_geometry.py`, `tests/test_td_engine_ext.py`

### Dev-проект и релиз
**Где**: `td/dsm_dev.toe` (канонический, без номера автосейва), `td/stand/` (синтетические патчи стенда; `reports/` и `smoke.toe` не в git), `release/` (локально, в git не идёт)
**Что**: сборка компонента `/project1/dsm`, стенд `/project1/stand` (DAT `run_check` ↔ `tools/engine_check.py`); `dsm.tox` публикуется в GitHub Releases

### Примеры, утилиты, документация
**Где**: `examples/` (патчи), `tools/` (перехват DMX `dmx_listen.py`, стенд `engine_check.py`), `docs/` (`engine.md` — контракт компонента)
**Спеки и steering**: `.kiro/`

## Именование

- **Файлы Python**: `snake_case.py`; шейдеры — `snake_case.glsl`.
- **Классы**: `PascalCase`; функции и переменные — `snake_case`.
- **Операторы TD**: по смыслу, с префиксами как в TD-практике (`geo_*`, `out_*`, `ui_*`, `debug_*`).
- **Кастом-пары TD**: с заглавной, одним словом (`Patchfile`, `Active`, `Master`).
- **Спеки**: `kebab-case` (`patch-core`, `pixel-engine`).

## Импорты

```python
from dsm_core.model import Patch          # ядро — как пакет
from dsm_core.addressing import assign    # без относительных путей к файлам проекта
```

## Правила кода

- Ядро не импортирует `td` и ничего из TouchDesigner.
- В TD нет Python каждый кадр: Python только при правке патча, дальше POP/GLSL.
- Никаких абсолютных путей и глобальных OP-шорткатов; ссылки относительные или через `parentshortcut`.
- Публичный контракт компонента — кастом-пары, коннекторы и DAT колбэков (см. `CONTRIBUTING.md`).

---
_Паттерны, а не дерево файлов: новые файлы по паттерну не требуют правки этого документа_
