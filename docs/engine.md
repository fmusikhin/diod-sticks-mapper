# Компонент `dsm` — pixel-engine (Version 0.1)

Контракт компонента, который бросают в чужой патч: кастом-пары, коннекторы, колбэки, точка подключения
`hook`, таблицы на границе Python/GPU, стенд проверки и стоимость кадра. Что внутри и почему — спека
`.kiro/specs/pixel-engine/` (`requirements.md`, `design.md`); формат патча — `patch-format.md`; ядро — `core-api.md`.

Разделы «Пары», «Коннекторы» и «Структура компонента» сверяются с живым компонентом режимом `docs`
стенда (`tools/engine_check.py`, см. «Стенд»): имена в обратных кавычках там — не пример, а факт.

## Как вставить

1. Положить `dsm.tox` в проект (это пустая заготовка: без патча, контента и состояния).
2. На `in1` — TOP с видео (тот же, что идёт на экраны).
3. `Patchfile` — путь к JSON патча относительно папки проекта (`.toe`) или абсолютный.
4. `Active` включить, когда адреса проверены: по умолчанию вывод выключен, а у выходов без IP — 127.0.0.1.

Ядро (`src/core/dsm_core`) расширение ищет само: от папки проекта вверх по дереву до `src/core` с пакетом
`dsm_core` (до 6 уровней). Если `.tox` живёт в проекте без исходников — хозяин добавляет `src/core` в `sys.path`
до инициализации компонента. Модуль геометрии и шейдер берутся из DAT внутри компонента (`geometry_mod`,
`px_process_glsl`), в dev-проекте они синхронизированы с файлами `src/td/dsm_geometry.py` и `src/glsl/px_process.glsl`.

## Пары

Страница `DSM`:

| Пара | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `Patchfile` | File | пусто | путь к патчу; смена → перезагрузка |
| `Reload` | Pulse | | перечитать патч вручную |
| `Autoreload` | Toggle | вкл. | следить за датой файла патча (`folderDAT`), перечитывать при правке снаружи |
| `Active` | Toggle | выкл. | отправка DMX; при выключенной — ни одного пакета |
| `Master` | Float 0–1 | 1 | общий множитель цвета (в шейдере, без пересборки) |
| `Localaddress` | Str | пусто | локальный IP сетевой карты для `dmxoutPOP`; пусто — системный |
| `Pixelschop` | Toggle | выкл. | заполнять `out3` (CHOP пикселей) — стоит отдельного кука на 50 тыс. ячеек |
| `Callbacks` | DAT | `./callbacks` | DAT с функциями колбэков |

Страница `Input` (предподготовка входа, всё в TOP до выборки):

| Пара | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `Cropleft` | Float 0–1 | 0 | кроп входа в долях |
| `Cropright` | Float 0–1 | 1 | |
| `Cropbottom` | Float 0–1 | 0 | |
| `Croptop` | Float 0–1 | 1 | |
| `Maxres` | Int | 0 | ограничение большей стороны в пикселях; 0 — обход |
| `Black` | Float | 0 | уровни чёрного и белого |
| `White` | Float | 1 | |
| `Ingamma` | Float | 1 | гамма входа |

Страница `Info` (все `readOnly`):

| Пара | Значение |
|---|---|
| `Id` | `dsm/mapper` |
| `Version` | `0.1` — `float(par.Version)` работает; бамп по CONTRIBUTING §4 |
| `Status` | `no patch` / `ok` / `error` |

## Коннекторы

| Коннектор | Тип | Содержимое |
|---|---|---|
| `in1` | TOP | вход: видео, тот же кадр, что на экранах |
| `out1` | TOP | превью: подготовленный вход, точки ячеек цветом вывода, стрелки направления данных |
| `out2` | TOP | пиксели: один пиксель на ячейку в порядке данных; раскладка `onerow` до 16 384 ячеек, иначе `square` |
| `out3` | CHOP | те же пиксели каналами `r g b a` (пусто при выключенной `Pixelschop`) |
| `out4` | DAT | таблица `info` (ниже) |
| `out5` | DAT | карта DMX всех юниверсов (`dmxmapDAT` реплик фикстур; обновляется по запросу, см. `RefreshMaps`) |

## Колбэки

DAT из пары `Callbacks` (шаблон — `src/td/dsm_callbacks_default.py`); исключение в колбэке печатается в textport,
загрузка не прерывается.

- `onPatchLoaded(patch, issues)` — после успешной загрузки: объект `Patch` ядра и список `Issue` проверки.
- `onOutputError(message)` — статус `error` (файл, JSON, схема, ядро) и новые сообщения узлов вывода/шейдера (один раз на текст).

## Таблица `info` (`out4`)

