"""Занятость каналов: отсортированные интервалы на пару (выход, юниверс)."""
from __future__ import annotations

import bisect

Interval = tuple[int, int, str]   # first, last (включительно, 1..512), fixture_id


class Occupancy:
    def __init__(self) -> None:
        self._map: dict[tuple[str, int], list[Interval]] = {}

    def intervals(self, output_id: str, universe: int) -> list[Interval]:
        return self._map.get((output_id, universe), [])

    def universes(self, output_id: str) -> list[int]:
        return sorted(u for o, u in self._map if o == output_id)

    def add(self, output_id: str, universe: int, first: int, last: int, fixture_id: str) -> None:
        lst = self._map.setdefault((output_id, universe), [])
        bisect.insort(lst, (first, last, fixture_id))

    def next_free(self, output_id: str, universe: int, start: int, width: int, limit: int) -> int | None:
        """Первый канал ≥ start, с которого свободны width каналов и последний ≤ limit; иначе None."""
        pos = start
        for first, last, _ in self.intervals(output_id, universe):
            if last < pos:
                continue
            if first > pos + width - 1:
                break
            pos = last + 1
        return pos if pos + width - 1 <= limit else None

    def overlaps(self) -> list[tuple[str, int, Interval, Interval]]:
        out = []
        for (o, u), lst in sorted(self._map.items()):
            for i in range(len(lst)):
                for j in range(i + 1, len(lst)):
                    if lst[j][0] > lst[i][1]:
                        break
                    out.append((o, u, lst[i], lst[j]))
        return out
