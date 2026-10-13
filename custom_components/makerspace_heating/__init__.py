"""Makerspace Heating: one place for all the space's heating decisions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import CONF_BOILER, DOMAIN, HUB_MODEL, MODE_ACTIVE, NAME
from .controller import HeatingController, async_turn_off_boiler
from .engine.persist import STORE_VERSION
from .options import control_mode
from .services import async_setup_services

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type MakerspaceHeatingConfigEntry = ConfigEntry[HeatingController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions once, whether or not an entry is loaded."""
    async_setup_services(hass)
    return True


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
    """Stop the platforms, then the controller (which saves state).

    An Active controller that no Active one replaces turns the boiler off, so
    it is not left firing with nothing in control. Valves stay as they are.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        controller = entry.runtime_data
        # A reload's update listener runs after the new options are applied.
        active_after = control_mode(entry.options) == MODE_ACTIVE and entry.disabled_by is None
        await controller.async_shutdown(
            release_boiler=controller.active and not active_after,
            # A Shadow/Paused boiler is only simulated: an Active successor must
            # not take it over as running (the real boiler is off, valves may be shut).
            handover=controller.active or not active_after,
        )
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MakerspaceHeatingConfigEntry) -> None:
    """After deletion (and unload): release the boiler and delete the saved state."""
    # A disabled entry already turned the boiler off when it was disabled.
    if control_mode(entry.options) == MODE_ACTIVE and entry.disabled_by is None:
        await async_turn_off_boiler(hass, entry.data[CONF_BOILER])
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
