# Implementation Plan — `pixel-engine`

Порядок задач = зависимость: задача N опирается на всё до неё, если не сказано иное. `(P)` — можно вести
параллельно с соседями по группе. Каждая подзадача заканчивается «чем проверено»: pytest, `td_get_errors`,
карта DMX, перехват или замер (правило проекта «готово — только с фактом проверки»). Правки `.toe` — только
через twozero; `td_get_hints('bootstrap')` в начале сессии; стенд этапа 0 `/project1/stage0` не трогается.
Задача 7 — документация: правила генерации её исключают, но требования 10.6–10.7 и `CONTRIBUTING.md` §7
обязывают; оставлена проверяемая часть (сверка доков с компонентом скриптом, контрольная точка `.toe`).

- [ ] 1. Фундамент: спайк адреса, файлы-источники, каркас компонента
- [x] 1.1 Спайк: адрес uint32 из таблицы доживает до карты DMX; правило квантования
  - В `/project1/stand` собрать мини-цепочку: таблица из 2 сегментов (10 + 5 точек) → `dattoPOP` с явными
    типами колонок (uint для адресных) → `linebreakPOP` по флагу начала strip (все лишние режимы выключены
    явно) → `attributeconvertPOP` point→prim → `dmxfixturePOP` + `dmxmapDAT`
  - Проверить `primAttributes`: три адресных атрибута класса primitive типа uint; `numPrims` = 2;
    карта DMX показывает юниверсы и каналы из таблицы, Auto Layout не сработал
  - Если тип не uint или карта пустая — повторить с `routingtable` на `dmxfixturePOP` (строка на примитив)
    и зафиксировать выбранный путь
  - Квантование: подать точкам `Color` 0, 0,5, 0,502, 1,0 и прочитать байты в карте DMX/перехвате;
    записать правило (`floor`/`round`) — его потом использует проверка 6.1
  - Готово: в `research.md` и `docs/architecture.md` §14 записаны факт «атрибуты / таблица» с цифрами
    (`primAttributes`, строки карты) и правило квантования
  - _Requirements: 3.1, 3.2, 5.5_

- [x] 1.2 Файлы-источники слоя TD и тестовая обвязка
  - Заглушки четырёх файлов из File Structure Plan: `src/td/dsm_geometry.py` (сигнатуры design, тела `NotImplementedError`),
    `src/td/dsm_engine_ext.py` (класс с `__init__`, подключение `src/core` в `sys.path` относительным путём),
    `src/td/dsm_callbacks_default.py` (пустые `onPatchLoaded`, `onOutputError`), `src/glsl/px_process.glsl` (проходной шейдер)
  - `pyproject.toml`: `src/td` в `pythonpath`; `tests/test_td_geometry.py` с фикстурами (патчи из `examples/`,
    фабрики линии, полилинии, прямоугольника, матрицы); тест синтаксиса расширен на `src/td` (Python 3.11,
    `dsm_geometry` не импортирует `td`)
  - Готово: `python -m pytest tests` зелёный с тестом «нет импорта td» и тривиальными тестами геометрии
  - _Requirements: 1.10, 10.5_

- [x] 1.3 Каркас компонента `dsm`
  - `/project1/dsm` (baseCOMP, `parentshortcut='dsm'`), страницы пар `DSM`, `Input`, `Info` по таблице design
    с умолчаниями (`Active` выключен, `Autoreload` включён, `Pixelschop` выключен, `Id = dsm/mapper`,
    `Version = 0.1`, `Status`), файловая пара `Patchfile` через `appendFile`
  - Вход `in1`, выходы `out1`…`out5` (превью, пиксели TOP, пиксели CHOP, info, карта DMX) в порядке `connectorder`;
    пока на выходах — пустые заглушки; `input/in_prepared` (nullTOP от `in1`) создаётся сразу как опора для 4.x
  - DAT `engine_ext`, `geometry_mod`, `px_process_glsl`, `callbacks` синхронизированы с файлами из 1.2
    (относительные пути от `project.folder`); расширение подключено `ext0object`
  - `agents_md` с контрактом; все операторы `viewer=True`; подгруппы `input`, `tables`, `engine`, `hook`,
    `delay`, `fixtures`, `output`, `diag`, `watch` созданы пустыми контейнерами
  - Готово: `td_get_errors('/project1/dsm', recursive)` пуст; `float(op('dsm').par.Version) == 0.1`;
    в textport `op('dsm').ext.DsmEngineExt` импортирует `dsm_core.api` без ошибок
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 1.10_

