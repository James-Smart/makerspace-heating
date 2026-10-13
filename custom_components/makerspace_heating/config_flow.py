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
