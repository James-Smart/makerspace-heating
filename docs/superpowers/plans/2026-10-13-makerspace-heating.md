# Makerspace Heating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Makerspace Heating custom integration for Home Assistant. It replaces the space's schedules, automations and generic thermostats with one component that decides room valves and the boiler, and explains every decision.

**Architecture:** A pure-Python decision engine (`custom_components/makerspace_heating/engine/`, no Home Assistant imports, time passed in) holds all the rules and is unit-tested with scenarios. A thin Home Assistant layer has three parts:
- a controller that reads states, runs the engine, switches devices, fetches the calendar, persists state and raises alerts;
- entity platforms;
- a config flow with one sub-entry per room.

**Tech Stack:**
- Python 3.14 and Home Assistant 2026.10 (latest stable only).
- `probatio` for schemas, which is Home Assistant core's validation library.
- `pytest` + `pytest-homeassistant-custom-component` 0.13.372 for tests.
- `uv` for the virtualenv.
- GitHub Actions running tests, hassfest and HACS validation.

**Spec:** `docs/superpowers/specs/2026-10-13-makerspace-heating-design.md`. Read it before starting; this plan implements it section by section.

## Global Constraints

- Home Assistant: latest stable only. Do not write compatibility code for older releases.
  - `manifest.json` and `hacs.json` minimum: `2026.10.0`.
  - Tests pin `pytest-homeassistant-custom-component==0.13.372`, which pins `homeassistant==2026.10.1`.
- Python 3.14. Create the venv with `uv venv --python 3.14 .venv`. Run tests with `.venv/bin/pytest`.
- Schemas use `import probatio` (as Home Assistant core does), never `import voluptuous`.
- Nothing under `custom_components/makerspace_heating/engine/` may import `homeassistant`.
- Domain is `makerspace_heating` and the display name is `Makerspace Heating`. Only one config entry is allowed (`single_config_entry`).
- Temperatures are °C. Room targets are limited to 5–30 °C.
- Spec defaults:
  - default boost 30 min; maximum boost 2 h; dropdown step 30 min
  - valve margin 0.3 °C; wait for valves 3 min (0 = off)
  - minimum run 5 min; minimum rest 5 min; keep-open after stop 3 min
  - fault delay 5 min; heating-but-not-warming 60 min / 0.5 °C; calendar failure 30 min
  - boost target 19 °C, event target 20 °C, frost target 7 °C, pre-heat 30 min
  - engine tick 30 s; calendar fetch every 5 min for the next 24 h
  - button debounce 2 s; startup grace 2 min
  - control mode for a new install: Shadow
- Status text must match the spec examples exactly, e.g. `Boost until 14:32, at temperature`, `Waiting for valves: Workshop (1 min 40 s)`, `Off: resting (2 min left)`.
- No device-level fallback or timeouts on the Shellys (spec §8.4).
- GitHub repository URL and code owner are **not yet known**.
  - Until the user supplies them, use `https://github.com/CHANGE-ME/fizz-hass` and `@CHANGE-ME` in `manifest.json`.
  - Task 15 stops and asks the user for the real values before CI is pushed.
- Commit after every task on a feature branch, never on `main`. End commit messages with the co-author trailer your session specifies.

## Interpretations decided while planning

The spec leaves these open; this plan settles them so every task agrees.

1. **Valve mismatch.** "3 consecutive ticks" is measured as 90 s since the valve's reported state first differed from the current command (`VALVE_MISMATCH_AFTER`). While a mismatch fault is active, the room stops calling but keeps sending its wanted command, so a recovered valve clears the fault. This avoids flapping.
2. **Fault delay.** The 5-minute fault delay applies to availability and mismatch faults. *Heating but not warming* and *calendar failing* already have their own thresholds, so they are raised as soon as those thresholds are reached. Nothing is raised in the first 2 minutes after startup.
3. **Wait for valves = 0.** Even with the wait set to 0, the boiler fires only once a calling room's valve *reports* on.
4. **Room off.** Turning a room off cancels its running boost.
5. **Shadow and Paused modes.** Valves are simulated as following their commands, so wait-for-valves and the boiler logic behave realistically. Both room and boiler statuses are prefixed `Shadow: ` or `Paused: `.
6. **Button releases.** A Shelly `btn_up` event (the release of a press already counted) is never a press.
7. **Restart while the boiler was on.** The boiler is treated as switched off at restart. Minimum rest counts from the restart, and the rooms that were open stay held open for the keep-open time.
8. **Status suffix.** The `, heating` / `, at temperature` / `, valve held open for boiler` suffix is applied to Event, Pre-heat and Boost statuses. "Frost protection" and "Off (frost protection only)" get a suffix only when heating or held.
9. **Dropdown options.** The boost dropdown also contains the default boost length if it is not a multiple of the step, so a physical-button boost always shows a length.
10. **Hub entity IDs.** These are prefixed by the hub device name, e.g. `sensor.makerspace_heating_boiler_status`. The spec's `sensor.boiler_status` was illustrative.
11. **Boost counting.** "Boosts today" counts boosts that actually started or extended a boost. A press that changes nothing because a longer boost is running is logged but not counted.

## Review Focus

These are failure modes the spec implies but most tasks' tests would not exercise. Each one has a pinned test in the task named.

1. **A Shelly button reconnecting or HA restarting must not start a boost.** The case is an event entity returning from `unavailable` with its old timestamp, or being restored at startup. A genuine first-ever press from `unknown` must still count. *Task 10, `test_is_press`, `test_reconnecting_button_does_not_boost`.*
2. **Unusable temperature states must make the room "no temperature reading", never crash a step.** This covers `unknown`, `unavailable`, `""`, `nan` and non-numbers. °F sensors must be converted to °C. *Task 10, `test_read_temperature`.*
3. **Calendar events must be handled without error.** This covers events already in progress at fetch time, naive datetimes, malformed or backwards items, and empty summaries. *Task 2, `TestParseEvents`; Task 10, `test_calendar_events_drive_event_demand`.*
4. **An admin lowering the maximum boost while a boost runs must not lose the boost.** The reload keeps it, the dropdown shows unknown, and nothing crashes. *Task 12, `test_lowering_max_boost_keeps_running_boost`.*
5. **A room deleted while its boost, stats or held valve are in storage must be dropped silently on restore.** *Task 8, `test_unknown_rooms_are_dropped`; Task 12, `test_removing_a_room_drops_its_entities_and_state`.*

## File map

```
.gitignore                                   Task 1
pyproject.toml                               Task 1  pytest config
requirements_test.txt                        Task 1  pinned test harness
hacs.json                                    Task 15
README.md                                    Task 15 install, setup, migration checklist
.github/workflows/ci.yml                     Task 15 tests + hassfest + HACS
dashboards/makerspace-heating.yaml           Task 15 example dashboard (built-in cards)
custom_components/makerspace_heating/
  __init__.py                                Task 1 (docstring) → Task 12 (setup) → Task 13 (services)
  manifest.json                              Task 1
  const.py                                   Task 9  keys, defaults, timings
  options.py                                 Task 9  options/sub-entry data → engine settings
  controller.py                              Task 10 (core) → Task 11 (alerts)
  entity.py                                  Task 12 base entity classes
  sensor.py, binary_sensor.py, button.py,
  select.py, switch.py                       Task 12 platforms
  translations/en.json                       Task 12 all UI strings
  services.py, services.yaml                 Task 13 makerspace_heating.boost
  config_flow.py                             Task 14 main flow, options, room sub-entries
  engine/
    __init__.py                              Task 1  docstring only
    timefmt.py                               Task 1  durations and clock times
    model.py                                 Task 2  shared data types
    schedule.py                              Task 2  tags, event parsing, pre-heat, upcoming
    room.py                                  Task 3  demand sources, thermostat, status text
    boiler.py                                Task 4  boiler state machine
    faults.py                                Task 5  fault debounce, not-warming watch
    stats.py                                 Task 6  daily counters
    engine.py                                Task 7  Engine: boosts, step(), decisions
    persist.py                               Task 8  dump/restore
tests/
  __init__.py
  unit/__init__.py, unit/helpers.py          Task 1
  unit/test_*.py                             Tasks 1–9 (no Home Assistant instance)
  ha/__init__.py, ha/conftest.py             Task 10 (+ Task 12 fixture)
  ha/test_controller.py                      Task 10
  ha/test_alerts.py                          Task 11
  ha/test_entities.py                        Task 12
  ha/test_services.py                        Task 13
  ha/test_config_flow.py                     Task 14
```

---

### Task 1: Project scaffold and time formatting

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `requirements_test.txt`
- Create: `custom_components/makerspace_heating/__init__.py`, `custom_components/makerspace_heating/manifest.json`
- Create: `custom_components/makerspace_heating/engine/__init__.py`, `custom_components/makerspace_heating/engine/timefmt.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/helpers.py`
- Test: `tests/unit/test_timefmt.py`

**Interfaces:**
- Produces:
  - `format_duration(delta: timedelta) -> str`
  - `format_clock(moment: datetime, zone: tzinfo | None) -> str`
  - Test helpers `tests.unit.helpers.LONDON: ZoneInfo` and `tests.unit.helpers.at(hour, minute=0, second=0) -> datetime` (13 Oct 2026, London time)

- [ ] **Step 1: Create the scaffold files**

`.gitignore`:
```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.coverage
htmlcov/
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`requirements_test.txt`:
```text
pytest-homeassistant-custom-component==0.13.372
```

`custom_components/makerspace_heating/__init__.py`:
```python
"""Makerspace Heating: one place for all the space's heating decisions."""
```

`custom_components/makerspace_heating/manifest.json`:
```json
{
  "domain": "makerspace_heating",
  "name": "Makerspace Heating",
  "codeowners": ["@CHANGE-ME"],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/CHANGE-ME/fizz-hass",
  "integration_type": "service",
  "iot_class": "calculated",
  "issue_tracker": "https://github.com/CHANGE-ME/fizz-hass/issues",
  "requirements": [],
  "single_config_entry": true,
  "version": "0.1.0"
}
```

`custom_components/makerspace_heating/engine/__init__.py`:
```python
"""Pure decision logic for Makerspace Heating.

Nothing in this package imports Home Assistant. Time is always passed in, so
every rule can be tested with plain timestamps.
"""
```

`tests/__init__.py`:
```python
"""Tests for Makerspace Heating."""
```

`tests/unit/__init__.py`:
```python
"""Tests that run without a Home Assistant instance."""
```

`tests/unit/helpers.py`:
```python
"""Shared helpers for engine tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Return a moment on 13 October 2026, London time."""
    return datetime(2026, 10, 13, hour, minute, second, tzinfo=LONDON)
```

- [ ] **Step 2: Create the virtualenv**

Run:
```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
```
Expected: installs about 150 packages, including `homeassistant==2026.10.1`.

- [ ] **Step 3: Write the failing test**

`tests/unit/test_timefmt.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_timefmt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.timefmt'`

- [ ] **Step 5: Write the implementation**

`custom_components/makerspace_heating/engine/timefmt.py`:
```python
"""Formatting of times and durations for status text."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo


def format_duration(delta: timedelta) -> str:
    """Format a duration as '40 s', '2 min', '1 min 40 s' or '1 h 30 min'.

    Seconds are dropped once the duration reaches an hour. Zero and negative
    durations format as '0 s'.
    """
    total = max(0, round(delta.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds and not hours:
        parts.append(f"{seconds} s")
    return " ".join(parts) or "0 s"


def format_clock(moment: datetime, zone: tzinfo | None) -> str:
    """Format a moment as HH:MM in the given time zone."""
    return moment.astimezone(zone).strftime("%H:%M")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_timefmt.py -v`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml requirements_test.txt custom_components tests
git commit -m "chore: scaffold integration and add time formatting"
```

---

### Task 2: Engine data model and calendar schedule

**Files:**
- Create: `custom_components/makerspace_heating/engine/model.py`
- Create: `custom_components/makerspace_heating/engine/schedule.py`
- Test: `tests/unit/test_schedule.py`

**Interfaces:**
- Consumes: nothing.
- Produces, in `engine/model.py`:
  - `HUB: str`
  - `Source(StrEnum)`: `EVENT`, `BOOST`, `FROST`
  - `FaultKind(StrEnum)`: `TEMP_UNAVAILABLE`, `VALVE_UNAVAILABLE`, `VALVE_MISMATCH`, `BUTTON_UNAVAILABLE`, `NOT_WARMING`, `BOILER_UNAVAILABLE`, `CALENDAR_FAILING`
  - `FaultKey(scope: str, kind: FaultKind)`
  - `GlobalSettings(default_boost, max_boost, boost_step, margin, valve_wait, min_run, min_rest, keep_open, fault_delay, not_warming_after, not_warming_rise, calendar_fail_after)`
  - `RoomSettings(room_id, name, tag, boost_target=19.0, event_target=20.0, frost_target=7.0, preheat=30 min, has_button=False)`
  - `Boost(ends: datetime, length: timedelta, origin: str)`
  - `CalendarEvent(summary, start, end, tags: frozenset[str])`
  - `EventDemand(summary, start, end, running: bool)`
  - `RoomInputs(temperature: float | None, valve_on: bool | None, button_available: bool = True)`
  - `Snapshot(now, rooms: dict[str, RoomInputs], boiler_available=True, events: tuple[CalendarEvent, ...] = (), calendar_failing_since: datetime | None = None, check_valves=True)`
  - `LogEntry(name: str, message: str, room_id: str | None = None)`
- Produces, in `engine/schedule.py`:
  - `TAG_FORMAT: re.Pattern`
  - `UpcomingEvent(summary, start, end, rooms: tuple[str, ...])`
  - `find_tags(text) -> frozenset[str]`
  - `parse_events(raw, default_zone) -> list[CalendarEvent]`
  - `event_demand(events, tag, preheat, now) -> EventDemand | None`
  - `upcoming(events, tag_names: Mapping[str, str], now) -> tuple[list[UpcomingEvent], frozenset[str]]`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_schedule.py`:
```python
"""Tests for calendar tag matching, parsing and event windows."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.model import (
    CalendarEvent,
    EventDemand,
)
from custom_components.makerspace_heating.engine.schedule import (
    TAG_FORMAT,
    event_demand,
    find_tags,
    parse_events,
    upcoming,
)
from tests.unit.helpers import LONDON, at

PREHEAT = timedelta(minutes=30)


def event(summary, start, end, *tags) -> CalendarEvent:
    return CalendarEvent(summary=summary, start=start, end=end, tags=frozenset(tags))


class TestFindTags:
    def test_finds_several_tags(self) -> None:
        assert find_tags("Pottery night #pottery #main") == {"pottery", "main"}

    def test_ignores_case(self) -> None:
        assert find_tags("#Workshop") == {"workshop"}

    def test_whole_words_only(self) -> None:
        assert find_tags("#workshop2") == {"workshop2"}
        assert find_tags("#workshop_x") == frozenset()
        assert find_tags("email#workshop") == frozenset()

    def test_hyphens_and_punctuation(self) -> None:
        assert find_tags("#wood-shop.") == {"wood-shop"}
        assert find_tags("(#workshop)") == {"workshop"}

    def test_tag_format(self) -> None:
        assert TAG_FORMAT.match("wood-shop2")
        assert not TAG_FORMAT.match("Workshop")
        assert not TAG_FORMAT.match("wood shop")


class TestParseEvents:
    def test_parses_timed_event_with_tags_from_description(self) -> None:
        raw = [
            {
                "start": "2026-10-13T19:00:00+01:00",
                "end": "2026-10-13T21:30:00+01:00",
                "summary": "Pottery night",
                "description": "Bring clay #pottery",
            }
        ]
        assert parse_events(raw, LONDON) == [
            event("Pottery night", at(19), at(21, 30), "pottery")
        ]

    def test_skips_all_day_events(self) -> None:
        raw = [{"start": "2026-10-13", "end": "2026-10-14", "summary": "Closed #workshop"}]
        assert parse_events(raw, LONDON) == []

    def test_naive_times_get_default_zone(self) -> None:
        raw = [{"start": "2026-10-13T19:00:00", "end": "2026-10-13T20:00:00", "summary": "A"}]
        [parsed] = parse_events(raw, LONDON)
        assert parsed.start == at(19)

    def test_skips_malformed_and_backwards_items(self) -> None:
        raw = [
            {"start": "not a time T", "end": "2026-10-13T20:00:00+01:00"},
            {"start": "2026-10-13T20:00:00+01:00", "end": "2026-10-13T19:00:00+01:00"},
            {"summary": "no times"},
        ]
        assert parse_events(raw, LONDON) == []

    def test_empty_summary_gets_a_name(self) -> None:
        raw = [
            {
                "start": "2026-10-13T19:00:00+01:00",
                "end": "2026-10-13T20:00:00+01:00",
                "description": "#main",
            }
        ]
        [parsed] = parse_events(raw, LONDON)
        assert parsed.summary == "Untitled event"
        assert parsed.tags == {"main"}


class TestEventDemand:
    def test_running_event(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "pottery", PREHEAT, at(20)) == EventDemand(
            "Pottery", at(19), at(21), running=True
        )

    def test_preheat_window(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        demand = event_demand(events, "pottery", PREHEAT, at(18, 30))
        assert demand is not None and demand.running is False
        assert event_demand(events, "pottery", PREHEAT, at(18, 29)) is None

    def test_other_rooms_events_ignored(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "main", PREHEAT, at(20)) is None

    def test_end_is_exclusive(self) -> None:
        events = [event("Pottery", at(19), at(21), "pottery")]
        assert event_demand(events, "pottery", PREHEAT, at(21)) is None

    def test_running_beats_preheat_and_latest_end_is_named(self) -> None:
        events = [
            event("Short", at(19), at(20), "w"),
            event("Long", at(19), at(22), "w"),
            event("Next", at(20), at(21), "w"),
        ]
        demand = event_demand(events, "w", PREHEAT, at(19, 45))
        assert demand is not None and demand.summary == "Long"

    def test_back_to_back_events_are_continuous(self) -> None:
        events = [event("A", at(19), at(20), "w"), event("B", at(20), at(21), "w")]
        assert event_demand(events, "w", timedelta(0), at(19, 59, 59)).summary == "A"
        assert event_demand(events, "w", timedelta(0), at(20)).summary == "B"

    def test_zero_preheat(self) -> None:
        events = [event("A", at(19), at(20), "w")]
        assert event_demand(events, "w", timedelta(0), at(18, 59)) is None


class TestUpcoming:
    def test_lists_tagged_future_events_in_start_order(self) -> None:
        events = [
            event("Later", at(20), at(21), "main"),
            event("Sooner", at(18), at(19), "workshop", "main"),
            event("Untagged", at(17), at(18)),
            event("Past", at(9), at(10), "main"),
        ]
        listed, unknown = upcoming(
            events, {"workshop": "Workshop", "main": "Main Space"}, at(12)
        )
        assert [(item.summary, item.rooms) for item in listed] == [
            ("Sooner", ("Main Space", "Workshop")),
            ("Later", ("Main Space",)),
        ]
        assert unknown == frozenset()

    def test_reports_unknown_tags(self) -> None:
        listed, unknown = upcoming(
            [event("Typo", at(18), at(19), "wrokshop")], {"workshop": "Workshop"}, at(12)
        )
        assert listed == []
        assert unknown == {"wrokshop"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_schedule.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.model'`

- [ ] **Step 3: Write the data model**

