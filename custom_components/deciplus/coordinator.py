"""Polling coordinator, payload → CalendarEvent conversion, lifecycle events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DeciplusAuthError, DeciplusClient, DeciplusError
from .const import (
    CONF_DAYS_AHEAD,
    CONF_SCAN_MINUTES,
    CONF_ZONE_ID,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    EVENT_BOOKING_CANCELLED,
    EVENT_BOOKING_CONFIRMED,
    EVENT_SESSION_AVAILABLE,
    EVENT_WAITING_POSITION_CHANGED,
    OPENING_DURATION,
    OPENING_REFRESH_DELAY,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeciplusData:
    """Events per calendar, each sorted by start, plus the member's queue positions."""

    bookings: list[CalendarEvent] = field(default_factory=list)
    waiting_list: list[CalendarEvent] = field(default_factory=list)
    available_sessions: list[CalendarEvent] = field(default_factory=list)
    full_sessions: list[CalendarEvent] = field(default_factory=list)
    booking_openings: list[CalendarEvent] = field(default_factory=list)
    wait_positions: dict[int, int] = field(default_factory=dict)  # session id → waitIndex
    unparsed: set[int] = field(default_factory=set)  # ids of skipped (malformed) items

    def lists(self) -> tuple[list[CalendarEvent], ...]:
        return (
            self.bookings,
            self.waiting_list,
            self.available_sessions,
            self.full_sessions,
            self.booking_openings,
        )


def _parse(value: str) -> datetime:
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Bad date: {value}")
    if parsed.tzinfo is None:  # API always sends an offset; never let a naive one through
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _fr(dt: datetime) -> str:
    return dt_util.as_local(dt).strftime("%d/%m/%Y %H:%M")


def _sid(ev: CalendarEvent) -> int:
    """Session id from a `<kind>-<id>` uid."""
    return int(ev.uid.rsplit("-", 1)[1])


def _lesson_event(lesson: dict[str, Any], **kwargs: Any) -> CalendarEvent:
    start = _parse(lesson["startDate"])
    return CalendarEvent(
        start=start,
        end=start + timedelta(seconds=lesson["duration"]),
        summary=lesson["description"],
        **kwargs,
    )


Converted = tuple[str, CalendarEvent] | None  # (DeciplusData field, event)


def _booking(item: dict[str, Any], zone_id: int, **_: Any) -> Converted:
    lesson = item["booking"]
    if lesson["resource"]["idz"] != zone_id:
        return None  # /bookings/upcoming spans all zones of the account
    ev = _lesson_event(
        lesson,
        uid=f"booking-{lesson['id']}",
        location=lesson["resource"].get("name"),
        description=(
            f"Séance n°{lesson['id']}\n"
            f"Réservé le {_fr(_parse(item['bookedDate']))}\n"
            f"Places : {item.get('numberOfReservedPlaces', 1)}"
        ),
    )
    return "bookings", ev


def _waiting(item: dict[str, Any], zone_id: int, positions: dict[int, int], **_: Any) -> Converted:
    lesson = item["booking"]
    if lesson["resource"]["idz"] != zone_id:
        return None
    ev = _lesson_event(
        lesson,
        uid=f"waiting-{lesson['id']}",
        location=lesson["resource"].get("name"),
        description=f"Séance n°{lesson['id']}\nEn attente – {item['waitIndex']}",
    )
    ev.summary = f"{ev.summary} (en attente – {item['waitIndex']})"
    positions[lesson["id"]] = item["waitIndex"]
    return "waiting_list", ev


