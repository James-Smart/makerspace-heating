"""Placeholder config flow; Task 14 replaces this with the real flows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class MakerspaceHeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Placeholder so entries can be set up before the real flow exists."""

    VERSION = 1