`custom_components/makerspace_heating/engine/model.py`:
```python
"""Plain data types used by the decision engine.

The controller converts Home Assistant state into these types and back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

HUB = "hub"
"""Fault scope for faults that belong to the hub rather than a room."""


class Source(StrEnum):
    """A source of heat demand for a room."""

    EVENT = "event"
    BOOST = "boost"
    FROST = "frost"


class FaultKind(StrEnum):
    """Kinds of fault the engine detects."""

    TEMP_UNAVAILABLE = "temp_unavailable"
    VALVE_UNAVAILABLE = "valve_unavailable"
    VALVE_MISMATCH = "valve_mismatch"
    BUTTON_UNAVAILABLE = "button_unavailable"
    NOT_WARMING = "not_warming"
    BOILER_UNAVAILABLE = "boiler_unavailable"
    CALENDAR_FAILING = "calendar_failing"


@dataclass(frozen=True)
class FaultKey:
    """Identifies one fault: a room id (or HUB) and the kind of fault."""

    scope: str
    kind: FaultKind


@dataclass(frozen=True)
class GlobalSettings:
    """Settings shared by all rooms (the admin options)."""

    default_boost: timedelta = timedelta(minutes=30)
    max_boost: timedelta = timedelta(hours=2)
    boost_step: timedelta = timedelta(minutes=30)
    margin: float = 0.3
    valve_wait: timedelta = timedelta(minutes=3)
    min_run: timedelta = timedelta(minutes=5)
    min_rest: timedelta = timedelta(minutes=5)
    keep_open: timedelta = timedelta(minutes=3)
    fault_delay: timedelta = timedelta(minutes=5)
    not_warming_after: timedelta = timedelta(minutes=60)
    not_warming_rise: float = 0.5
    calendar_fail_after: timedelta = timedelta(minutes=30)


@dataclass(frozen=True)
class RoomSettings:
    """Settings for one room (a room sub-entry)."""

    room_id: str
    name: str
    tag: str
    boost_target: float = 19.0
    event_target: float = 20.0
    frost_target: float = 7.0
    preheat: timedelta = timedelta(minutes=30)
    has_button: bool = False


@dataclass(frozen=True)
class Boost:
    """A running boost."""

    ends: datetime
    length: timedelta
    origin: str


@dataclass(frozen=True)
class CalendarEvent:
    """A timed calendar event and the room tags it carries."""

    summary: str
    start: datetime
    end: datetime
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EventDemand:
    """The calendar event currently driving a room's event source."""

    summary: str
    start: datetime
    end: datetime
    running: bool


@dataclass(frozen=True)
class RoomInputs:
    """What the controller observed for one room.

    temperature is in °C, or None when unusable. valve_on is None when the
    valve switch is unavailable.
    """

    temperature: float | None
    valve_on: bool | None
    button_available: bool = True


@dataclass(frozen=True)
class Snapshot:
    """Everything the engine needs for one step."""

    now: datetime
    rooms: dict[str, RoomInputs] = field(default_factory=dict)
    boiler_available: bool = True
    events: tuple[CalendarEvent, ...] = ()
    calendar_failing_since: datetime | None = None
    check_valves: bool = True


@dataclass(frozen=True)
class LogEntry:
    """A logbook line, shown by Home Assistant as '<name> <message>'."""

    name: str
    message: str
    room_id: str | None = None
```

- [ ] **Step 4: Write the schedule module**

`custom_components/makerspace_heating/engine/schedule.py`:
```python
"""Calendar tag matching, event parsing and pre-heat windows."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from .model import CalendarEvent, EventDemand

TAG_FORMAT = re.compile(r"^[a-z0-9-]+$")
"""A valid room tag: lowercase letters, digits and hyphens."""

_TAG_IN_TEXT = re.compile(r"(?<![a-z0-9_])#([a-z0-9-]+)(?![a-z0-9_-])", re.IGNORECASE)


@dataclass(frozen=True)
class UpcomingEvent:
    """A tagged event listed by the upcoming-heating sensor."""

    summary: str
    start: datetime
    end: datetime
    rooms: tuple[str, ...]


def find_tags(text: str) -> frozenset[str]:
    """Return the lowercase tags written as '#tag' in text, whole words only."""
    return frozenset(tag.lower() for tag in _TAG_IN_TEXT.findall(text))


def parse_events(
    raw: Iterable[Mapping[str, Any]], default_zone: tzinfo
) -> list[CalendarEvent]:
    """Convert calendar.get_events items into CalendarEvents.

    All-day events (date-only start or end), malformed items and events that do
    not end after they start are skipped. Naive times get default_zone.
    """
    events: list[CalendarEvent] = []
    for item in raw:
        start_text = str(item.get("start", ""))
        end_text = str(item.get("end", ""))
        if "T" not in start_text or "T" not in end_text:
            continue
        try:
            start = _aware(datetime.fromisoformat(start_text), default_zone)
            end = _aware(datetime.fromisoformat(end_text), default_zone)
        except ValueError:
            continue
        if end <= start:
            continue
        summary = str(item.get("summary") or "").strip()
        description = str(item.get("description") or "")
        events.append(
            CalendarEvent(
                summary=summary or "Untitled event",
                start=start,
                end=end,
                tags=find_tags(f"{summary}\n{description}"),
            )
        )
    return events


def _aware(moment: datetime, zone: tzinfo) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)


def event_demand(
    events: Iterable[CalendarEvent], tag: str, preheat: timedelta, now: datetime
) -> EventDemand | None:
    """Return the event driving this tag's room right now, if any.

    A running event wins (the one ending latest is named). Otherwise an event
    whose pre-heat window has started is returned (the one starting soonest).
    """
    tagged = [event for event in events if tag in event.tags]
    running = [event for event in tagged if event.start <= now < event.end]
    if running:
        chosen = max(running, key=lambda event: event.end)
        return EventDemand(chosen.summary, chosen.start, chosen.end, running=True)
    warming = [event for event in tagged if event.start - preheat <= now < event.start]
    if warming:
        chosen = min(warming, key=lambda event: event.start)
        return EventDemand(chosen.summary, chosen.start, chosen.end, running=False)
    return None


def upcoming(
    events: Iterable[CalendarEvent], tag_names: Mapping[str, str], now: datetime
) -> tuple[list[UpcomingEvent], frozenset[str]]:
    """List tagged events that have not ended, and tags that match no room.

    tag_names maps each room tag to its room name.
    """
    listed: list[UpcomingEvent] = []
    unknown: set[str] = set()
    for event in sorted(events, key=lambda event: event.start):
        unknown |= event.tags.difference(tag_names)
        if event.end <= now:
            continue
        rooms = tuple(sorted(tag_names[tag] for tag in event.tags if tag in tag_names))
        if rooms:
            listed.append(UpcomingEvent(event.summary, event.start, event.end, rooms))
    return listed, frozenset(unknown)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_schedule.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add custom_components/makerspace_heating/engine tests/unit/test_schedule.py
git commit -m "feat(engine): add data model and calendar tag scheduling"
```

---

### Task 3: Room decisions

**Files:**
- Create: `custom_components/makerspace_heating/engine/room.py`
- Test: `tests/unit/test_room.py`

**Interfaces:**
- Consumes (from Task 2): `Boost`, `EventDemand`, `RoomSettings`, `Source`; and `format_clock` from Task 1.
- Produces:
  - `Demand(source: Source, target: float, boost: Boost | None, event: EventDemand | None)`
  - `room_demand(settings: RoomSettings, enabled: bool, boost: Boost | None, event: EventDemand | None, now: datetime) -> Demand`
  - `wants_heat(temperature: float, target: float, margin: float, was_wanting: bool) -> bool`
  - `room_status(enabled: bool, demand: Demand, calling: bool, held: bool, fault: str | None, now: datetime) -> str`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_room.py`:
```python
"""Tests for room demand, thermostat and status text."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.model import (
    Boost,
    EventDemand,
    RoomSettings,
    Source,
)
from custom_components.makerspace_heating.engine.room import (
    Demand,
    room_demand,
    room_status,
    wants_heat,
)
from tests.unit.helpers import at

ROOM = RoomSettings(room_id="w", name="Workshop", tag="workshop")
BOOST = Boost(ends=at(14, 32), length=timedelta(minutes=30), origin="Button")
EVENT = EventDemand("Pottery night", at(19), at(21, 30), running=True)
PREHEAT = EventDemand("Pottery night", at(19), at(21, 30), running=False)


class TestRoomDemand:
    def test_frost_only(self) -> None:
        demand = room_demand(ROOM, True, None, None, at(14))
        assert (demand.source, demand.target) == (Source.FROST, 7.0)

    def test_boost(self) -> None:
        demand = room_demand(ROOM, True, BOOST, None, at(14))
        assert (demand.source, demand.target, demand.boost) == (Source.BOOST, 19.0, BOOST)

    def test_expired_boost_is_ignored(self) -> None:
        demand = room_demand(ROOM, True, BOOST, None, at(14, 32))
        assert (demand.source, demand.boost) == (Source.FROST, None)

    def test_event_wins_when_higher_and_boost_still_reported(self) -> None:
        demand = room_demand(ROOM, True, BOOST, EVENT, at(14))
        assert (demand.source, demand.target) == (Source.EVENT, 20.0)
        assert demand.boost == BOOST

    def test_boost_wins_when_higher(self) -> None:
        hot = RoomSettings(room_id="w", name="Workshop", tag="workshop", boost_target=22.0)
        demand = room_demand(hot, True, BOOST, EVENT, at(14))
        assert (demand.source, demand.target) == (Source.BOOST, 22.0)

    def test_tie_prefers_event(self) -> None:
        tied = RoomSettings(room_id="w", name="Workshop", tag="workshop", boost_target=20.0)
        assert room_demand(tied, True, BOOST, EVENT, at(14)).source is Source.EVENT

    def test_off_room_gets_frost_only(self) -> None:
        demand = room_demand(ROOM, False, BOOST, EVENT, at(14))
        assert (demand.source, demand.boost, demand.event) == (Source.FROST, None, None)


class TestWantsHeat:
    def test_opens_below_margin(self) -> None:
        assert wants_heat(18.6, 19.0, 0.3, False)

    def test_opens_exactly_at_margin(self) -> None:
        assert wants_heat(18.7, 19.0, 0.3, False)

    def test_closes_at_target(self) -> None:
        assert not wants_heat(19.0, 19.0, 0.3, True)

    def test_keeps_state_inside_margin(self) -> None:
        assert wants_heat(18.8, 19.0, 0.3, True)
        assert not wants_heat(18.8, 19.0, 0.3, False)


class TestRoomStatus:
    def test_event_heating(self) -> None:
        demand = Demand(Source.EVENT, 20.0, None, EVENT)
        assert (
            room_status(True, demand, True, False, None, at(20))
            == "Event: Pottery night until 21:30, heating"
        )

    def test_preheat_at_temperature(self) -> None:
        demand = Demand(Source.EVENT, 20.0, None, PREHEAT)
        assert (
            room_status(True, demand, False, False, None, at(18, 40))
            == "Pre-heating for Pottery night (starts 19:00), at temperature"
        )

    def test_boost(self) -> None:
        demand = Demand(Source.BOOST, 19.0, BOOST, None)
        assert (
            room_status(True, demand, False, False, None, at(14))
            == "Boost until 14:32, at temperature"
        )

    def test_frost_idle_and_heating(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert room_status(True, demand, False, False, None, at(14)) == "Frost protection"
        assert (
            room_status(True, demand, True, False, None, at(14))
            == "Frost protection, heating"
        )

    def test_held_open_for_boiler(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert (
            room_status(True, demand, False, True, None, at(14))
            == "Frost protection, valve held open for boiler"
        )

    def test_off(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert (
            room_status(False, demand, False, False, None, at(14))
            == "Off (frost protection only)"
        )

    def test_fault_wins(self) -> None:
        demand = Demand(Source.BOOST, 19.0, BOOST, None)
        assert (
            room_status(True, demand, False, False, "no temperature reading", at(14))
            == "Fault: no temperature reading"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_room.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.room'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/room.py`:
```python
"""Per-room demand, thermostat and status decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import Boost, EventDemand, RoomSettings, Source
from .timefmt import format_clock

_TIE_ORDER = {Source.EVENT: 0, Source.BOOST: 1, Source.FROST: 2}


@dataclass(frozen=True)
class Demand:
    """The room's winning source and target, plus the active boost and event."""

    source: Source
    target: float
    boost: Boost | None
    event: EventDemand | None


def room_demand(
    settings: RoomSettings,
    enabled: bool,
    boost: Boost | None,
    event: EventDemand | None,
    now: datetime,
) -> Demand:
    """Pick the highest target among active sources (ties: event, boost, frost).

    Frost is always active. Events and boosts count only while the room is
    enabled, and a boost only until it ends.
    """
    active_boost = boost if enabled and boost is not None and boost.ends > now else None
    active_event = event if enabled else None
    candidates = [(Source.FROST, settings.frost_target)]
    if active_event is not None:
        candidates.append((Source.EVENT, settings.event_target))
    if active_boost is not None:
        candidates.append((Source.BOOST, settings.boost_target))
    source, target = max(candidates, key=lambda item: (item[1], -_TIE_ORDER[item[0]]))
    return Demand(source=source, target=target, boost=active_boost, event=active_event)


def wants_heat(temperature: float, target: float, margin: float, was_wanting: bool) -> bool:
    """Thermostat with a margin: open at target − margin, close at target."""
    if temperature <= round(target - margin, 3):
        return True
    if temperature >= target:
        return False
    return was_wanting