- [ ] 2. Чистая геометрия: позиции ячеек и строки таблиц
- [x] 2.1 Позиции ячеек на холсте
  - Линия, полилиния, прямоугольник: центр ячейки `i` на `(i + 0,5) / n` длины дуги; у полилинии —
    по суммарной длине отрезков; позиция по форме — для `grid_index`, адрес — по `data_index` из `cell_order`
    ядра (`direction`, `cell_offset` уже учтены ядром)
  - Матрица на `rect`: клетки сетки W×H с поворотом `rotation_deg`, порядок — `cell_order`; матрица на
    `line`/`polyline` — как лента по длине
  - Единицы `m` → пиксели через `px_per_m`; UV = позиция / размер холста, V вверх как в TD
  - Готово: тесты — линия 10 ячеек даёт центры `(i+0,5)/10`; `reverse` зеркалит соответствие `data_index`;
    полилиния с отрезками 1:3 равномерна по длине; `rect` с поворотом 90° кладёт клетки на место;
    все 16 раскладок 4×4 совпадают с `cell_order`; матрица на линии = лента; `m` → px
  - _Requirements: 3.4, 3.5, 3.5a, 3.6_

- [x] 2.2 Строки ячеек, служебных каналов и стрелок
  - Строки ячеек с колонками design (`P`, `Tex`, `cell`, `fx`, `prof`, `dgrp`, `active`, `bright`, `col`, `mute`,
    `Color`), порядок — сегменты `segments_by_fixture`, внутри — `data_index` от `first_cell`; таблица примитивов
    `geo_prims` (строка = сегмент: `vertices` — индексы строк ячеек через пробел, uint-адрес) — по итогам спайка 1.1;
    строки служебных каналов head/tail (`v = default/255`) и их `raw_prims`;
    таблица стрелок `fx_arrows` (фикстура, первая и последняя ячейка в px) для превью
  - Исключения из вывода: фикстуры из `Issue` уровня error, `unassigned`; формы `curve`/`arc`/`custom` и выборка
    `area`/`elongated` — предупреждения в `warnings`; `disabled`/`every_n` → `active = 0`, адрес сохранён
  - Готово: на `sticks_8x_artnet.json` число строк = Σ `n_cells`, адресные колонки `geo_prims` = `segment_table`,
    `vertices` каждого сегмента начинаются с `first_cell`; фикстура с `channel_overlap` и `unassigned` отсутствуют;
    `bar_common_channels.json` даёт строки head/tail; `fx_arrows` — по строке на выводимую фикстуру
  - _Requirements: 2.4, 2.5, 3.1, 3.3, 3.7, 3.8, 4.2a, 4.4, 5.4_

- [x] 2.3 Профили, группы задержки, группы вывода и маршрутизация
  - Профили (`color_order`, `bits`, умолчания `cell_extra`), группы задержки (`round(delay_ms·fps/1000)` кадров,
    0 — без задержки), группы вывода `(protocol, multicast, rate_hz, art_sync)` с перечнем выходов
  - Маршрутизация на группу: Art-Net → `net subnet universe` через `from_port_address`, sACN → сквозной;
    `ip = null` → `127.0.0.1` с предупреждением; broadcast → `255.255.255.255`; priority sACN; `GeoTables` собран целиком
  - Готово: тесты — два одинаковых выхода → одна группа, разные `art_sync` → две; `delay_ms = 50` при 60 fps → 3 кадра;
    юниверс 20 Art-Net → `0 1 4`; выход без IP → `127.0.0.1` и предупреждение `target_missing`;
    `matrix_16x16_sacn.json` → multicast-группа с priority
  - _Requirements: 6.2, 6.3, 6.4, 7.3, 7.5_

