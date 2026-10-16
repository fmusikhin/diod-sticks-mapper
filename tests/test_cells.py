from dsm_core.cells import cell_count, cell_order, matrix_order
from dsm_core.model import Direction, FixtureKind, FixtureType, MatrixLayout
from conftest import fixture, rgb_type

# сетка 4×3: grid_index = row*4 + col, строка 0 сверху
#  0  1  2  3
#  4  5  6  7
#  8  9 10 11
EXPECTED = {
    MatrixLayout.TL_ROWS:       [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    MatrixLayout.TL_ROWS_SNAKE: [0, 1, 2, 3, 7, 6, 5, 4, 8, 9, 10, 11],
    MatrixLayout.TL_COLS:       [0, 4, 8, 1, 5, 9, 2, 6, 10, 3, 7, 11],
    MatrixLayout.TL_COLS_SNAKE: [0, 4, 8, 9, 5, 1, 2, 6, 10, 11, 7, 3],
    MatrixLayout.TR_ROWS:       [3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8],
    MatrixLayout.TR_ROWS_SNAKE: [3, 2, 1, 0, 4, 5, 6, 7, 11, 10, 9, 8],
    MatrixLayout.TR_COLS:       [3, 7, 11, 2, 6, 10, 1, 5, 9, 0, 4, 8],
    MatrixLayout.TR_COLS_SNAKE: [3, 7, 11, 10, 6, 2, 1, 5, 9, 8, 4, 0],
    MatrixLayout.BL_ROWS:       [8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3],
    MatrixLayout.BL_ROWS_SNAKE: [8, 9, 10, 11, 7, 6, 5, 4, 0, 1, 2, 3],
    MatrixLayout.BL_COLS:       [8, 4, 0, 9, 5, 1, 10, 6, 2, 11, 7, 3],
    MatrixLayout.BL_COLS_SNAKE: [8, 4, 0, 1, 5, 9, 10, 6, 2, 3, 7, 11],
    MatrixLayout.BR_ROWS:       [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
    MatrixLayout.BR_ROWS_SNAKE: [11, 10, 9, 8, 4, 5, 6, 7, 3, 2, 1, 0],
    MatrixLayout.BR_COLS:       [11, 7, 3, 10, 6, 2, 9, 5, 1, 8, 4, 0],
    MatrixLayout.BR_COLS_SNAKE: [11, 7, 3, 2, 6, 10, 9, 5, 1, 0, 4, 8],
}


def test_all_16_layouts_on_4x3():
    assert len(EXPECTED) == 16 == len(MatrixLayout)
    for layout, exp in EXPECTED.items():
        assert matrix_order(4, 3, layout) == exp, layout


def test_reverse_and_offset_on_5_cells():
    ft = rgb_type(cells=5)
    fx = fixture("fx1", direction=Direction.REVERSE, cell_offset=2)
    # reverse: [4,3,2,1,0]; сдвиг 2 по кругу: data 0 → grid[(0+2)%5] = 2
    assert [c.grid_index for c in cell_order(ft, fx)] == [2, 1, 0, 4, 3]
    assert [c.grid_index for c in cell_order(ft, fixture("fx2"))] == [0, 1, 2, 3, 4]


def test_every_n_and_disabled_keep_indices():
    ft = rgb_type(cells=10)
    cells = cell_order(ft, fixture("fx1", every_n=3, disabled=frozenset({0, 4})))
    assert len(cells) == 10
    assert [c.data_index for c in cells if c.active] == [3, 6, 9]


def test_strip_cell_count_from_length():
    ft = FixtureType(id="s", name="s", kind=FixtureKind.STRIP, cells=60, pitch_mm=1000 / 60)
    assert cell_count(ft, fixture("fx1", length_mm=2500)) == 150
    assert cell_count(ft, fixture("fx1")) == 60
    ft2 = FixtureType(id="s", name="s", kind=FixtureKind.STRIP, cells=60, cells_per_m=30)
    assert cell_count(ft2, fixture("fx1", length_mm=1990)) == 59


def test_matrix_cells_follow_layout():
    ft = FixtureType(id="m", name="m", kind=FixtureKind.MATRIX, cells=12, width=4, height=3,
                     layout=MatrixLayout.TL_ROWS_SNAKE)
    assert [c.grid_index for c in cell_order(ft, fixture("fx1"))] == EXPECTED[MatrixLayout.TL_ROWS_SNAKE]
