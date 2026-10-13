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
