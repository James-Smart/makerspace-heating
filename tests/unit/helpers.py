"""Shared helpers for engine tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Return a moment on 13 October 2026, London time."""
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=LONDON)
