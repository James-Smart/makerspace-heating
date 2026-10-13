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
CALENDAR_TIMEOUT: Final = timedelta(seconds=60)
BUTTON_DEBOUNCE: Final = timedelta(seconds=2)
RESEND_INTERVAL: Final = timedelta(seconds=30)
SAVE_DELAY: Final = 10  # seconds
HANDOVER_WINDOW: Final = timedelta(minutes=2)
"""State saved by a reload this recently is a handover, not a restart."""
COMMAND_WAIT: Final = timedelta(seconds=10)
"""How long stopping waits for valve and boiler commands still in flight."""

EVENT_LOGBOOK_ENTRY: Final = "logbook_entry"
SERVICE_BOOST: Final = "boost"
ATTR_DURATION: Final = "duration"
