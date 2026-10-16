"""Перехват своих Art-Net / sACN пакетов на этой машине.

Слушает порт протокола на каждом из указанных локальных адресов (по умолчанию 127.0.0.1)
и складывает в JSON: по адресу назначения → по юниверсу → последние байты DMX, число пакетов
и средний интервал между пакетами. Нужен для сверки вывода маппера байт в байт.

usage: python tools/dmx_listen.py artnet|sacn SECONDS OUT.json [--bind 127.0.0.1,127.0.0.2]

Art-Net: юниверс — 15-битный port-address (SubUni | Net<<8), как в пакете ArtDmx.
sACN:    юниверс — как в пакете E1.31 (с 1).
"""
import argparse
import json
import select
import socket
import struct
import time

ARTNET_PORT = 6454
SACN_PORT = 5568


def parse_artnet(d):
    if d[:8] != b'Art-Net\x00':
        return None
    opcode = struct.unpack('<H', d[8:10])[0]
    if opcode == 0x5200:            # ArtSync
        return ('sync', None, b'')
    if opcode != 0x5000:            # не ArtDmx
        return None
    uni = d[14] | (d[15] << 8)
    n = struct.unpack('>H', d[16:18])[0]
    return ('dmx', uni, d[18:18 + n])


def parse_sacn(d):
    if len(d) < 126 or d[4:16] != b'ASC-E1.17\x00\x00\x00':
        return None
    uni = struct.unpack('>H', d[113:115])[0]
    n = struct.unpack('>H', d[123:125])[0] - 1     # минус стартовый код
    return ('dmx', uni, d[126:126 + n])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('proto', choices=['artnet', 'sacn'])
    ap.add_argument('seconds', type=float)
    ap.add_argument('out')
    ap.add_argument('--bind', default='127.0.0.1',
                    help='локальные адреса через запятую; на каждый — свой сокет')
    a = ap.parse_args()

    port = ARTNET_PORT if a.proto == 'artnet' else SACN_PORT
    parse = parse_artnet if a.proto == 'artnet' else parse_sacn
    socks = {}
    for ip in a.bind.split(','):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((ip, port))
        s.setblocking(False)
        socks[s] = ip

    # dst ip -> universe -> {data, count, times}
    res = {ip: {} for ip in socks.values()}
    sync = {ip: 0 for ip in socks.values()}
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        ready, _, _ = select.select(list(socks), [], [], 0.1)
        for s in ready:
            d, addr = s.recvfrom(2048)
            p = parse(d)
            if p is None:
                continue
            kind, uni, data = p
            ip = socks[s]
            if kind == 'sync':
                sync[ip] += 1
                continue
            u = res[ip].setdefault(uni, {'data': [], 'count': 0, 'times': [], 'src': addr[0]})
            u['data'] = list(data)
            u['count'] += 1
            u['times'].append(time.time())

    out = {'proto': a.proto, 'seconds': a.seconds, 'dst': {}}
    for ip, unis in res.items():
        out['dst'][ip] = {'artsync': sync[ip], 'universes': {}}
        for uni, u in sorted(unis.items()):
            ts = u['times']
            gaps = [b - c for c, b in zip(ts, ts[1:])]
            out['dst'][ip]['universes'][str(uni)] = {
                'len': len(u['data']),
                'data': u['data'],
                'packets': u['count'],
                'rate_hz': round(u['count'] / a.seconds, 1),
                'gap_ms_avg': round(1000 * sum(gaps) / len(gaps), 2) if gaps else None,
                'gap_ms_max': round(1000 * max(gaps), 2) if gaps else None,
                'src': u['src'],
            }
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)

    for ip, d in out['dst'].items():
        unis = d['universes']
        print(ip, 'universes', sorted(int(k) for k in unis),
              'rate', {k: v['rate_hz'] for k, v in unis.items()},
              'artsync', d['artsync'])


if __name__ == '__main__':
    main()
