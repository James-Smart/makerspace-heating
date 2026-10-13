"""Tests for daily runtime statistics."""

from datetime import date, timedelta

from custom_components.makerspace_heating.engine.stats import (
    MAX_STEP_GAP,
    Stats,
    accumulate,
)

TODAY = date(2026, 10, 13)


def test_accumulates_room_and_boiler_time() -> None:
    stats = Stats()
    stats.roll(TODAY)
    accumulate(stats, timedelta(seconds=30), ["w"], True)
    accumulate(stats, timedelta(seconds=30), ["w", "m"], False)
    assert stats.room("w").heating_seconds == 60
    assert stats.room("m").heating_seconds == 30
    assert stats.boiler_on_seconds == 30


def test_caps_long_gaps_so_downtime_is_not_counted() -> None:
    assert MAX_STEP_GAP == timedelta(seconds=60)
    stats = Stats()
    accumulate(stats, timedelta(hours=3), ["w"], True)
    assert stats.room("w").heating_seconds == 60
    assert stats.boiler_on_seconds == 60


def test_ignores_negative_intervals() -> None:
    stats = Stats()
    accumulate(stats, timedelta(seconds=-5), ["w"], True)
    assert stats.rooms == {}
    assert stats.boiler_on_seconds == 0


def test_roll_resets_only_on_a_new_day() -> None:
    stats = Stats()
    stats.roll(TODAY)
    stats.room("w").boosts = 2
    stats.boiler_starts = 3
    stats.roll(TODAY)
    assert stats.room("w").boosts == 2
    stats.roll(date(2026, 10, 14))
    assert stats.rooms == {}
    assert stats.boiler_starts == 0
    assert stats.day == date(2026, 10, 14)
