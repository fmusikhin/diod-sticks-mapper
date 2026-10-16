import pytest

from dsm_core.protocol import (Protocol, dotted, format_universe, from_port_address, sacn_multicast,
                               to_port_address, universe_in_range)


def test_stage0_universe_20_is_0_1_4():
    assert to_port_address(0, 1, 4) == 20
    assert from_port_address(20) == (0, 1, 4)
    assert dotted(20) == "0.1.4"
    assert format_universe(Protocol.ARTNET, 20) == "20 (0.1.4)"
    assert format_universe(Protocol.SACN, 20) == "20"


def test_roundtrip_and_limits():
    assert from_port_address(32767) == (127, 15, 15)
    for pa in (0, 1, 255, 256, 4095, 32767):
        assert to_port_address(*from_port_address(pa)) == pa
    with pytest.raises(ValueError):
        from_port_address(32768)
    with pytest.raises(ValueError):
        to_port_address(0, 16, 0)


def test_ranges():
    assert universe_in_range(Protocol.ARTNET, 0) and not universe_in_range(Protocol.ARTNET, 32768)
    assert universe_in_range(Protocol.SACN, 63999) and not universe_in_range(Protocol.SACN, 0)


def test_sacn_multicast():
    assert sacn_multicast(1) == "239.255.0.1"
    assert sacn_multicast(256) == "239.255.1.0"
    with pytest.raises(ValueError):
        sacn_multicast(64000)
