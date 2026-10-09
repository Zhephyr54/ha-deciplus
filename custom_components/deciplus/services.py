"""deciplus.book_session / deciplus.cancel_booking, targeted at a club device."""

from __future__ import annotations

from functools import partial
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .booking import book, cancel
from .const import DOMAIN

ATTR_DEVICE = "device_id"
ATTR_SESSION = "session_id"
ATTR_FALLBACK = "waiting_list_fallback"
ATTR_PLACE = "place_id"
ATTR_GUESTS = "guests"
ATTR_SCHEDULE = "schedule"

SERVICE_BOOK = "book_session"
SERVICE_CANCEL = "cancel_booking"


def _session_id(value: Any) -> int:
    """Accepts 1013 or any calendar event uid (`booking-1013`, `waiting-`, `session-`, `opening-`, `prebooking-`)."""
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
        vol.Optional(ATTR_SCHEDULE, default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,  # the target may also come as entity_id / area_id / label_id
)
CANCEL_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_DEVICE): _devices, vol.Required(ATTR_SESSION): _session_id},
    extra=vol.ALLOW_EXTRA,
)


def coordinator_for_device(hass: HomeAssistant, device_id: str):
    """The loaded Deciplus coordinator behind a club device, or None."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    for entry_id in (device.primary_config_entry, *device.config_entries):
        entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


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
        if coordinator := coordinator_for_device(hass, device_id):
            return coordinator
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")


async def _book(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    result = await book(
        _coordinator(hass, call),
        call.data[ATTR_SESSION],
        fallback=call.data[ATTR_FALLBACK],
        guests=call.data[ATTR_GUESTS],
        place_id=call.data.get(ATTR_PLACE),
        schedule=call.data[ATTR_SCHEDULE],
    )
    return result if call.return_response else None


async def _cancel(hass: HomeAssistant, call: ServiceCall) -> None:
    await cancel(_coordinator(hass, call), call.data[ATTR_SESSION])


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
