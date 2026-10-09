"""Calendar entities: bookings, waiting list, available/full sessions, booking openings,
pre-bookings. The three "mine" calendars accept Delete from the calendar card."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DeciplusConfigEntry
from .booking import cancel
from .entity import DeciplusEntity

CALENDARS = (
    "bookings",
    "waiting_list",
    "available_sessions",
    "full_sessions",
    "booking_openings",
    "prebookings",
)
DELETABLE = {"bookings", "waiting_list", "prebookings"}  # cancel / leave the queue / drop


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
        if key in DELETABLE:
            self._attr_supported_features = CalendarEntityFeature.DELETE_EVENT

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

    async def async_delete_event(
        self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        """The card's Delete button: same as deciplus.cancel_booking on the event."""
        try:
            session_id = int(uid.rsplit("-", 1)[-1])
        except ValueError as err:
            raise HomeAssistantError(f"Not a Deciplus event: {uid}") from err
        await cancel(self.coordinator, session_id)
