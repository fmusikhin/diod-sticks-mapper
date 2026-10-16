"""Публичный фасад ядра. Потребители (слой TD, интерфейс, Locate) правят патч только через эти функции.

Ни одна функция не пишет файлы и не ходит в сеть, кроме явных ``load``/``save``.
Все операции правки — чистые: возвращают новый патч, исходный не меняется.
"""
from __future__ import annotations

# документ
from .io_json import dumps, load, loads, new_patch, save                                       # noqa: F401
from .schema import SCHEMA_VERSION, from_dict, to_dict, validate_document                      # noqa: F401
from .errors import CsvError, LayoutError, OperationError, PatchError, SchemaError             # noqa: F401
# модель и поиск по id
from .model import (Acceptance, Address, AddressMode, ArcShape, Canvas, Channel, ChannelBlock, CurveShape,  # noqa: F401
                    CustomShape, Direction, Fixture, FixtureKind, FixtureType, Group, LineShape, MatrixLayout,
                    Output, OutputRules, Patch, PolylineShape, Port, RectShape, Sampling, Shape, TargetKind,
                    get_fixture, get_fixture_type, get_group, get_output, get_port)
from .protocol import Protocol, dotted, format_universe, from_port_address, sacn_multicast, to_port_address  # noqa: F401
# правка
from .ops import (add_fixture, add_fixture_type, add_group, add_output, add_port, assign_to_output,  # noqa: F401
                  clear_address, duplicate_fixtures, fix_reversed, next_ids, remove_fixture, remove_fixture_type,
                  remove_group, remove_output, remove_port, set_acceptance, set_group_members, set_manual_address,
                  update_fixture, update_fixture_type, update_group, update_output, update_port)
# ячейки и адресация
from .cells import Cell, cell_count, cell_order, matrix_order                                  # noqa: F401
from .addressing import (Segment, assign_sequential, auto_assign, auto_assign_all, fixture_segments,  # noqa: F401
                         layout_fixture, segments_by_fixture)
# проверки
from .validate import validate                                                                  # noqa: F401
from .validate_types import Issue, IssueCode, IssueLevel                                        # noqa: F401
# таблицы и CSV
from .tables import (Rows, address_table, cell_table, issue_table, routing_table, segment_table,  # noqa: F401
                     summary)
from .csv_io import (RU, CsvDialect, CsvImportResult, CsvRowError, detect_dialect, export_fixtures_csv,  # noqa: F401
                     fixtures_rows, import_fixtures_csv, rows_to_csv)
# пресеты
from .presets import FIXTURE_PRESETS, PORT_PRESETS, preset_fixture_type                        # noqa: F401

__all__ = [n for n in dir() if not n.startswith("_")]