- [ ] 3. Расширение: загрузка патча и события
- [x] 3.1 Загрузка, статус, колбэки, таблица info
  - `Reload`/`LoadPatch`: путь относительно `project.folder`; ядро — `load`, `auto_assign_all`, `validate`,
    `segments_by_fixture`, `routing_table`; `build_tables`; запись каждой таблицы одной операцией `text`
    (`geo_cells`, `geo_prims`, `geo_raw`, `raw_prims`, `fx_arrows`, `profiles`, `delay_groups`, `out_groups`, `routing_<grp>`, `info`)
    только после успешного расчёта; раскладка `pixels_top` (`onerow` ≤ 16 384 ячеек, иначе `square`)
    выставляется парой здесь, по числу ячеек, а не выражением в кадре
  - Ошибка файла или схемы → таблицы не тронуты, `status = error`, `info.message` с путём к полю,
    `onOutputError`; успех → `status = ok`, `onPatchLoaded(patch, issues)`; исключение в колбэке — в textport,
    загрузка продолжается; без патча — `status = no patch`; `par.Status` зеркалит статус
  - `info`: `status`, `patch_path`, `fixtures`, `cells`, `universes`, `outputs`, `errors`, `warnings`, `skipped`
    (фикстуры без вывода и причина), предупреждения о формах и выборке
  - Готово: загрузка каждого `examples/*.json` заполняет таблицы без ошибок операторов; битый JSON оставляет
    прежние таблицы и зовёт `onOutputError`; `info` совпадает с `summary(p)` ядра; `par.Status` меняется
  - _Requirements: 2.1, 2.2, 2.3, 2.6, 2.8, 9.1, 9.4, 9.5_

- [x] 3.2 Реакция на пары и автоперезагрузка файла
  - `parexecuteDAT`: `Reload` (пульс), смена `Patchfile` → `Reload`; `Autoreload` → активность `watch_folder`
  - `watch/`: `folderDAT` на папку патча с фильтром по имени файла, обновление по времени;
    `datexecuteDAT onTableChange` → `Reload` при смене даты файла; параметры `folderDAT` сверить
    `td_get_par_info`; если дата не обновляется — запасной путь `timerCHOP` 1 Гц + mtime в расширении
  - Готово: правка файла патча снаружи при включённой `Autoreload` обновляет `info` в течение 1–2 с;
    при выключенной — нет
  - _Requirements: 2.7_

- [ ] 4. POP-конвейер
- [x] 4.1 Предподготовка входа
  - `in1` → `cropTOP` (пары `Crop*`) → `resolutionTOP` (`Maxres`, 0 — обход) → `levelTOP` (`Black`, `White`,
    `Ingamma`) → `in_prepared`; все пары — через `parent.dsm.par.*`
  - Готово: при умолчаниях `in_prepared` байт в байт равен входу (проверка `compareTOP`/`analyzeTOP`);
    вход 1920×1080 с `Maxres = 1280` даёт 1280×720; без входа — чёрный кадр
  - _Requirements: 1.9, 4.1, 4.2, 4.6_

- [x] 4.2 Геометрия из таблиц, выборка и обработка цвета
  - `engine/`: `pts_cells` (`dattoPOP`: `geo_cells` + `geo_prims`, `primtype=linestrips`, типы колонок и
    `primattr` uint явно — путь B спайка 1.1) → `px_process` (`glslPOP`) → `px_out`; то же для `geo_raw` + `raw_prims` → `raw_out`
  - Шейдер `px_process.glsl`: `Color = texture(in_prepared, Tex) · uMaster · bright · col · (1 − mute) · active`,
    `sampler0filter = linear`, `extendu/v = zero`; `uMaster` привязан к `parent.dsm.par.Master`
  - `hook/`: baseCOMP с `inPOP → outPOP`, подключён после `px_out`; контракт атрибутов в `agents_md`
  - Готово: на `sticks_8x_artnet.json` `numPrims(px_out)` = число сегментов, `primAttributes` uint, точки
    с `Color` из тестовой рампы на входе; UV за холстом даёт чёрный; `Master = 0` гасит всё без пересборки
    (`geo_cells` не изменилась); профилировщик — `engine/` без Python-куков
  - _Depends: 1.1, 3.1_
  - _Requirements: 3.1, 3.2, 3.3, 3.9, 4.3, 4.5, 4.7, 5.1, 5.6, 5.7_

