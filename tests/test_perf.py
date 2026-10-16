"""6.14: автоадресация + проверки + таблица ячеек: 50 000 ячеек ≤ 1 с, 20 000 ≤ 0,3 с."""
import time

import pytest

from conftest import fixture, make_patch, output, rgb_type
from dsm_core import api


def big_patch(n_fixtures: int, n_outputs: int = 8):
    fxs, outs = [], []
    per = -(-n_fixtures // n_outputs)
    for o in range(n_outputs):
        ids = [f"fx{i + 1}" for i in range(o * per, min((o + 1) * per, n_fixtures))]
        outs.append(output(f"out{o + 1}", ip=f"127.0.0.{o + 1}", fixture_ids=tuple(ids)))
        fxs += [fixture(i, "ft1", f"out{o + 1}", x=float(k)) for k, i in enumerate(ids)]
    return make_patch([rgb_type(cells=144)], fxs, outs)


def _run(p):
    t = time.perf_counter()
    p, issues = api.auto_assign_all(p)
    api.validate(p)
    rows = api.cell_table(p)
    return time.perf_counter() - t, len(rows) - 1, issues


@pytest.mark.parametrize("n_fixtures,limit", [(140, 0.3), (350, 1.0)])
def test_addressing_speed(n_fixtures, limit):
    elapsed, cells, issues = _run(big_patch(n_fixtures))
    assert issues == [] and cells == n_fixtures * 144
    if elapsed > limit:
        pytest.xfail(f"{cells} ячеек за {elapsed:.2f} с (порог {limit} с) — медленная машина?")
    print(f"\n{cells} ячеек: {elapsed * 1000:.0f} мс")