Две колонки `key` / `value`: `status`, `patch_path`, `fixtures`, `cells`, `universes`, `outputs`, `errors`, `warnings`,
`skipped` (фикстуры без вывода и причина), `pixels_layout`, `message`, `fps`, `project_rate`, `input` (`connected`/`none`),
`canvas_w`, `canvas_h`; по строке на счётчик узлов `rate_*`/`cooks_*`, на `node_message`, на `warning` геометрии и на
`issue_error`/`issue_warning` ядра. Обновляется при загрузке патча и раз в секунду (`UpdateCounters`).

## Что считается, что нет (0.1)

- Фикстуры с ошибками ядра не выводятся, остальные работают (`skipped` в `info`).
- Формы входа: `line`, `polyline`, `rect` (матрица как сетка; матрица на `line`/`polyline` — как лента);
  выборка — одна точка на ячейку с билинейной фильтрацией; `area`/`elongated` — этап 2 (предупреждение в `info`).
- Частота (`rate_hz`), ArtSync и задержка (`delay_ms`) — только из патча, на выход; группы задержки — `cachePOP`
  в кадрах при fps проекта.
- Гамма, баланс белого, RGB→RGBW, лимит мощности — спека `color-power`; альфа `Color` = 0 даёт W/A без значения.
- Профили: порядок цвета строкой, 8/16 бит (старший байт первым), каналы ячейки `cell_extra` (до 4), сырые `head`/`tail`.

## Структура компонента

Корень — `baseCOMP` `dsm`, `parentshortcut = dsm` (внутри всё через `parent.dsm`, абсолютных путей нет).
Расширение `DsmEngineExt` — DAT `engine_ext` (`src/td/dsm_engine_ext.py`). DAT корня: `agents_md` (контракт для агентов),
`geometry_mod`, `px_process_glsl`, `callbacks`, `par_exec` (`Patchfile`/`Reload`/`Active` → расширение).

| Подконтейнер | Что внутри |
|---|---|
| `input` | `in1` → `crop` → `resize` → `levels` → `in_prepared` (пары `Input`) |
| `tables` | DAT от расширения: `geo_cells`, `geo_prims`, `geo_raw`, `raw_prims`, `fx_arrows`, `arrow_pts`, `arrow_prims`, `profiles`, `delay_groups`, `out_groups`, `fixture_sets`, `raw_sets`, `map_sets`, `routing_<grp>`, `info` |
| `engine` | `pts_cells` (`dattoPOP` из `geo_cells` + `geo_prims`) → `px_process` (`glslPOP`, выборка и обработка; ошибки компиляции — `px_process_info`) → `px_out` → `out1`; `pts_raw` → `raw_out` → `out2` (служебные каналы) |
| `hook` | `in1` → `out1` — точка подключения `locate-test` и `palette-generators` (контракт ниже) |
| `delay` | `in1`/`in2` (ячейки/служебные) → реплики `grp<k>` по `delay_groups` (репликатор `rep`; внутри `sel_c`/`sel_r` → `keep_c`/`keep_r` → `cache_c`/`cache_r`) → `merge_c`/`merge_r` → `px_final`/`raw_final` |
| `fixtures` | `in1`/`in2` → реплики `p<k>g<j>` по `fixture_sets` (репликатор `rep`; профиль × группа вывода): `sel` → `keep` → `attrs` → `fixture` (`dmxfixturePOP`) → `map` (`dmxmapDAT`, по запросу); `raw_g<j>` по `raw_sets` (репликатор `rep_raw`) |
| `output` | реплики `out_g<j>` по `out_groups` (репликатор `rep`): `out` (`dmxoutPOP` на группу выхода; протокол, multicast, частота, ArtSync, маршрутизация из `routing_<grp>`) |
| `diag` | `counters` (1 Гц → `UpdateCounters`), превью `preview_render`/`preview` (`geo_pts` с `mat_pts`, `geo_arrows` с `mat_arrows`, `cam`, `bg`), `pixels_top`, `pixels_chop` → `pixels_named`, карты `rep_maps` → `map_<set>` → `dmxmap_all` |
| `watch` | `watch_folder` (`folderDAT` на папку патча) + `watch_exec` → `CheckFile` при `Autoreload` |
| `templates` | мастера репликаторов `fx_tpl`, `raw_tpl`, `delay_tpl`, `out_tpl`, `map_tpl`; `allowCooking = False` (иначе шаблон кукается и дублирует вывод) |

Выходы корня: `sel_preview` → `out1`, `sel_pixels` → `out2`, `sel_pxchop` → `out3`, `sel_info` → `out4`, `sel_map` → `out5`.

Публичные методы расширения (промоутятся на COMP): `Reload`, `LoadPatch(path)`, `ReportError(message)`,
`ApplyActive`, `RefreshMaps(on, frames)` (карты DMX активны только по запросу — иначе 10–25 мс CPU на кук),
`TuneBypass` (обход `keep_c`/`cache_c`/`merge_c` и их `_r`-пары при одной группе), `UpdateCounters`, `CheckFile`, `ResolvePath(path)`.

## Контракт точки подключения `hook`

