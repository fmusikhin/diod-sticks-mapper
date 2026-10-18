# Research & Design Decisions — `pixel-engine`

## Summary
- **Feature**: `pixel-engine`
- **Discovery Scope**: Extension (первый код под TouchDesigner поверх готового ядра `dsm_core`
  и стенда этапа 0 `td/dsm_dev.toe:/project1/stage0`) — лёгкая разведка по стенду, карточкам
  операторов TD 2025.33070 (twozero `td_get_docs`), бутстрапу twozero и доменному скиллу
  `dmxfixture-pop-geometry`.
- **Key Findings**:
  - Стенд этапа 0 уже содержит рабочий образец контракта: `attributePOP` класса **primitive**
    с `DMXFixtureUniverse`, `DMXFixtureChannel` (uint, канал с 1), `DMXFixtureNetAddress` (uint×4)
    → `lookuptexturePOP` (`_PointU` → `Color`) → `dmxfixturePOP` (один блок `RGB` = `Color(0,1,2)`)
    → `dmxoutPOP` (`rate = me.time.rate`, `netaddress = 127.0.0.1`, таблица маршрутизации DAT
    с колонками `net subnet universe netaddress priority kinetport`). Перехват совпал байт в байт.
  - `dmxfixturePOP` платит за примитив, а не за канал (2,6 мс GPU на 50 тыс. px при strip на фикстуру,
    25 мс при point-примитивах). Адресные атрибуты — только класс primitive и только uint32;
    int32 отбрасывается с предупреждением, отсутствие атрибутов молча уходит в Auto Layout.
  - В TD 2025 **нет `scriptPOP`**. Путь «Python → геометрия без Python в кадре»: Python пишет
    `tableDAT` → `dattoPOP` (колонки → атрибуты, тип задаётся явно: `attr0overrideauto` + `attr0type=uint`)
    → `linebreakPOP` по точечному флагу `LineBreak` (`everyn`, `bydistancethreshold`,
    `useinputlinebreaks` выключать явно; `uselinestripindex` не группирует; **поправка спайка 1.1: флаг входа читает `useinputlinebreaks=True`**) → `attributeconvertPOP`
    `pointtoprim` для адресных атрибутов. DAT меняется только при загрузке патча, дальше цепочка статична.
  - `dmxfixturePOP.routingtable` (DAT: примитив → net/subnet/universe/channel/netaddress) переопределяет
    атрибуты — запасной путь, если uint не переживёт `attributeconvertPOP`.
  - `glslPOP` имеет блок `sampler0top` с `sampler0filter = linear` и `sampler0extendu/v = zero`:
    выборка цвета точкой с билинейной фильтрацией и чёрным за границей — одним шейдером вместе
    с обработкой (мастер, яркость, цвет, mute). `lookuptexturePOP` остаётся запасным вариантом выборки.
  - `cachePOP`: «outputindex — 0 текущий, отрицательное — в прошлое», единицы `frames` → задержка
    выхода в кадрах без Python. `deletePOP` по атрибуту делит точки на группы задержки и профили.
  - `dmxoutPOP`: `rate`, `sendartsync`, `multicast`, `priority`, `localaddress` — параметры **узла**,
    не юниверса; `routingtable` даёт `netaddress` и `priority` на юниверс. Поэтому «один узел вывода
    на протокол» уточняется до «один узел на группу (протокол, multicast, rate_hz, art_sync)» —
    число узлов по-прежнему не зависит от числа контроллеров и фикстур.
  - `poptoTOP` (точки → пиксели, GPU) и `poptoCHOP` (GPU → CPU readback) дают выходы «пиксели как TOP
    и CHOP»; CHOP — чтение с GPU каждый кадр, его стоит включать парой.
  - twozero бутстрап: `viewer=True`, `parentshortcut` на корне, относительные пути, кастом-пары с заглавной,
    состояние — в атрибутах расширения (не `op.store`), `ext0object = op('./dat').module.Class(me)`.

## Research Log

### Как получить геометрию из патча без Python в кадре
- **Context**: 3.1–3.3, 3.9 — один примитив на сегмент ядра, точки = ячейки, Python только при правке.
- **Sources Consulted**: `td_get_docs('dattoPOP, linebreakPOP, attributeconvertPOP, attributePOP, glsladvancedPOP')`,
  скилл `dmxfixture-pop-geometry`, `/project1/stage0` (`addr_a`, `break_universe170`, `merge_fixtures`).
