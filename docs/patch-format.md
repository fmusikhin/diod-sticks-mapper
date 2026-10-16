# Формат патча (JSON), схема 1

Патч — один файл JSON, источник правды. Читает и пишет его только ядро `dsm_core` (`src/core`).
Запись детерминирована: отсортированные ключи, отступ 2, UTF-8 без BOM, LF, координаты с точностью
0,001. Неизвестные поля у документа и у любого объекта с `id` сохраняются при записи (с предупреждением).

```json
{
  "schema_version": 1,
  "canvas": {"width": 1280, "height": 720, "units": "px", "px_per_m": null, "background": null},
  "fixture_types": [...],
  "fixtures": [...],
  "outputs": [...],
  "groups": [...]
}
```

## `schema_version`
Целое. Файл старше текущей версии мигрируется (пока миграций нет: версия одна), новее — отказ с сообщением
«обновите ядро», файл не трогается.

## `canvas`
| Поле | Тип | Умолчание | Смысл |
|---|---|---|---|
| `width`, `height` | int ≥ 1 | 1280, 720 | размер холста в пикселях |
| `units` | `px` \| `m` | `px` | единицы координат фикстур |
| `px_per_m` | число > 0 | — | обязательно при `m` |
| `background` | строка | null | подложка, **относительный** путь от файла патча |

## `fixture_types[]` — профиль
| Поле | Тип | Умолчание | Смысл |
|---|---|---|---|
| `id`, `name` | строка | — | обязательны; id вида `ft1` |
| `kind` | `strip` \| `stick` \| `matrix` \| `bar` \| `custom` | — | обязательно |
| `cells` | int ≥ 1 | — | число ячеек; у `matrix` = `width × height`, у `custom` = число `custom_cells` |
| `width`, `height` | int ≥ 1 | 1 | сетка матрицы |
| `layout` | одна из 16 строк `tl_rows`, `tl_rows_snake`, `tl_cols`, … `br_cols_snake` | `tl_rows` | угол старта (tl/tr/bl/br) × первая линия (rows/cols) × змейка |
| `custom_cells` | `[[x, y], …]` | [] | относительные позиции ячеек для `custom` |
| `color_order` | строка из букв `RGBWA` без повторов | `RGB` | порядок цветовых каналов ячейки |
| `bits` | 8 \| 16 | 8 | 16 бит = два DMX-канала на цветовой канал (старший, младший) |
| `head`, `cell_extra`, `tail` | список каналов | [] | общие каналы прибора; нецветовые каналы ячейки (после цветовых); хвост |
| `pitch_mm`, `cells_per_m`, `length_mm` | число > 0 | — | физика (любое поле необязательно) |
| `gamma` | 0,1–10 | 1 | параметры цвета — данные, в этой версии не считаются |
| `white_balance` | `[r, g, b]` | `[1,1,1]` | |
| `white_mode` | строка | `none` | режим выделения белого |
| `watts_per_cell` | число | — | для лимита мощности (позже) |

Канал: `{"name": "dim", "role": "control" | "color", "default": 0–255, "bits": 8 | 16}`.

Производные: `каналов_на_ячейку = len(color_order) × (bits / 8) + ширина cell_extra`;
`всего = head + cells × каналов_на_ячейку + tail`.

## `fixtures[]` — экземпляр
| Поле | Тип | Умолчание | Смысл |
|---|---|---|---|
| `id`, `name`, `type_id` | строка | — | обязательны; `type_id` → профиль |
| `shape` | объект формы | — | обязательно, см. ниже |
| `groups` | `[id, …]` | [] | группы (зеркало `groups[].fixture_ids`) |
| `sampling` | `{"kind": "point" \| "area" \| "elongated", "size", "width", "along"}` | point | выборка цвета (считает `pixel-engine`) |
| `output_shape` | форма | null | физическая форма (2D) |
| `direction` | `forward` \| `reverse` | forward | при `reverse` первая ячейка данных — в конце формы |
| `cell_offset` | int | 0 | на сколько ячеек начало данных сдвинуто по форме (по кругу) |
| `disabled` | `[индекс, …]` | [] | отключённые ячейки (индексы в данных); адрес сохраняют |
| `every_n` | int ≥ 1 | 1 | активна каждая N-я ячейка начиная с первой; адреса у всех |
| `length_mm` | число | null | у `strip`: число ячеек = `floor(length / pitch)` (или через `cells_per_m`) |
| `address` | объект | unassigned | см. ниже |
| `brightness` | 0–1 | 1 | |
| `color` | `[r, g, b]` | `[1,1,1]` | |
| `mute` | bool | false | |
| `acceptance` | `none` \| `ok` \| `reversed` \| `dead` \| `misplaced` \| `wrong_address` | none | статус приёмки (Locate) |

