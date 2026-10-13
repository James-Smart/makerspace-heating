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
    CONTROL_MODES,
    MODE_SHADOW,
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
    """Return the control mode: Shadow for a new install, or if the stored mode is unknown."""
    mode = options.get(CONF_CONTROL_MODE)
    return mode if mode in CONTROL_MODES else MODE_SHADOW


def notify_targets(options: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (notify entities, legacy notify actions) for alerts."""
    return (
        list(options.get(CONF_NOTIFY_ENTITIES, [])),
        list(options.get(CONF_NOTIFY_ACTIONS, [])),
    )
