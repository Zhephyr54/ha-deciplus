"""On when Deciplus would refuse the member a booking right now, whatever the session."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeciplusConfigEntry
from .entity import DeciplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DeciplusBookingBlocked(entry)])


class DeciplusBookingBlocked(DeciplusEntity, BinarySensorEntity):
    """Member-wide refusals (quota, unpaid, blacklist, …) read off a probe session's pre-check."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: DeciplusConfigEntry) -> None:
        super().__init__(entry, "booking_blocked")

    @property
    def is_on(self) -> bool | None:
        found = self.coordinator.data.blockers
        return None if found is None else bool(found)  # unknown when nothing could be probed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {"reasons": list(data.blockers or []), **{f"probe_{k}": v for k, v in (data.probe or {}).items()}}
