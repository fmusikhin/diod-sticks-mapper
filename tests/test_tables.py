from conftest import fixture, make_patch, output, rgb_type
from dsm_core.addressing import auto_assign_all
from dsm_core.model import TargetKind
from dsm_core.protocol import Protocol
from dsm_core.tables import address_table, cell_table, issue_table, routing_table, segment_table, summary


def test_address_table_stage0(stage0_patch):
    p = auto_assign_all(stage0_patch)[0]
    rows = address_table(p)
    assert rows[0][:8] == ["fixture_id", "name", "type", "output", "protocol", "universe", "universe_dotted", "channel"]
    r = rows[1]
    assert r[5] == "20" and r[6] == "0.1.4" and r[7] == "1" and r[8] == "200" and r[9] == "600"
    assert r[10] == "20:1-510;21:1-90"
    assert all(len(x) == len(rows[0]) for x in rows)


def test_cell_table_rows_and_positions(stage0_patch):
    p = auto_assign_all(stage0_patch)[0]
    rows = cell_table(p)
    fx1 = [r for r in rows[1:] if r[0] == "fx1"]
    assert len(fx1) == 200 and all(len(r) == len(rows[0]) for r in rows)
    assert fx1[0][3:6] == ["20", "1", "3"] and fx1[169][3:6] == ["20", "508", "3"] and fx1[170][3:6] == ["21", "1", "3"]


def test_segment_and_routing_tables(stage0_patch):
    p = auto_assign_all(stage0_patch)[0]
    segs = segment_table(p)
    assert segs[1] == ["fx1", "20", "1", "510", "cells", "0", "170", "0"]
    rt = routing_table(p)
    assert rt[1][3:7] == ["20", "0.1.4", "unicast", "127.0.0.1"] and rt[2][3] == "21"
    assert [r[6] for r in rt[1:]] == ["127.0.0.1", "127.0.0.1", "127.0.0.2", "127.0.0.3"]


def test_routing_default_ip_and_sacn_multicast():
    p = make_patch([rgb_type(cells=10)], [fixture("fx1"), fixture("fx2", "ft1", "out2", x=10.0)],
                   [output(fixture_ids=("fx1",)),
                    output("out2", protocol=Protocol.SACN, start=1, fixture_ids=("fx2",), target_kind=TargetKind.MULTICAST)])
    p = auto_assign_all(p)[0]
    rt = routing_table(p)
    assert rt[1][6] == "127.0.0.1" and rt[2][6] == "239.255.0.1" and rt[2][4] == ""


def test_summary_and_issue_table(stage0_patch):
    p = auto_assign_all(stage0_patch)[0]
    s = summary(p)
    assert s == {"fixtures": 3, "cells": 388, "channels": 1164, "universes": 4, "outputs": 3, "errors": 0, "warnings": 0}
    assert issue_table(p) == [["level", "code", "message", "fixture_ids", "output_id", "port_id", "universe", "channels"]]