- [x] 4.3 Группы задержки
  - `delay/`: `replicatorCOMP` по `delay_groups`; шаблон `tpl_delay`: `deletePOP` (keep `dgrp == k`, line strip
    удаляется целиком) → `cachePOP` (`cachesize = frames + 1`, `outputindex = −frames`, единицы frames);
    группа 0 — без кэша; `mergePOP` → `px_final`
  - Тестовый патч `td/stand/delay_test.json` (синтетический, два выхода на 127.0.0.1 с `delay_ms` 0 и 50)
  - Готово: тестовый патч даёт две реплики; рампа-тест показывает сдвиг
    группы 1 ровно на 3 кадра при 60 fps (сравнение `Color` по `poptoDAT` на двух кадрах); без задержек —
    одна реплика, `px_final` = `hook/out1`
  - _Requirements: 7.1, 7.5, 7.6_

- [x] 4.4 Профили DMX и карта
  - `fixtures/`: `replicatorCOMP` по `profiles`; `tpl_profile`: `deletePOP` (keep `prof == i`) → `dmxfixturePOP`
    с блоком цвета (`Color` с перестановкой по `color_order`, `b8`/`b16`, normalized), блоками `cell_extra`
    константами с `merge`, `W`/`A` без значения — константа 0; `dmxmapDAT` на каждый; `fixture_raw`
    (один 8-битный блок `v`) для head/tail
  - Готово: `dmxmapDAT` для `strip_ws2812b.json` показывает порядок GRB; `bar_common_channels.json` —
    общие каналы, ячейки, хвост на своих каналах по `segment_table`; вариант профиля с `bits = 16` даёт
    два канала на компонент (старший первым)
  - _Requirements: 5.2, 5.3, 5.4, 6.1_

- [x] 4.5 Узлы вывода и маршрутизация
  - `output/`: `replicatorCOMP` по `out_groups`; `tpl_out`: `dmxoutPOP` (`interface` по протоколу, `multicast`,
    `netaddress = 127.0.0.1`, `routingtable = ./routing`, `rate = min(rate_hz, me.time.rate)`, `sendartsync`,
    `localaddress ← Localaddress`, `viewer = False`), ссылки `fixture<i>pop` на `dmxfixturePOP` своей группы;
    таблица `routing` из `routing_<grp>`
  - Выражение активности узла — здесь и только здесь: `active = parent.dsm.par.Active and status == ok`
    (статус читается из `info`/`par.Status`)
  - Готово: на `examples/` при одинаковых настройках ровно один узел на протокол; `dmx_listen.py` на 127.0.0.1
    видит пакеты всех юниверсов обоих протоколов с частотой ≈ fps проекта; `Active = False` или `status ≠ ok` —
    пакетов нет; узел с недоступным IP (192.0.2.1) не роняет fps и не даёт ошибок; `art_sync = true` →
    пакеты ArtSync в перехвате
  - _Requirements: 2.8, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 7.2, 7.4, 7.7_

- [ ] 5. Диагностика и выходы компонента
- [x] 5.1 (P) Счётчики и сообщения
  - `diag/counters`: `timerCHOP` 1 Гц → `UpdateCounters` в расширении: fps проекта, `rate` и `totalCooks`
    узлов за секунду, `warnings()`/`errors()` узлов вывода и шейдера, состояние входа (`in1` подключён);
    новое сообщение → `onOutputError`; textport — только события
  - Готово: `info` обновляется раз в секунду; отключение входа даёт `input = none`; предупреждение узла
    появляется в `info.message` и в колбэке один раз
  - _Boundary: diag counters, DsmEngineExt.UpdateCounters_
  - _Requirements: 4.6, 6.8, 7.2, 9.1, 9.4, 9.5_

