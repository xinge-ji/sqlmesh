from __future__ import annotations

import typing as t
from datetime import datetime, timedelta, tzinfo
from functools import lru_cache

from croniter import croniter
from sqlglot.helper import first

from sqlmesh.utils.date import TimeLike, now, to_datetime


def interval_seconds(cron: str, tz: t.Optional[tzinfo] = None) -> int:
    return _interval_seconds(cron, tz or now(minute_floor=False).tzinfo)  # type: ignore[arg-type]


@lru_cache(maxsize=16384)
def _interval_seconds(cron: str, tz: tzinfo) -> int:
    """Computes the interval seconds of a cron statement if it is deterministic.

    Args:
        cron: The cron string.
        tz: The timezone to evaluate the cron schedule in.

    Returns:
        The number of seconds that cron represents if it is stable, otherwise 0.
    """
    deltas = set()
    cron_iterator = croniter(cron, now(minute_floor=False, tz=tz))
    curr = to_datetime(cron_iterator.get_next(datetime), tz=tz)

    for _ in range(5):
        prev = curr
        curr = to_datetime(cron_iterator.get_next(datetime), tz=tz)
        deltas.add(curr - prev)

        if len(deltas) > 1:
            return 0
    return int(first(deltas).total_seconds())


class CroniterCache:
    def __init__(self, cron: str, time: t.Optional[TimeLike] = None, tz: t.Optional[tzinfo] = None):
        self.cron = cron
        self.tz = tz
        self.curr: datetime = to_datetime(now() if time is None else time, tz=self.tz)
        self.interval_seconds = interval_seconds(self.cron, tz=self.tz)

    def get_next(self, estimate: bool = False) -> datetime:
        if estimate and self.interval_seconds:
            self.curr = self.curr + timedelta(seconds=self.interval_seconds)
        else:
            self.curr = to_datetime(croniter(self.cron, self.curr).get_next(datetime), tz=self.tz)
        return self.curr

    def get_prev(self, estimate: bool = False) -> datetime:
        if estimate and self.interval_seconds:
            self.curr = self.curr - timedelta(seconds=self.interval_seconds)
        else:
            self.curr = to_datetime(croniter(self.cron, self.curr).get_prev(datetime), tz=self.tz)
        return self.curr
