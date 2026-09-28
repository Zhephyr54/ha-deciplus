"""Base entity: one club device per config entry."""

from __future__ import annotations

from homeassistant.const import CONF_DOMAIN
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DeciplusConfigEntry
from .const import CONF_ZONE_NAME, DOMAIN, MEMBER_APP
from .coordinator import DeciplusCoordinator


class DeciplusEntity(CoordinatorEntity[DeciplusCoordinator]):
    """Named after its translation key, attached to the club device."""

    _attr_has_entity_name = True

    def __init__(self, entry: DeciplusConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data[CONF_ZONE_NAME],
            manufacturer="Xplor",
            model="Deciplus",
            configuration_url=f"{MEMBER_APP}/{entry.data[CONF_DOMAIN]}/calendar",
        )