- **Findings**: вариант этапа 0 (`linePOP` + `attributePOP` на фикстуру + `mergePOP`) не масштабируется:
  число операторов растёт с числом фикстур, а сборка через `td_create_operator` на 100 фикстур — минуты.
  `dattoPOP` читает всю таблицу разом (строка = точка), типы колонок задаются в блоках `attr`;
  `linebreakPOP` режет strip по флагу; `glsladvancedPOP` умеет генерировать точки из буфера, но это
  сложнее и не нужно.
- **Implications**: расширение пишет две таблицы (`geo_cells`: строка = ячейка; `geo_raw`: строка =
  служебный канал head/tail) — дальше статическая цепочка POP. Табличные строки считает чистый модуль
  `src/td/dsm_geometry.py` (без `td`), поэтому UV по длине дуги и раскладка матрицы тестируются pytest (10.5).

### Класс и тип адресных атрибутов после `attributeconvertPOP`
- **Context**: 3.2 — uint32 на примитиве.
- **Findings**: карточка `attributeconvertPOP` (`convertop=pointtoprim`, `inputattrs`, `newattrs`,
  `deleteorig`) не говорит, сохраняется ли тип uint. Этап 0 задавал атрибуты сразу на примитиве.
- **Implications**: первая задача реализации — спайк: `dattoPOP(uint)` → `linebreakPOP` → `attributeconvertPOP`
  → проверить `primAttributes` (`type == uint`) и `dmxmapDAT`. Если тип теряется — запасной путь:
  `routingtable` DAT на `dmxfixturePOP` (примитив → адрес), который по документации сильнее атрибутов.

### Профиль `dmxfixturePOP` и порядок блоков
- **Context**: 5.2–5.4 — порядок цвета, 16 бит, head/tail/cell_extra.
- **Findings**: блок `dmxchan` итерирует по точкам или примитивам (`valuetype`), берёт атрибут или константу,
  `valueres` b8/b16, `normalized`, `merge` — интерливинг с блоком выше. Порядок каналов внутри ячейки —
  порядок компонентов в `attr` (`Color(1,0,2)` = GRB). Как блоки класса primitive чередуются с блоками
  класса point внутри одного примитива — в карточке не описано.
- **Implications**: head/tail не смешиваются с ячейками в одном примитиве: они идут отдельными сегментами
  ядра (`kind = head|tail`) → отдельная таблица `geo_raw` (точка = один DMX-канал, атрибут `v` 0–1)
  → один «сырой» `dmxfixturePOP` с профилем из одного 8-битного канала. Ячейки: один `dmxfixturePOP`
  на профиль: блок цвета (`Color` в порядке `color_order`, `b8|b16`, normalized) + блоки `cell_extra`
  (константы `default/255`, `merge` с блоком выше — проверяется по `dmxmapDAT`). `W`/`A` при отсутствии
  значения — константа 0 (5.2).

### Выборка цвета: `glslPOP` против `lookuptexturePOP`
- **Context**: 4.3–4.5, 5.1, 5.7.
- **Findings**: `lookuptexturePOP` (стенд) даёт `Color` по `Tex` с `interpolate` и `inputextend = zero`,
  но дальше нужен ещё `mathPOP`/GLSL для мастера, яркости, цвета, mute. `glslPOP` делает всё за один
  проход: `sampler0top`, `sampler0filter=linear`, `sampler0extendu/v=zero`, униформ `uMaster`,
  точечные атрибуты `bright`, `col`, `mute`, `active`.
- **Implications**: один `glslPOP px_process` (шейдер `src/glsl/px_process.glsl`). Тест стенда проверяет,
  что `floor`/`round` квантование совпадает с ожиданием ядра (5.5); правило фиксируется по факту замера.

### Задержка на выход
- **Context**: 7.5 — `delay_ms` на выход (решение заказчика: в этой спеке).
- **Sources Consulted**: `td_get_hints('pop_history_cache')`, `td_get_docs('cachePOP, deletePOP')`.
- **Findings**: `cachePOP` хранит N кадров и отдаёт кадр `outputindex` (в `frames`); точность — кадр.
  `dmxoutPOP` задержки не имеет.
- **Implications**: расширение считает группы задержки `dgrp` (0 — без задержки; k — `round(delay_ms·fps/1000)`
  кадров) и пишет их в таблицу групп; цепочка `deletePOP(keep dgrp==k) → cachePOP(-n_k)` на группу
  (реплицируется по таблице при загрузке) → `mergePOP`. Группа 0 идёт мимо кэша. При смене fps проекта
  число кадров пересчитывается при перезагрузке (ограничение — в документацию).

