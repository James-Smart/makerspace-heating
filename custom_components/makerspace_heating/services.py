"""The makerspace_heating.boost action (spec §7.3)."""

from __future__ import annotations

import probatio
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import ATTR_DURATION, DOMAIN, SERVICE_BOOST
from .controller import HeatingController, describe_origin

BOOST_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Optional(ATTR_DURATION): cv.time_period,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def handle_boost(call: ServiceCall) -> None:
        controller = _controller(hass)
        room_ids = _room_ids(hass, controller, call.data[ATTR_DEVICE_ID])
        origin = await describe_origin(hass, call.context)
        controller.async_boost_rooms(room_ids, call.data.get(ATTR_DURATION), origin)

    hass.services.async_register(DOMAIN, SERVICE_BOOST, handle_boost, schema=BOOST_SCHEMA)


def _controller(hass: HomeAssistant) -> HeatingController:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Makerspace Heating is not loaded")
    return entries[0].runtime_data


def _room_ids(
    hass: HomeAssistant, controller: HeatingController, device_ids: list[str]
) -> list[str]:
    registry = dr.async_get(hass)
    room_ids: list[str] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        room_id = None
        if device is not None:
            room_id = next(
                (
                    identifier
                    for domain, identifier in device.identifiers
                    if domain == DOMAIN and identifier in controller.rooms
                ),
                None,
            )
        if room_id is None:
            raise ServiceValidationError(
                f"Device {device_id} is not a Makerspace Heating room"
            )
        room_ids.append(room_id)
    return room_ids
