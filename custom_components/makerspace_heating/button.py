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
