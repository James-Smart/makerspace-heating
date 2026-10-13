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
