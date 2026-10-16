# API ядра `dsm_core`

Пакет — `src/core/dsm_core`, чистый Python 3.11 (стандартная библиотека), импортируется и внутри TD
без установки пакетов. Единственная точка входа — `dsm_core.api`:

```python
import sys; sys.path.insert(0, "<корень>/src/core")   # в TD — относительным путём от компонента
from dsm_core import api
```

Все операции — чистые функции: принимают `Patch`, возвращают новый `Patch`; исходный не меняется.
При ошибке входов — `OperationError`, и ничего не изменено. Файлы трогают только `load` и `save`.

## Документ
| Функция | Что делает |
|---|---|
| `new_patch() -> Patch` | пустой патч: холст 1280×720 px |
| `loads(text) -> (Patch, notes)` / `load(path)` | JSON → патч; миграция; заметки о миграциях и неизвестных полях; `SchemaError` с `.path`, `.reason`, `.errors` |
| `dumps(patch) -> str` / `save(patch, path)` | детерминированная запись; `save` — через временный файл |
| `validate_document(doc: dict) -> list[SchemaError]` | все ошибки схемы с путями |
| `from_dict`, `to_dict` | dict ↔ модель |

## Правка
| Функция | Примечание |
|---|---|
| `add_fixture_type(p, ft)`, `update_fixture_type(p, id, **поля)`, `remove_fixture_type(p, id)` | удаление с фикстурами — отказ с их перечислением |
| `add_fixture(p, fx)`, `update_fixture(p, id, **поля)`, `remove_fixture(p, id)` | фикстура сама встаёт в список выхода/порта по адресу и вычищается из портов и групп |
| `duplicate_fixtures(p, ids, dx, dy, count=1) -> (Patch, новые id)` | копии блоком: новые id, тот же профиль, группы, выход/порт; адрес `auto` без номера |
| `add_output`, `update_output`, `remove_output` | удаление с адресованными фикстурами — отказ |
| `add_port(p, out_id, port)`, `update_port`, `remove_port` | |
| `add_group`, `update_group`, `set_group_members`, `remove_group` | `Fixture.groups` следует за `Group.fixture_ids` |
| `set_manual_address(p, fx_id, out_id, universe, channel, port_id=None)` | режим `manual` |
| `assign_to_output(p, fx_id, out_id, port_id=None)`, `clear_address(p, fx_id)` | режим `auto` без номера / `unassigned` |
| `set_acceptance(p, fx_id, Acceptance)`, `fix_reversed(p, fx_id)` | «перевёрнута → исправить»: инверсия направления, сброс статуса |
| `next_ids(p, "fixture", n)` | свободные id (`fx7`, …) |

## Ячейки и адресация
| Функция | Что возвращает |
|---|---|
| `cell_order(ft, fx) -> list[Cell]` | `Cell(data_index, grid_index, active)` с учётом 16 раскладок, `reverse`, `cell_offset`, `disabled`, `every_n` |
| `cell_count(ft, fx)` | число ячеек (у ленты — из длины) |
| `layout_fixture(ft, fx, output, universe, channel) -> list[Segment]` | сегменты по правилам выхода; `LayoutError`, если не помещается |
| `auto_assign(p, out_id) -> (Patch, issues)`, `auto_assign_all(p)` | ручные адреса не трогаются и обходятся; результат пишется в патч; идемпотентно |
| `assign_sequential(p, ids, universe, channel)` | подряд от адреса: первая `manual`, остальные `auto` (дубль блоком, Ctrl+D) |
| `fixture_segments(p, fx)`, `segments_by_fixture(p)` | сегменты по текущим адресам — контракт с `pixel-engine` |

`Segment(universe, first_channel, last_channel, kind: head|cells|tail, first_cell, n_cells, partial)`.
Раскладка зависит только от стартового адреса и правил выхода — фикстуру делит лишь граница юниверса,
поэтому движок, зная `(universe, channel)`, получит те же сегменты. При автоадресации препятствие
(ручной адрес) сдвигает начало фикстуры за себя.

Правила по умолчанию совпадают с поведением `dmxfixturePOP` (этап 0): канал с 1, ячейка целиком
в следующий юниверс, 511–512 пустые для 3-канальных ячеек, Art-Net 20 = 0.1.4.

