"""Calendar entities: bookings, waiting list, available/full sessions, booking openings."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DeciplusConfigEntry
from .entity import DeciplusEntity

CALENDARS = (
    "bookings",
    "waiting_list",
    "available_sessions",
    "full_sessions",
    "booking_openings",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the calendars."""
    async_add_entities(DeciplusCalendar(entry, key) for key in CALENDARS)


class DeciplusCalendar(DeciplusEntity, CalendarEntity):
    """One calendar backed by one list of the coordinator data."""

    def __init__(self, entry: DeciplusConfigEntry, key: str) -> None:
        super().__init__(entry, key)
        self._key = key

    @property
    def _events(self) -> list[CalendarEvent]:
        return getattr(self.coordinator.data, self._key)

    @property
    def event(self) -> CalendarEvent | None:
        """Current or next event."""
        now = dt_util.now()
        return next((e for e in self._events if e.end > now), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events overlapping the range."""
        return [e for e in self._events if e.end > start_date and e.start < end_date]
