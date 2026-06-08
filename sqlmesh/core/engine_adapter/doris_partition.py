from __future__ import annotations

import re
import typing as t
from datetime import date, timedelta

from sqlmesh.utils.date import to_datetime, to_timestamp

_DORIS_PARTITION_RE = re.compile(
    r"^\s*FROM\s*\(.+?\)\s+TO\s*\(.+?\)\s+INTERVAL\s+(?P<count>\d+)\s+(?P<unit>[A-Z]+)\s*$",
    flags=re.IGNORECASE,
)
_SUPPORTED_PARTITION_UNITS = {"DAY", "MONTH", "YEAR"}


def doris_partition_bounds(
    partition_text: str,
    intervals: t.Iterable[t.Tuple[t.Any, t.Any]],
) -> t.Optional[t.Tuple[date, date, int, str]]:
    match = _DORIS_PARTITION_RE.match(partition_text)
    if not match:
        return None

    count = int(match.group("count"))
    unit = match.group("unit").upper()
    if unit not in _SUPPORTED_PARTITION_UNITS:
        return None

    interval_list = list(intervals)
    starts = [to_datetime(start) for start, _ in interval_list]
    ends = [to_datetime(end) for _, end in interval_list]
    if not starts or not ends:
        return None

    from_dt = _floor_doris_partition_boundary(min(starts), unit, count)
    to_dt = _ceil_doris_partition_boundary(max(ends), unit, count)
    return from_dt, to_dt, count, unit


def doris_partition_text_for_intervals(
    partition_text: str,
    intervals: t.Iterable[t.Tuple[t.Any, t.Any]],
) -> t.Optional[str]:
    bounds = doris_partition_bounds(partition_text, intervals)
    if not bounds:
        return None

    from_dt, to_dt, count, unit = bounds
    return f"FROM ('{from_dt:%Y-%m-%d}') TO ('{to_dt:%Y-%m-%d}') INTERVAL {count} {unit}"


def parse_doris_partition_range(text: str) -> t.Optional[t.Tuple[date, date]]:
    keys = re.findall(
        r"keys:\s*\[\s*(?P<date>\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?\s*\]",
        text,
        flags=re.IGNORECASE,
    )
    if len(keys) >= 2:
        return date.fromisoformat(keys[0]), date.fromisoformat(keys[1])

    match = re.search(
        r"\[\s*\(?['\"]?(?P<start>\d{4}-\d{2}-\d{2})"
        r"(?:\s+\d{2}:\d{2}:\d{2})?['\"]?\)?\s*,\s*"
        r"\(?['\"]?(?P<end>\d{4}-\d{2}-\d{2})"
        r"(?:\s+\d{2}:\d{2}:\d{2})?['\"]?\)?\s*\)",
        text,
    )
    if match:
        return date.fromisoformat(match.group("start")), date.fromisoformat(match.group("end"))

    return None


def doris_partition_ranges(
    start: date,
    end: date,
    count: int,
    unit: str,
) -> t.List[t.Tuple[date, date]]:
    ranges = []
    current = start
    while current < end:
        next_date = _add_doris_partition_interval(current, unit, count)
        ranges.append((current, min(next_date, end)))
        current = next_date
    return ranges


def missing_partition_ranges(
    required_ranges: t.Iterable[t.Tuple[date, date]],
    existing_ranges: t.Iterable[t.Tuple[date, date]],
) -> t.List[t.Tuple[date, date]]:
    existing = list(existing_ranges)
    return [
        required_range
        for required_range in required_ranges
        if not any(_covers_range(existing_range, required_range) for existing_range in existing)
    ]


def doris_partition_name(start: date, unit: str) -> str:
    if unit == "DAY":
        return f"`p{start:%Y%m%d}`"
    if unit == "MONTH":
        return f"`p{start:%Y%m}`"
    return f"`p{start:%Y}`"


def _covers_range(existing_range: t.Tuple[date, date], required_range: t.Tuple[date, date]) -> bool:
    return existing_range[0] <= required_range[0] and existing_range[1] >= required_range[1]


def _floor_doris_partition_boundary(dt: t.Any, unit: str, count: int) -> date:
    value = to_datetime(dt)
    if unit == "DAY":
        day = value.date()
        epoch = date(1970, 1, 1)
        offset = (day - epoch).days // count * count
        return epoch + timedelta(days=offset)
    if unit == "MONTH":
        month_index = value.year * 12 + value.month - 1
        floored = month_index // count * count
        return date(floored // 12, floored % 12 + 1, 1)
    if unit == "YEAR":
        year = value.year // count * count
        return date(year, 1, 1)
    raise ValueError(f"Unsupported Doris partition unit: {unit}")


def _ceil_doris_partition_boundary(dt: t.Any, unit: str, count: int) -> date:
    value = to_datetime(dt)
    floor = _floor_doris_partition_boundary(value, unit, count)
    if to_timestamp(floor) == to_timestamp(value):
        return floor
    return _add_doris_partition_interval(floor, unit, count)


def _add_doris_partition_interval(value: date, unit: str, count: int) -> date:
    if unit == "DAY":
        return value + timedelta(days=count)
    if unit == "MONTH":
        month_index = value.year * 12 + value.month - 1 + count
        return date(month_index // 12, month_index % 12 + 1, 1)
    if unit == "YEAR":
        return date(value.year + count, 1, 1)
    raise ValueError(f"Unsupported Doris partition unit: {unit}")
