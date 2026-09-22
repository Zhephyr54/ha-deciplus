"""deciplus.book_session / deciplus.cancel_booking, targeted at a club device."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from functools import partial
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .api import DeciplusApiError, DeciplusAuthError, DeciplusError
from .const import DOMAIN
from .coordinator import _parse

ATTR_DEVICE = "device_id"
ATTR_SESSION = "session_id"
ATTR_FALLBACK = "waiting_list_fallback"
ATTR_PLACE = "place_id"
ATTR_GUESTS = "guests"

SERVICE_BOOK = "book_session"
SERVICE_CANCEL = "cancel_booking"


def _session_id(value: Any) -> int:
    """Accepts 1013 or any calendar event uid (`booking-1013`, `waiting-`, `session-`, `opening-`)."""
    match = re.fullmatch(r"(?:[a-z]+-)?(\d+)", str(value).strip())
    if not match:
        raise vol.Invalid(f"invalid session id {value!r}")
    return int(match[1])


_devices = vol.All(cv.ensure_list, [cv.string])

BOOK_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE): _devices,
        vol.Required(ATTR_SESSION): _session_id,
        vol.Optional(ATTR_FALLBACK, default=True): cv.boolean,
        vol.Optional(ATTR_PLACE): vol.Coerce(int),
        vol.Optional(ATTR_GUESTS, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=5)),
    },
    extra=vol.ALLOW_EXTRA,  # the target may also come as entity_id / area_id / label_id
)
CANCEL_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_DEVICE): _devices, vol.Required(ATTR_SESSION): _session_id},
    extra=vol.ALLOW_EXTRA,
)

OPENING_RETRIES = 6  # BOOKING_NOT_AVAILABLE_AFTER right at bookable_at: clock skew
OPENING_RETRY_DELAY = 10
OPENING_RETRY_WINDOW = timedelta(minutes=2)  # only retry when bookable_at is this close
SEAT_RETRIES = 3  # PLACE_NOT_AVAILABLE on an auto-picked seat: pick another one


def _coordinator(hass: HomeAssistant, call: ServiceCall):
    """The loaded Deciplus entry behind the target (device, or entity/area/label → device)."""
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    data = call.data
    # resolved by hand: HA's target helper changed module and signature across our floor
    device_ids = list(data.get(ATTR_DEVICE) or [])
    for entity_id in cv.ensure_list(data.get("entity_id") or []):
        if (entity := entities.async_get(entity_id)) and entity.device_id:
            device_ids.append(entity.device_id)
    for area_id in cv.ensure_list(data.get("area_id") or []):
        device_ids += [d.id for d in dr.async_entries_for_area(registry, area_id)]
    for label_id in cv.ensure_list(data.get("label_id") or []):
        device_ids += [d.id for d in dr.async_entries_for_label(registry, label_id)]
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            continue
        for entry_id in (device.primary_config_entry, *device.config_entries):
            entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
            if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
                return entry.runtime_data
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")


def _api_error(err: Exception) -> HomeAssistantError:
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


def _opens_soon(session: dict[str, Any]) -> bool:
    """True when bookable_at is unknown or within the retry window (or already passed)."""
    raw = session.get("bookable_at")
    if not raw:
        return True
    try:
        return _parse(raw) - dt_util.now() < OPENING_RETRY_WINDOW
    except ValueError:
        return True


async def _book(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(hass, call)
    client = coordinator.client
    session_id: int = call.data[ATTR_SESSION]
    fallback: bool = call.data[ATTR_FALLBACK]
    guests: int = call.data[ATTR_GUESTS]
    place: int | None = call.data.get(ATTR_PLACE)
    auto_seat = place is None
    result: dict[str, Any]
    try:
        session = await client.async_get_session(session_id)
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
                    place = free_seat(await client.async_get_session(session_id))
                    if place is None:
                        raise _no_free_seat() from err
                    continue
                if err.code == "BOOKING_COMPLETE" and fallback and not guests:
                    has_spots = False  # lost the race (with guests: surface the refusal instead)
                    break
                raise
        if not has_spots:
            if not fallback:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="session_full"
                )
            result = {"result": "waiting_list", "wait_index": await client.async_join_queue(session_id)}
    except DeciplusAuthError as err:
        coordinator.config_entry.async_start_reauth(hass)
        raise _api_error(err) from err
    except (DeciplusError, KeyError, TypeError) as err:  # incl. unexpected payload shapes
        raise _api_error(err) from err
    # re-booked right after a cancel: the pending "ignore" would otherwise stick to the id
    coordinator.ignore_cancel.discard(session_id)
    await coordinator.async_request_refresh()
    return result if call.return_response else None


async def _cancel(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator = _coordinator(hass, call)
    session_id: int = call.data[ATTR_SESSION]
    # registered before the call so a timeout cannot hide a cancel the server did perform
    coordinator.ignore_cancel.add(session_id)
    try:
        await coordinator.client.async_cancel(session_id)
    except asyncio.CancelledError:  # BaseException: not covered by the clauses below
        coordinator.ignore_cancel.discard(session_id)
        raise
    except DeciplusAuthError as err:
        coordinator.ignore_cancel.discard(session_id)
        coordinator.config_entry.async_start_reauth(hass)
        raise _api_error(err) from err
    except (DeciplusApiError, KeyError, TypeError) as err:  # the server answered: nothing changed
        coordinator.ignore_cancel.discard(session_id)
        raise _api_error(err) from err
    except DeciplusError as err:  # transport: outcome unknown, let a refresh settle it
        await coordinator.async_refresh()
        # a booking cancelled behind our back is gone now and was silenced; whatever survives
        # must not be masked later, so never keep the id (a still-failing refresh means one
        # informative "cancelled" event on the next poll, not a lost one)
        coordinator.ignore_cancel.discard(session_id)
        raise _api_error(err) from err
    await coordinator.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOK,
        partial(_book, hass),
        BOOK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, partial(_cancel, hass), CANCEL_SCHEMA)