### Формы (`shape.kind`)
| kind | Поля | Поддержка в этой версии |
|---|---|---|
| `line` | `p1: [x, y]`, `p2` | полностью |
| `polyline` | `points` (≥ 2) | полностью |
| `rect` | `center`, `size: [w, h]`, `rotation_deg` | полностью |
| `curve` | `points` (≥ 4, опорные Безье) | только валидация |
| `arc` | `center`, `radius`, `start_deg`, `end_deg` | только валидация |
| `custom` | `points` (по ячейке) | только валидация |

### Адрес
```json
{"mode": "auto" | "manual" | "unassigned", "output_id": "out1", "port_id": null,
 "universe": 20, "channel": 1, "channel_offset": 0}
```
- `universe` — сквозной номер: Art-Net port-address 0–32767 (`net·256 + subnet·16 + universe`; 20 = 0.1.4),
  sACN 1–63999. `channel` — первый канал фикстуры, **с 1**.
- `auto`: номер записан автоадресацией и пересчитывается ею; `manual`: не трогается и обходится.
- `channel_offset` — сколько каналов пропустить перед фикстурой при автоадресации.
- Фикстура с `output_id` обязана быть ровно в одном из списков: `outputs[].fixture_ids` или
  `outputs[].ports[].fixture_ids` (там — порядок цепочки).

## `outputs[]` — выход (аналог Lumiverse)
| Поле | Тип | Умолчание | Смысл |
|---|---|---|---|
| `id`, `name` | строка | — | обязательны |
| `protocol` | `artnet` \| `sacn` | — | обязательно |
| `target_kind` | `unicast` \| `broadcast` (Art-Net) \| `multicast` (sACN) | unicast / multicast | |
| `ip` | IPv4 или null | null | null = цель не задана → вывод на **127.0.0.1** (предупреждение) |
| `start_universe` | int | — | обязательно; в диапазоне протокола |
| `universe_count` | int или null | null | null = авто; иначе ограничение и заявленный диапазон |
| `rules` | `{"whole_pixel": true, "auto_span": true, "skip_511_512": false}` | как в примере | ячейка не делится; фикстура продолжается в следующем юниверсе; каналы 511–512 не используются |
| `ports` | список портов | [] | |
| `fixture_ids` | `[id, …]` | [] | порядок фикстур выхода вне портов |
| `rate_hz` | 1–1000 | 60 | |
| `art_sync` | bool | false | |
| `priority` | 0–200 | 100 | sACN |
| `delay_ms` | число ≥ 0 | 0 | |

Юниверс заполняется полностью: ячеек в юниверсе = `floor(512 / каналов_на_ячейку)` (3 → 170, 4 → 128, 7 → 73).
Цель одна на выход: другой IP — другой выход.

Порт: `{"id": "port1", "number": 1, "fixture_ids": [...], "new_universe": true, "max_cells": null, "max_universes": null}`.
`new_universe` — первая фикстура порта начинается с канала 1 нового юниверса; лимиты — только для проверки.

## `groups[]`
`{"id": "grp1", "name": "...", "fixture_ids": [...]}` — именованные множества, без вложенности.

## Ограничения
- Все id уникальны в своём разделе и не меняются операциями ядра.
- Ссылки (`type_id`, `output_id`, `port_id`, `groups`, `fixture_ids`) должны существовать — иначе ошибка схемы
  с путём к полю (`fixtures[3].address.channel: 513 вне диапазона 1–512`).
- Абсолютные пути в документе запрещены.