## Проверки
`validate(p) -> list[Issue]` — не бросает и не меняет патч; ошибки раньше предупреждений.
`Issue(level, code, message, fixture_ids, output_id, port_id, universe, channels)`.

| Код | Уровень | Когда |
|---|---|---|
| `channel_overlap` | error | пересечение каналов двух фикстур в одном юниверсе одной цели |
| `over_512` | error | ручной адрес не помещается при выключенном `auto_span` |
| `fixture_too_long` | error | фикстура не помещается (кончились юниверсы, `auto_span` выключен) |
| `cell_on_boundary` | warning при `whole_pixel`, error без него | ручной адрес кладёт ячейку на границу юниверса; в тексте — куда движок её перенесёт |
| `port_limit` | error | превышены `max_cells` / `max_universes` порта |
| `universe_dup` | error | один юниверс на одной цели у двух выходов |
| `universe_range` | error | юниверс вне диапазона протокола |
| `broken_ref` | error | ссылка на несуществующий профиль, выход, порт, группу, фикстуру |
| `protocol_mismatch` | error | multicast у Art-Net, broadcast у sACN |
| `target_missing` | warning | цель не задана — вывод на 127.0.0.1 |
| `unassigned` | warning | у фикстуры нет адреса |
| `degenerate_shape` | warning | линия нулевой длины и т. п. |
| `stale_auto` | warning | авто-адрес устарел (после правки профиля пересчёт даст другой) |

## Таблицы (строки одинаковой длины, заголовок первой строкой — годится для DAT)
| Функция | Колонки |
|---|---|
| `address_table(p)` | fixture_id, name, type, output, protocol, universe, universe_dotted, channel, cells, channels, universes (`20:1-510;21:1-90`), mode, acceptance |
| `segment_table(p)` | fixture_id, universe, first_channel, last_channel, kind, first_cell, n_cells, partial |
| `cell_table(p)` | fixture_id, data_index, grid_index, universe, first_channel, channels, active |
| `routing_table(p)` | output_id, name, protocol, universe, universe_dotted, target_kind, ip, priority, delay_ms, rate_hz, art_sync — строка на занятый юниверс |
| `issue_table(p)` | level, code, message, fixture_ids, output_id, port_id, universe, channels |
| `summary(p) -> dict` | fixtures, cells, channels, universes, outputs, errors, warnings |

## CSV
| Функция | Примечание |
|---|---|
| `export_fixtures_csv(p, dialect=CsvDialect())` | колонки: id, name, fixture_type, groups (`a\|b`), output, port, universe, channel, address_mode, channel_offset, cells, length_mm, direction, cell_offset, shape, x1, y1, x2, y2, points, disabled, every_n, status |
| `rows_to_csv(rows, dialect)` | любая таблица; `RU` = `;` и десятичная запятая |
| `detect_dialect(text)` | по первой строке |
| `import_fixtures_csv(p, text) -> CsvImportResult(patch, created, updated, errors)` | обязательны name, fixture_type, shape и координаты; строка с `id` существующей фикстуры обновляет её; ошибки строк (`CsvRowError(line, column, reason)`) не прерывают импорт; BOM Excel допускается |

`points` для форм кроме `line`: polyline/curve/custom — `x y;x y;…`; rect — `cx cy;w h;rot`; arc — `cx cy;r;a0 a1`.

## Пресеты
`FIXTURE_PRESETS` (ws2811_rgb, ws2812b_grb, sk6812_rgbw, tm1934_rgb, stick_1m_144, matrix_16x16, bar_common),
`preset_fixture_type(preset_id, new_id)`, `PORT_PRESETS` (числа для проверки портов).

## Протокол
`to_port_address(net, subnet, universe)`, `from_port_address(pa)`, `dotted(pa)` (`"0.1.4"`),
`format_universe(protocol, u)`, `sacn_multicast(u)` (`239.255.hi.lo`).

## Проверка
```bash
python -m pytest tests
```
88 тестов, ≈ 1,5 с. Примеры в `examples/` генерируются `python tools/make_examples.py`.