def _session(s: dict[str, Any], now: datetime, names: dict[int, str], **_: Any) -> Converted:
    status = (s.get("status") or {}).get("status")
    if status not in ("available", "waiting-list"):
        return None  # registered / registered-waiting-list: already in bookings / waiting_list
    # full vs available by head-count, like the vendor UI (and services._book)
    full = s["bookedMembers"] >= s["maxBookings"]
    location = names.get(s.get("resourceId"))
    start = _parse(s["startDate"])
    # both dates are nullable; the vendor UI treats a missing bookable_at as "open now"
    opens = _parse(s["bookable_at"]) if s.get("bookable_at") else now
    closes = _parse(s["bookable_until"]) if s.get("bookable_until") else start
    if opens > now:  # not bookable yet: one event at the opening instant, for triggers
        return "booking_openings", CalendarEvent(
            start=opens,
            end=opens + OPENING_DURATION,
            summary=f"Ouverture : {s['description']} {dt_util.as_local(start):%d/%m %H:%M}",
            location=location,
            uid=f"opening-{s['id']}",
            description=(
                f"Séance n°{s['id']}\nSéance le {_fr(start)}\nOuverture des réservations"
            ),
        )
    if closes < now:
        return None
    ev = _lesson_event(
        s,
        uid=f"session-{s['id']}",
        location=location,
        description=(
            f"Séance n°{s['id']}\n"
            f"Inscrits : {s['bookedMembers']}/{s['maxBookings']}\n"
            f"Réservable jusqu'au {_fr(closes)}"
        ),
    )
    ev.summary = f"{ev.summary} {s['bookedMembers']}/{s['maxBookings']}"
    return ("full_sessions" if full else "available_sessions"), ev