### Частота, ArtSync, multicast — параметры узла вывода
- **Context**: 6.1–6.3, 7.2–7.4 — патч задаёт их на выход, узлов — один на протокол.
- **Findings**: `dmxoutPOP.rate`, `sendartsync`, `multicast` — на весь узел; `routingtable` — `netaddress`,
  `priority`, `source`, `cid` на юниверс.
- **Implications**: группы вывода = уникальные `(protocol, multicast, rate_hz, art_sync)`; на группу — один
  `dmxoutPOP` с таблицей маршрутизации её юниверсов. При одинаковых настройках выходов — ровно один узел
  на протокол. `rate = min(rate_hz, me.time.rate)`; broadcast = `255.255.255.255` в `netaddress`;
  `ip = null` → `127.0.0.1` (6.4). Группа ссылается на свои `dmxfixturePOP` через параметры `fixture0pop`
  (не провода), иначе одна фикстура попала бы во все узлы.

### Автоперезагрузка файла патча
- **Context**: 2.7.
- **Findings**: нативного «следить за файлом» для JSON нет; `folderDAT` показывает дату изменения файлов
  папки и умеет обновляться сам (параметры обновления уточнить по `td_get_par_info('folderDAT')`);
  `datexecuteDAT onTableChange` зовёт `Reload`. Python выполняется только при изменении таблицы — не в кадре.
- **Implications**: `watch_folder` (folderDAT на папку патча, фильтр по имени файла) → `watch_exec`.
  Пара `Autoreload` включает/выключает `folderDAT.active`.

### Счётчики и диагностика
- **Context**: 9.1 — fps, частота пакетов, ошибки.
- **Findings**: `infoCHOP` на `dmxoutPOP` — состав каналов не описан в карточке; перехват
  `tools/dmx_listen.py` считает интервалы пакетов на стороне приёмника.
- **Implications**: в MVP «частота пакетов» = `rate` узла и число кук `dmxoutPOP` за секунду
  (`totalCooks`, читается расширением раз в секунду по `timerCHOP`, не в кадре); если `infoCHOP`
  даёт счётчик пакетов — используем его (проверяется задачей). Остальное — таблица `info`, которую
  расширение пишет при загрузке и при событиях.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| Таблица → `dattoPOP` → статичная POP-цепочка | Python пишет DAT при загрузке; дальше только POP/GLSL | ноль Python в кадре; число операторов не зависит от патча; таблицу видно глазами | один большой DAT (50 тыс. строк) пишется ~0,3–1 с | выбрано |
| Операторы на фикстуру (как этап 0) | `linePOP`+`attributePOP` на каждую | уже проверено | O(фикстур) операторов, минуты сборки, нечитаемо | отклонено |
| `glsladvancedPOP` генерирует точки из буфера | Python кладёт параметры, шейдер строит геометрию | гибко | сложная топология strip в compute, трудно отлаживать | отклонено для MVP |
| Адрес атрибутами на примитиве | как на этапе 0 | проверено байт в байт | зависит от сохранения uint при конвертации класса | основной путь |
| Адрес таблицей `routingtable` фикстуры | DAT примитив → адрес | не зависит от типов атрибутов, сильнее атрибутов | не проверено на стенде | запасной путь (спайк) |
| Один `dmxoutPOP` на протокол | — | минимум узлов | `rate`/ArtSync/multicast на узел | уточнено до группы настроек |

## Design Decisions

### Decision: границы компонента `dsm` и точка подключения
- **Context**: 1.x, 5.6 — контракт компонента, подключение `locate-test` и `palette-generators`.
- **Selected Approach**: корень `dsm` (baseCOMP, `parentshortcut='dsm'`) → внутри `engine` (POP-конвейер),
  `output` (узлы вывода и маршрутизация), `input` (предподготовка), `diag` (превью, карта, info),
  `hook` — baseCOMP с `inPOP → outPOP` между `px_process` и группами задержки; будущие спеки кладут
  операторы внутрь `hook`, не трогая геометрию и адреса.
- **Rationale**: каждый контур можно проверять отдельно; `hook` — явный шов, а не договорённость «вставить где-то».
- **Trade-offs**: один лишний COMP в цепочке (стоимость ~0).

### Decision: расширение держит состояние, таблицы — производные
- **Context**: 2.x, 9.x; бутстрап twozero H9 (нет `op.store`).
- **Selected Approach**: `DsmEngineExt` (`src/td/dsm_engine_ext.py` ↔ DAT `engine_ext`): `self.patch`,
  `self.issues`, `self.status`; метод `Reload()` → ядро (`load`, `auto_assign_all`, `validate`,
  `segments_by_fixture`, `routing_table`) → `dsm_geometry.build_tables()` → запись DAT (`geo_cells`,
  `geo_raw`, `profiles`, `delay_groups`, `out_groups`, `routing_*`, `info`) → колбэк `onPatchLoaded`.
