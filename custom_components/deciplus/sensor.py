"""Bookings held, with the learned booking limits as attributes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeciplusConfigEntry
from .entity import DeciplusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DeciplusHeldBookings(entry)])


class DeciplusHeldBookings(DeciplusEntity, SensorEntity):
    """Upcoming bookings of the account (all sites): what the simultaneous quota counts."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-check"

    def __init__(self, entry: DeciplusConfigEntry) -> None:
        super().__init__(entry, "held_bookings")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.held)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        quotas = self.coordinator.quotas
        return {
            "waiting_list": self.coordinator.data.waiting,
            # learned limits: unknown (None) until reached once, see quotas.learn
            "quota": quotas.get("simultaneous"),
            "quota_per_day": quotas.get("per_day"),
            "quota_per_activity": dict(quotas.get("per_activity") or {}),
        }
