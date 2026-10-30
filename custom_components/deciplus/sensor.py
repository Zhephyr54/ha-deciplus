"""Bookings held, with the learned booking limits as attributes; pre-bookings pending."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DeciplusConfigEntry
from .entity import DeciplusEntity
from .prebooking import dump, next_opens


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DeciplusHeldBookings(entry), DeciplusPendingPrebookings(entry)])


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


class DeciplusPendingPrebookings(DeciplusEntity, SensorEntity):
    """Pre-bookings still waiting for their opening (or blocked after it). Updated the
    moment the queue changes, from any side: the picker, the card, a settled opening."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: DeciplusConfigEntry) -> None:
        super().__init__(entry, "pending_prebookings")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.prebookings)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        queue = self.coordinator.prebookings
        opens = next_opens(queue, dt_util.now())
        return {
            "blocked": sum(1 for p in queue.values() if p.blocked),
            "next_opens": opens.isoformat() if opens else None,
            "sessions": dump(queue),
        }