- **Rationale**: при ошибке — исключение до записи таблиц, старая геометрия остаётся (2.3).
- **Trade-offs**: `.tox` без состояния: при открытии проекта патч перечитывается с диска по `Patchfile`.

### Decision: две таблицы геометрии — ячейки и служебные каналы
- См. Research Log «Профиль dmxfixturePOP». Ячейки → `dmxfixturePOP` на профиль; head/tail → один «сырой».

### Decision: группы вывода и группы задержки — из патча, реплицируются при загрузке
- `replicatorCOMP` над таблицами `out_groups` и `delay_groups` — Python при загрузке, не в кадре.
  Шаблоны групп — внутри компонента (`tpl_delay`, `tpl_out`), без внешних ссылок.

### Decision: выходы «пиксели как CHOP» включаются парой
- `poptoCHOP` — readback с GPU каждый кадр; пара `Pixelschop` (по умолчанию выключена) держит бюджет 8.1.
  Выход-коннектор существует всегда (контракт), при выключенной паре отдаёт пустой CHOP.

### Decision: тест стенда — скрипт в репозитории, запускается внутри TD
- `tools/engine_check.py` — модуль, исполняемый в TD (`td_execute_python`/DAT `stand/run_check`):
  для каждого `examples/*.json` — `Reload`, кук, `dmxmapDAT` против `segment_table`, запуск
  `tools/dmx_listen.py` подпроцессом на 127.0.0.x, сравнение байт с ожиданием ядра (`cell_table`
  + квантование), JSON-отчёт в `td/stand/reports/` (в `.gitignore`). Замер стоимости — как `perf_probe`
  этапа 0 (`realTime=False`, `totalCooks`).

## Спайк 1.1 — факты стенда (18.10.2026, TD 2025.33070, `/project1/stand/spike`)

- **uint32 переживает `attributeconvertPOP pointtoprim`**: после конвертации `primAttributes` =
  `DMXFixtureUniverse UINT32×1`, `DMXFixtureChannel UINT32×1`, `DMXFixtureNetAddress UINT32×4`; карта DMX
  показывает юниверс 7 каналы 1–30 → 127.0.0.1 и юниверс 9 каналы 4–18 → 127.0.0.2 — ровно как в таблице.
- **Есть более короткий путь — `dattoPOP` со второй таблицей примитивов** (`primitivesdat`, `primtype=linestrips`,
  колонка `vertices` = индексы точек через пробел, блоки `primattr` с `overrideauto` + `type=uint`): line strip
  с uint-атрибутами класса primitive сразу, без `linebreakPOP` и `attributeconvertPOP`. Карта DMX идентична пути A.
  **Выбран для design**: `engine/pts_cells` получает две таблицы (`geo_cells` точки, `geo_prims` примитивы);
  `strips_cells` и `addr_cells` не нужны. Расширение пишет `geo_prims`: строка на сегмент, `vertices` — индексы
  строк `geo_cells` этого сегмента по порядку, адресные колонки из `Segment` и маршрутизации.
- `linebreakPOP`: флаг входа читается параметром **`useinputlinebreaks=True`** (+ `linebreakname`);
  `uselinebreak` относится к выходному атрибуту — с ним одним получается один strip (поправка к скиллу
  `dmxfixture-pop-geometry`). Нужен, если путь B когда-нибудь откажет.
- `dattoPOP`: автоконвертация колонок создаёт `P`, но **не `Color`** — цветовую колонку задавать блоком
  (`Color(*)` → `Color`, тип `color`, 4 компонента); `LineBreak` должен быть `int` (uint даёт предупреждение
  «LineBreak should be int1»). Имена колонок компонентов — `Name(0)`, `Name(1)`, …, маска `Name(*)`.
- **Квантование** `dmxfixturePOP` при `normalized=True`, 8 бит: **байт = floor(v·255)** (усечение, не округление):
  0→0, 0,001→0, 0,1→25, 0,2→51, 0,25→63, 0,3→76, 0,4→102, 0,498→126, 0,5→127, 0,502→128, 0,6→153,
  0,75→191, 0,9→229, 0,999→254, 1,0→255. Это правило использует проверка 6.1 (`cell_table` × floor).