def build_data(
    upcoming: dict[str, Any] | None,
    sessions: list[dict[str, Any]] | None,
    resources: list[dict[str, Any]] | None,
    zone_id: int,
    now: datetime,
) -> DeciplusData:
    """Pure conversion of the API payloads. Text is French like the club data.

    A malformed item is logged and skipped instead of failing the whole refresh; its id is
    kept in `unparsed` so a skipped booking is not mistaken for a cancelled one. A list
    that is not a list is a ValueError: the whole payload is suspect, not one item.
    """
    data = DeciplusData()
    upcoming = upcoming or {}  # an empty 200 body arrives as None
    bookings = upcoming.get("bookings") or []
    waiting = upcoming.get("waitingBookings") or []
    if not all(isinstance(x, list) for x in (bookings, waiting, sessions or [])):
        raise ValueError("Unexpected Deciplus payload shape")
    ctx = {
        "zone_id": zone_id,
        "now": now,
        "names": {
            r["id"]: r.get("name") for r in resources or [] if isinstance(r, dict) and "id" in r
        },
        "positions": data.wait_positions,
    }
    work = [
        *((_booking, i) for i in bookings),
        *((_waiting, i) for i in waiting),
        *((_session, s) for s in sessions or []),
    ]
    for convert, item in work:
        try:
            result = convert(item, **ctx)
        except (KeyError, TypeError, ValueError, AttributeError) as err:  # incl. a null item
            _LOGGER.warning("Skipping malformed Deciplus item %r: %r", item, err)
            try:  # bookings nest the lesson under "booking", sessions are the lesson
                data.unparsed.add(int((item.get("booking") or item)["id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
            continue
        if result:
            getattr(data, result[0]).append(result[1])

    for events in data.lists():
        events.sort(key=lambda e: e.start)
    return data


def _payload(ev: CalendarEvent, zone_id: int, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": _sid(ev),
        "zone_id": zone_id,
        "summary": ev.summary,
        "start": ev.start.isoformat(),
        **extra,
    }


def diff_events(
    old: DeciplusData | None,
    new: DeciplusData,
    zone_id: int,
    now: datetime,
    ignore_cancel: set[int],
) -> list[tuple[str, dict[str, Any]]]:
    """Bus events describing what changed between two polls (none on the first)."""
    if old is None:
        return []
    by_id = lambda evs: {_sid(e): e for e in evs}  # noqa: E731
    old_full, old_book, old_wait = by_id(old.full_sessions), by_id(old.bookings), by_id(old.waiting_list)
    new_book, new_wait = by_id(new.bookings), by_id(new.waiting_list)
    out: list[tuple[str, dict[str, Any]]] = []

    for sid, ev in by_id(new.available_sessions).items():
        if sid in old_full:  # a full session gained a free place
            out.append((EVENT_SESSION_AVAILABLE, _payload(ev, zone_id)))

    for sid, ev in new_book.items():
        if sid in old_wait and sid not in old_book:  # promoted from the waiting list
            out.append(
                (
                    EVENT_BOOKING_CONFIRMED,
                    _payload(ev, zone_id, previous_position=old.wait_positions.get(sid)),
                )
            )

    for kind, olds in (("booking", old_book), ("waiting_list", old_wait)):
        for sid, ev in olds.items():
            if (
                sid in new_book
                or sid in new_wait
                or sid in new.unparsed
                or ev.start <= now
                or sid in ignore_cancel
            ):
                continue  # still there (maybe unreadable), started normally, or cancelled through HA
            out.append((EVENT_BOOKING_CANCELLED, _payload(ev, zone_id, kind=kind)))

    for sid, ev in new_wait.items():
        prev, cur = old.wait_positions.get(sid), new.wait_positions.get(sid)
        if prev is not None and cur is not None and prev != cur:
            out.append(
                (
                    EVENT_WAITING_POSITION_CHANGED,
                    _payload(ev, zone_id, position=cur, previous_position=prev),
                )
            )
    return out


def next_opening(data: DeciplusData, now: datetime) -> datetime | None:
    """Start of the earliest booking window still to open."""
    return min((e.start for e in data.booking_openings if e.start > now), default=None)


class DeciplusCoordinator(DataUpdateCoordinator[DeciplusData]):
    """Polls the members API; fires lifecycle events; refreshes right after an opening."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: DeciplusClient) -> None:
        minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self.zone_id: int = entry.data[CONF_ZONE_ID]
        self.days_ahead: int = entry.options.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)
        self.ignore_cancel: set[int] = set()  # cancellations made through our own service
        self._opening_unsub: CALLBACK_TYPE | None = None
        entry.async_on_unload(self._cancel_opening_refresh)

    @callback
    def _cancel_opening_refresh(self) -> None:
        if self._opening_unsub:
            self._opening_unsub()
            self._opening_unsub = None

    @callback
    def _refresh_at_opening(self, _now: datetime) -> None:
        self._opening_unsub = None
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> DeciplusData:
        now = dt_util.now()
        today = now.date()
        try:
            upcoming, sessions, resources = await asyncio.gather(
                self.client.async_get_upcoming(),
                self.client.async_get_sessions(
                    self.zone_id, today, today + timedelta(days=self.days_ahead)
                ),
                self.client.async_get_resources(self.zone_id),
            )
        except DeciplusAuthError as err:
            raise ConfigEntryAuthFailed from err
        except DeciplusError as err:
            raise UpdateFailed(str(err)) from err
        if upcoming is None or sessions is None:
            # an empty 200: treating it as "nothing booked" would fire one cancelled event
            # per booking, and the next poll could not take that back
            raise UpdateFailed("Empty payload from Deciplus")
        try:
            data = build_data(upcoming, sessions, resources, self.zone_id, now)
        except ValueError as err:
            raise UpdateFailed(str(err)) from err

        for event, payload in diff_events(self.data, data, self.zone_id, now, self.ignore_cancel):
            self.hass.bus.async_fire(event, payload)
        # an ignored id is consumed once the cancellation shows in the data
        self.ignore_cancel &= {_sid(e) for e in (*data.bookings, *data.waiting_list)}

        # openings are known in advance: refresh right after the next one, no polling needed
        self._cancel_opening_refresh()
        if opens := next_opening(data, now):
            self._opening_unsub = async_track_point_in_time(
                self.hass, self._refresh_at_opening, opens + OPENING_REFRESH_DELAY
            )
        return data
