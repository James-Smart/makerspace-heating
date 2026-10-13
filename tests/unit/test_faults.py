"""Tests for fault debounce and the heating-but-not-warming watch."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.faults import (
    STARTUP_GRACE,
    FaultTracker,
    WarmingWatch,
)
from custom_components.makerspace_heating.engine.model import (
    HUB,
    FaultKey,
    FaultKind,
    GlobalSettings,
)
from tests.unit.helpers import at

SETTINGS = GlobalSettings()
TEMP = FaultKey("w", FaultKind.TEMP_UNAVAILABLE)
CALENDAR = FaultKey(HUB, FaultKind.CALENDAR_FAILING)


class TestFaultTracker:
    def test_raises_after_fault_delay(self) -> None:
        tracker = FaultTracker(started_at=at(9))
        assert tracker.update({TEMP: "msg"}, SETTINGS, at(10)) == {}
        assert tracker.update({TEMP: "msg"}, SETTINGS, at(10, 4, 59)) == {}
        assert tracker.update({TEMP: "msg"}, SETTINGS, at(10, 5)) == {TEMP: "msg"}

    def test_clearing_restarts_the_delay(self) -> None:
        tracker = FaultTracker(started_at=at(9))
        tracker.update({TEMP: "msg"}, SETTINGS, at(10))
        tracker.update({}, SETTINGS, at(10, 3))
        assert tracker.update({TEMP: "msg"}, SETTINGS, at(10, 6)) == {}
        assert tracker.update({TEMP: "msg"}, SETTINGS, at(10, 11)) == {TEMP: "msg"}

    def test_self_timed_kinds_raise_immediately(self) -> None:
        tracker = FaultTracker(started_at=at(9))
        assert tracker.update({CALENDAR: "cal"}, SETTINGS, at(10)) == {CALENDAR: "cal"}

    def test_startup_grace(self) -> None:
        assert STARTUP_GRACE == timedelta(minutes=2)
        tracker = FaultTracker(started_at=at(10))
        assert tracker.update({CALENDAR: "cal"}, SETTINGS, at(10, 1, 59)) == {}
        assert tracker.update({CALENDAR: "cal"}, SETTINGS, at(10, 2)) == {CALENDAR: "cal"}

    def test_zero_delay(self) -> None:
        settings = GlobalSettings(fault_delay=timedelta(0))
        tracker = FaultTracker(started_at=at(9))
        assert tracker.update({TEMP: "msg"}, settings, at(10)) == {TEMP: "msg"}


class TestWarmingWatch:
    def test_flags_long_call_without_rise(self) -> None:
        watch = WarmingWatch()
        assert watch.update("w", True, 15.0, SETTINGS, at(10)) is None
        assert watch.update("w", True, 15.2, SETTINGS, at(10, 59)) is None
        assert watch.update("w", True, 15.2, SETTINGS, at(11)) == (
            "heating for over 1 h, only warmed 0.2 °C. "
            "Stuck valve, airlock or window open?"
        )

    def test_reaching_the_rise_restarts_the_watch(self) -> None:
        watch = WarmingWatch()
        watch.update("w", True, 15.0, SETTINGS, at(10))
        watch.update("w", True, 15.5, SETTINGS, at(10, 30))
        assert watch.update("w", True, 15.6, SETTINGS, at(11)) is None
        assert watch.update("w", True, 15.6, SETTINGS, at(11, 30)) is not None

    def test_stopping_resets(self) -> None:
        watch = WarmingWatch()
        watch.update("w", True, 15.0, SETTINGS, at(10))
        watch.update("w", False, 15.0, SETTINGS, at(10, 30))
        watch.update("w", True, 15.0, SETTINGS, at(10, 31))
        assert watch.update("w", True, 15.0, SETTINGS, at(11)) is None

    def test_missing_temperature_resets(self) -> None:
        watch = WarmingWatch()
        watch.update("w", True, 15.0, SETTINGS, at(10))
        watch.update("w", True, None, SETTINGS, at(10, 30))
        assert watch.update("w", True, 15.0, SETTINGS, at(11)) is None
