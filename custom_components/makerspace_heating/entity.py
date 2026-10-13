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