- [x] 5.2 (P) Превью, пиксели, карта DMX на выходах
  - `diag/preview`: `renderTOP` ортографической камеры над холстом — фон `in_prepared`, точки `px_final`
    цветом вывода, стрелки из таблицы `fx_arrows` (dattoPOP → линии); расширение не правится
  - `pixels_top` (`poptoTOP`; раскладку ставит расширение в 3.1, здесь только читается),
    `pixels_chop` (`poptoCHOP`, `active ← Pixelschop`), `dmxmap_all` (`mergeDAT` всех карт), подключение к `out1`…`out5`
  - Готово: превью показывает картинку с точками и стрелками для `sticks_8x_artnet.json`, стоит ≤ 0,5 мс
    по `gpuCookTime + cpuCookTime`; `out2` — TOP с числом пикселей = ячеек; `out3` пуст при выключенной паре
    и даёт каналы `r g b` при включённой; `out5` содержит все юниверсы
  - _Boundary: diag preview и pixels, коннекторы out_
  - _Requirements: 1.4, 8.3, 9.2, 9.3, 9.3a_

- [ ] 6. Стенд и проверка
- [ ] 6.1 Сверка карты DMX и перехват байт в байт
  - `tools/engine_check.py` режим `examples`: для каждого `examples/*.json` — `Reload`, 2 кадра, `dmxmap_all`
    против `segment_table` (сквозной юниверс через `dsm_core.protocol`), запуск `tools/dmx_listen.py`
    подпроцессом на 127.0.0.x из маршрутизации, `Active` на 2 с, сравнение байт с `cell_table` по правилу
    квантования из 1.1; отчёт JSON в `td/stand/reports/` (в `.gitignore`) и печать «совпало / расхождения»
  - DAT `/project1/stand/run_check` запускает скрипт из репозитория относительным путём
  - Готово: все четыре примера — «совпало» по карте и по байтам в Art-Net и sACN, включая пустые 511–512
    и head/tail бара
  - _Requirements: 3.11, 5.5, 6.10, 10.1, 10.2_

- [ ] 6.2 Замер стоимости кадра, сборки и отсутствие Python в кадре
  - Режим `perf`: синтетические патчи 14 400, 20 000 и 50 000 ячеек (генерируются в скрипте), `realTime = False`,
    120 кадров, `totalCooks`, CPU+GPU по узлам компонента, с `Pixelschop` выключенным и включённым;
    профилировщик TD — ноль Python-куков за окно; время `Reload` (14 400 и 50 000 ячеек)
  - Готово: отчёт с цифрами; 20 000 ≤ 1 мс, 50 000 ≤ 3,5 мс, `Reload` ≤ 1 с / ≤ 3 с — иначе задача
    оптимизации по рычагам design (превью, strip на юниверс) до выполнения порога
  - _Requirements: 3.9, 3.10, 8.1, 8.2, 8.3, 8.4_

- [ ] 6.3 Дым-тест `.tox`, копии и граничные случаи
  - Экспорт `dsm.tox` пустой заготовкой (без патча, контента, состояния) в `release/`; `release/` и
    `td/stand/smoke.toe` добавить в `.gitignore` (как `td/stand/reports/` в 6.1); режим `smoke` в два шага: (а) скрипт грузит `.tox` в пустой `baseCOMP`
    `/project1/stand/smoke` текущего `.toe` и проверяет `Id`/`Version`/`float(Version)`, `errors()`/`scriptErrors()`,
    `strip_ws2812b.json` → перехват на 127.0.0.1; (б) то же вручную в новом `td/stand/smoke.toe` (в `.gitignore`)
  - Две копии компонента в одном проекте с разными патчами — независимый вывод, нет конфликтов имён;
    ошибка схемы после рабочей загрузки — прежняя карта DMX на месте; правка файла при `Autoreload` —
    новая карта; `Master = 0,5` — байты вдвое меньше без пересборки
  - Готово: отчёт `smoke` «пройдено» по всем пунктам `CONTRIBUTING.md` §5; факты записаны в `docs/architecture.md` §13
  - _Requirements: 1.7, 1.8, 2.3, 2.7, 5.7, 6.6, 10.4_

