"""Makerspace Heating: one place for all the space's heating decisions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, HUB_MODEL, NAME
from .controller import HeatingController

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type MakerspaceHeatingConfigEntry = ConfigEntry[HeatingController]


async def async_setup_entry(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> bool:
    """Start the controller and the entity platforms."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer=NAME,
        model=HUB_MODEL,
    )
    controller = HeatingController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> None:
    """Reload after options or rooms change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> bool:
    """Stop the platforms, then the controller (which saves state)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
