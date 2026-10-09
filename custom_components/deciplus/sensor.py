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
    """Registrations of the account (all sites), bookings and waiting-list entries alike:
    the number the simultaneous quota applies to. Each stops counting at its class start."""

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
        data = self.coordinator.data
        return {
            "bookings": len(data.held) - data.waiting,
            "waiting_list": data.waiting,
            # learned limits: unknown (None) until reached once, see quotas.learn
            "quota": quotas.get("simultaneous"),
            "quota_per_day": quotas.get("per_day"),
            "quota_per_activity": dict(quotas.get("per_activity") or {}),
        }
