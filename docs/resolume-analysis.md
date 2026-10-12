# Resolume Arena: DMX и пиксель-маппинг — разбор

Состояние на октябрь 2026: актуальная ветка Arena 7.x (7.28, сентябрь 2026), мануал по DMX — v7.22,
об Arena 8 упоминаний нет. Форум Resolume напрямую не читается (403) — данные с форума взяты
из поисковых выдержек. Пометки: **(подтв.)** — есть источник, **(вывод)** — заключение по отсутствию упоминаний.

## Модель данных

- **Lumiverse** — набор DMX-юниверсов, тип экрана в Advanced Output. Auto Span (с 7.5): фикстура,
  вышедшая за 512 каналов, автоматически занимает следующий юниверс. Align Output: пиксель
  не делится между юниверсами (170 RGB на юниверс). (подтв.) [dmx][7.5]
- Адресация — только Subnet (0–15) и Universe (0–15), без Net: до 256 юниверсов на IP.
  Нумерация с нуля. (подтв.) [f106629][f59709]
- **Только Art-Net**, sACN нет. Одна цель на лумиверс: broadcast, IP или найденный узел (по имени).
  Одна сетевая карта на всё. (подтв.) [dmx][f101255]
- **Fixture Editor**: сетка W×H, 16 вариантов раскладки, цвет RGB и перестановки, L, CMY, RGBW/RGBWA
  (белый — приглушением других каналов, без настроек). Только 8 бит. Одна пиксельная зона на фикстуру;
  служебные каналы с постоянным значением — только до или после неё. Гамма на пресет (2.5).
  Произвольный порядок каналов — только правкой XML. (подтв.) [fe][f93976][f94514]
- Фикстура на композиции — прямоугольник с поворотом; цвет берётся **одной точкой в центре клетки**.
  Линий, полилиний, кривых нет. (подтв.) [dmx][f98683]

## Интерфейс

- Привязки, стрелки ±1/±10 px, мультивыбор, арифметика в полях, Alt-дубль и Ctrl+D (повтор
  смещения). (подтв.) [is][a51]
- Вкладка DMX Output: карта каналов, перетаскивание меняет адрес, конфликты красным. (подтв.) [dmx]
- Автоадрес — следом за предыдущей фикстурой; при дубле группы фикстуры раскладываются по адресам
  поодиночке. (подтв.) [f80013]
- Тест — только общая тест-карта; подсветки или соло фикстуры нет. (вывод)
- Импорта CSV / MVR / GDTF нет; массовые правки — через XML пресета. (вывод) [f92337]
- Пресет выхода хранится отдельно от композиции, но библиотеку фикстур переносят вручную. (подтв.) [ao][dl]

## Производительность

- Частота и задержка (по умолчанию 40 мс) — общие на все юниверсы; ArtSync есть в мануале v7.22. [dmx]
- Частота вывода — около 40 Гц, запрос на 60 Гц без ответа. (вывод) [f95234]
- 16 бит — открытый запрос с 2019 года. (подтв.) [f105975]

## Что хвалят

Маппинг прямо на живой композиции; Auto Span и Align Output; наглядная карта каналов; пресеты выхода
отдельно от контента.

## Минусы и как DSM их закрывает

| Минус Resolume | DSM |
|---|---|
| только прямоугольные сетки | линия, полилиния, кривая, дуга, матрица, своя таблица точек |
| выборка одной точкой — мерцание | точка, область, вытянутая область |
| нельзя отключить пиксель, нет шага и «каждый N-й» | есть все три |
| служебные каналы только до/после пикселей | профиль блоками, каналы внутри ячейки |
| порядок цвета из меню, RGBW без настроек, только 8 бит | порядок строкой, режимы белого, 8/16 бит |
| только Art-Net, без Net, ≤256 юниверсов на IP | Art-Net 4, sACN unicast/multicast с priority, IP на юниверс |
| не знает портов контроллера | универсальные правила порта |
| дубль группы — адреса вразнобой | адреса блоком |
| нет импорта, правки в XML | CSV/JSON, генератор массивов, Python-API |
| нет подсветки фикстуры | Locate |
| нет лимита мощности | общий бюджет и на выход |
| ~40 Гц, DMX не быстрее рендера | частота = fps проекта, ArtSync |
| нет 3D | выходная карта в 3D (позже) |

## У других — что стоит взять

- **MadMapper 6**: фикстуры Line / Circle / Bézier / Matrix / Custom; выборка Point / Box / Anamorphic;
  перевод отдельного пикселя в UNPATCHED; пропуск каналов 511–512; 8/16 бит; пакетное создание из CSV;
  LED Scanner (раскладка по камере). [mm][mms]
- **MADRIX 5**: Matrix Generator, Auto Address, импорт CSV. [mx][mxcsv]
- **ENTTEC ELM**: Art-Net, sACN, KiNet, ArtSync, смещение адресов для слияния с пультом. [elm]
- **TouchDesigner 2025**: DMX Fixture POP (профиль блоками на точку или примитив, адрес атрибутами),
  DMX Out POP (Art-Net / sACN / KiNET, маршрутизация по юниверсам, ArtSync, Multipliers). [tdfix][tdout]

## Источники

- [dmx] https://resolume.com/support/en/dmx
- [7.5] https://resolume.com/blog/20839
- [fe] https://resolume.com/support/en/fixture-editor
- [is] https://resolume.com/support/en/input-selection
- [ao] https://resolume.com/support/en/advanced-output
- [dl] https://resolume.com/support/en/directory-list
- [a51] https://resolume.com/article/51
- [f106629] https://resolume.com/forum/viewtopic.php?p=106629
- [f59709] https://resolume.com/forum/viewtopic.php?p=59709
- [f101255] https://resolume.com/forum/viewtopic.php?p=101255
- [f93976] https://resolume.com/forum/viewtopic.php?p=93976
- [f94514] https://resolume.com/forum/viewtopic.php?p=94514
- [f98683] https://resolume.com/forum/viewtopic.php?p=98683
- [f80013] https://www.resolume.com/forum/viewtopic.php?p=80013
- [f92337] https://resolume.com/forum/viewtopic.php?p=92337
- [f95234] https://www.resolume.com/forum/viewtopic.php?p=95234
- [f105975] https://www.resolume.com/forum/viewtopic.php?p=105975
- [mm] https://docs.madmapper.com/madmapper/6/5.-dmx-and-led-mapping/dmx-fixtures
- [mms] https://docs.madmapper.com/madmapper/6/5.-dmx-and-led-mapping/tools-led-scanner
- [mx] https://help.madrix.com/m5/html/madrix/hidd_matrix_generator.html
- [mxcsv] https://help.madrix.com/m5/html/madrix/hidd_csv_import.html
- [elm] https://www.enttec.com/product/dmx-lighting-control-software/pixel-mapping-software-2025/
- [tdfix] https://docs.derivative.ca/DMX_Fixture_POP
- [tdout] https://docs.derivative.ca/DMX_Out_POP
