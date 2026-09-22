"""Xplor Deciplus: sync club bookings into Home Assistant calendars."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import DeciplusClient
from .const import DOMAIN
from .coordinator import DeciplusCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.CALENDAR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type DeciplusConfigEntry = ConfigEntry[DeciplusCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain services once."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DeciplusConfigEntry) -> bool:
    """Set up from a config entry."""

    @callback
    def persist_token(token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ACCESS_TOKEN: token}
        )

    client = DeciplusClient(
        async_get_clientsession(hass),
        entry.data[CONF_DOMAIN],
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        token=entry.data.get(CONF_ACCESS_TOKEN),
        on_token=persist_token,
    )
    coordinator = DeciplusCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    options_at_setup = dict(entry.options)

    async def reload_on_options_change(hass: HomeAssistant, entry: DeciplusConfigEntry) -> None:
        # update listeners also fire for persist_token(): only options warrant a reload
        if dict(entry.options) != options_at_setup:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeciplusConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
