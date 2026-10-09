"""`deciplus/subscribe_planning`: one club's planning as structured rows, pushed on every
refresh and right after every action. What the companion card renders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import CONF_ZONE_NAME
from .coordinator import DeciplusData, Session
from .prebooking import Prebooking, projected
from .services import coordinator_for_device

# what the card may do on a row, by state
ACTIONS = {
    "available": ["book"],
    "full": ["queue"],
    "opening": ["prebook"],
    "booked": ["cancel"],
    "waiting": ["cancel"],
    "prebooked": ["cancel"],
    "closed": [],
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _row(s: Session, p: Prebooking | None, data: DeciplusData, queue: dict[int, Prebooking]) -> dict[str, Any]:
    state = "prebooked" if p else s.state
    return {
        "id": s.id,
        "title": s.summary,
        "activity_id": s.activity_id,
        "start": _iso(s.start),
        "end": _iso(s.end),
        "location": s.location,
        "state": state,
        "booked": s.booked,
        "max": s.max,
        "opens": _iso(s.opens),
        "closes": _iso(s.closes),
        "wait_index": s.wait_index,
        "places": s.places,
        "penalty_after": _iso(s.penalty_after),
        "penalty_text": s.penalty_text,
        "projected": projected(p, data.held, queue) if p else None,
        "blocked_code": p.blocked if p else None,
        "can": ACTIONS[state],
    }


def planning(
    data: DeciplusData, queue: dict[int, Prebooking], quotas: dict[str, Any], days_ahead: int, club: str
) -> dict[str, Any]:
    """Pure: the feed payload. A pre-booking overrides its session's state; one whose
    session left the horizon (shorter `days_ahead`) is still shown, as an opening row."""
    known = {s.id for s in data.sessions}
    rows = [(s, queue.get(s.id)) for s in data.sessions]
    rows += [
        (Session(p.session_id, p.start, p.end, p.summary, p.location, p.opens, "opening"), p)
        for p in queue.values()
        if p.session_id not in known
    ]
    rows.sort(key=lambda r: r[0].start)
    return {
        "club": club,
        "horizon_days": days_ahead,
        "quota": {
            "held": len(data.held),
            "bookings": len(data.held) - data.waiting,
            "waiting_list": data.waiting,
            "quota": quotas.get("simultaneous"),
            "quota_per_day": quotas.get("per_day"),
            "quota_per_activity": dict(quotas.get("per_activity") or {}),
            "blocked": list(data.blockers or []),
        },
        "rows": [_row(s, p, data, queue) for s, p in rows],
    }


@websocket_api.websocket_command(
    {vol.Required("type"): "deciplus/subscribe_planning", vol.Required("device_id"): str}
)
@callback
def ws_subscribe_planning(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = coordinator_for_device(hass, msg["device_id"])
    if coordinator is None or coordinator.data is None:
        connection.send_error(msg["id"], "not_found", "No loaded Xplor Deciplus club behind this device")
        return
    club = coordinator.config_entry.data[CONF_ZONE_NAME]

    @callback
    def push() -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                planning(coordinator.data, coordinator.prebookings, coordinator.quotas, coordinator.days_ahead, club),
            )
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(push)
    connection.send_result(msg["id"])
    push()


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe_planning)
