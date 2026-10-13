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
