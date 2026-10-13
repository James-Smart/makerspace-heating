"""Tests for status time formatting."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.makerspace_heating.engine.timefmt import (
    format_clock,
    format_duration,
)


def test_seconds_only() -> None:
    assert format_duration(timedelta(seconds=40)) == "40 s"


def test_minutes_and_seconds() -> None:
    assert format_duration(timedelta(minutes=1, seconds=40)) == "1 min 40 s"


def test_whole_minutes() -> None:
    assert format_duration(timedelta(minutes=2)) == "2 min"


def test_hours_drop_seconds() -> None:
    assert format_duration(timedelta(hours=1, minutes=30, seconds=5)) == "1 h 30 min"


def test_whole_hours() -> None:
    assert format_duration(timedelta(hours=2)) == "2 h"


def test_zero_and_negative() -> None:
    assert format_duration(timedelta(0)) == "0 s"
    assert format_duration(timedelta(seconds=-5)) == "0 s"


def test_clock_uses_given_zone() -> None:
    moment = datetime(2026, 10, 13, 18, 0, tzinfo=ZoneInfo("UTC"))
    assert format_clock(moment, ZoneInfo("Europe/London")) == "19:00"
