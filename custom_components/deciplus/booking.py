"""The booking / cancelling routine shared by the services, the calendars and the picker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import DeciplusApiError, DeciplusAuthError, DeciplusError
from .const import DOMAIN
from .prebooking import Prebooking
from .quotas import codes

OPENING_RETRIES = 6  # BOOKING_NOT_AVAILABLE_AFTER right at bookable_at: clock skew
OPENING_RETRY_DELAY = 10
OPENING_RETRY_WINDOW = timedelta(minutes=2)  # only retry when bookable_at is this close
SEAT_RETRIES = 3  # PLACE_NOT_AVAILABLE on an auto-picked seat: pick another one


def parse(value: str) -> datetime:
    """An API date; the API always sends an offset, a naive one is localised rather than let through."""
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Bad date: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def api_error(err: Exception) -> HomeAssistantError:
    code = getattr(err, "code", None) or ""
    message = getattr(err, "message", None) or str(err)
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"code": code, "message": message},
    )


def _no_free_seat() -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key="no_free_seat")


def free_seat(session: dict[str, Any]) -> int | None:
    """First bookable seat of a seat-map session (ModalPlaceMap.vue → onClickCell)."""
    places = (session.get("map") or {}).get("places") or []
    return next(
        (
            p["id"]
            for p in places
            if p.get("type") == "seat" and p.get("isAvailable") == "O" and not p.get("isBooked")
        ),
        None,
    )


def opens_at(session: dict[str, Any]) -> datetime | None:
    """bookable_at, None when absent or unreadable (the vendor UI then treats it as open)."""
    raw = session.get("bookable_at")
    if not raw:
        return None
    try:
        return parse(raw)
    except ValueError:
        return None


def _opens_soon(session: dict[str, Any]) -> bool:
    """True when bookable_at is unknown or within the retry window (or already passed)."""
    opens = opens_at(session)
    return opens is None or opens - dt_util.now() < OPENING_RETRY_WINDOW


def _prebooking(
    coordinator: Any, session_id: int, session: dict[str, Any], opens: datetime, fallback: bool, guests: int
) -> Prebooking:
    # the planning row has the location resolved from /resources; the detail call has not
    known = next((s for s in coordinator.data.sessions if s.id == session_id), None) if coordinator.data else None
    start = parse(session["startDate"])
    return Prebooking(
        session_id,
        session.get("description") or (known.summary if known else f"Séance n°{session_id}"),
        known.location if known else (session.get("resource") or {}).get("name"),
        start,
        start + timedelta(seconds=int(session.get("duration") or 0)),
        opens,
        fallback,
        guests,
    )


async def book(
    coordinator: Any,
    session_id: int,
    *,
    fallback: bool = True,
    guests: int = 0,
    place_id: int | None = None,
    schedule: bool = False,
) -> dict[str, Any]:
    """Book, or queue when full (fallback), or pre-book when not open yet (schedule).

    Raises the translated HomeAssistantError the services expose."""
    client = coordinator.client
    place = place_id
    auto_seat = place is None
    result: dict[str, Any]
    try:
        session, messages = await client.async_get_session(session_id)
        # the pre-check is a free observation for the learned limits; the server stays the
        # authority on the booking itself (a queue entry may be allowed where a booking is not)
        if coordinator.observe(session, codes(messages), coordinator.data.held if coordinator.data else []):
            coordinator.async_update_listeners()
        if schedule and (opens := opens_at(session)) and opens > dt_util.now():
            n = coordinator.schedule(_prebooking(coordinator, session_id, session, opens, fallback, guests))
            return {
                "result": "scheduled",
                "opens": opens.isoformat(),
                "projected": n,  # registrations held at the opening, this one included
                "quota": coordinator.quotas.get("simultaneous"),
            }
        free = session["maxBookings"] - session["bookedMembers"]
        if guests and free <= guests:  # not enough room for member + guests: never queue alone
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_enough_places")
        has_spots = free > 0
        if has_spots and auto_seat and session.get("map"):
            place = free_seat(session)
            if place is None:  # room by head-count but no bookable seat: don't queue silently
                raise _no_free_seat()
        # retry "not available yet" only around the opening instant (clock skew), not
        # for a session that opens days from now
        opening_left = OPENING_RETRIES if _opens_soon(session) else 0
        seat_left = SEAT_RETRIES
        while has_spots:
            try:
                await client.async_book(session_id, place_id=place, guests=guests)
                result = {"result": "booked", **({"place_id": place} if place else {})}
                break
            except DeciplusApiError as err:
                if err.code == "BOOKING_NOT_AVAILABLE_AFTER" and opening_left > 0:
                    opening_left -= 1
                    await asyncio.sleep(OPENING_RETRY_DELAY)
                    continue
                if err.code == "PLACE_NOT_AVAILABLE" and auto_seat and seat_left > 0:
                    seat_left -= 1  # someone took our seat: pick another, don't queue
                    place = free_seat((await client.async_get_session(session_id))[0])
                    if place is None:
                        raise _no_free_seat() from err
                    continue
                if err.code == "BOOKING_COMPLETE" and fallback and not guests:
                    has_spots = False  # lost the race (with guests: surface the refusal instead)
                    break
                raise
        if not has_spots:
            if not fallback:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="session_full")
            result = {"result": "waiting_list", "wait_index": await client.async_join_queue(session_id)}
    except DeciplusAuthError as err:
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise api_error(err) from err
    except (DeciplusError, KeyError, TypeError, ValueError) as err:  # incl. unexpected payload shapes
        raise api_error(err) from err
    # re-booked right after a cancel: the pending "ignore" would otherwise stick to the id
    coordinator.ignore_cancel.discard(session_id)
    await coordinator.async_request_refresh()
    return result


async def cancel(coordinator: Any, session_id: int) -> None:
    """Drop a pre-booking, or cancel a booking / leave a waiting list on the server."""
    if coordinator.unschedule(session_id):
        return
    # registered before the call so a timeout cannot hide a cancel the server did perform
    coordinator.ignore_cancel.add(session_id)
    try:
        await coordinator.client.async_cancel(session_id)
    except asyncio.CancelledError:  # BaseException: not covered by the clauses below
        coordinator.ignore_cancel.discard(session_id)
        raise
    except DeciplusAuthError as err:
        coordinator.ignore_cancel.discard(session_id)
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise api_error(err) from err
    except (DeciplusApiError, KeyError, TypeError) as err:  # the server answered: nothing changed
        coordinator.ignore_cancel.discard(session_id)
        raise api_error(err) from err
    except DeciplusError as err:  # transport: outcome unknown, let a refresh settle it
        await coordinator.async_refresh()
        # a booking cancelled behind our back is gone now and was silenced; whatever survives
        # must not be masked later, so never keep the id (a still-failing refresh means one
        # informative "cancelled" event on the next poll, not a lost one)
        coordinator.ignore_cancel.discard(session_id)
        raise api_error(err) from err
    await coordinator.async_request_refresh()
