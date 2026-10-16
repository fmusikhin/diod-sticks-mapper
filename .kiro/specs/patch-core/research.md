# Research & Design Decisions — `patch-core`

## Summary
- **Feature**: `patch-core`
- **Discovery Scope**: New Feature (greenfield: `src/core`, `tests`, `examples` пусты), лёгкая разведка —
  внешних зависимостей нет, только стандартная библиотека.
- **Key Findings**:
  - Движок TD (этап 0) сам выполняет «ячейка целиком в следующий юниверс» и заполняет юниверс
    по `floor(512 / каналов_на_ячейку)` — правила ядра по умолчанию с ним совпадают; правила, которых
    движок не знает (`skip_511_512`, `channel_offset`, порты), ядро выражает **сегментами** фикстуры
    по юниверсам, а `pixel-engine` строит по одному line strip на сегмент (стенд этапа 0: strip на юниверс
    стоит столько же, сколько strip на фикстуру).
  - `jsonschema` не в stdlib → валидация схемы пишется руками (небольшой набор проверок с путём к полю).
  - В рабочем Python 3.12 нет `pytest` — ставится `pip install pytest` (dev-зависимость, не ядро).
  - Пакет ядра должен импортироваться как `dsm_core` (steering `structure.md`) → каталог
    `src/core/dsm_core/`, в `sys.path` добавляется `src/core` (pytest — через `pyproject.toml`,
    в TD — расширением компонента относительным путём).

## Research Log

### Что движок TD делает сам, а что должно считать ядро
- **Context**: таблица адресов ядра — контракт с `pixel-engine` (Requirement 8.3).
- **Sources Consulted**: `docs/architecture.md` §13 (факты стенда), `docs.derivative.ca/DMX_Fixture_POP`.
- **Findings**: при `DMXFixtureUniverse`/`DMXFixtureChannel` на примитиве и фикстуре длиннее юниверса
  `dmxfixturePOP` переносит ячейку целиком в следующий юниверс; 511–512 пустые для RGB; канал с 1;
  Art-Net 20 = 0.1.4. Общие каналы (head) и хвост (tail) движок как блоки профиля тоже умеет, но
  пропуск 511–512 для 4-канальных ячеек и `channel_offset` — нет.
- **Implications**: ядро выдаёт для каждой фикстуры список сегментов `(universe, first_channel,
  first_cell, n_cells)`; при правилах по умолчанию сегментов ровно столько, сколько движок построил бы сам.
  `pixel-engine` строит strip на сегмент — всегда, тогда он не зависит от того, совпадают правила или нет.

### Детерминированный JSON и неизвестные поля
- **Context**: Requirement 1.3, 1.7.
- **Findings**: `json.dumps(sort_keys=True, indent=2, ensure_ascii=False)` + `\n`, LF, UTF-8 без BOM —
  детерминированно в stdlib. Неизвестные поля хранятся в `extra: dict` каждого объекта модели и
  сливаются обратно при записи (известные поля имеют приоритет).
- **Implications**: `float` в координатах пишется как есть (`repr`), для стабильности координаты
  округляются до 3 знаков при записи (0,001 px / мм достаточно).

### Идентификаторы
- **Context**: Requirement 1.2 — стабильные, читаемые в CSV и в листах для монтажников.
- **Alternatives**: uuid4 (непредсказуемы в тестах, нечитаемы), последовательные с префиксом.
- **Findings**: последовательные `ft1…`, `fx1…`, `out1…`, `port1…`, `grp1…`; новый id = префикс +
  (максимальный номер + 1) среди существующих; переименование id не поддерживается.
- **Implications**: слияние двух патчей (будущий импорт) переназначает id с отчётом — не в этой спеке.

### CSV из русского Excel
- **Context**: Requirement 9.7.
- **Findings**: `csv.Sniffer` ненадёжен на коротких файлах; по заголовку: если в первой строке `;`
  и нет `,` — разделитель `;` и десятичная запятая. `utf-8-sig` на чтении снимает BOM Excel.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| Функциональное ядро | модель — dataclasses; операции — чистые функции `op(patch, …) → Patch` | легко тестировать, атомарность «или всё, или ничего» бесплатно, нет скрытого состояния | копии патча на каждую операцию | выбрано; патч мал (сотни объектов), копия дешёвая |
