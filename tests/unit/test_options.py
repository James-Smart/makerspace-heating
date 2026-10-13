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
    MODE_PAUSED,
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
    assert control_mode({"control_mode": MODE_PAUSED}) == MODE_PAUSED


def test_unknown_control_mode_is_shadow() -> None:
    assert control_mode({"control_mode": "Active"}) == MODE_SHADOW
    assert control_mode({"control_mode": "turbo"}) == MODE_SHADOW
    assert control_mode({"control_mode": None}) == MODE_SHADOW


def test_notify_targets() -> None:
    assert notify_targets({}) == ([], [])
    assert notify_targets(
        {CONF_NOTIFY_ENTITIES: ["notify.admin"], CONF_NOTIFY_ACTIONS: ["notify.mobile_app_x"]}
    ) == (["notify.admin"], ["notify.mobile_app_x"])