- `autolayout` на `dmxfixturePOP` можно оставить включённым: атрибуты примитива имеют приоритет (карта взяла 7/1 и 9/4, а не автораскладку).
- `dmxmapDAT` показывает значения с `updateval`/`updatevalpulse` и отстаёт на кук — читать кадром позже.

## Факты реализации 4.2–4.4 (18.10.2026)

- `glslPOP` (не advanced): чтение `TDIn_<Attr>(вход, id)`, запись `<Attr>[id]`, `TDIndex()`/`TDNumElements()`;
  выходной атрибут должен существовать во входе (`Color` задаётся в `dattoPOP` блоком типа `color`).
- `dmxfixturePOP`, блок профиля: в `attr` можно перечислить **несколько атрибутов/компонентов через пробел** —
  каналы идут в этом порядке (`Color(1) Color(0) Color(2) zero` = GRBW с нулевым W). `merge` второго блока даёт
  на ячейку «значения блока A, затем блока B» (`R G B ex0 ex1`). `valueres=b16`: старший байт первым (0,1 → 25, 153).
- `mergePOP.inputConnectors[i].disconnect()` сжимает входы — отключать `inputConnectors[0]`, пока `inputs` не пуст.
- Провода только внутри одной сети: контуры `engine → hook → delay → fixtures` соединяются через `outPOP/inPOP`
  и `comp.inputConnectors[i].connect(other.outputConnectors[j])`.
- Отклонение от design в `delay/`: группа 0 тоже проходит через `cachePOP` (размер 1, индекс 0 — проходной),
  чтобы шаблон реплики был единообразным; цена проверяется замером 6.2.
- Мастер репликатора, лежащий в той же сети, кукается и дублирует вывод; флаг `allowCooking` копируется в реплики и из колбэка не
  поднимается надёжно → мастера живут в отдельном `templates` COMP с `allowCooking=False`, реплики создаются в целевой сети.
- `dmxmapDAT` большой карты заполняется не за один кук — читать через десяток кадров после пересборки.
- `folderDAT.names` сравнивает имя без расширения; колбэки `parameterexecuteDAT`/`datexecuteDAT`/репликатора
  выполняются в куке после вызвавшего скрипта.

## Risks & Mitigations
- ~~uint теряется при `attributeconvertPOP`~~ — спайк 1.1: не теряется; выбран путь через таблицу примитивов `dattoPOP` (ещё короче).
- Порядок блоков `cell_extra` в `dmxfixturePOP` не совпадёт с ядром → проверка `dmxmapDAT` на `bar_common_channels.json`;
  запасной путь — `cell_extra` как компоненты одного атрибута `chan` (uvec/float4) с `merge`.
- `dattoPOP` на 50 тыс. строк дольше 3 с (3.10) → замер; запасной путь — `fileinPOP`/бинарный формат
  без uint (адрес — таблицей маршрутизации).
- `cachePOP` с разными топологиями групп задержки → каждая группа кэширует только свои точки (после `deletePOP`).
- `poptoCHOP` съедает бюджет → пара `Pixelschop` по умолчанию выключена.
- `folderDAT` не видит изменение без смены размера файла → проверить по дате; запасной путь —
  `timerCHOP` 1 Гц + сравнение mtime в расширении (Python раз в секунду, не в кадре).
- Non-Commercial: `resolutionTOP` предподготовки не превышает 1280 → движок не зависит от размера входа.

## References
- `docs/architecture.md` §4, §6, §11, §13, §14 — конвейер, факты этапа 0, грабли.
- `docs/core-api.md` — `segments_by_fixture`, `routing_table`, `cell_order`, `segment_table`, `cell_table`.
- `/project1/stage0` в `td/dsm_dev.toe` — образец адресации и `perf_probe`.
- twozero: `td_get_hints('bootstrap', 'pop_basics', 'pop_attribute_system', 'pop_lookup_pops',
  'pop_cross_family_bridges', 'pop_glsl_minimal', 'pop_self_sufficient_comp', 'pop_history_cache')`;
  `td_get_docs` по `dmxfixturePOP`, `dmxoutPOP`, `lookuptexturePOP`, `glslPOP`, `glsladvancedPOP`,
  `linebreakPOP`, `attributePOP`, `attributeconvertPOP`, `dattoPOP`, `cachePOP`, `deletePOP`, `dmxmapDAT`,
  `poptoTOP`, `poptoCHOP`, `inPOP`, `outPOP`, `linePOP`.
- Скилл `dmxfixture-pop-geometry` (forum.derivative.ca «DMX POP performance»).