| Класс-редактор с мутацией | `PatchEditor` держит патч и мутирует | привычно для UI | атомарность и undo надо строить руками | отклонено; undo — забота `mapping-editor` поверх функций |
| Валидация jsonschema | внешняя библиотека | стандарт | не stdlib, не импортируется в TD | отклонено |

## Design Decisions

### Decision: адрес хранится в патче, таблицы — производные
- **Context**: патч — источник правды (steering), автоадресация должна быть воспроизводима.
- **Selected Approach**: `Fixture.address = {mode, output_id, port_id, universe, channel}`; автоадресация
  записывает результат в патч с `mode = auto`; таблицы считаются из патча детерминированно.
- **Rationale**: движок и UI читают один документ; ручные и авто адреса видны одинаково.
- **Trade-offs**: при изменении профиля адреса `auto` могут устареть — операция правки профиля
  помечает выход «требует переадресации» через проверку (warning), не переадресует молча.

### Decision: сегменты фикстуры по юниверсам — контракт с движком
- См. Research Log выше. Сегмент = непрерывный блок ячеек одного юниверса.

### Decision: занятость каналов — интервалы на юниверс
- **Context**: авто обходит ручные адреса (6.10), проверка пересечений (7.2).
- **Selected Approach**: `Occupancy` — словарь `(output_id, universe) → отсортированный список
  интервалов (first, last, fixture_id)`; пересечение — слияние отсортированных списков.
- **Rationale**: на 50 000 ячеек интервалов ≈ число фикстур × сегментов, не ячеек — укладывается в 1 с.

### Decision: порядок ячеек — отдельный модуль `cells.py`
- **Context**: 16 раскладок матрицы, `reverse`, `cell_offset`, `disabled`, `every_n` (3.6, 4.4–4.7).
- **Selected Approach**: `cell_order(fixture_type, fixture) → list[Cell]`, где `Cell = (data_index,
  grid_index, active)`; позиции на холсте не считаются (решение заказчика: геометрия в `pixel-engine`).

### Decision: публичный API — модуль `api.py` с функциями
- **Context**: Requirement 10; UI и TD-слой правят патч только через ядро.
- **Selected Approach**: один модуль-фасад реэкспортирует функции остальных модулей; сигнатуры в design.md.

## Synthesis
- **Generalization**: «дубль группы блоком» (6.11) и «Ctrl+D подряд» (6.12) — частные случаи одной
  операции `assign_sequential(patch, fixture_ids, start)`; «перевёрнута → исправить» (4.9) — частный
  случай `update_fixture`. Все экспорты CSV — одна функция `rows_to_csv(rows, dialect)`.
- **Build vs Adopt**: всё stdlib (`json`, `csv`, `dataclasses`, `ipaddress`, `enum`); `jsonschema`
  и `pydantic` отклонены (не stdlib).
- **Simplification**: нет слоя «репозиторий», нет событий, нет undo в ядре; миграции — список функций
  `migrate_N_to_N+1`, пока одна версия — пустой список; `custom`-форма, `curve`, `arc` — только валидация.

## Risks & Mitigations
- Ядро и движок разойдутся по правилу переноса ячейки — тест 11.4 на случай этапа 0 + сверка
  `DMX Map DAT` в `pixel-engine`.
- Производительность cell-таблицы на 50 000 строк — строки как кортежи, без dataclass на ячейку;
  тест-порог 1 с.
- Путь пользователя с кириллицей (`C:\Users\ПК`) — только относительные пути, тесты через `tmp_path`.

## References
- `docs/architecture.md` §3, §5, §13 — модель, адресация, факты стенда.
- `docs/resolume-analysis.md` — модель Lumiverse (образец правил выхода).
- https://docs.derivative.ca/DMX_Fixture_POP , https://docs.derivative.ca/DMX_Out_POP — контракт движка.
- Art-Net 4 (Artistic Licence): port-address 15 бит = Net(7) | Sub-Net(4) | Universe(4).
- ANSI E1.31 (sACN): universe 1–63999, priority 0–200, multicast 239.255.hi.lo.
