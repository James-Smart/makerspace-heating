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