`hook/in1 → hook/out1` — POP между `engine` и группами задержки. Точки, по одной на ячейку, в порядке сегментов ядра:
`P` (px холста), `Tex` (UV), `Color` (float4 — результат выборки и обработки, альфа 0), `fx` (uint, индекс
фикстуры), `cell` (uint, `data_index`), `prof`, `dgrp`, `ogrp` (uint), `active` (uint 0/1), `bright`, `col` (float3),
`mute` (uint). Примитивы — line strip, один на сегмент ядра, с uint32 `DMXFixtureUniverse`, `DMXFixtureChannel`,
`DMXFixtureNetAddress` (+ `fx`, `seg`) — не менять. Спеки `locate-test` и `palette-generators` кладут операторы внутрь
`hook` и подменяют только `Color` (по `fx`/`cell`/`active`); число точек, порядок и примитивы на выходе равны входу.
Служебные каналы `head`/`tail` идут мимо `hook` (`engine/out2` → `delay/in2`).

## Таблицы — граница Python/GPU

Патч читается ядром (`load` → `auto_assign_all` → `validate` → `segments_by_fixture` → `routing_table`),
`dsm_geometry.build_tables` строит таблицы, расширение пишет каждую одной операцией `text`, потом пульсирует
`recreateall` репликаторам. Дальше кадр идёт без Python.

| Таблица | Колонки |
|---|---|
| `geo_cells` | `P(0..2)`, `Tex(0..2)`, `cell`, `fx`, `prof`, `dgrp`, `ogrp`, `active`, `bright`, `col(0..2)`, `mute`, `Color(0..3)` |
| `geo_prims` | `vertices` (индексы через пробел), `fx`, `seg`, `DMXFixtureUniverse`, `DMXFixtureChannel`, `DMXFixtureNetAddress(0..3)` |
| `geo_raw` / `raw_prims` | служебные каналы: `v`, `fx`, `dgrp`, `ogrp` / как `geo_prims` |
| `fixture_sets` / `raw_sets` | `set`, `prof`, `ogrp`, `type_id`, `grp` / `set`, `ogrp`, `grp` |
| `profiles` | `prof`, `type_id`, `name`, `color_order`, `bits`, `channels_per_cell`, `extra_defaults` |
| `delay_groups` | `dgrp`, `frames`, `delay_ms` |
| `out_groups` | `grp`, `protocol`, `multicast`, `rate_hz`, `art_sync`, `output_ids` |
| `routing_<grp>` | `net`, `subnet`, `universe`, `netaddress`, `priority`, `kinetport` |
| `fx_arrows` | `fx`, `fixture_id`, `x1`, `y1`, `x2`, `y2` |

Адрес без IP: unicast → 127.0.0.1 с предупреждением `target_missing`; broadcast → 255.255.255.255.
Байт DMX = `floor(float32(v)·255)`; 16 бит — старший байт первым.

## Стенд

`tools/engine_check.py` — DAT `run_check` в `/project1/stand` dev-проекта `td/dsm_dev.toe`, вход
`op('/project1/stand/run_check').module.start(mode)`; отчёты — `td/stand/reports/<mode>_<дата>.json` (в `.gitignore`).

| Режим | Что делает |
|---|---|
| `examples` | каждый `examples/*.json` и `td/stand/*.json`: карта DMX против `cell_table`/`segment_table` ядра, перехват Art-Net/sACN (`tools/dmx_listen.py`) байт в байт на сером входе |
| `perf` | синтетические стики 144 px GRB на 8 выходах: 14 400 / 20 016 / 49 968 ячеек, `realTime = False`, 120 кадров, два прохода (`Pixelschop` выкл./вкл.) |
| `smoke` | CONTRIBUTING §5: экспорт пустой заготовки `release/dsm.tox`, `loadTox` в пустой COMP, `Id`/`Version`/`errors()`, две копии с разными патчами и протоколами, `Master 0,5`, ошибка схемы после рабочей загрузки, автоперезагрузка, тишина при `Active = Off` |
| `docs` | этот документ против живого компонента: пары, коннекторы, операторы структуры |

Ядро вне TD — `python -m pytest tests` (геометрия и расширение покрыты без TD: `tests/test_td_*.py`).

## Стоимость кадра

Замер 18.10.2026 (`start('perf')`): движок 2,5 / 2,6–2,8 / 2,7–3,7 мс на 14 400 / 20 016 / 49 968 ячеек, из них `dmxfixturePOP`
1,0–1,1 / 1,5–1,9 / 1,6–2,7 мс; диагностика (превью, счётчики, CHOP) 2,0–2,8 мс; Python-куков в кадре 0; сборка 0,32 / 0,49 / 0,99 с.
Порог 8.2 (≤ 3,5 мс на 50 тыс.) выполняется, **порог 8.1 (≤ 1 мс на 20 тыс.) не достигнут** — пол самого `dmxfixturePOP`.
Подробности и ловушки — `architecture.md` §13 п.4–5 и §14.
