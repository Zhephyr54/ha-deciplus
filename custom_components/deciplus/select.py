"""Picker: choose a session to book now, queue for, or pre-book at its opening."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeciplusConfigEntry
from .booking import book
from .entity import DeciplusEntity
from .prebooking import labels


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DeciplusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DeciplusBookPicker(entry)])


class DeciplusBookPicker(DeciplusEntity, SelectEntity):
    """An action picker, not a value: the state stays unknown, picking an option books
    (bookable), queues (full) or pre-books (not open yet) that session."""

    _attr_icon = "mdi:calendar-plus"

    def __init__(self, entry: DeciplusConfigEntry) -> None:
        super().__init__(entry, "book")
        self._labels: dict[str, int] = {}

    @property
    def options(self) -> list[str]:
        coordinator = self.coordinator
        self._labels = labels(coordinator.data.sessions, set(coordinator.prebookings))
        return list(self._labels)

    @property
    def current_option(self) -> str | None:
        return None

    async def async_select_option(self, option: str) -> None:
        # HA already checked the option is one of `options`, which refreshed `_labels`
        await book(self.coordinator, self._labels[option], schedule=True)