- [ ] 7. Документация, сверка доков с компонентом, контрольная точка
  - `docs/engine.md`: пары, коннекторы, колбэки, контракт `hook`, таблицы, стенд, стоимость кадра;
    `docs/architecture.md` §4 и §6 по факту, §13 таблица стоимости на 20 000 / 50 000, §14 грабли из 1.1–6.3;
    `README.md` (таблица спек, структура), `CHANGELOG.md`, `.kiro/steering/structure.md`, `agents_md` компонента
  - Режим `docs` в `tools/engine_check.py`: имена пар, коннекторов и операторов из `docs/engine.md` сверяются
    с живым компонентом — расхождения печатаются
  - `td/dsm_dev.toe` — контрольная точка (канонический файл) после прохождения 6.x
  - Готово: режим `docs` — «расхождений нет»; коммит контрольной точки в ветку `spec/pixel-engine`
  - _Requirements: 8.4, 10.3, 10.6, 10.7_

## Implementation Notes
- 1.1 (18.10.2026): адрес на примитив — `dattoPOP` с `primitivesdat` (`primtype=linestrips`, `primattr` uint),
  без `linebreakPOP`/`attributeconvertPOP`; `Color` в `dattoPOP` задавать блоком типа `color`; `linebreakPOP`
  читает флаг входа через `useinputlinebreaks`; байт = floor(v·255); `autolayout` не мешает атрибутам.
  Стенд: `/project1/stand/spike` (agents_md внутри). `.toe` не закоммичен — контрольная точка в задаче 7.
- 1.2: сигнатуры `dsm_geometry` защищены тестом `inspect.signature` — менять только вместе с design.
- 1.3: компонент `/project1/dsm` в `td/dsm_dev.toe` (сохранён как dsm_dev.7.toe, канонический обновлён); `.toe` — на контрольной точке 7.
- 2.1: ось y холста направлена вверх (как V в TD), поворот — против часовой; rect для ленты/стика — вдоль ширины по центральной оси.
- 2.2/2.3: broadcast без IP → 255.255.255.255 (явный выбор, как multicast), unicast без IP → 127.0.0.1 + `target_missing`; группа вывода без юниверсов даёт routing только с заголовком — 3.1 пишет его как есть.
- 3.1/3.2: модуль геометрии расширение берёт из DAT `geometry_mod.module` (TD перезагружает при смене текста),
  в `sys.path` — только `src/core`; `folderDAT.names` сравнивает имя БЕЗ расширения; колбэки parexec/datexec
  выполняются в куке, не внутри вызвавшего скрипта — проверять через кадр; провода только внутри одной сети
  (источник для `dsm` — `selectTOP` рядом с ним).
- 4.2: API glslPOP — чтение `TDIn_<Attr>(вход, id)`, запись `<Attr>[id]` (не `TDInPoint_`/`oTDPoint_` — это glsladvancedPOP);
  `Tex` в POP трёхкомпонентный → в geo_cells колонка Tex(2)=0; COMP→COMP провода — `inputConnectors[i].connect(src.outputConnectors[j])`
  и только внутри одной сети (engine отдаёт данные через outPOP).
- 4.3: мастера репликаторов — в `templates` (allowCooking=False на COMP), иначе шаблон кукается и дублирует вывод;
  `mergePOP` сжимает входы при disconnect — отключать вход 0 в цикле; группа 0 идёт через cachePOP size 1/index 0.
- 4.4: блок профиля `dmxfixturePOP` принимает список атрибутов через пробел (порядок каналов), `merge` даёт «цвет, затем extra»
  на ячейку, b16 — старший байт первым; cell_extra > 4 каналов пока обрезается (предупреждение — в 5.1); карты dmxmapDAT
  заполняются ~25 кадров после пересборки; реплики пересоздаёт расширение после записи таблиц (пульс recreateall).
- 4.5: dmxoutPOP шлёт все юниверсы указанных фикстур → dmxfixturePOP реплицируется на (профиль × группа вывода): таблицы
  fixture_sets/raw_sets; `active` и `rate` на узлах — константы от расширения (ApplyActive, колбэк репликатора), rate
  пересчитывается при перезагрузке патча; ArtSync широковещательный — перехват на 0.0.0.0; td/stand/output_test.json.
- 5.1/5.2: карта на dmxoutPOP пуста при выключенном active — карты собираются с dmxfixturePOP реплик через selectDAT по map_sets;
  renscope у poptoCHOP не переименовывает — renameCHOP; превью на форсированных куках 0,35–0,8 мс (steady-state ~0,04) —
  перепроверить с живым входом в 6.2; карты обновляются десятки кадров после пересборки.