def room_status(
    enabled: bool,
    demand: Demand,
    calling: bool,
    held: bool,
    fault: str | None,
    now: datetime,
) -> str:
    """Plain-English status naming the winning source (spec §3.6)."""
    if fault is not None:
        return f"Fault: {fault}"
    if calling:
        suffix = ", heating"
    elif held:
        suffix = ", valve held open for boiler"
    else:
        suffix = ", at temperature"
    zone = now.tzinfo
    if not enabled:
        return "Off (frost protection only)" + (suffix if calling or held else "")
    if demand.source is Source.EVENT and demand.event is not None:
        event = demand.event
        if event.running:
            return f"Event: {event.summary} until {format_clock(event.end, zone)}{suffix}"
        return (
            f"Pre-heating for {event.summary} "
            f"(starts {format_clock(event.start, zone)}){suffix}"
        )
    if demand.source is Source.BOOST and demand.boost is not None:
        return f"Boost until {format_clock(demand.boost.ends, zone)}{suffix}"
    return "Frost protection" + (suffix if calling or held else "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_room.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/engine/room.py tests/unit/test_room.py
git commit -m "feat(engine): add room demand, thermostat and status rules"
```

---

### Task 4: Boiler decisions

**Files:**
- Create: `custom_components/makerspace_heating/engine/boiler.py`
- Test: `tests/unit/test_boiler.py`

**Interfaces:**
- Consumes: `GlobalSettings` (Task 2); `format_duration` (Task 1).
- Produces:
  - `BoilerState(on=False, last_on=None, last_off=None, last_open_calling=frozenset(), held_rooms=frozenset(), hold_until=None)`. This is a mutable dataclass.
  - `BoilerDecision(on: bool, status: str, held_rooms: frozenset[str], turned_on: bool = False, turned_off: bool = False)`
  - `step_boiler(state: BoilerState, settings: GlobalSettings, calling: Mapping[str, str], valve_on_since: Mapping[str, datetime], now: datetime) -> BoilerDecision`
    - `calling` maps room id → room name.
    - `valve_on_since` maps room id → when its valve was first seen reporting on.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_boiler.py`:
```python
"""Tests for boiler decisions and valve protections (spec §4)."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.boiler import BoilerState, step_boiler
from custom_components.makerspace_heating.engine.model import GlobalSettings
from tests.unit.helpers import at

SETTINGS = GlobalSettings()
WORKSHOP = {"w": "Workshop"}


def test_off_when_nothing_calls() -> None:
    decision = step_boiler(BoilerState(), SETTINGS, {}, {}, at(10))
    assert (decision.on, decision.status, decision.held_rooms) == (
        False,
        "Off: no rooms calling",
        frozenset(),
    )


def test_waits_for_valve_to_report_open() -> None:
    decision = step_boiler(BoilerState(), SETTINGS, WORKSHOP, {}, at(10))
    assert not decision.on
    assert decision.status == "Waiting for valves: Workshop (valve not open yet)"


def test_waits_for_valve_open_time_then_fires() -> None:
    state = BoilerState()
    since = {"w": at(10)}
    waiting = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 1, 20))
    assert not waiting.on
    assert waiting.status == "Waiting for valves: Workshop (1 min 40 s)"
    fired = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 3))
    assert fired.on and fired.turned_on
    assert fired.status == "On: Workshop calling"


def test_zero_wait_still_needs_valve_reported_on() -> None:
    settings = GlobalSettings(valve_wait=timedelta(0))
    state = BoilerState()
    assert not step_boiler(state, settings, WORKSHOP, {}, at(10)).on
    assert step_boiler(state, settings, WORKSHOP, {"w": at(10)}, at(10)).on


def test_minimum_run_holds_last_valve_open() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    decision = step_boiler(state, SETTINGS, {}, since, at(10, 2))
    assert decision.on
    assert decision.status == "On: holding for minimum run time"
    assert decision.held_rooms == {"w"}


def test_turns_off_after_minimum_run_and_keeps_valve_open() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    stopped = step_boiler(state, SETTINGS, {}, since, at(10, 5))
    assert not stopped.on and stopped.turned_off
    assert stopped.status == "Off: no rooms calling"
    assert stopped.held_rooms == {"w"}
    assert step_boiler(state, SETTINGS, {}, since, at(10, 7, 59)).held_rooms == {"w"}
    assert step_boiler(state, SETTINGS, {}, since, at(10, 8)).held_rooms == frozenset()


def test_no_keep_open_when_zero() -> None:
    settings = GlobalSettings(keep_open=timedelta(0))
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, settings, WORKSHOP, since, at(10))
    assert step_boiler(state, settings, {}, since, at(10, 5)).held_rooms == frozenset()


def test_minimum_rest_blocks_restart() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    step_boiler(state, SETTINGS, {}, since, at(10, 5))
    resting = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 7))
    assert not resting.on
    assert resting.status == "Off: resting (3 min left)"
    assert step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 10)).on


def test_new_room_waiting_while_on_holds_previous_valve() -> None:
    state = BoilerState()
    step_boiler(state, SETTINGS, WORKSHOP, {"w": at(9)}, at(10))
    main = {"m": "Main Space"}
    waiting = step_boiler(state, SETTINGS, main, {"w": at(9)}, at(10, 1))
    assert waiting.on
    assert waiting.status == "On: Main Space calling"
    assert waiting.held_rooms == {"w"}
    opened = step_boiler(state, SETTINGS, main, {"w": at(9), "m": at(10, 2)}, at(10, 2))
    assert opened.held_rooms == frozenset()


def test_names_all_calling_rooms_sorted() -> None:
    since = {"w": at(9), "m": at(9)}
    decision = step_boiler(
        BoilerState(), SETTINGS, {"w": "Workshop", "m": "Main Space"}, since, at(10)
    )
    assert decision.status == "On: Main Space, Workshop calling"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_boiler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.boiler'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/boiler.py`:
```python
"""Boiler on/off decisions with valve protections (spec §4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import GlobalSettings
from .timefmt import format_duration

_ZERO = timedelta(0)


@dataclass
class BoilerState:
    """What the boiler logic remembers between steps."""

    on: bool = False
    last_on: datetime | None = None
    last_off: datetime | None = None
    last_open_calling: frozenset[str] = frozenset()
    held_rooms: frozenset[str] = frozenset()
    hold_until: datetime | None = None


@dataclass(frozen=True)
class BoilerDecision:
    """The boiler decision for one step."""

    on: bool
    status: str
    held_rooms: frozenset[str]
    turned_on: bool = False
    turned_off: bool = False


def step_boiler(
    state: BoilerState,
    settings: GlobalSettings,
    calling: Mapping[str, str],
    valve_on_since: Mapping[str, datetime],
    now: datetime,
) -> BoilerDecision:
    """Decide the boiler for this step, updating state in place.

    calling maps room id -> room name for rooms calling for heat.
    valve_on_since maps room id -> when its valve was first seen reporting on.
    """
    open_calling = frozenset(room for room in calling if room in valve_on_since)
    turned_on = turned_off = False

    if state.on:
        if open_calling:
            state.last_open_calling = open_calling
        if calling:
            status = f"On: {_names(calling)} calling"
        elif state.last_on is not None and now - state.last_on < settings.min_run:
            status = "On: holding for minimum run time"
        else:
            state.on = False
            state.last_off = now
            turned_off = True
            if settings.keep_open > _ZERO:
                state.held_rooms = state.last_open_calling
                state.hold_until = now + settings.keep_open
            status = "Off: no rooms calling"
    else:
        rest_left = (
            settings.min_rest - (now - state.last_off)
            if state.last_off is not None
            else _ZERO
        )
        ready = [
            room for room in open_calling if now - valve_on_since[room] >= settings.valve_wait
        ]
        if not calling:
            status = "Off: no rooms calling"
        elif rest_left > _ZERO:
            status = f"Off: resting ({format_duration(rest_left)} left)"
        elif ready:
            state.on = True
            state.last_on = now
            state.last_open_calling = open_calling
            state.held_rooms = frozenset()
            state.hold_until = None
            turned_on = True
            status = f"On: {_names(calling)} calling"
        else:
            status = (
                "Waiting for valves: "
                f"{_waiting(calling, valve_on_since, settings.valve_wait, now)}"
            )

    if state.on:
        held = frozenset() if open_calling else state.last_open_calling
    elif state.hold_until is not None and now < state.hold_until:
        held = state.held_rooms
    else:
        state.held_rooms = frozenset()
        state.hold_until = None
        held = frozenset()
    return BoilerDecision(
        on=state.on,
        status=status,
        held_rooms=held,
        turned_on=turned_on,
        turned_off=turned_off,
    )


def _names(calling: Mapping[str, str]) -> str:
    return ", ".join(sorted(calling.values()))


def _waiting(
    calling: Mapping[str, str],
    valve_on_since: Mapping[str, datetime],
    wait: timedelta,
    now: datetime,
) -> str:
    best: tuple[timedelta, str] | None = None
    for room, name in calling.items():
        since = valve_on_since.get(room)
        if since is None:
            continue
        left = wait - (now - since)
        if best is None or left < best[0]:
            best = (left, name)
    if best is None:
        return f"{_names(calling)} (valve not open yet)"
    return f"{best[1]} ({format_duration(best[0])})"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_boiler.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/engine/boiler.py tests/unit/test_boiler.py
git commit -m "feat(engine): add boiler state machine with valve protections"
```

---

### Task 5: Fault tracking

**Files:**
- Create: `custom_components/makerspace_heating/engine/faults.py`
- Test: `tests/unit/test_faults.py`

**Interfaces:**
- Consumes: `FaultKey`, `FaultKind`, `GlobalSettings` (Task 2); `format_duration` (Task 1).
- Produces:
  - `STARTUP_GRACE: timedelta` (2 min)
  - `FaultTracker(started_at: datetime)`, with method `.update(conditions: Mapping[FaultKey, str], settings, now) -> dict[FaultKey, str]` that returns the raised faults
  - `WarmingWatch()`, with method `.update(room_id: str, calling: bool, temperature: float | None, settings, now) -> str | None`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_faults.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_faults.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.faults'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/faults.py`:
```python
"""Fault debounce and the heating-but-not-warming watch (spec §7.5)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from .model import FaultKey, FaultKind, GlobalSettings
from .timefmt import format_duration

STARTUP_GRACE = timedelta(minutes=2)
"""No faults are raised this soon after the engine starts."""

_SELF_TIMED = frozenset({FaultKind.NOT_WARMING, FaultKind.CALENDAR_FAILING})
"""Kinds whose condition already includes its own threshold."""


class FaultTracker:
    """Turns fault conditions into raised faults once they have lasted long enough."""

    def __init__(self, started_at: datetime) -> None:
        self._started_at = started_at
        self._since: dict[FaultKey, datetime] = {}

    def update(
        self, conditions: Mapping[FaultKey, str], settings: GlobalSettings, now: datetime
    ) -> dict[FaultKey, str]:
        """Record this step's conditions and return the raised ones (key -> message)."""
        for key in list(self._since):
            if key not in conditions:
                del self._since[key]
        for key in conditions:
            self._since.setdefault(key, now)
        if now - self._started_at < STARTUP_GRACE:
            return {}
        return {
            key: message
            for key, message in conditions.items()
            if now - self._since[key] >= _delay(key, settings)
        }


def _delay(key: FaultKey, settings: GlobalSettings) -> timedelta:
    return timedelta(0) if key.kind in _SELF_TIMED else settings.fault_delay


class WarmingWatch:
    """Spots rooms that call for heat for a long time without warming up."""

    def __init__(self) -> None:
        self._tracks: dict[str, tuple[datetime, float]] = {}

    def update(
        self,
        room_id: str,
        calling: bool,
        temperature: float | None,
        settings: GlobalSettings,
        now: datetime,
    ) -> str | None:
        """Return a fault message if the room has called too long without warming."""
        if not calling or temperature is None:
            self._tracks.pop(room_id, None)
            return None
        since, start_temperature = self._tracks.setdefault(room_id, (now, temperature))
        rise = temperature - start_temperature
        if rise >= settings.not_warming_rise:
            self._tracks[room_id] = (now, temperature)
            return None
        if now - since >= settings.not_warming_after:
            return (
                f"heating for over {format_duration(settings.not_warming_after)}, "
                f"only warmed {max(rise, 0.0):.1f} °C. "
                "Stuck valve, airlock or window open?"
            )
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_faults.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/engine/faults.py tests/unit/test_faults.py
git commit -m "feat(engine): add fault debounce and not-warming watch"
```

---

### Task 6: Daily statistics

**Files:**
- Create: `custom_components/makerspace_heating/engine/stats.py`
- Test: `tests/unit/test_stats.py`

**Interfaces:**
- Produces:
  - `MAX_STEP_GAP: timedelta` (60 s)
  - `RoomStats(heating_seconds: float = 0.0, boosts: int = 0)`
  - `Stats(day: date | None, rooms: dict[str, RoomStats], boiler_on_seconds: float, boiler_starts: int)`, with `.roll(today: date)` and `.room(room_id) -> RoomStats`
  - `accumulate(stats, elapsed: timedelta, calling_rooms: Iterable[str], boiler_on: bool) -> None`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_stats.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.stats'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/stats.py`:
```python
"""Daily runtime statistics (spec §7.1, §7.2)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

MAX_STEP_GAP = timedelta(seconds=60)
"""Longest interval credited by one step, so downtime is never counted."""


@dataclass
class RoomStats:
    """Today's counters for one room."""

    heating_seconds: float = 0.0
    boosts: int = 0


@dataclass
class Stats:
    """Today's counters for all rooms and the boiler."""

    day: date | None = None
    rooms: dict[str, RoomStats] = field(default_factory=dict)
    boiler_on_seconds: float = 0.0
    boiler_starts: int = 0

    def roll(self, today: date) -> None:
        """Reset every counter when the local date changes."""
        if self.day != today:
            self.day = today
            self.rooms = {}
            self.boiler_on_seconds = 0.0
            self.boiler_starts = 0

    def room(self, room_id: str) -> RoomStats:
        """Return (creating if needed) one room's counters."""
        return self.rooms.setdefault(room_id, RoomStats())


def accumulate(
    stats: Stats, elapsed: timedelta, calling_rooms: Iterable[str], boiler_on: bool
) -> None:
    """Credit the interval since the last step to calling rooms and the boiler."""
    seconds = min(elapsed, MAX_STEP_GAP).total_seconds()
    if seconds <= 0:
        return
    for room_id in calling_rooms:
        stats.room(room_id).heating_seconds += seconds
    if boiler_on:
        stats.boiler_on_seconds += seconds
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_stats.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/engine/stats.py tests/unit/test_stats.py
git commit -m "feat(engine): add daily runtime statistics"
```

---

### Task 7: Engine orchestration

**Files:**
- Create: `custom_components/makerspace_heating/engine/engine.py`
- Test: `tests/unit/test_engine.py`

**Interfaces:**
- Consumes everything from Tasks 1–6:
  - `event_demand` (schedule)
  - `room_demand`, `wants_heat`, `room_status` (room)
  - `BoilerState`, `step_boiler` (boiler)
  - `FaultTracker`, `WarmingWatch` (faults)
  - `Stats`, `accumulate` (stats)
  - the model types
- Produces:
  - `VALVE_MISMATCH_AFTER: timedelta` (90 s) and `BOOST_OFF = "Off"`
  - `BoostError(Exception)`
  - `RoomState(enabled=True, boost=None, wants=False)`. This is a mutable dataclass.
  - `RoomDecision(room_id, source, target, calling, valve_on, status, boost, event, problem)`
  - `Decision(rooms: dict[str, RoomDecision], boiler_on: bool, boiler_status: str, faults: dict[FaultKey, str], log: tuple[LogEntry, ...])`
  - `boost_choices(settings: GlobalSettings) -> dict[str, timedelta]`
  - `Engine(settings, rooms: Iterable[RoomSettings], now)`:
    - public attributes `settings`, `rooms`, `room_state`, `boiler`, `stats`, `last_step`
    - methods:
      - `commanded_valve(room_id) -> bool`
      - `press(room_id, now) -> bool`
      - `boost(room_id, length, origin, now) -> Boost`, which raises `BoostError`
      - `cancel_boost(room_id, origin, now) -> None`
      - `set_enabled(room_id, enabled) -> None`
      - `step(snapshot: Snapshot) -> Decision`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_engine.py`:
```python
"""Scenario tests for the whole engine (spec §3–§7)."""

from datetime import timedelta

import pytest

from custom_components.makerspace_heating.engine.engine import (
    BOOST_OFF,
    BoostError,
    Engine,
    boost_choices,
)
from custom_components.makerspace_heating.engine.model import (
    HUB,
    CalendarEvent,
    FaultKey,
    FaultKind,
    GlobalSettings,
    LogEntry,
    RoomInputs,
    RoomSettings,
    Snapshot,
    Source,
)
from tests.unit.helpers import at

WORKSHOP = RoomSettings(room_id="w", name="Workshop", tag="workshop", has_button=True)
MAIN = RoomSettings(room_id="m", name="Main Space", tag="main")


def make_engine(settings: GlobalSettings | None = None) -> Engine:
    return Engine(settings or GlobalSettings(), [WORKSHOP, MAIN], at(8))


def snap(now, w_temp=18.0, w_valve=False, m_temp=18.0, m_valve=False, **kwargs) -> Snapshot:
    return Snapshot(
        now=now,
        rooms={"w": RoomInputs(w_temp, w_valve), "m": RoomInputs(m_temp, m_valve)},
        **kwargs,
    )


class TestBoosts:
    def test_press_again_restarts_the_thirty_minutes(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        assert engine.room_state["w"].boost.ends == at(10, 30)
        engine.press("w", at(10, 20))
        assert engine.room_state["w"].boost.ends == at(10, 50)

    def test_button_never_shortens_a_longer_boost(self) -> None:
        engine = make_engine()
        engine.boost("w", timedelta(hours=2), "Dashboard (Sam)", at(10))
        assert engine.press("w", at(10, 10)) is False
        assert engine.room_state["w"].boost.ends == at(12)
        log = engine.step(snap(at(10, 10))).log
        assert LogEntry("Workshop", "button pressed; boost already runs until 12:00", "w") in log

    def test_dashboard_boost_replaces_even_if_shorter(self) -> None:
        engine = make_engine()
        engine.boost("w", timedelta(hours=2), "Dashboard (Sam)", at(10))
        engine.boost("w", timedelta(minutes=30), "Dashboard (Sam)", at(10, 10))
        assert engine.room_state["w"].boost.ends == at(10, 40)

    def test_boost_limits(self) -> None:
        engine = make_engine()
        with pytest.raises(BoostError, match="at most 2 h"):
            engine.boost("w", timedelta(hours=3), "x", at(10))
        with pytest.raises(BoostError):
            engine.boost("w", timedelta(0), "x", at(10))
        engine.set_enabled("w", False)
        with pytest.raises(BoostError, match="Room is off"):
            engine.boost("w", timedelta(minutes=30), "x", at(10))

    def test_boost_heats_the_room(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        room = engine.step(snap(at(10))).rooms["w"]
        assert (room.source, room.target, room.calling, room.valve_on) == (
            Source.BOOST,
            19.0,
            True,
            True,
        )
        assert room.status == "Boost until 10:30, heating"
        assert room.boost.origin == "Button"

    def test_boost_end_is_logged(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        decision = engine.step(snap(at(10, 30)))
        assert LogEntry("Workshop", "boost ended", "w") in decision.log
        assert decision.rooms["w"].boost is None

    def test_turning_room_off_cancels_boost_and_ignores_presses(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.set_enabled("w", False)
        assert engine.room_state["w"].boost is None
        assert engine.press("w", at(10, 1)) is False
        log = engine.step(snap(at(10, 1))).log
        assert LogEntry("Workshop", "button press ignored: room off", "w") in log

    def test_cancel_boost(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.cancel_boost("w", "Dashboard (Sam)", at(10, 5))
        assert engine.room_state["w"].boost is None

    def test_boost_choices(self) -> None:
        assert list(boost_choices(GlobalSettings())) == [
            BOOST_OFF,
            "30 min",
            "1 h",
            "1 h 30 min",
            "2 h",
        ]
        odd = GlobalSettings(boost_step=timedelta(minutes=45))
        assert list(boost_choices(odd)) == [BOOST_OFF, "30 min", "45 min", "1 h 30 min"]


class TestEvents:
    def test_preheat_before_event(self) -> None:
        engine = make_engine()
        events = (CalendarEvent("Class", at(10, 30), at(12), frozenset({"workshop"})),)
        room = engine.step(snap(at(10), events=events)).rooms["w"]
        assert room.source is Source.EVENT
        assert room.status == "Pre-heating for Class (starts 10:30), heating"

    def test_boost_outlasts_event(self) -> None:
        engine = make_engine()
        events = (CalendarEvent("Class", at(10), at(10, 30), frozenset({"workshop"})),)
        engine.press("w", at(10, 20))
        room = engine.step(snap(at(10, 40), events=events)).rooms["w"]
        assert room.source is Source.BOOST


class TestHeatingCycle:
    def test_valve_then_boiler_then_hold_then_close(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        first = engine.step(snap(at(10)))
        assert first.rooms["w"].valve_on
        assert first.boiler_status == "Waiting for valves: Workshop (valve not open yet)"

        opening = engine.step(snap(at(10, 0, 5), w_valve=True))
        assert opening.boiler_status == "Waiting for valves: Workshop (3 min)"

        on = engine.step(snap(at(10, 3, 5), w_valve=True))
        assert on.boiler_on
        assert LogEntry("Boiler", "turned on (Workshop calling)") in on.log
        assert engine.stats.boiler_starts == 1

        warm = engine.step(snap(at(10, 10), w_temp=19.0, w_valve=True))
        assert not warm.boiler_on
        assert LogEntry("Boiler", "turned off (no rooms calling)") in warm.log
        assert warm.rooms["w"].valve_on
        assert warm.rooms["w"].status == "Boost until 10:30, valve held open for boiler"

        closed = engine.step(snap(at(10, 13), w_temp=19.0, w_valve=True))
        assert not closed.rooms["w"].valve_on
        assert closed.rooms["w"].status == "Boost until 10:30, at temperature"

    def test_off_room_still_gets_frost_protection(self) -> None:
        engine = make_engine()
        engine.set_enabled("w", False)
        room = engine.step(snap(at(10), w_temp=5.0)).rooms["w"]
        assert (room.source, room.calling) == (Source.FROST, True)
        assert room.status == "Off (frost protection only), heating"

    def test_stats_count_heating_time_and_boosts(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        engine.step(snap(at(10, 0, 30)))
        engine.step(snap(at(10, 1)))
        assert engine.stats.room("w").heating_seconds == 60
        assert engine.stats.room("w").boosts == 1

    def test_commanded_valve(self) -> None:
        engine = make_engine()
        assert engine.commanded_valve("w") is False
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        assert engine.commanded_valve("w") is True


class TestFaults:
    def test_missing_temperature_closes_valve_then_raises(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        room = engine.step(snap(at(10), w_temp=None)).rooms["w"]
        assert (room.calling, room.valve_on) == (False, False)
        assert room.status == "Fault: no temperature reading"
        assert not room.problem
        later = engine.step(snap(at(10, 5), w_temp=None))
        assert later.faults[FaultKey("w", FaultKind.TEMP_UNAVAILABLE)] == (
            "Workshop: temperature sensor unavailable"
        )
        assert later.rooms["w"].problem

    def test_unavailable_valve_stops_calling_but_keeps_command(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        decision = engine.step(snap(at(10), w_valve=None))
        room = decision.rooms["w"]
        assert (room.calling, room.valve_on) == (False, True)
        assert room.status == "Fault: valve unavailable"
        assert decision.boiler_status == "Off: no rooms calling"

    def test_valve_not_following_commands(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        assert engine.step(snap(at(10, 0, 30))).rooms["w"].calling
        assert engine.step(snap(at(10, 1, 59))).rooms["w"].calling
        room = engine.step(snap(at(10, 2))).rooms["w"]
        assert room.status == "Fault: valve not responding"
        assert not room.calling
        assert room.valve_on

    def test_no_mismatch_faults_when_not_checking_valves(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10), check_valves=False))
        room = engine.step(snap(at(10, 5), check_valves=False)).rooms["w"]
        assert room.calling

    def test_hub_faults(self) -> None:
        engine = make_engine()
        engine.step(snap(at(10), boiler_available=False))
        decision = engine.step(
            snap(at(10, 5), boiler_available=False, calendar_failing_since=at(9, 30))
        )
        assert decision.faults[FaultKey(HUB, FaultKind.BOILER_UNAVAILABLE)] == (
            "Boiler switch unavailable"
        )
        assert decision.faults[FaultKey(HUB, FaultKind.CALENDAR_FAILING)] == (
            "Calendar could not be read for over 30 min"
        )

    def test_unavailable_button_is_alert_only(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        rooms = {"w": RoomInputs(18.0, False, button_available=False)}
        engine.step(Snapshot(now=at(10), rooms=rooms))
        decision = engine.step(Snapshot(now=at(10, 5), rooms=rooms))
        assert FaultKey("w", FaultKind.BUTTON_UNAVAILABLE) in decision.faults
        assert decision.rooms["w"].calling
        assert decision.rooms["w"].status == "Boost until 10:30, heating"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.engine'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/engine.py`:
```python
"""The decision engine: holds heating state and decides each step."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .boiler import BoilerState, step_boiler
from .faults import FaultTracker, WarmingWatch
from .model import (
    HUB,
    Boost,
    EventDemand,
    FaultKey,
    FaultKind,
    GlobalSettings,
    LogEntry,
    RoomInputs,
    RoomSettings,
    Snapshot,
    Source,
)
from .room import room_demand, room_status, wants_heat
from .schedule import event_demand
from .stats import Stats, accumulate
from .timefmt import format_clock, format_duration

VALVE_MISMATCH_AFTER = timedelta(seconds=90)
"""A valve not matching its command for this long (3 ticks) is a fault."""

BOOST_OFF = "Off"
"""The boost dropdown option that means 'no boost'."""


class BoostError(Exception):
    """A boost request was rejected. The message is shown to the user."""


@dataclass
class RoomState:
    """What the engine remembers about one room between steps."""

    enabled: bool = True
    boost: Boost | None = None
    wants: bool = False


@dataclass(frozen=True)
class RoomDecision:
    """One room's decision for one step."""

    room_id: str
    source: Source
    target: float
    calling: bool
    valve_on: bool
    status: str
    boost: Boost | None
    event: EventDemand | None
    problem: bool


@dataclass(frozen=True)
class Decision:
    """Everything decided in one step."""

    rooms: dict[str, RoomDecision]
    boiler_on: bool
    boiler_status: str
    faults: dict[FaultKey, str]
    log: tuple[LogEntry, ...]


def boost_choices(settings: GlobalSettings) -> dict[str, timedelta]:
    """Dropdown options: 'Off', step multiples up to the maximum, and the default."""
    lengths: set[timedelta] = set()
    length = settings.boost_step
    while settings.boost_step > timedelta(0) and length <= settings.max_boost:
        lengths.add(length)
        length += settings.boost_step
    if timedelta(0) < settings.default_boost <= settings.max_boost:
        lengths.add(settings.default_boost)
    return {BOOST_OFF: timedelta(0)} | {
        format_duration(item): item for item in sorted(lengths)
    }


class Engine:
    """Holds heating state and turns snapshots into decisions."""

    def __init__(
        self, settings: GlobalSettings, rooms: Iterable[RoomSettings], now: datetime
    ) -> None:
        self.settings = settings
        self.rooms = {room.room_id: room for room in rooms}
        self.room_state = {room_id: RoomState() for room_id in self.rooms}
        self.boiler = BoilerState()
        self.stats = Stats()
        self.last_step: datetime | None = None
        self._faults = FaultTracker(started_at=now)
        self._warming = WarmingWatch()
        self._valve_on_since: dict[str, datetime] = {}
        self._mismatch_since: dict[str, datetime] = {}
        self._commanded: dict[str, bool] = {}
        self._prev_calling: frozenset[str] = frozenset()
        self._prev_boiler_on = False
        self._pending_log: list[LogEntry] = []

    def commanded_valve(self, room_id: str) -> bool:
        """The valve state the last step asked for (False before the first step)."""
        return self._commanded.get(room_id, False)

    def press(self, room_id: str, now: datetime) -> bool:
        """Handle a physical button press. Returns True if the boost changed."""
        room = self.rooms[room_id]
        state = self.room_state[room_id]
        if not state.enabled:
            self._log(room, "button press ignored: room off")
            return False
        new_end = now + self.settings.default_boost
        current = state.boost
        if current is not None and current.ends >= new_end:
            self._log(
                room,
                f"button pressed; boost already runs until {_clock(current.ends, now)}",
            )
            return False
        state.boost = Boost(ends=new_end, length=self.settings.default_boost, origin="Button")
        self._started(room, state.boost, now)
        return True

    def boost(self, room_id: str, length: timedelta, origin: str, now: datetime) -> Boost:
        """Start or replace a boost of a chosen length (dashboard or action)."""
        room = self.rooms[room_id]
        state = self.room_state[room_id]
        if not state.enabled:
            raise BoostError("Room is off")
        if length <= timedelta(0) or length > self.settings.max_boost:
            raise BoostError(
                "Boost length must be more than 0 and at most "
                f"{format_duration(self.settings.max_boost)}"
            )
        state.boost = Boost(ends=now + length, length=length, origin=origin)
        self._started(room, state.boost, now)
        return state.boost

    def cancel_boost(self, room_id: str, origin: str, now: datetime) -> None:
        """End a running boost early."""
        state = self.room_state[room_id]
        if state.boost is not None and state.boost.ends > now:
            state.boost = None
            self._log(self.rooms[room_id], f"boost cancelled by {origin}")

    def set_enabled(self, room_id: str, enabled: bool) -> None:
        """Turn a room on or off. Off cancels its boost; frost protection stays."""
        state = self.room_state[room_id]
        if state.enabled == enabled:
            return
        state.enabled = enabled
        if not enabled:
            state.boost = None
        self._log(
            self.rooms[room_id],
            "turned on" if enabled else "turned off (frost protection only)",
        )

    def step(self, snapshot: Snapshot) -> Decision:
        """Decide valves, boiler, statuses and faults for this moment."""
        now = snapshot.now
        settings = self.settings
        self.stats.roll(now.date())
        if self.last_step is not None:
            accumulate(self.stats, now - self.last_step, self._prev_calling, self._prev_boiler_on)
        self.last_step = now
        log = self._pending_log
        self._pending_log = []
        conditions: dict[FaultKey, str] = {}
        calling: dict[str, str] = {}
        evaluated = {}

        for room_id, room in self.rooms.items():
            state = self.room_state[room_id]
            inputs = snapshot.rooms.get(room_id, RoomInputs(temperature=None, valve_on=None))
            if state.boost is not None and state.boost.ends <= now:
                state.boost = None
                log.append(LogEntry(room.name, "boost ended", room_id))
            self._track_valve(room_id, inputs.valve_on, now)
            event = event_demand(snapshot.events, room.tag, room.preheat, now)
            demand = room_demand(room, state.enabled, state.boost, event, now)
            fault = self._room_conditions(room, inputs, snapshot.check_valves, now, conditions)
            if inputs.temperature is None:
                state.wants = False
            else:
                state.wants = wants_heat(
                    inputs.temperature, demand.target, settings.margin, state.wants
                )
            room_calling = state.wants and fault is None
            if room_calling:
                calling[room_id] = room.name
            warming = self._warming.update(
                room_id, room_calling, inputs.temperature, settings, now
            )
            if warming is not None:
                conditions[FaultKey(room_id, FaultKind.NOT_WARMING)] = f"{room.name}: {warming}"
            evaluated[room_id] = (demand, fault)

        if not snapshot.boiler_available:
            conditions[FaultKey(HUB, FaultKind.BOILER_UNAVAILABLE)] = "Boiler switch unavailable"
        failing_since = snapshot.calendar_failing_since
        if failing_since is not None and now - failing_since >= settings.calendar_fail_after:
            conditions[FaultKey(HUB, FaultKind.CALENDAR_FAILING)] = (
                "Calendar could not be read for over "
                f"{format_duration(settings.calendar_fail_after)}"
            )

        boiler = step_boiler(self.boiler, settings, calling, self._valve_on_since, now)
        if boiler.turned_on:
            self.stats.boiler_starts += 1
            names = ", ".join(sorted(calling.values()))
            log.append(LogEntry("Boiler", f"turned on ({names} calling)"))
        if boiler.turned_off:
            log.append(LogEntry("Boiler", "turned off (no rooms calling)"))

        raised = self._faults.update(conditions, settings, now)
        rooms_out: dict[str, RoomDecision] = {}
        for room_id, (demand, fault) in evaluated.items():
            state = self.room_state[room_id]
            is_calling = room_id in calling
            held = room_id in boiler.held_rooms
            valve_on = state.wants or held
            if self._commanded.get(room_id) != valve_on:
                self._mismatch_since.pop(room_id, None)
            self._commanded[room_id] = valve_on
            rooms_out[room_id] = RoomDecision(
                room_id=room_id,
                source=demand.source,
                target=demand.target,
                calling=is_calling,
                valve_on=valve_on,
                status=room_status(
                    state.enabled, demand, is_calling, held and not is_calling, fault, now
                ),
                boost=state.boost,
                event=demand.event,
                problem=any(key.scope == room_id for key in raised),
            )
        self._prev_calling = frozenset(calling)
        self._prev_boiler_on = boiler.on
        return Decision(
            rooms=rooms_out,
            boiler_on=boiler.on,
            boiler_status=boiler.status,
            faults=raised,
            log=tuple(log),
        )

    def _room_conditions(
        self,
        room: RoomSettings,
        inputs: RoomInputs,
        check_valves: bool,
        now: datetime,
        conditions: dict[FaultKey, str],
    ) -> str | None:
        """Add the room's fault conditions; return its heating fault text, if any."""
        fault: str | None = None
        if inputs.temperature is None:
            conditions[FaultKey(room.room_id, FaultKind.TEMP_UNAVAILABLE)] = (
                f"{room.name}: temperature sensor unavailable"
            )
            fault = "no temperature reading"
        if inputs.valve_on is None:
            conditions[FaultKey(room.room_id, FaultKind.VALVE_UNAVAILABLE)] = (
                f"{room.name}: valve switch unavailable"
            )
            fault = fault or "valve unavailable"
        elif not check_valves:
            self._mismatch_since.pop(room.room_id, None)
        elif self._valve_mismatched(room.room_id, inputs.valve_on, now):
            conditions[FaultKey(room.room_id, FaultKind.VALVE_MISMATCH)] = (
                f"{room.name}: valve is not following commands"
            )
            fault = fault or "valve not responding"
        if room.has_button and not inputs.button_available:
            conditions[FaultKey(room.room_id, FaultKind.BUTTON_UNAVAILABLE)] = (
                f"{room.name}: button unavailable"
            )
        return fault

    def _valve_mismatched(self, room_id: str, reported: bool, now: datetime) -> bool:
        commanded = self._commanded.get(room_id)
        if commanded is None or commanded == reported:
            self._mismatch_since.pop(room_id, None)
            return False
        since = self._mismatch_since.setdefault(room_id, now)
        return now - since >= VALVE_MISMATCH_AFTER

    def _track_valve(self, room_id: str, reported: bool | None, now: datetime) -> None:
        if reported:
            self._valve_on_since.setdefault(room_id, now)
        else:
            self._valve_on_since.pop(room_id, None)

    def _started(self, room: RoomSettings, boost: Boost, now: datetime) -> None:
        self.stats.roll(now.date())
        self.stats.room(room.room_id).boosts += 1
        self._log(room, f"boost until {_clock(boost.ends, now)} (started by {boost.origin})")

    def _log(self, room: RoomSettings, message: str) -> None:
        self._pending_log.append(LogEntry(room.name, message, room.room_id))


def _clock(moment: datetime, now: datetime) -> str:
    return format_clock(moment, now.tzinfo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_engine.py -v`
Expected: all passed

- [ ] **Step 5: Run the whole unit suite**

Run: `.venv/bin/pytest tests/unit -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add custom_components/makerspace_heating/engine/engine.py tests/unit/test_engine.py
git commit -m "feat(engine): add engine orchestration with boosts, faults and stats"
```

---

### Task 8: Persistence

**Files:**
- Create: `custom_components/makerspace_heating/engine/persist.py`
- Test: `tests/unit/test_persist.py`

**Interfaces:**
- Consumes: `Engine` (Task 7), `BoilerState` (Task 4), `Boost` (Task 2), `Stats`/`RoomStats` (Task 6).
- Produces:
  - `STORE_VERSION = 1`
  - `dump(engine: Engine) -> dict[str, Any]`, which is JSON-serialisable
  - `restore(engine: Engine, data: Mapping[str, Any], now: datetime) -> None`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_persist.py`:
```python
"""Tests for saving and restoring engine state (spec §8.2)."""

import json
from datetime import timedelta

from custom_components.makerspace_heating.engine.engine import Engine
from custom_components.makerspace_heating.engine.model import (
    GlobalSettings,
    RoomInputs,
    RoomSettings,
    Snapshot,
)
from custom_components.makerspace_heating.engine.persist import dump, restore
from tests.unit.helpers import at

WORKSHOP = RoomSettings(room_id="w", name="Workshop", tag="workshop")
MAIN = RoomSettings(room_id="m", name="Main Space", tag="main")


def make_engine(now=None) -> Engine:
    return Engine(GlobalSettings(), [WORKSHOP, MAIN], now or at(8))


def snap(now, valve: bool) -> Snapshot:
    return Snapshot(
        now=now,
        rooms={"w": RoomInputs(18.0, valve), "m": RoomInputs(18.0, False)},
    )


def saved(engine: Engine) -> dict:
    return json.loads(json.dumps(dump(engine)))


def test_round_trip_while_boiler_was_on() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.set_enabled("m", False)
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    assert engine.boiler.on

    fresh = make_engine(at(10, 4))
    restore(fresh, saved(engine), at(10, 4))

    assert fresh.room_state["w"].boost == engine.room_state["w"].boost
    assert fresh.room_state["m"].enabled is False
    assert fresh.stats.room("w").boosts == 1
    assert fresh.boiler.on is False
    assert fresh.boiler.last_off == at(10, 4)
    assert fresh.boiler.held_rooms == {"w"}
    assert fresh.boiler.hold_until == at(10, 7)
    assert fresh.last_step == engine.last_step


def test_boiler_off_times_are_kept_when_it_was_off() -> None:
    engine = make_engine()
    engine.boiler.last_off = at(9, 58)
    fresh = make_engine(at(10))
    restore(fresh, saved(engine), at(10))
    assert fresh.boiler.last_off == at(9, 58)
    assert fresh.boiler.held_rooms == frozenset()


def test_expired_boost_is_dropped() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    fresh = make_engine(at(11))
    restore(fresh, saved(engine), at(11))
    assert fresh.room_state["w"].boost is None


def test_unknown_rooms_are_dropped() -> None:
    data = {
        "rooms": {"gone": {"enabled": False, "boost": None}},
        "boiler": {
            "on": False,
            "last_on": None,
            "last_off": None,
            "held_rooms": ["gone"],
            "last_open_calling": ["gone"],
            "hold_until": at(10, 2).isoformat(),
        },
        "stats": {
            "day": "2026-10-13",
            "rooms": {"gone": {"heating_seconds": 5.0, "boosts": 1}},
            "boiler_on_seconds": 0.0,
            "boiler_starts": 0,
        },
        "last_step": None,
    }
    fresh = make_engine(at(10))
    restore(fresh, data, at(10))
    assert set(fresh.room_state) == {"w", "m"}
    assert "gone" not in fresh.stats.rooms
    assert fresh.boiler.held_rooms == frozenset()


def test_bad_sections_are_skipped() -> None:
    fresh = make_engine(at(10))
    restore(
        fresh,
        {"rooms": "nonsense", "boiler": {"last_on": "garbage"}, "stats": {"day": 5}},
        at(10),
    )
    assert fresh.room_state["w"].enabled is True
    assert fresh.boiler.last_on is None
    assert fresh.stats.day is None


def test_boost_length_survives() -> None:
    engine = make_engine()
    engine.boost("w", timedelta(hours=1), "Dashboard (Sam)", at(10))
    fresh = make_engine(at(10, 1))
    restore(fresh, saved(engine), at(10, 1))
    assert fresh.room_state["w"].boost.length == timedelta(hours=1)
    assert fresh.room_state["w"].boost.origin == "Dashboard (Sam)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_persist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.engine.persist'`

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/engine/persist.py`:
```python
"""Save and restore engine state as JSON-compatible dicts (spec §8.2)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .boiler import BoilerState
from .engine import Engine
from .model import Boost
from .stats import RoomStats, Stats

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


def dump(engine: Engine) -> dict[str, Any]:
    """Return the engine state that should survive a restart."""
    boiler = engine.boiler
    stats = engine.stats
    return {
        "rooms": {
            room_id: {"enabled": state.enabled, "boost": _dump_boost(state.boost)}
            for room_id, state in engine.room_state.items()
        },
        "boiler": {
            "on": boiler.on,
            "last_on": _dump_time(boiler.last_on),
            "last_off": _dump_time(boiler.last_off),
            "held_rooms": sorted(boiler.held_rooms),
            "last_open_calling": sorted(boiler.last_open_calling),
            "hold_until": _dump_time(boiler.hold_until),
        },
        "stats": {
            "day": stats.day.isoformat() if stats.day else None,
            "rooms": {
                room_id: {"heating_seconds": item.heating_seconds, "boosts": item.boosts}
                for room_id, item in stats.rooms.items()
            },
            "boiler_on_seconds": stats.boiler_on_seconds,
            "boiler_starts": stats.boiler_starts,
        },
        "last_step": _dump_time(engine.last_step),
    }


def restore(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    """Load saved state into a fresh engine.

    Rooms that no longer exist are dropped. An unreadable section is skipped
    and the engine keeps its defaults for it.
    """
    sections: tuple[Callable[[Engine, Mapping[str, Any], datetime], None], ...] = (
        _restore_rooms,
        _restore_boiler,
        _restore_stats,
        _restore_last_step,
    )
    for section in sections:
        try:
            section(engine, data, now)
        except (AttributeError, KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring unreadable saved state in %s", section.__name__)


def _restore_rooms(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    for room_id, item in data.get("rooms", {}).items():
        state = engine.room_state.get(room_id)
        if state is None:
            continue
        state.enabled = bool(item.get("enabled", True))
        boost = _load_boost(item.get("boost"))
        state.boost = boost if boost is not None and boost.ends > now and state.enabled else None


def _restore_boiler(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    item = data.get("boiler", {})
    boiler = BoilerState(
        last_on=_load_time(item.get("last_on")),
        last_off=_load_time(item.get("last_off")),
    )
    known = engine.rooms
    if item.get("on"):
        # The boiler is switched off at startup: rest counts from now and the
        # valves that were open stay open for the keep-open time.
        boiler.last_off = now
        if engine.settings.keep_open > timedelta(0):
            boiler.held_rooms = frozenset(
                room for room in item.get("last_open_calling", []) if room in known
            )
            boiler.hold_until = now + engine.settings.keep_open
    else:
        hold_until = _load_time(item.get("hold_until"))
        if hold_until is not None and hold_until > now:
            boiler.held_rooms = frozenset(
                room for room in item.get("held_rooms", []) if room in known
            )
            boiler.hold_until = hold_until
    engine.boiler = boiler


def _restore_stats(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    item = data.get("stats", {})
    day = item.get("day")
    engine.stats = Stats(
        day=date.fromisoformat(day) if day is not None else None,
        rooms={
            room_id: RoomStats(
                heating_seconds=float(values.get("heating_seconds", 0.0)),
                boosts=int(values.get("boosts", 0)),
            )
            for room_id, values in item.get("rooms", {}).items()
            if room_id in engine.rooms
        },
        boiler_on_seconds=float(item.get("boiler_on_seconds", 0.0)),
        boiler_starts=int(item.get("boiler_starts", 0)),
    )


def _restore_last_step(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    engine.last_step = _load_time(data.get("last_step"))


def _dump_time(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _load_time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _dump_boost(boost: Boost | None) -> dict[str, Any] | None:
    if boost is None:
        return None
    return {
        "ends": boost.ends.isoformat(),
        "length_seconds": boost.length.total_seconds(),
        "origin": boost.origin,
    }


def _load_boost(item: Mapping[str, Any] | None) -> Boost | None:
    if not item:
        return None
    return Boost(
        ends=datetime.fromisoformat(item["ends"]),
        length=timedelta(seconds=float(item["length_seconds"])),
        origin=str(item.get("origin", "Unknown")),
    )
```

In `test_bad_sections_are_skipped`, the `stats` section has `day: 5`. `date.fromisoformat(5)` raises `TypeError`, which is caught, so `fresh.stats.day` stays `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_persist.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/engine/persist.py tests/unit/test_persist.py
git commit -m "feat(engine): persist boosts, room state, boiler timings and stats"
```

---

### Task 9: Constants and options mapping

**Files:**
- Create: `custom_components/makerspace_heating/const.py`
- Create: `custom_components/makerspace_heating/options.py`
- Test: `tests/unit/test_options.py`

**Interfaces:**
- Consumes: `GlobalSettings`, `RoomSettings` (Task 2).
- Produces, in `const.py`:
  - every `CONF_*` key named below
  - `OPTION_DEFAULTS`, `ROOM_DEFAULTS`
  - `MODE_ACTIVE`, `MODE_SHADOW`, `MODE_PAUSED`, `CONTROL_MODES`
  - `DOMAIN`, `NAME`, `SUBENTRY_ROOM`, `HUB_MODEL`, `ROOM_MODEL`
  - timings `TICK_INTERVAL`, `CALENDAR_REFRESH`, `CALENDAR_WINDOW`, `BUTTON_DEBOUNCE`, `RESEND_INTERVAL`, `SAVE_DELAY`
  - `EVENT_LOGBOOK_ENTRY`, `SERVICE_BOOST`, `ATTR_DURATION`
- Produces, in `options.py`:
  - `RoomConfig(settings: RoomSettings, valve: str, temperature: str, button: str | None)`
  - `global_settings(options) -> GlobalSettings`
  - `room_config(room_id, data) -> RoomConfig`
  - `control_mode(options) -> str`
  - `notify_targets(options) -> tuple[list[str], list[str]]`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_options.py`:
```python
"""Tests for turning config entry data into engine settings."""

from datetime import timedelta

from custom_components.makerspace_heating.const import (
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_EVENT_TARGET,
    CONF_FROST_TARGET,
    CONF_MARGIN,
    CONF_MAX_BOOST,
    CONF_NAME,
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    CONF_VALVE_WAIT,
    MODE_ACTIVE,
    MODE_SHADOW,
)
from custom_components.makerspace_heating.engine.model import GlobalSettings, RoomSettings
from custom_components.makerspace_heating.options import (
    RoomConfig,
    control_mode,
    global_settings,
    notify_targets,
    room_config,
)

ROOM = {
    CONF_NAME: "Workshop",
    CONF_TAG: "workshop",
    CONF_VALVE: "switch.workshop_valve",
    CONF_TEMPERATURE: "sensor.workshop_temperature",
    CONF_BUTTON: "event.workshop_button",
    CONF_BOOST_TARGET: 19.5,
    CONF_EVENT_TARGET: 21.0,
    CONF_FROST_TARGET: 8.0,
    CONF_PREHEAT: 45,
}


def test_defaults_when_options_are_empty() -> None:
    assert global_settings({}) == GlobalSettings()


def test_minutes_become_timedeltas() -> None:
    settings = global_settings({CONF_MAX_BOOST: 90, CONF_VALVE_WAIT: 0, CONF_MARGIN: 0.5})
    assert settings.max_boost == timedelta(minutes=90)
    assert settings.valve_wait == timedelta(0)
    assert settings.margin == 0.5


def test_room_config() -> None:
    assert room_config("abc", ROOM) == RoomConfig(
        settings=RoomSettings(
            room_id="abc",
            name="Workshop",
            tag="workshop",
            boost_target=19.5,
            event_target=21.0,
            frost_target=8.0,
            preheat=timedelta(minutes=45),
            has_button=True,
        ),
        valve="switch.workshop_valve",
        temperature="sensor.workshop_temperature",
        button="event.workshop_button",
    )


def test_room_without_button() -> None:
    data = {key: value for key, value in ROOM.items() if key != CONF_BUTTON}
    config = room_config("abc", data)
    assert config.button is None
    assert config.settings.has_button is False


def test_control_mode() -> None:
    assert control_mode({}) == MODE_SHADOW
    assert control_mode({"control_mode": MODE_ACTIVE}) == MODE_ACTIVE


def test_notify_targets() -> None:
    assert notify_targets({}) == ([], [])
    assert notify_targets(
        {CONF_NOTIFY_ENTITIES: ["notify.admin"], CONF_NOTIFY_ACTIONS: ["notify.mobile_app_x"]}
    ) == (["notify.admin"], ["notify.mobile_app_x"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_options.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.const'`

- [ ] **Step 3: Write `const.py`**

`custom_components/makerspace_heating/const.py`:
```python
"""Constants for Makerspace Heating."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

DOMAIN: Final = "makerspace_heating"
NAME: Final = "Makerspace Heating"
SUBENTRY_ROOM: Final = "room"
HUB_MODEL: Final = "Boiler and calendar"
ROOM_MODEL: Final = "Room"

# Main entry data
CONF_BOILER: Final = "boiler_switch"
CONF_CALENDAR: Final = "calendar"

# Options (durations are stored in minutes)
CONF_CONTROL_MODE: Final = "control_mode"
CONF_DEFAULT_BOOST: Final = "default_boost_minutes"
CONF_MAX_BOOST: Final = "max_boost_minutes"
CONF_BOOST_STEP: Final = "boost_step_minutes"
CONF_MARGIN: Final = "valve_margin"
CONF_VALVE_WAIT: Final = "valve_wait_minutes"
CONF_MIN_RUN: Final = "min_run_minutes"
CONF_MIN_REST: Final = "min_rest_minutes"
CONF_KEEP_OPEN: Final = "keep_open_minutes"
CONF_FAULT_DELAY: Final = "fault_delay_minutes"
CONF_NOT_WARMING_MINUTES: Final = "not_warming_minutes"
CONF_NOT_WARMING_RISE: Final = "not_warming_rise"
CONF_CALENDAR_FAIL: Final = "calendar_fail_minutes"
CONF_NOTIFY_ENTITIES: Final = "notify_entities"
CONF_NOTIFY_ACTIONS: Final = "notify_actions"

MODE_ACTIVE: Final = "active"
MODE_SHADOW: Final = "shadow"
MODE_PAUSED: Final = "paused"
CONTROL_MODES: Final = [MODE_ACTIVE, MODE_SHADOW, MODE_PAUSED]

OPTION_DEFAULTS: Final[dict[str, Any]] = {
    CONF_CONTROL_MODE: MODE_SHADOW,
    CONF_DEFAULT_BOOST: 30,
    CONF_MAX_BOOST: 120,
    CONF_BOOST_STEP: 30,
    CONF_MARGIN: 0.3,
    CONF_VALVE_WAIT: 3,
    CONF_MIN_RUN: 5,
    CONF_MIN_REST: 5,
    CONF_KEEP_OPEN: 3,
    CONF_FAULT_DELAY: 5,
    CONF_NOT_WARMING_MINUTES: 60,
    CONF_NOT_WARMING_RISE: 0.5,
    CONF_CALENDAR_FAIL: 30,
    CONF_NOTIFY_ENTITIES: [],
    CONF_NOTIFY_ACTIONS: [],
}

# Room sub-entry data
CONF_NAME: Final = "name"
CONF_TAG: Final = "tag"
CONF_VALVE: Final = "valve_switch"
CONF_TEMPERATURE: Final = "temperature_sensor"
CONF_BUTTON: Final = "button"
CONF_BOOST_TARGET: Final = "boost_target"
CONF_EVENT_TARGET: Final = "event_target"
CONF_FROST_TARGET: Final = "frost_target"
CONF_PREHEAT: Final = "preheat_minutes"

ROOM_DEFAULTS: Final[dict[str, Any]] = {
    CONF_BOOST_TARGET: 19.0,
    CONF_EVENT_TARGET: 20.0,
    CONF_FROST_TARGET: 7.0,
    CONF_PREHEAT: 30,
}

# Runtime timings (spec §5.3, §8.1)
TICK_INTERVAL: Final = timedelta(seconds=30)
CALENDAR_REFRESH: Final = timedelta(minutes=5)
CALENDAR_WINDOW: Final = timedelta(hours=24)
BUTTON_DEBOUNCE: Final = timedelta(seconds=2)
RESEND_INTERVAL: Final = timedelta(seconds=30)
SAVE_DELAY: Final = 10  # seconds

EVENT_LOGBOOK_ENTRY: Final = "logbook_entry"
SERVICE_BOOST: Final = "boost"
ATTR_DURATION: Final = "duration"
```

- [ ] **Step 4: Write `options.py`**

`custom_components/makerspace_heating/options.py`:
```python
"""Turn config entry options and room sub-entries into engine settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .const import (
    CONF_BOOST_STEP,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR_FAIL,
    CONF_CONTROL_MODE,
    CONF_DEFAULT_BOOST,
    CONF_EVENT_TARGET,
    CONF_FAULT_DELAY,
    CONF_FROST_TARGET,
    CONF_KEEP_OPEN,
    CONF_MARGIN,
    CONF_MAX_BOOST,
    CONF_MIN_REST,
    CONF_MIN_RUN,
    CONF_NAME,
    CONF_NOT_WARMING_MINUTES,
    CONF_NOT_WARMING_RISE,
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    CONF_VALVE_WAIT,
    OPTION_DEFAULTS,
    ROOM_DEFAULTS,
)
from .engine.model import GlobalSettings, RoomSettings


@dataclass(frozen=True)
class RoomConfig:
    """A room's engine settings plus the Home Assistant entities it uses."""

    settings: RoomSettings
    valve: str
    temperature: str
    button: str | None


def global_settings(options: Mapping[str, Any]) -> GlobalSettings:
    """Build shared settings from the entry options, filling in defaults."""

    def value(key: str) -> float:
        return float(options.get(key, OPTION_DEFAULTS[key]))

    def minutes(key: str) -> timedelta:
        return timedelta(minutes=value(key))

    return GlobalSettings(
        default_boost=minutes(CONF_DEFAULT_BOOST),
        max_boost=minutes(CONF_MAX_BOOST),
        boost_step=minutes(CONF_BOOST_STEP),
        margin=value(CONF_MARGIN),
        valve_wait=minutes(CONF_VALVE_WAIT),
        min_run=minutes(CONF_MIN_RUN),
        min_rest=minutes(CONF_MIN_REST),
        keep_open=minutes(CONF_KEEP_OPEN),
        fault_delay=minutes(CONF_FAULT_DELAY),
        not_warming_after=minutes(CONF_NOT_WARMING_MINUTES),
        not_warming_rise=value(CONF_NOT_WARMING_RISE),
        calendar_fail_after=minutes(CONF_CALENDAR_FAIL),
    )


def room_config(room_id: str, data: Mapping[str, Any]) -> RoomConfig:
    """Build a room's settings from its sub-entry data."""

    def value(key: str) -> float:
        return float(data.get(key, ROOM_DEFAULTS[key]))

    button = data.get(CONF_BUTTON) or None
    return RoomConfig(
        settings=RoomSettings(
            room_id=room_id,
            name=str(data[CONF_NAME]),
            tag=str(data[CONF_TAG]).lower(),
            boost_target=value(CONF_BOOST_TARGET),
            event_target=value(CONF_EVENT_TARGET),
            frost_target=value(CONF_FROST_TARGET),
            preheat=timedelta(minutes=value(CONF_PREHEAT)),
            has_button=button is not None,
        ),
        valve=str(data[CONF_VALVE]),
        temperature=str(data[CONF_TEMPERATURE]),
        button=button,
    )


def control_mode(options: Mapping[str, Any]) -> str:
    """Return the control mode (Shadow for a new install)."""
    return str(options.get(CONF_CONTROL_MODE, OPTION_DEFAULTS[CONF_CONTROL_MODE]))


def notify_targets(options: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (notify entities, legacy notify actions) for alerts."""
    return (
        list(options.get(CONF_NOTIFY_ENTITIES, [])),
        list(options.get(CONF_NOTIFY_ACTIONS, [])),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_options.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add custom_components/makerspace_heating/const.py custom_components/makerspace_heating/options.py tests/unit/test_options.py
git commit -m "feat: add constants and options-to-settings mapping"
```

---

### Task 10: Controller core

**Files:**
- Create: `custom_components/makerspace_heating/controller.py`
- Create: `tests/ha/__init__.py`, `tests/ha/conftest.py`
- Test: `tests/ha/test_controller.py`

**Interfaces:**
- Consumes:
  - `Engine`, `BoostError`, `Decision` (Task 7)
  - `RoomInputs`, `Snapshot`, `CalendarEvent` (Task 2)
  - `STORE_VERSION`, `dump`, `restore` (Task 8)
  - `parse_events`, `upcoming`, `UpcomingEvent` (Task 2)
  - `RoomConfig`, `global_settings`, `room_config`, `control_mode` (Task 9)
  - constants (Task 9)
- Produces (Tasks 11–13 rely on these exact names):
  - `read_temperature(state: State | None) -> float | None`
  - `read_switch(state: State | None) -> bool | None`
  - `is_press(old: State | None, new: State | None) -> bool`
  - `async describe_origin(hass, context: Context | None) -> str`
  - `HeatingController(hass, entry)`:
    - attributes: `hass`, `entry`, `settings`, `mode`, `boiler_entity`, `calendar_entity`, `rooms: dict[str, RoomConfig]`, `engine`, `decision: Decision | None`, `events`, `calendar_failing_since`, `upcoming: list[UpcomingEvent]`, `unknown_tags: frozenset[str]`
    - property `active`
    - methods:
      - `status_prefix() -> str`
      - `async async_setup()` and `async async_shutdown()`
      - `async_add_listener(cb) -> unsubscribe`
      - `async_step()`
      - `async async_refresh_calendar()`
      - `async_boost(room_id, length: timedelta | None, origin)`, which raises `ServiceValidationError`
      - `async_cancel_boost(room_id, origin)`
      - `async_set_enabled(room_id, enabled)`
      - `_log_entry(name, message)` (used by Task 11)
      - `_call_task(domain, service, data)` (used by Task 11)

The test devices use `input_boolean` for the valve and boiler. That gives real `turn_on`/`turn_off` services that change state, without clashing with the `switch` platform the integration loads in Task 12.

- [ ] **Step 1: Write the shared test fixtures**

`tests/ha/__init__.py`:
```python
"""Tests that run inside a Home Assistant test instance."""
```

`tests/ha/conftest.py`:
```python
"""Shared fixtures for tests that run inside Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import (
    CONF_BOILER,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR,
    CONF_EVENT_TARGET,
    CONF_FROST_TARGET,
    CONF_NAME,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    DOMAIN,
    NAME,
    SUBENTRY_ROOM,
)
from custom_components.makerspace_heating.controller import HeatingController

BOILER = "input_boolean.boiler"
CALENDAR = "calendar.events"
VALVE = "input_boolean.workshop_valve"
TEMPERATURE = "sensor.workshop_temperature"
BUTTON = "event.workshop_button"
ROOM_ID = "01WORKSHOPROOM"

WORKSHOP: dict[str, Any] = {
    CONF_NAME: "Workshop",
    CONF_TAG: "workshop",
    CONF_VALVE: VALVE,
    CONF_TEMPERATURE: TEMPERATURE,
    CONF_BUTTON: BUTTON,
    CONF_BOOST_TARGET: 19.0,
    CONF_EVENT_TARGET: 20.0,
    CONF_FROST_TARGET: 7.0,
    CONF_PREHEAT: 30,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/ in every test."""


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Options for the test entry. Override with pytest.mark.parametrize."""
    return {}


@pytest.fixture
def entry(hass: HomeAssistant, entry_options: dict[str, Any]) -> MockConfigEntry:
    """A config entry with one room (Workshop), added but not set up."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data={CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR},
        options=entry_options,
        subentries_data=[
            {
                "data": WORKSHOP,
                "subentry_id": ROOM_ID,
                "subentry_type": SUBENTRY_ROOM,
                "title": "Workshop",
                "unique_id": None,
            }
        ],
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
async def devices(hass: HomeAssistant) -> None:
    """Fake boiler, valve, temperature sensor, button and calendar."""
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"boiler": {"name": "Boiler"}, "workshop_valve": {"name": "Valve"}}},
    )
    hass.states.async_set(CALENDAR, "off")
    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(BUTTON, "unknown", {"event_types": ["single_push"]})
    await hass.async_block_till_done()


@pytest.fixture
def calendar_events() -> list[dict[str, Any]]:
    """Events the fake calendar returns. Tests append to this list."""
    return []


@pytest.fixture
def calendar_calls(
    hass: HomeAssistant, calendar_events: list[dict[str, Any]]
) -> list[ServiceCall]:
    """A fake calendar.get_events action."""
    calls: list[ServiceCall] = []

    async def handle(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        return {CALENDAR: {"events": calendar_events}}

    hass.services.async_register(
        "calendar", "get_events", handle, supports_response=SupportsResponse.ONLY
    )
    return calls


@pytest.fixture
async def controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list[ServiceCall],
) -> AsyncGenerator[HeatingController]:
    """A running controller for the test entry (no entity platforms)."""
    heating = HeatingController(hass, entry)
    await heating.async_setup()
    await hass.async_block_till_done()
    yield heating
    await heating.async_shutdown()
```

- [ ] **Step 2: Write the failing test**

`tests/ha/test_controller.py`:
```python
"""Tests for the controller: inputs, commands, calendar and persistence."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.makerspace_heating.const import (
    CONF_CONTROL_MODE,
    EVENT_LOGBOOK_ENTRY,
    MODE_ACTIVE,
)
from custom_components.makerspace_heating.controller import (
    HeatingController,
    is_press,
    read_temperature,
)
from custom_components.makerspace_heating.engine.model import Source

from .conftest import BOILER, BUTTON, CALENDAR, ROOM_ID, VALVE

ACTIVE = {CONF_CONTROL_MODE: MODE_ACTIVE}


def press(hass: HomeAssistant, second: int, event_type: str = "single_push") -> None:
    """Simulate a Shelly event entity firing (each press needs a new timestamp)."""
    hass.states.async_set(
        BUTTON,
        f"2026-10-13T10:00:{second:02d}.000+00:00",
        {"event_type": event_type, "event_types": ["single_push"]},
    )


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("state", "attributes", "expected"),
    [
        ("18.5", {"unit_of_measurement": "°C"}, 18.5),
        ("64.4", {"unit_of_measurement": "°F"}, 18.0),
        ("unknown", {}, None),
        ("unavailable", {}, None),
        ("", {}, None),
        ("nan", {}, None),
        ("warm", {}, None),
    ],
)
def test_read_temperature(state: str, attributes: dict, expected: float | None) -> None:
    result = read_temperature(State("sensor.t", state, attributes))
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_is_press() -> None:
    first, second = "2026-10-13T10:00:00+00:00", "2026-10-13T10:00:05+00:00"

    def event(state: str, event_type: str = "single_push") -> State:
        return State(BUTTON, state, {"event_type": event_type})

    def binary(state: str) -> State:
        return State("binary_sensor.button", state)

    assert is_press(event("unknown"), event(first))
    assert is_press(event(first), event(second))
    assert not is_press(event("unavailable"), event(first))
    assert not is_press(event(first), event("unavailable"))
    assert not is_press(None, event(first))
    assert not is_press(event(first), event(second, "btn_up"))
    assert not is_press(event(first), event(first))
    assert is_press(binary("off"), binary("on"))
    assert not is_press(binary("unavailable"), binary("on"))
    assert not is_press(binary("on"), binary("off"))


async def test_starts_in_frost_protection(controller: HeatingController) -> None:
    room = controller.decision.rooms[ROOM_ID]
    assert room.source is Source.FROST
    assert room.status == "Frost protection"
    assert controller.decision.boiler_status == "Off: no rooms calling"
    assert controller.status_prefix() == "Shadow: "


async def test_button_press_starts_one_boost(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    press(hass, 1)
    press(hass, 2)  # the same physical press reported twice
    await hass.async_block_till_done()
    room = controller.decision.rooms[ROOM_ID]
    assert room.boost is not None and room.boost.origin == "Button"
    assert controller.engine.stats.room(ROOM_ID).boosts == 1

    await advance(hass, freezer, timedelta(seconds=3))
    press(hass, 3)
    await hass.async_block_till_done()
    assert controller.engine.stats.room(ROOM_ID).boosts == 2


async def test_reconnecting_button_does_not_boost(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    press(hass, 1)
    await hass.async_block_till_done()
    controller.async_cancel_boost(ROOM_ID, "test")
    await advance(hass, freezer, timedelta(seconds=3))
    hass.states.async_set(BUTTON, "unavailable")
    await hass.async_block_till_done()
    press(hass, 1)  # back online, reporting the old timestamp
    await hass.async_block_till_done()
    assert controller.decision.rooms[ROOM_ID].boost is None


async def test_shadow_mode_sends_no_commands(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert controller.decision.rooms[ROOM_ID].valve_on is True
    assert hass.states.get(VALVE).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_active_mode_opens_valve_then_fires_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    assert hass.states.get(BOILER).state == "off"

    await advance(hass, freezer, timedelta(minutes=3))
    assert controller.decision.boiler_on
    assert hass.states.get(BOILER).state == "on"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_manual_change_is_switched_back(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    assert any(
        event.data["name"] == "Workshop valve"
        and event.data["message"] == "was switched off by hand; switching it back on"
        for event in logbook
    )


async def test_boost_writes_logbook_entry(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, timedelta(hours=1), "Dashboard (Sam)")
    await hass.async_block_till_done()
    assert any(
        event.data["name"] == "Workshop"
        and event.data["message"].startswith("boost until ")
        and event.data["message"].endswith("(started by Dashboard (Sam))")
        for event in logbook
    )


async def test_calendar_events_drive_event_demand(
    hass: HomeAssistant,
    controller: HeatingController,
    calendar_events: list[dict],
    calendar_calls: list,
) -> None:
    now = dt_util.now()
    calendar_events.append(
        {
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "summary": "Pottery night",
            "description": "#workshop #wrokshop",
        }
    )
    await controller.async_refresh_calendar()
    room = controller.decision.rooms[ROOM_ID]
    assert room.source is Source.EVENT
    assert room.status.startswith("Event: Pottery night until ")
    assert controller.unknown_tags == {"wrokshop"}
    assert [event.summary for event in controller.upcoming] == ["Pottery night"]
    call = calendar_calls[-1]
    assert call.data["entity_id"] == CALENDAR
    assert call.data["end_date_time"] - call.data["start_date_time"] == timedelta(hours=24)


async def test_calendar_failure_is_recorded(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    hass.services.async_remove("calendar", "get_events")
    await controller.async_refresh_calendar()
    assert controller.calendar_failing_since is not None


async def test_boost_survives_restart(
    hass: HomeAssistant, entry, devices, calendar_calls
) -> None:
    first = HeatingController(hass, entry)
    await first.async_setup()
    first.async_boost(ROOM_ID, timedelta(hours=1), "test")
    await first.async_shutdown()

    second = HeatingController(hass, entry)
    await second.async_setup()
    assert second.decision.rooms[ROOM_ID].boost.length == timedelta(hours=1)
    await second.async_shutdown()


async def test_off_room_rejects_boost(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    controller.async_set_enabled(ROOM_ID, False)
    with pytest.raises(ServiceValidationError, match="Room is off"):
        controller.async_boost(ROOM_ID, None, "test")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_controller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'custom_components.makerspace_heating.controller'`

- [ ] **Step 4: Write the implementation**

`custom_components/makerspace_heating/controller.py`:
```python
"""Connects the decision engine to Home Assistant."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    BUTTON_DEBOUNCE,
    CALENDAR_REFRESH,
    CALENDAR_WINDOW,
    CONF_BOILER,
    CONF_CALENDAR,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
    MODE_ACTIVE,
    MODE_PAUSED,
    MODE_SHADOW,
    RESEND_INTERVAL,
    SAVE_DELAY,
    SUBENTRY_ROOM,
    TICK_INTERVAL,
)
from .engine.engine import BoostError, Decision, Engine
from .engine.model import CalendarEvent, RoomInputs, Snapshot
from .engine.persist import STORE_VERSION, dump, restore
from .engine.schedule import UpcomingEvent, parse_events
from .engine.schedule import upcoming as list_upcoming
from .options import RoomConfig, control_mode, global_settings, room_config

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

_PREFIXES = {MODE_SHADOW: "Shadow: ", MODE_PAUSED: "Paused: "}


def read_temperature(state: State | None) -> float | None:
    """Return a sensor state's temperature in °C, or None if unusable."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
        value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
    return value


def read_switch(state: State | None) -> bool | None:
    """Return True/False for on/off, or None when unavailable or unknown."""
    if state is None:
        return None
    if state.state == STATE_ON:
        return True
    if state.state == STATE_OFF:
        return False
    return None


def is_press(old: State | None, new: State | None) -> bool:
    """Whether a button entity's state change is a new physical press.

    Event entities count a new timestamp, except when coming back from
    unavailable (the old timestamp is restored) or for a release (btn_up).
    Binary sensors count off -> on.
    """
    if old is None or new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    if new.domain == "binary_sensor":
        return old.state == STATE_OFF and new.state == STATE_ON
    if old.state == STATE_UNAVAILABLE or old.state == new.state:
        return False
    return new.attributes.get("event_type") != "btn_up"


async def describe_origin(hass: HomeAssistant, context: Context | None) -> str:
    """Describe who started a boost from a service call's context (spec §7.4)."""
    if context is not None and context.user_id:
        user = await hass.auth.async_get_user(context.user_id)
        if user is not None and user.name:
            return f"Dashboard ({user.name})"
        return "Dashboard"
    return "Automation/script"


class HeatingController:
    """Runs the engine against live Home Assistant state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings = global_settings(entry.options)
        self.mode = control_mode(entry.options)
        self.boiler_entity: str = entry.data[CONF_BOILER]
        self.calendar_entity: str = entry.data[CONF_CALENDAR]
        self.rooms: dict[str, RoomConfig] = {
            subentry_id: room_config(subentry_id, subentry.data)
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_ROOM
        }
        self.engine = Engine(
            self.settings, [room.settings for room in self.rooms.values()], dt_util.now()
        )
        self.decision: Decision | None = None
        self.events: tuple[CalendarEvent, ...] = ()
        self.calendar_failing_since: datetime | None = None
        self.upcoming: list[UpcomingEvent] = []
        self.unknown_tags: frozenset[str] = frozenset()
        self._tag_names = {room.settings.tag: room.settings.name for room in self.rooms.values()}
        self._button_rooms = {
            room.button: room_id for room_id, room in self.rooms.items() if room.button
        }
        self._owned_names = {self.boiler_entity: "Boiler"} | {
            room.valve: f"{room.settings.name} valve" for room in self.rooms.values()
        }
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: list[Callable[[], None]] = []
        self._last_press: dict[str, datetime] = {}
        self._desired: dict[str, bool] = {}
        self._sent: dict[str, tuple[bool, datetime]] = {}
        self._fetching = False
        self._seen_unknown_tags: set[str] = set()

    @property
    def active(self) -> bool:
        """Whether the integration is switching devices."""
        return self.mode == MODE_ACTIVE

    def status_prefix(self) -> str:
        """'Shadow: ' or 'Paused: ' when not in control, otherwise ''."""
        return _PREFIXES.get(self.mode, "")

    async def async_setup(self) -> None:
        """Restore state, subscribe to inputs and make the first decision."""
        now = dt_util.now()
        saved = await self._store.async_load()
        if saved:
            restore(self.engine, saved, now)
        tracked = [self.boiler_entity, self.calendar_entity]
        for room in self.rooms.values():
            tracked += [room.valve, room.temperature]
            if room.button:
                tracked.append(room.button)
        self._unsubscribe += [
            async_track_state_change_event(self.hass, tracked, self._on_state_change),
            async_track_time_interval(self.hass, self._on_tick, TICK_INTERVAL),
            async_track_time_interval(self.hass, self._on_calendar_timer, CALENDAR_REFRESH),
            async_at_started(self.hass, self._on_started),
        ]
        self.async_step()

    async def async_shutdown(self) -> None:
        """Stop listening and save state. Safe to call twice."""
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        await self._store.async_save(dump(self.engine))

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener after every decision. Returns an unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_boost(self, room_id: str, length: timedelta | None, origin: str) -> None:
        """Start a boost (default length when length is None)."""
        try:
            self.engine.boost(
                room_id,
                length if length is not None else self.settings.default_boost,
                origin,
                dt_util.now(),
            )
        except BoostError as err:
            raise ServiceValidationError(str(err)) from err
        self.async_step()

    @callback
    def async_cancel_boost(self, room_id: str, origin: str) -> None:
        """End a room's boost early."""
        self.engine.cancel_boost(room_id, origin, dt_util.now())
        self.async_step()

    @callback
    def async_set_enabled(self, room_id: str, enabled: bool) -> None:
        """Turn a room on or off."""
        self.engine.set_enabled(room_id, enabled)
        self.async_step()

    @callback
    def async_step(self) -> None:
        """Read inputs, run the engine and act on the decision."""
        now = dt_util.now()
        states = self.hass.states
        rooms: dict[str, RoomInputs] = {}
        for room_id, room in self.rooms.items():
            valve = read_switch(states.get(room.valve))
            if not self.active and valve is not None:
                valve = self.engine.commanded_valve(room_id)
            button = states.get(room.button) if room.button else None
            rooms[room_id] = RoomInputs(
                temperature=read_temperature(states.get(room.temperature)),
                valve_on=valve,
                button_available=room.button is None
                or (button is not None and button.state != STATE_UNAVAILABLE),
            )
        snapshot = Snapshot(
            now=now,
            rooms=rooms,
            boiler_available=read_switch(states.get(self.boiler_entity)) is not None,
            events=self.events,
            calendar_failing_since=self.calendar_failing_since,
            check_valves=self.active,
        )
        self.decision = self.engine.step(snapshot)
        self.upcoming, self.unknown_tags = list_upcoming(self.events, self._tag_names, now)
        if self.active:
            self._apply(self.decision, now)
        for line in self.decision.log:
            self._log_entry(line.name, line.message)
        self._store.async_delay_save(lambda: dump(self.engine), SAVE_DELAY)
        for listener in list(self._listeners):
            listener()

    async def async_refresh_calendar(self) -> None:
        """Fetch the next 24 hours of events from the calendar."""
        if self._fetching:
            return
        self._fetching = True
        now = dt_util.now()
        try:
            response = await self.hass.services.async_call(
                "calendar",
                "get_events",
                {
                    ATTR_ENTITY_ID: self.calendar_entity,
                    "start_date_time": now,
                    "end_date_time": now + CALENDAR_WINDOW,
                },
                blocking=True,
                return_response=True,
            )
            raw = response[self.calendar_entity]["events"]  # type: ignore[index]
            events = parse_events(raw, dt_util.get_default_time_zone())
        except (HomeAssistantError, probatio.Invalid, KeyError, TypeError) as err:
            if self.calendar_failing_since is None:
                self.calendar_failing_since = now
            _LOGGER.warning("Could not read calendar %s: %s", self.calendar_entity, err)
        else:
            self.calendar_failing_since = None
            self.events = tuple(events)
            self._warn_unknown_tags(now)
        finally:
            self._fetching = False
        self.async_step()

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old_state, new_state = event.data["old_state"], event.data["new_state"]
        room_id = self._button_rooms.get(entity_id)
        if room_id is not None and is_press(old_state, new_state):
            self._handle_press(room_id)
        if entity_id == self.calendar_entity:
            self.entry.async_create_task(
                self.hass, self.async_refresh_calendar(), "makerspace heating calendar"
            )
        if self.active and entity_id in self._owned_names:
            self._note_manual_change(entity_id, old_state, new_state)
        self.async_step()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self.async_step()

    async def _on_calendar_timer(self, _now: datetime) -> None:
        await self.async_refresh_calendar()

    async def _on_started(self, _hass: HomeAssistant) -> None:
        await self.async_refresh_calendar()

    @callback
    def _handle_press(self, room_id: str) -> None:
        now = dt_util.now()
        last = self._last_press.get(room_id)
        if last is not None and now - last < BUTTON_DEBOUNCE:
            return
        self._last_press[room_id] = now
        self.engine.press(room_id, now)

    @callback
    def _apply(self, decision: Decision, now: datetime) -> None:
        for room_id, room_decision in decision.rooms.items():
            self._ensure(self.rooms[room_id].valve, room_decision.valve_on, now)
        self._ensure(self.boiler_entity, decision.boiler_on, now)

    @callback
    def _ensure(self, entity_id: str, on: bool, now: datetime) -> None:
        """Send a command if the device is not already in the wanted state."""
        self._desired[entity_id] = on
        if read_switch(self.hass.states.get(entity_id)) in (None, on):
            return
        sent = self._sent.get(entity_id)
        if sent is not None and sent[0] == on and now - sent[1] < RESEND_INTERVAL:
            return
        self._sent[entity_id] = (on, now)
        domain = entity_id.split(".", 1)[0]
        self._call_task(domain, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id})

    @callback
    def _note_manual_change(
        self, entity_id: str, old_state: State | None, new_state: State | None
    ) -> None:
        """Log when someone changes an owned switch away from what we wanted."""
        desired = self._desired.get(entity_id)
        before, after = read_switch(old_state), read_switch(new_state)
        if desired is None or before != desired or after is None or after == desired:
            return
        self._sent.pop(entity_id, None)
        self._log_entry(
            self._owned_names[entity_id],
            f"was switched {'on' if after else 'off'} by hand; "
            f"switching it back {'on' if desired else 'off'}",
        )

    @callback
    def _warn_unknown_tags(self, now: datetime) -> None:
        _, unknown = list_upcoming(self.events, self._tag_names, now)
        for tag in sorted(unknown - self._seen_unknown_tags):
            _LOGGER.warning("A calendar event uses #%s, which matches no room", tag)
        self._seen_unknown_tags |= unknown

    @callback
    def _log_entry(self, name: str, message: str) -> None:
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY, {"name": name, "message": message, "domain": DOMAIN}
        )

    @callback
    def _call_task(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.entry.async_create_task(
            self.hass, self._call(domain, service, data), f"makerspace heating {domain}.{service}"
        )

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except (HomeAssistantError, probatio.Invalid) as err:
            _LOGGER.warning("Could not call %s.%s: %s", domain, service, err)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_controller.py -v`
Expected: all passed

If `test_active_mode_opens_valve_then_fires_boiler` fails because the boiler is still off, check that the valve's state change triggered a step that recorded `valve_on_since`. The step after `advance` must see the valve on for ≥ 3 min. Do not loosen the assertion.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add custom_components/makerspace_heating/controller.py tests/ha
git commit -m "feat: add controller connecting the engine to Home Assistant"
```

---

### Task 11: Alerts: Repairs issues, notifications and fault logbook entries

**Files:**
- Modify: `custom_components/makerspace_heating/controller.py`
- Test: `tests/ha/test_alerts.py`

**Interfaces:**
- Consumes:
  - `HeatingController._log_entry` and `_call_task` (Task 10)
  - `notify_targets` (Task 9)
  - `Decision.faults: dict[FaultKey, str]` (Task 7)
- Produces:
  - `HeatingController._update_alerts(raised: dict[FaultKey, str])`
  - Repairs issue id `f"{key.scope}_{key.kind}"` with translation key `fault` and placeholder `message`
  - On shutdown, the controller deletes its issues.

- [ ] **Step 1: Write the failing test**

`tests/ha/test_alerts.py`:
```python
"""Tests for fault alerts: Repairs, notifications and logbook (spec §7.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.makerspace_heating.const import (
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
)
from custom_components.makerspace_heating.controller import HeatingController

from .conftest import ROOM_ID, TEMPERATURE

NOTIFY = {
    CONF_NOTIFY_ENTITIES: ["notify.admin"],
    CONF_NOTIFY_ACTIONS: ["notify.mobile_app_phone"],
}
ISSUE_ID = f"{ROOM_ID}_temp_unavailable"
MESSAGE = "Workshop: temperature sensor unavailable"


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_fault_is_raised_after_delay_and_cleared(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    mobile = async_mock_service(hass, "notify", "mobile_app_phone")
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    issues = ir.async_get(hass)

    hass.states.async_set(TEMPERATURE, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=2))
    assert issues.async_get_issue(DOMAIN, ISSUE_ID) is None

    await advance(hass, freezer, timedelta(minutes=3))
    issue = issues.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "fault"
    assert issue.translation_placeholders == {"message": MESSAGE}
    assert send_message[-1].data == {
        "entity_id": "notify.admin",
        "message": f"Heating fault: {MESSAGE}",
        "title": "Makerspace Heating",
    }
    assert mobile[-1].data == {
        "message": f"Heating fault: {MESSAGE}",
        "title": "Makerspace Heating",
    }
    assert controller.decision.rooms[ROOM_ID].problem

    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert send_message[-1].data["message"] == f"Heating fault cleared: {MESSAGE}"
    assert any(event.data["message"] == f"fault: {MESSAGE}" for event in logbook)
    assert any(event.data["message"] == f"fault cleared: {MESSAGE}" for event in logbook)


async def test_shutdown_removes_issues(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    hass.states.async_set(TEMPERATURE, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=5))
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await controller.async_shutdown()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_alerts.py -v`
Expected: FAIL. `issues.async_get_issue(...)` returns `None` after 5 minutes (assertion error on `issue is not None`).

- [ ] **Step 3: Add alerts to the controller**

In `custom_components/makerspace_heating/controller.py`, make these changes.

1. Add the issue registry import below the existing `from homeassistant.exceptions ...` line:
```python
from homeassistant.helpers import issue_registry as ir
```

2. Add `NAME` to the `from .const import (...)` list, and add `FaultKey` to the model import, which becomes:
```python
from .engine.model import CalendarEvent, FaultKey, RoomInputs, Snapshot
```

3. Change the options import to include `notify_targets`:
```python
from .options import RoomConfig, control_mode, global_settings, notify_targets, room_config
```

4. In `HeatingController.__init__`, after `self._seen_unknown_tags: set[str] = set()`, add:
```python
        self.notify_entities, self.notify_actions = notify_targets(entry.options)
        self._raised: dict[FaultKey, str] = {}
```

5. In `async_step`, replace:
```python
        for line in self.decision.log:
            self._log_entry(line.name, line.message)
```
with:
```python
        for line in self.decision.log:
            self._log_entry(line.name, line.message)
        self._update_alerts(self.decision.faults)
```

6. In `async_shutdown`, after `self._unsubscribe.clear()`, add:
```python
        for key in self._raised:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
        self._raised.clear()
```

7. Add these methods to `HeatingController`, after `_note_manual_change`:
```python
    @callback
    def _update_alerts(self, raised: dict[FaultKey, str]) -> None:
        """Create and remove Repairs issues and send notifications on changes."""
        for key, message in raised.items():
            if key in self._raised:
                continue
            self._raised[key] = message
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(key),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="fault",
                translation_placeholders={"message": message},
            )
            self._log_entry(NAME, f"fault: {message}")
            self._notify(f"Heating fault: {message}")
        for key in [key for key in self._raised if key not in raised]:
            message = self._raised.pop(key)
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
            self._log_entry(NAME, f"fault cleared: {message}")
            self._notify(f"Heating fault cleared: {message}")

    @staticmethod
    def _issue_id(key: FaultKey) -> str:
        return f"{key.scope}_{key.kind}"

    @callback
    def _notify(self, message: str) -> None:
        for entity_id in self.notify_entities:
            self._call_task(
                "notify",
                "send_message",
                {ATTR_ENTITY_ID: entity_id, "message": message, "title": NAME},
            )
        for action in self.notify_actions:
            domain, _, service = action.partition(".")
            self._call_task(domain, service, {"message": message, "title": NAME})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_alerts.py tests/ha/test_controller.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add custom_components/makerspace_heating/controller.py tests/ha/test_alerts.py
git commit -m "feat: raise Repairs issues and notifications for heating faults"
```

---

### Task 12: Integration setup and entities

**Files:**
- Modify: `custom_components/makerspace_heating/__init__.py` (replace contents)
- Create: `custom_components/makerspace_heating/entity.py`
- Create: `custom_components/makerspace_heating/sensor.py`, `binary_sensor.py`, `button.py`, `select.py`, `switch.py`
- Create: `custom_components/makerspace_heating/translations/en.json`
- Modify: `tests/ha/conftest.py` (append the `loaded_entry` fixture)
- Test: `tests/ha/test_entities.py`

**Interfaces:**
- Consumes:
  - `HeatingController` and `describe_origin` (Tasks 10–11)
  - `boost_choices`, `BOOST_OFF`, `RoomDecision` (Task 7)
  - `format_clock` (Task 1)
  - constants (Task 9)
- Produces:
  - `MakerspaceHeatingConfigEntry = ConfigEntry[HeatingController]`
  - `PLATFORMS`
  - entity IDs for a room named Workshop:
    - `sensor.workshop_status`, `sensor.workshop_target_temperature`, `sensor.workshop_boost_ends`, `sensor.workshop_heating_time_today`, `sensor.workshop_boosts_today`
    - `binary_sensor.workshop_heating`, `binary_sensor.workshop_problem`
    - `button.workshop_boost`
    - `select.workshop_boost_for`
    - `switch.workshop_enabled`
  - hub entity IDs:
    - `sensor.makerspace_heating_boiler_status`, `sensor.makerspace_heating_boiler_on_time_today`, `sensor.makerspace_heating_boiler_starts_today`, `sensor.makerspace_heating_upcoming_heating`
    - `binary_sensor.makerspace_heating_boiler_demand`, `binary_sensor.makerspace_heating_heating_problem`
  - room device identifier `(DOMAIN, subentry_id)`; hub device identifier `(DOMAIN, entry_id)`

- [ ] **Step 1: Add the loaded-entry fixture**

Append to `tests/ha/conftest.py`:
```python
@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list[ServiceCall],
) -> MockConfigEntry:
    """The integration fully set up with its entity platforms."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
```

- [ ] **Step 2: Write the failing test**

`tests/ha/test_entities.py`:
```python
"""Tests for the entities and the dashboard-facing behaviour (spec §7.1, §7.2)."""

from __future__ import annotations

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.makerspace_heating.const import CONF_MAX_BOOST, DOMAIN

from .conftest import ROOM_ID


async def select(hass: HomeAssistant, option: str, context: Context | None = None) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.workshop_boost_for", "option": option},
        blocking=True,
        context=context,
    )


async def test_entities_are_created(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert hass.states.get("sensor.workshop_status").state == "Shadow: Frost protection"
    assert (
        hass.states.get("sensor.makerspace_heating_boiler_status").state
        == "Shadow: Off: no rooms calling"
    )
    assert hass.states.get("sensor.workshop_target_temperature").state == "7.0"
    assert hass.states.get("sensor.workshop_boost_ends").state == "unknown"
    assert hass.states.get("sensor.workshop_boosts_today").state == "0"
    assert hass.states.get("sensor.workshop_heating_time_today").state == "0.0"
    assert hass.states.get("binary_sensor.workshop_heating").state == "off"
    assert hass.states.get("binary_sensor.workshop_problem").state == "off"
    assert hass.states.get("binary_sensor.makerspace_heating_boiler_demand").state == "off"
    assert hass.states.get("binary_sensor.makerspace_heating_heating_problem").state == "off"
    assert hass.states.get("switch.workshop_enabled").state == "on"
    assert hass.states.get("button.workshop_boost") is not None
    boost_for = hass.states.get("select.workshop_boost_for")
    assert boost_for.state == "Off"
    assert boost_for.attributes["options"] == ["Off", "30 min", "1 h", "1 h 30 min", "2 h"]
    assert (
        hass.states.get("sensor.makerspace_heating_upcoming_heating").state
        == "None in the next 24 h"
    )
    room_device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ROOM_ID)})
    assert room_device is not None and room_device.name == "Workshop"


async def test_boost_button_records_dashboard_user(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_admin_user: MockUser
) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.workshop_boost"},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    status = hass.states.get("sensor.workshop_status")
    assert status.state.startswith("Shadow: Boost until ")
    assert status.attributes["boost_started_by"] == f"Dashboard ({hass_admin_user.name})"
    assert status.attributes["source"] == "boost"
    assert hass.states.get("select.workshop_boost_for").state == "30 min"
    assert hass.states.get("sensor.workshop_boost_ends").state != "unknown"
    assert hass.states.get("binary_sensor.workshop_heating").state == "on"
    assert hass.states.get("sensor.workshop_boosts_today").state == "1"


async def test_boost_dropdown_starts_and_cancels(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "1 h")
    assert hass.states.get("select.workshop_boost_for").state == "1 h"
    assert (
        hass.states.get("sensor.workshop_status").attributes["boost_started_by"]
        == "Automation/script"
    )
    await select(hass, "Off")
    assert hass.states.get("select.workshop_boost_for").state == "Off"
    assert hass.states.get("sensor.workshop_status").state == "Shadow: Frost protection"


async def test_enabled_switch(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.workshop_enabled"}, blocking=True
    )
    assert hass.states.get("switch.workshop_enabled").state == "off"
    assert (
        hass.states.get("sensor.workshop_status").state
        == "Shadow: Off (frost protection only)"
    )


async def test_lowering_max_boost_keeps_running_boost(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "2 h")
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_MAX_BOOST: 60})
    await hass.async_block_till_done()
    boost_for = hass.states.get("select.workshop_boost_for")
    assert boost_for.attributes["options"] == ["Off", "30 min", "1 h"]
    assert boost_for.state == "unknown"
    assert hass.states.get("sensor.workshop_status").state.startswith("Shadow: Boost until ")


async def test_removing_a_room_drops_its_entities_and_state(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "1 h")
    hass.config_entries.async_remove_subentry(loaded_entry, ROOM_ID)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.workshop_status") is None
    assert loaded_entry.runtime_data.rooms == {}
    assert hass.states.get("sensor.makerspace_heating_boiler_status") is not None


async def test_unload(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.workshop_status").state == "unavailable"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_entities.py -v`
Expected: FAIL. `assert await hass.config_entries.async_setup(...)` fails because the integration has no `async_setup_entry`.

- [ ] **Step 4: Write the integration setup**

Replace `custom_components/makerspace_heating/__init__.py` with:
```python
"""Makerspace Heating: one place for all the space's heating decisions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, HUB_MODEL, NAME
from .controller import HeatingController

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type MakerspaceHeatingConfigEntry = ConfigEntry[HeatingController]


async def async_setup_entry(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> bool:
    """Start the controller and the entity platforms."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer=NAME,
        model=HUB_MODEL,
    )
    controller = HeatingController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> None:
    """Reload after options or rooms change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> bool:
    """Stop the platforms, then the controller (which saves state)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
```

- [ ] **Step 5: Write the entity base classes**

`custom_components/makerspace_heating/entity.py`:
```python
"""Base classes for Makerspace Heating entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, ROOM_MODEL
from .controller import HeatingController
from .engine.engine import RoomDecision


class HeatingEntity(Entity):
    """An entity that refreshes after every controller decision."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: HeatingController, key: str) -> None:
        self.controller = controller
        self._attr_translation_key = key

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.async_add_listener(self.async_write_ha_state))


class HubEntity(HeatingEntity):
    """An entity on the hub device (boiler and calendar)."""

    def __init__(self, controller: HeatingController, key: str) -> None:
        super().__init__(controller, key)
        entry_id = controller.entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})


class RoomEntity(HeatingEntity):
    """An entity on a room's device."""

    def __init__(self, controller: HeatingController, room_id: str, key: str) -> None:
        super().__init__(controller, key)
        self.room_id = room_id
        self._attr_unique_id = f"{room_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room_id)},
            name=controller.rooms[room_id].settings.name,
            manufacturer=NAME,
            model=ROOM_MODEL,
            via_device=(DOMAIN, controller.entry.entry_id),
        )

    @property
    def room_decision(self) -> RoomDecision | None:
        """This room's latest decision, or None before the first step."""
        decision = self.controller.decision
        return None if decision is None else decision.rooms.get(self.room_id)
```

- [ ] **Step 6: Write the sensor platform**

`custom_components/makerspace_heating/sensor.py`:
```python
"""Sensors: room status, target, boost end and stats; hub status and stats."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import MakerspaceHeatingConfigEntry
from .controller import HeatingController
from .engine.timefmt import format_clock
from .entity import HubEntity, RoomEntity

MAX_STATE_LENGTH = 255


def _clip(text: str) -> str:
    return text if len(text) <= MAX_STATE_LENGTH else text[: MAX_STATE_LENGTH - 1] + "…"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakerspaceHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(
        [
            BoilerStatusSensor(controller),
            BoilerOnTimeSensor(controller),
            BoilerStartsSensor(controller),
            UpcomingHeatingSensor(controller),
        ]
    )
    for room_id in controller.rooms:
        async_add_entities(
            [
                RoomStatusSensor(controller, room_id),
                TargetTemperatureSensor(controller, room_id),
                BoostEndsSensor(controller, room_id),
                HeatingTimeSensor(controller, room_id),
                BoostsTodaySensor(controller, room_id),
            ],
            config_subentry_id=room_id,
        )


class RoomStatusSensor(RoomEntity, SensorEntity):
    """Plain-English room status (spec §3.6)."""

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "status")

    @property
    def native_value(self) -> str | None:
        decision = self.room_decision
        if decision is None:
            return None
        return _clip(self.controller.status_prefix() + decision.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        decision = self.room_decision
        all_decisions = self.controller.decision
        if decision is None or all_decisions is None:
            return {}
        boost, event = decision.boost, decision.event
        return {
            "source": decision.source.value,
            "target": decision.target,
            "calling": decision.calling,
            "valve_on": decision.valve_on,
            "boost_ends": boost.ends.isoformat() if boost else None,
            "boost_started_by": boost.origin if boost else None,
            "event": event.summary if event else None,
            "event_ends": event.end.isoformat() if event else None,
            "faults": [
                message
                for key, message in all_decisions.faults.items()
                if key.scope == self.room_id
            ],
        }


class TargetTemperatureSensor(RoomEntity, SensorEntity):
    """The room's current effective target."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "target_temperature")

    @property
    def native_value(self) -> float | None:
        decision = self.room_decision
        return None if decision is None else decision.target


class BoostEndsSensor(RoomEntity, SensorEntity):
    """When the running boost ends; unknown when there is none."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "boost_ends")

    @property
    def native_value(self) -> datetime | None:
        decision = self.room_decision
        if decision is None or decision.boost is None:
            return None
        return decision.boost.ends


class HeatingTimeSensor(RoomEntity, SensorEntity):
    """Hours the room has called for heat today."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "heating_time_today")

    @property
    def native_value(self) -> float:
        stats = self.controller.engine.stats.rooms.get(self.room_id)
        return round(stats.heating_seconds / 3600, 4) if stats else 0.0


class BoostsTodaySensor(RoomEntity, SensorEntity):
    """Boosts started today."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "boosts_today")

    @property
    def native_value(self) -> int:
        stats = self.controller.engine.stats.rooms.get(self.room_id)
        return stats.boosts if stats else 0


class BoilerStatusSensor(HubEntity, SensorEntity):
    """Plain-English boiler status (spec §4.4)."""

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "boiler_status")

    @property
    def native_value(self) -> str | None:
        decision = self.controller.decision
        if decision is None:
            return None
        return _clip(self.controller.status_prefix() + decision.boiler_status)


class BoilerOnTimeSensor(HubEntity, SensorEntity):
    """Hours the boiler has been on today."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "boiler_on_time_today")

    @property
    def native_value(self) -> float:
        return round(self.controller.engine.stats.boiler_on_seconds / 3600, 4)


class BoilerStartsSensor(HubEntity, SensorEntity):
    """Boiler starts today."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "boiler_starts_today")

    @property
    def native_value(self) -> int:
        return self.controller.engine.stats.boiler_starts


class UpcomingHeatingSensor(HubEntity, SensorEntity):
    """The next tagged event, with the 24-hour list and unknown tags (spec §5.5)."""

    _unrecorded_attributes = frozenset({"events"})

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "upcoming_heating")

    @property
    def native_value(self) -> str:
        now = dt_util.now()
        upcoming = next((item for item in self.controller.upcoming if item.start > now), None)
        if upcoming is None:
            return "None in the next 24 h"
        start = upcoming.start.astimezone(now.tzinfo)
        day = "" if start.date() == now.date() else "tomorrow "
        return _clip(f"{upcoming.summary} ({day}{format_clock(start, now.tzinfo)})")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "events": [
                {
                    "summary": item.summary,
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "rooms": list(item.rooms),
                }
                for item in self.controller.upcoming
            ],
            "unknown_tags": sorted(self.controller.unknown_tags),
        }
```

- [ ] **Step 7: Write the binary sensor, button, select and switch platforms**

`custom_components/makerspace_heating/binary_sensor.py`:
```python
"""Binary sensors: room heating/problem, boiler demand, hub problem."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MakerspaceHeatingConfigEntry
from .controller import HeatingController
from .engine.model import HUB
from .entity import HubEntity, RoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakerspaceHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities([BoilerDemandSensor(controller), HubProblemSensor(controller)])
    for room_id in controller.rooms:
        async_add_entities(
            [RoomHeatingSensor(controller, room_id), RoomProblemSensor(controller, room_id)],
            config_subentry_id=room_id,
        )


class RoomHeatingSensor(RoomEntity, BinarySensorEntity):
    """On while the room is calling for heat."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "heating")

    @property
    def is_on(self) -> bool | None:
        decision = self.room_decision
        return None if decision is None else decision.calling


class RoomProblemSensor(RoomEntity, BinarySensorEntity):
    """On while the room has a raised fault."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "problem")

    @property
    def is_on(self) -> bool | None:
        decision = self.room_decision
        return None if decision is None else decision.problem


class BoilerDemandSensor(HubEntity, BinarySensorEntity):
    """The boiler on/off decision."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "boiler_demand")

    @property
    def is_on(self) -> bool | None:
        decision = self.controller.decision
        return None if decision is None else decision.boiler_on


class HubProblemSensor(HubEntity, BinarySensorEntity):
    """On while the boiler or calendar has a raised fault."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "heating_problem")

    @property
    def is_on(self) -> bool | None:
        decision = self.controller.decision
        if decision is None:
            return None
        return any(key.scope == HUB for key in decision.faults)
```

`custom_components/makerspace_heating/button.py`:
```python
"""The per-room Boost button: same as the physical button, from a dashboard."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MakerspaceHeatingConfigEntry
from .controller import HeatingController, describe_origin
from .entity import RoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakerspaceHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    for room_id in controller.rooms:
        async_add_entities([BoostButton(controller, room_id)], config_subentry_id=room_id)


class BoostButton(RoomEntity, ButtonEntity):
    """Start a default-length boost."""

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "boost")

    async def async_press(self) -> None:
        origin = await describe_origin(self.hass, self._context)
        self.controller.async_boost(self.room_id, None, origin)
```

`custom_components/makerspace_heating/select.py`:
```python
"""The per-room 'Boost for…' dropdown."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MakerspaceHeatingConfigEntry
from .controller import HeatingController, describe_origin
from .engine.engine import BOOST_OFF, boost_choices
from .entity import RoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakerspaceHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    for room_id in controller.rooms:
        async_add_entities([BoostForSelect(controller, room_id)], config_subentry_id=room_id)


class BoostForSelect(RoomEntity, SelectEntity):
    """Choose a boost length; 'Off' ends the boost."""

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "boost_for")
        self._choices = boost_choices(controller.settings)
        self._attr_options = list(self._choices)

    @property
    def current_option(self) -> str | None:
        decision = self.room_decision
        if decision is None or decision.boost is None:
            return BOOST_OFF
        for label, length in self._choices.items():
            if length == decision.boost.length:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        origin = await describe_origin(self.hass, self._context)
        if option == BOOST_OFF:
            self.controller.async_cancel_boost(self.room_id, origin)
        else:
            self.controller.async_boost(self.room_id, self._choices[option], origin)
```

`custom_components/makerspace_heating/switch.py`:
```python
"""The per-room on/off switch (spec §3.3)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MakerspaceHeatingConfigEntry
from .controller import HeatingController
from .entity import RoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MakerspaceHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    for room_id in controller.rooms:
        async_add_entities([RoomEnabledSwitch(controller, room_id)], config_subentry_id=room_id)


class RoomEnabledSwitch(RoomEntity, SwitchEntity):
    """On: normal behaviour. Off: boosts and events ignored, frost protection kept."""

    def __init__(self, controller: HeatingController, room_id: str) -> None:
        super().__init__(controller, room_id, "enabled")

    @property
    def is_on(self) -> bool:
        return self.controller.engine.room_state[self.room_id].enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.controller.async_set_enabled(self.room_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.controller.async_set_enabled(self.room_id, False)
```

- [ ] **Step 8: Write the translations (all UI strings, including later tasks')**

`custom_components/makerspace_heating/translations/en.json`:
```json
{
  "config": {
    "step": {
      "user": {
        "title": "Makerspace Heating",
        "description": "Choose the boiler switch and the shared events calendar. Add rooms afterwards from the integration page.",
        "data": {
          "boiler_switch": "Boiler switch",
          "calendar": "Events calendar"
        },
        "data_description": {
          "boiler_switch": "The Shelly relay that calls the boiler for heat.",
          "calendar": "Events tagged with a room's tag (for example #workshop) heat that room."
        }
      },
      "reconfigure": {
        "title": "Makerspace Heating",
        "data": {
          "boiler_switch": "Boiler switch",
          "calendar": "Events calendar"
        }
      }
    },
    "error": {
      "boiler_is_valve": "This switch is already a room's valve."
    },
    "abort": {
      "single_instance_allowed": "Makerspace Heating is already set up.",
      "reconfigure_successful": "Settings saved."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Heating settings",
        "data": {
          "control_mode": "Control mode",
          "default_boost_minutes": "Default boost length",
          "max_boost_minutes": "Maximum boost length",
          "boost_step_minutes": "Boost dropdown step",
          "valve_margin": "Valve margin",
          "valve_wait_minutes": "Wait for valves",
          "min_run_minutes": "Boiler minimum run",
          "min_rest_minutes": "Boiler minimum rest",
          "keep_open_minutes": "Keep valves open after the boiler stops",
          "fault_delay_minutes": "Fault delay",
          "not_warming_minutes": "Heating-but-not-warming time",
          "not_warming_rise": "Heating-but-not-warming minimum rise",
          "calendar_fail_minutes": "Calendar failure threshold",
          "notify_entities": "Notify entities",
          "notify_actions": "Legacy notify actions"
        },
        "data_description": {
          "control_mode": "Shadow decides everything but switches nothing. Use it to compare with the old setup before switching to Active.",
          "default_boost_minutes": "Used by the physical buttons and the dashboard Boost button.",
          "max_boost_minutes": "The longest boost anyone can start from a dashboard or action.",
          "valve_wait_minutes": "How long a valve must report open before the boiler fires. 0 turns the wait off.",
          "notify_actions": "Actions such as notify.mobile_app_phone. Each fault is sent once when raised and once when cleared."
        }
      }
    },
    "error": {
      "default_above_max": "The default boost cannot be longer than the maximum boost.",
      "step_above_max": "The dropdown step cannot be longer than the maximum boost.",
      "invalid_notify_action": "Use the form notify.service_name, for example notify.mobile_app_phone."
    }
  },
  "config_subentries": {
    "room": {
      "entry_type": "Room",
      "initiate_flow": {
        "user": "Add room"
      },
      "step": {
        "user": {
          "title": "Add room",
          "data": {
            "name": "Room name",
            "tag": "Calendar tag",
            "valve_switch": "Valve switch",
            "temperature_sensor": "Temperature sensor",
            "button": "Demand-heat button",
            "boost_target": "Boost target",
            "event_target": "Event target",
            "frost_target": "Frost target",
            "preheat_minutes": "Pre-heat lead time"
          },
          "data_description": {
            "tag": "Lowercase letters, digits and hyphens. Calendar events containing #tag heat this room.",
            "button": "Optional. A Shelly input as an event entity (button mode) or a binary sensor (switch mode).",
            "preheat_minutes": "How long before an event starts to begin heating."
          }
        },
        "reconfigure": {
          "title": "Edit room",
          "data": {
            "name": "Room name",
            "tag": "Calendar tag",
            "valve_switch": "Valve switch",
            "temperature_sensor": "Temperature sensor",
            "button": "Demand-heat button",
            "boost_target": "Boost target",
            "event_target": "Event target",
            "frost_target": "Frost target",
            "preheat_minutes": "Pre-heat lead time"
          }
        }
      },
      "error": {
        "name_required": "Enter a room name.",
        "invalid_tag": "Use lowercase letters, digits and hyphens only.",
        "tag_in_use": "Another room already uses this tag.",
        "valve_is_boiler": "This switch is the boiler.",
        "valve_in_use": "Another room already uses this valve.",
        "frost_above_targets": "The frost target must not be above the boost and event targets."
      },
      "abort": {
        "reconfigure_successful": "Room saved."
      }
    }
  },
  "selector": {
    "control_mode": {
      "options": {
        "active": "Active: control the heating",
        "shadow": "Shadow: decide but do not switch anything",
        "paused": "Paused: stop switching for maintenance"
      }
    }
  },
  "entity": {
    "sensor": {
      "status": { "name": "Status" },
      "target_temperature": { "name": "Target temperature" },
      "boost_ends": { "name": "Boost ends" },
      "heating_time_today": { "name": "Heating time today" },
      "boosts_today": { "name": "Boosts today" },
      "boiler_status": { "name": "Boiler status" },
      "boiler_on_time_today": { "name": "Boiler on time today" },
      "boiler_starts_today": { "name": "Boiler starts today" },
      "upcoming_heating": { "name": "Upcoming heating" }
    },
    "binary_sensor": {
      "heating": { "name": "Heating" },
      "problem": { "name": "Problem" },
      "boiler_demand": { "name": "Boiler demand" },
      "heating_problem": { "name": "Heating problem" }
    },
    "button": {
      "boost": { "name": "Boost" }
    },
    "select": {
      "boost_for": { "name": "Boost for" }
    },
    "switch": {
      "enabled": { "name": "Enabled" }
    }
  },
  "issues": {
    "fault": {
      "title": "{message}",
      "description": "Makerspace Heating detected this fault. It clears automatically once fixed."
    }
  },
  "services": {
    "boost": {
      "name": "Boost",
      "description": "Start or restart a boost in one or more rooms.",
      "fields": {
        "duration": {
          "name": "Duration",
          "description": "How long to boost for. Defaults to the default boost length and must not exceed the maximum."
        }
      }
    }
  }
}
```

- [ ] **Step 9: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_entities.py -v`
Expected: all passed

If an entity ID assertion fails, print `hass.states.async_entity_ids()` and compare. The device name ("Workshop", "Makerspace Heating") plus the translated entity name sets the ID. Fix the name or translation key, not the expected ID, so the IDs stay as listed in **Interfaces**.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed

- [ ] **Step 11: Commit**

```bash
git add custom_components/makerspace_heating tests/ha
git commit -m "feat: add integration setup, entities and translations"
```

---

### Task 13: The `makerspace_heating.boost` action

**Files:**
- Create: `custom_components/makerspace_heating/services.py`, `custom_components/makerspace_heating/services.yaml`
- Modify: `custom_components/makerspace_heating/__init__.py`
- Test: `tests/ha/test_services.py`

**Interfaces:**
- Consumes:
  - `HeatingController.async_boost(room_id, length, origin)` and `describe_origin` (Task 10)
  - `SERVICE_BOOST`, `ATTR_DURATION` (Task 9)
- Produces:
  - `async_setup_services(hass) -> None`
  - action `makerspace_heating.boost` with fields `device_id` (one or more room devices) and optional `duration`

- [ ] **Step 1: Write the failing test**

`tests/ha/test_services.py`:
```python
"""Tests for the makerspace_heating.boost action (spec §7.3)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import DOMAIN

from .conftest import ROOM_ID


def device_id(hass: HomeAssistant, identifier: str) -> str:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, identifier)})
    assert device is not None
    return device.id


async def boost(hass: HomeAssistant, data: dict) -> None:
    await hass.services.async_call(DOMAIN, "boost", data, blocking=True)


async def test_boost_with_duration(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await boost(hass, {"device_id": device_id(hass, ROOM_ID), "duration": {"minutes": 90}})
    assert hass.states.get("select.workshop_boost_for").state == "1 h 30 min"
    assert (
        hass.states.get("sensor.workshop_status").attributes["boost_started_by"]
        == "Automation/script"
    )


async def test_boost_default_duration(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await boost(hass, {"device_id": [device_id(hass, ROOM_ID)]})
    assert hass.states.get("select.workshop_boost_for").state == "30 min"


async def test_boost_rejects_too_long(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError, match="at most 2 h"):
        await boost(hass, {"device_id": device_id(hass, ROOM_ID), "duration": {"hours": 3}})


async def test_boost_rejects_non_room_devices(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hub = device_id(hass, loaded_entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not a Makerspace Heating room"):
        await boost(hass, {"device_id": hub})


async def test_boost_rejects_off_room(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.workshop_enabled"}, blocking=True
    )
    with pytest.raises(ServiceValidationError, match="Room is off"):
        await boost(hass, {"device_id": device_id(hass, ROOM_ID)})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_services.py -v`
Expected: FAIL with `ServiceNotFound` for `makerspace_heating.boost`

- [ ] **Step 3: Write the action**

`custom_components/makerspace_heating/services.py`:
```python
"""The makerspace_heating.boost action (spec §7.3)."""

from __future__ import annotations

import probatio
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import ATTR_DURATION, DOMAIN, SERVICE_BOOST
from .controller import HeatingController, describe_origin

BOOST_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Optional(ATTR_DURATION): cv.time_period,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def handle_boost(call: ServiceCall) -> None:
        controller = _controller(hass)
        room_ids = _room_ids(hass, controller, call.data[ATTR_DEVICE_ID])
        origin = await describe_origin(hass, call.context)
        for room_id in room_ids:
            controller.async_boost(room_id, call.data.get(ATTR_DURATION), origin)

    hass.services.async_register(DOMAIN, SERVICE_BOOST, handle_boost, schema=BOOST_SCHEMA)


def _controller(hass: HomeAssistant) -> HeatingController:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Makerspace Heating is not loaded")
    return entries[0].runtime_data


def _room_ids(
    hass: HomeAssistant, controller: HeatingController, device_ids: list[str]
) -> list[str]:
    registry = dr.async_get(hass)
    room_ids: list[str] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        room_id = None
        if device is not None:
            room_id = next(
                (
                    identifier
                    for domain, identifier in device.identifiers
                    if domain == DOMAIN and identifier in controller.rooms
                ),
                None,
            )
        if room_id is None:
            raise ServiceValidationError(
                f"Device {device_id} is not a Makerspace Heating room"
            )
        room_ids.append(room_id)
    return room_ids
```

`custom_components/makerspace_heating/services.yaml`:
```yaml
boost:
  target:
    device:
      integration: makerspace_heating
  fields:
    duration:
      required: false
      example: "01:00:00"
      selector:
        duration:
```

- [ ] **Step 4: Register the action at integration setup**

In `custom_components/makerspace_heating/__init__.py`:

1. Add these imports after `from homeassistant.helpers import device_registry as dr`:
```python
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
```
2. Add after the existing `from .controller import HeatingController` line:
```python
from .services import async_setup_services
```
3. Add after the `PLATFORMS` list:
```python
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
```
4. Add above `async_setup_entry`:
```python
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions once, whether or not an entry is loaded."""
    async_setup_services(hass)
    return True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_services.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add custom_components/makerspace_heating tests/ha/test_services.py
git commit -m "feat: add makerspace_heating.boost action"
```

---

### Task 14: Config flow, options and room sub-entries

**Files:**
- Create: `custom_components/makerspace_heating/config_flow.py`
- Test: `tests/ha/test_config_flow.py`

**Interfaces:**
- Consumes:
  - constants (Task 9)
  - `TAG_FORMAT` (Task 2)
  - translations (Task 12): config/options/sub-entry strings and the error keys used below
- Produces:
  - `MakerspaceHeatingConfigFlow`, with steps `user` and `reconfigure`
  - `MakerspaceHeatingOptionsFlow`, with step `init`
  - `RoomSubentryFlow` (sub-entry type `room`), with steps `user` and `reconfigure`
  - error keys:
    - `boiler_is_valve`
    - `default_above_max`, `step_above_max`, `invalid_notify_action`
    - `name_required`, `invalid_tag`, `tag_in_use`, `valve_is_boiler`, `valve_in_use`, `frost_above_targets`

The integration's update listener (Task 12) reloads the entry after any options or sub-entry change. So the flows use `async_create_entry` and `async_update_and_abort`, never the `*_reload_and_abort` helpers, which refuse to run alongside an update listener.

- [ ] **Step 1: Write the failing test**

`tests/ha/test_config_flow.py`:
```python
"""Tests for the config flow, options flow and room sub-entries (spec §6)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import (
    CONF_BOILER,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR,
    CONF_CONTROL_MODE,
    CONF_DEFAULT_BOOST,
    CONF_EVENT_TARGET,
    CONF_FROST_TARGET,
    CONF_MAX_BOOST,
    CONF_NAME,
    CONF_NOTIFY_ACTIONS,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    DOMAIN,
    MODE_ACTIVE,
    OPTION_DEFAULTS,
    SUBENTRY_ROOM,
)

from .conftest import BOILER, CALENDAR, ROOM_ID, VALVE, WORKSHOP

NEW_ROOM = {
    CONF_NAME: "Main Space",
    CONF_TAG: "#Main",
    CONF_VALVE: "input_boolean.main_valve",
    CONF_TEMPERATURE: "sensor.main_temperature",
    CONF_BOOST_TARGET: 19.0,
    CONF_EVENT_TARGET: 20.0,
    CONF_FROST_TARGET: 7.0,
    CONF_PREHEAT: 45,
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.makerspace_heating.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Makerspace Heating"
    assert result["data"] == {CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR}


async def test_only_one_entry_allowed(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_main_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: VALVE, CONF_CALENDAR: CALENDAR}
    )
    assert result["errors"] == {CONF_BOILER: "boiler_is_valve"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: "input_boolean.new_boiler", CONF_CALENDAR: CALENDAR}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BOILER] == "input_boolean.new_boiler"


async def test_options_flow_saves(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**OPTION_DEFAULTS, CONF_CONTROL_MODE: MODE_ACTIVE, CONF_MAX_BOOST: 90},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CONTROL_MODE] == MODE_ACTIVE
    assert entry.options[CONF_MAX_BOOST] == 90


@pytest.mark.parametrize(
    ("change", "field", "error"),
    [
        ({CONF_DEFAULT_BOOST: 150}, CONF_DEFAULT_BOOST, "default_above_max"),
        ({CONF_NOTIFY_ACTIONS: ["mobile_app_phone"]}, CONF_NOTIFY_ACTIONS, "invalid_notify_action"),
    ],
)
async def test_options_flow_validates(
    hass: HomeAssistant, entry: MockConfigEntry, change: dict, field: str, error: str
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTION_DEFAULTS, **change}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_add_room(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_ROOM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    added = next(sub for sub in entry.subentries.values() if sub.title == "Main Space")
    assert added.data[CONF_TAG] == "main"
    assert CONF_BUTTON not in added.data


@pytest.mark.parametrize(
    ("change", "field", "error"),
    [
        ({CONF_TAG: "workshop"}, CONF_TAG, "tag_in_use"),
        ({CONF_TAG: "main space"}, CONF_TAG, "invalid_tag"),
        ({CONF_VALVE: VALVE}, CONF_VALVE, "valve_in_use"),
        ({CONF_VALVE: BOILER}, CONF_VALVE, "valve_is_boiler"),
        ({CONF_FROST_TARGET: 21.0}, CONF_FROST_TARGET, "frost_above_targets"),
        ({CONF_NAME: "  "}, CONF_NAME, "name_required"),
    ],
)
async def test_room_validation(
    hass: HomeAssistant, entry: MockConfigEntry, change: dict, field: str, error: str
) -> None:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**NEW_ROOM, **change}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_reconfigure_room(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await entry.start_subentry_reconfigure_flow(hass, ROOM_ID)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**WORKSHOP, CONF_EVENT_TARGET: 21.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[ROOM_ID].data[CONF_EVENT_TARGET] == 21.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/ha/test_config_flow.py -v`
Expected: FAIL. `async_init` raises `UnknownHandler`, or the flow aborts with reason `not_implemented`, because there is no config flow.

- [ ] **Step 3: Write the implementation**

`custom_components/makerspace_heating/config_flow.py`:
```python
"""Config flow, options flow and room sub-entries (spec §6)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_BOILER,
    CONF_BOOST_STEP,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR,
    CONF_CALENDAR_FAIL,
    CONF_CONTROL_MODE,
    CONF_DEFAULT_BOOST,
    CONF_EVENT_TARGET,
    CONF_FAULT_DELAY,
    CONF_FROST_TARGET,
    CONF_KEEP_OPEN,
    CONF_MARGIN,
    CONF_MAX_BOOST,
    CONF_MIN_REST,
    CONF_MIN_RUN,
    CONF_NAME,
    CONF_NOT_WARMING_MINUTES,
    CONF_NOT_WARMING_RISE,
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    CONF_VALVE_WAIT,
    CONTROL_MODES,
    DOMAIN,
    NAME,
    OPTION_DEFAULTS,
    ROOM_DEFAULTS,
    SUBENTRY_ROOM,
)
from .engine.schedule import TAG_FORMAT

_NOTIFY_ACTION = re.compile(r"^notify\.[a-z0-9_]+$")

_SWITCH = EntitySelector(EntitySelectorConfig(domain=["switch", "input_boolean"]))


def _minutes(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="min",
            mode=NumberSelectorMode.BOX,
        )
    )


def _degrees(minimum: float, maximum: float, step: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="°C",
            mode=NumberSelectorMode.BOX,
        )
    )


MAIN_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_BOILER): _SWITCH,
        probatio.Required(CONF_CALENDAR): EntitySelector(
            EntitySelectorConfig(domain="calendar")
        ),
    }
)

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CONTROL_MODE): SelectSelector(
            SelectSelectorConfig(
                options=CONTROL_MODES,
                translation_key=CONF_CONTROL_MODE,
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        probatio.Required(CONF_DEFAULT_BOOST): _minutes(5, 480),
        probatio.Required(CONF_MAX_BOOST): _minutes(5, 720),
        probatio.Required(CONF_BOOST_STEP): _minutes(5, 240),
        probatio.Required(CONF_MARGIN): _degrees(0.1, 2.0, 0.1),
        probatio.Required(CONF_VALVE_WAIT): _minutes(0, 30),
        probatio.Required(CONF_MIN_RUN): _minutes(0, 60),
        probatio.Required(CONF_MIN_REST): _minutes(0, 60),
        probatio.Required(CONF_KEEP_OPEN): _minutes(0, 30),
        probatio.Required(CONF_FAULT_DELAY): _minutes(0, 60),
        probatio.Required(CONF_NOT_WARMING_MINUTES): _minutes(10, 480),
        probatio.Required(CONF_NOT_WARMING_RISE): _degrees(0.1, 5.0, 0.1),
        probatio.Required(CONF_CALENDAR_FAIL): _minutes(5, 1440),
        probatio.Optional(CONF_NOTIFY_ENTITIES): EntitySelector(
            EntitySelectorConfig(domain="notify", multiple=True)
        ),
        probatio.Optional(CONF_NOTIFY_ACTIONS): TextSelector(TextSelectorConfig(multiple=True)),
    }
)

ROOM_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): TextSelector(),
        probatio.Required(CONF_TAG): TextSelector(),
        probatio.Required(CONF_VALVE): _SWITCH,
        probatio.Required(CONF_TEMPERATURE): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        probatio.Optional(CONF_BUTTON): EntitySelector(
            EntitySelectorConfig(domain=["event", "binary_sensor"])
        ),
        probatio.Required(CONF_BOOST_TARGET): _degrees(5, 30, 0.5),
        probatio.Required(CONF_EVENT_TARGET): _degrees(5, 30, 0.5),
        probatio.Required(CONF_FROST_TARGET): _degrees(5, 30, 0.5),
        probatio.Required(CONF_PREHEAT): _minutes(0, 240),
    }
)


class MakerspaceHeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the boiler switch and the events calendar."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MakerspaceHeatingOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=NAME, data=user_input)
        return self.async_show_form(step_id="user", data_schema=MAIN_SCHEMA)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            valves = {sub.data.get(CONF_VALVE) for sub in entry.subentries.values()}
            if user_input[CONF_BOILER] in valves:
                errors[CONF_BOILER] = "boiler_is_valve"
            else:
                return self.async_update_and_abort(entry, data=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(MAIN_SCHEMA, user_input or entry.data),
            errors=errors,
        )


class MakerspaceHeatingOptionsFlow(OptionsFlow):
    """Admin settings shared by all rooms (spec §6.1)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                CONF_NOTIFY_ENTITIES: [],
                CONF_NOTIFY_ACTIONS: [],
                **user_input,
            }
            errors = _validate_options(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        current = {**OPTION_DEFAULTS, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, current),
            errors=errors,
        )


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or edit a room (spec §6.2)."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean_room(user_input)
            errors = _validate_room(self._get_entry(), user_input, editing=None)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                ROOM_SCHEMA, user_input or ROOM_DEFAULTS
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean_room(user_input)
            errors = _validate_room(entry, user_input, editing=subentry.subentry_id)
            if not errors:
                return self.async_update_and_abort(
                    entry, subentry, title=user_input[CONF_NAME], data=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                ROOM_SCHEMA, user_input or subentry.data
            ),
            errors=errors,
        )


def _validate_options(options: Mapping[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if options[CONF_DEFAULT_BOOST] > options[CONF_MAX_BOOST]:
        errors[CONF_DEFAULT_BOOST] = "default_above_max"
    if options[CONF_BOOST_STEP] > options[CONF_MAX_BOOST]:
        errors[CONF_BOOST_STEP] = "step_above_max"
    if any(not _NOTIFY_ACTION.match(action) for action in options[CONF_NOTIFY_ACTIONS]):
        errors[CONF_NOTIFY_ACTIONS] = "invalid_notify_action"
    return errors


def _clean_room(user_input: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    data[CONF_NAME] = str(data[CONF_NAME]).strip()
    data[CONF_TAG] = str(data[CONF_TAG]).strip().lstrip("#").lower()
    if not data.get(CONF_BUTTON):
        data.pop(CONF_BUTTON, None)
    return data


def _validate_room(
    entry: ConfigEntry, data: Mapping[str, Any], editing: str | None
) -> dict[str, str]:
    errors: dict[str, str] = {}
    others = [
        sub.data
        for sub_id, sub in entry.subentries.items()
        if sub_id != editing and sub.subentry_type == SUBENTRY_ROOM
    ]
    if not data[CONF_NAME]:
        errors[CONF_NAME] = "name_required"
    if not TAG_FORMAT.match(data[CONF_TAG]):
        errors[CONF_TAG] = "invalid_tag"
    elif any(other.get(CONF_TAG) == data[CONF_TAG] for other in others):
        errors[CONF_TAG] = "tag_in_use"
    if data[CONF_VALVE] == entry.data[CONF_BOILER]:
        errors[CONF_VALVE] = "valve_is_boiler"
    elif any(other.get(CONF_VALVE) == data[CONF_VALVE] for other in others):
        errors[CONF_VALVE] = "valve_in_use"
    if data[CONF_FROST_TARGET] > min(data[CONF_BOOST_TARGET], data[CONF_EVENT_TARGET]):
        errors[CONF_FROST_TARGET] = "frost_above_targets"
    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/ha/test_config_flow.py -v`
Expected: all passed

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add custom_components/makerspace_heating/config_flow.py tests/ha/test_config_flow.py
git commit -m "feat: add config flow, options flow and room sub-entries"
```

---

### Task 15: Dashboard, README, HACS metadata and CI

**Files:**
- Create: `dashboards/makerspace-heating.yaml`, `README.md`, `hacs.json`, `.github/workflows/ci.yml`
- Modify: `custom_components/makerspace_heating/manifest.json` (real repository URL and code owner)

**Interfaces:**
- Consumes: entity IDs from Task 12, options and rooms from Task 14.
- Produces: an installable HACS repository.

- [ ] **Step 1: Get the repository details**

Ask the user for the GitHub repository URL and the code owner's GitHub handle. In `manifest.json`, replace:
- `https://github.com/CHANGE-ME/fizz-hass` in `documentation`;
- `https://github.com/CHANGE-ME/fizz-hass/issues` in `issue_tracker`;
- `@CHANGE-ME` in `codeowners`.

Use the same URL in `README.md` below. If the user does not have a repository yet, leave the `CHANGE-ME` values and say so in your report. hassfest and HACS validation will fail until they are replaced.

- [ ] **Step 2: Write `hacs.json`**

```json
{
  "name": "Makerspace Heating",
  "homeassistant": "2026.10.0",
  "render_readme": true
}
```

- [ ] **Step 3: Write the example dashboard**

`dashboards/makerspace-heating.yaml`:
```yaml
# Makerspace Heating dashboard (built-in cards only).
#
# Settings → Dashboards → Add dashboard → New dashboard from scratch.
# Open it, then ⋮ → Edit dashboard → ⋮ → Raw configuration editor, and paste this.
# Copy the "Workshop" section once per room and replace "workshop" with the
# room's entity prefix (the room name, lowercase, spaces as underscores).
# Replace sensor.workshop_temperature with the room's own temperature sensor.
title: Heating
views:
  - title: Heating
    type: sections
    max_columns: 3
    sections:
      - type: grid
        cards:
          - type: heading
            heading: Boiler
          - type: tile
            entity: sensor.makerspace_heating_boiler_status
            grid_options:
              columns: full
          - type: tile
            entity: sensor.makerspace_heating_upcoming_heating
            grid_options:
              columns: full
          - type: tile
            entity: binary_sensor.makerspace_heating_heating_problem
          - type: tile
            entity: sensor.makerspace_heating_boiler_starts_today
          - type: statistics-graph
            title: Boiler hours per day
            entities:
              - sensor.makerspace_heating_boiler_on_time_today
            stat_types:
              - change
            period: day
            chart_type: bar
            days_to_show: 14
      - type: grid
        cards:
          - type: heading
            heading: Workshop
          - type: tile
            entity: sensor.workshop_status
            grid_options:
              columns: full
          - type: tile
            entity: button.workshop_boost
            name: Boost 30 min
            tap_action:
              action: perform-action
              perform_action: button.press
              target:
                entity_id: button.workshop_boost
          - type: tile
            entity: select.workshop_boost_for
            features:
              - type: select-options
          - type: tile
            entity: switch.workshop_enabled
          - type: tile
            entity: binary_sensor.workshop_problem
          - type: history-graph
            title: Temperature and target
            hours_to_show: 24
            entities:
              - entity: sensor.workshop_temperature
              - entity: sensor.workshop_target_temperature
          - type: statistics-graph
            title: Heating hours per day
            entities:
              - sensor.workshop_heating_time_today
            stat_types:
              - change
            period: day
            chart_type: bar
            days_to_show: 14
```

- [ ] **Step 4: Write the README**

`README.md` (replace `REPO_URL` with the value from Step 1):
````markdown
# Makerspace Heating

A Home Assistant integration that makes every heating decision for the space in
one place. It controls the room valves and the boiler, and explains why each room
and the boiler are doing what they are.

Design: `docs/superpowers/specs/2026-10-13-makerspace-heating-design.md`.

## What it does

- **Rooms** heat to the highest of three demands:
  - **Event**: a tagged calendar event, including pre-heat before it starts.
  - **Boost**: a button press or a dashboard boost.
  - **Frost**: always on, so rooms never drop below the frost target.
- **The boiler** runs only while a room calls for heat and its valve has
  reported open for the *wait for valves* time. It keeps at least one valve open
  while running and for a few minutes after stopping, and never short-cycles.
- **Faults**, such as a dead sensor, an unresponsive valve, an unreadable
  calendar or a room heating without warming, appear under Settings → Repairs.
  They can also be sent to your phone or chat.
- **Every decision** has a plain-English status and a logbook entry.

## Install

1. In HACS, add this repository as a custom repository (type: Integration):
   `REPO_URL`.
2. Install **Makerspace Heating** and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → **Makerspace Heating**.
   Choose the boiler's Shelly switch and the shared events calendar.
4. On the integration page choose **Add room** for each room. Pick its valve
   switch, temperature sensor, optional button, temperatures and a **tag**.
5. Open **Configure** on the integration to change shared settings: boost limits,
   boiler timings, alert targets and the control mode.

## Booking heat with the calendar

Put the room's tag anywhere in an event's title or description: `#workshop`,
`#main`. One event can carry several tags. All-day events are ignored. Typos show
up in the **Upcoming heating** sensor's `unknown_tags` attribute.

## Rolling out safely

New installs start in **Shadow** mode. Shadow decides everything and shows
statuses, stats and alerts, but switches nothing.

1. List every automation, schedule helper, timer and generic thermostat currently
   used for heating.
2. Recreate each schedule as calendar events with room tags. Use recurring events
   for weekly sessions.
3. Leave Makerspace Heating in Shadow for a few days. Compare each room's
   **Status** and the **Boiler status** with what the heating actually did.
4. Disable the old heating automations and schedule helpers, and remove the
   generic thermostats.
5. Configure → Control mode → **Active**.
6. Watch the boiler status and the logbook for the first day.

**Paused** stops all switching for maintenance or testing.

## Dashboard

`dashboards/makerspace-heating.yaml` is a ready-made dashboard using only built-in
cards. Paste it into the raw configuration editor of a new dashboard, then copy
the Workshop section for each room.

## Defaults

| Setting | Default |
|---|---|
| Default / maximum boost, dropdown step | 30 min / 2 h / 30 min |
| Valve margin | 0.3 °C |
| Wait for valves (0 = off) | 3 min |
| Boiler minimum run / rest | 5 min / 5 min |
| Keep valves open after stop | 3 min |
| Fault delay | 5 min |
| Heating but not warming | 60 min with < 0.5 °C rise |
| Calendar failure threshold | 30 min |
| Boost / event / frost target | 19 / 20 / 7 °C |
| Pre-heat | 30 min |

## To check on site

- Is there an automatic bypass valve? If so, *wait for valves* and *keep open*
  can be shortened or set to 0.
- How long do the valve actuators take to open? This sets *wait for valves*.
- Are the button inputs in button mode (event entities) or switch mode (binary
  sensors)? Both work.

## Development

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
.venv/bin/pytest
```

The decision rules live in `custom_components/makerspace_heating/engine/` and
never import Home Assistant.
````

- [ ] **Step 5: Write the CI workflow**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
      - name: Install test dependencies
        run: |
          uv venv --python 3.14 .venv
          uv pip install --python .venv -r requirements_test.txt
      - name: Run tests
        run: .venv/bin/pytest

  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: hacs/action@main
        with:
          category: integration
          ignore: brands
```

- [ ] **Step 6: Run the full suite one last time**

Run: `.venv/bin/pytest -q`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add README.md hacs.json dashboards .github custom_components/makerspace_heating/manifest.json
git commit -m "docs: add README, example dashboard, HACS metadata and CI"
```

- [ ] **Step 8: Report CI follow-ups**

Tell the user:
- hassfest and the HACS action only run on GitHub. After pushing, check both jobs, and fix whatever hassfest reports (it validates the manifest, `services.yaml` and translations).
- HACS also wants a repository description and topics set on GitHub.
- Note whether the `CHANGE-ME` values are still present.
````

