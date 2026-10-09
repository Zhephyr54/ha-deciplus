"""Pre-bookings: sessions to book the second their window opens. Pure helpers, like quotas.py."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import logging
from typing import Any, NamedTuple

from homeassistant.components.calendar import CalendarEvent
from homeassistant.util import dt as dt_util

from .quotas import UNEVALUATED

_LOGGER = logging.getLogger(__name__)

DAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")

# refusals about the session itself: a pre-booking that hits one of them is over
FINAL_CODES = {
    "BOOKING_COMPLETE",
    "BOOKING_NOT_AVAILABLE_BEFORE",
    "BOOKING_NOT_FOUND",
    "BOOKING_HAS_BEEN_CANCELLED",
    "BOOKING_HAS_BEEN_CANCELLED_NOT_FOUND",
    "PLACE_NOT_AVAILABLE",
    "INVITATIONS_NOT_AVAILABLE",
    "CANNOT_ADD_TO_BOOKING_QUEUE",
    "MEMBER_HAS_REGISTERED",
    "MEMBER_HAS_REGISTERED_ON_WAITING_LIST",
}
# pre-check sentences that do not block a retry: the booking routine handles them itself
HARD = {"BOOKING_COMPLETE", "BOOKING_NOT_AVAILABLE_AFTER", "PLACE_NOT_AVAILABLE"}


class Prebooking(NamedTuple):
    session_id: int
    summary: str
    location: str | None
    start: datetime
    end: datetime
    opens: datetime  # bookable_at
    fallback: bool  # join the waiting list if full at the opening
    guests: int
    blocked: str | None = None  # rule that refused it at the opening (quota…): retried each poll


def retryable(translation_key: str | None, code: str | None) -> bool:
    """A refusal worth retrying later: about the member (quota, unpaid…) or no answer at all."""
    return translation_key == "api_error" and code not in FINAL_CODES


def still_blocked(found: list[str]) -> str | None:
    """From a pre-check: the rule that would still refuse the member, None when worth a try."""
    return next((c for c in found if c not in HARD and c not in UNEVALUATED), None)


def projected(p: Prebooking, held: Iterable[Any], queue: dict[int, Prebooking]) -> int:
    """Registrations held at p's opening, p included: what is held now and still upcoming
    then, plus the other pre-bookings opening before it. A floor (hand bookings unknown)."""
    return (
        1
        + sum(1 for h in held if h.start and h.start > p.opens)
        + sum(1 for q in queue.values() if q.session_id != p.session_id and q.opens < p.opens)
    )


def dump(queue: dict[int, Prebooking]) -> list[dict[str, Any]]:
    """What the store writes."""
    return [
        {
            **p._asdict(),
            "start": p.start.isoformat(),
            "end": p.end.isoformat(),
            "opens": p.opens.isoformat(),
        }
        for p in queue.values()
    ]


def _dt(value: Any) -> datetime:
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"Bad date: {value}")
    return parsed


def load(raw: Any) -> dict[int, Prebooking]:
    """What the store read; an unreadable item is dropped, never fatal."""
    queue: dict[int, Prebooking] = {}
    for item in raw or []:
        try:
            p = Prebooking(
                int(item["session_id"]),
                str(item["summary"]),
                item.get("location"),
                _dt(item["start"]),
                _dt(item["end"]),
                _dt(item["opens"]),
                bool(item.get("fallback", True)),
                int(item.get("guests") or 0),
                str(item["blocked"]) if item.get("blocked") else None,
            )
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            _LOGGER.warning("Dropping unreadable pre-booking %r: %r", item, err)
            continue
        queue[p.session_id] = p
    return queue


def split(
    queue: dict[int, Prebooking], registered: set[int], now: datetime
) -> tuple[list[Prebooking], list[Prebooking], list[Prebooking]]:
    """(to book now, expired, already registered by other means); the rest waits."""
    due: list[Prebooking] = []
    expired: list[Prebooking] = []
    done: list[Prebooking] = []
    for p in queue.values():
        if p.start <= now:
            expired.append(p)
        elif p.session_id in registered:
            done.append(p)
        elif p.opens <= now:
            due.append(p)
    return due, expired, done


def next_opens(queue: dict[int, Prebooking], now: datetime) -> datetime | None:
    return min((p.opens for p in queue.values() if p.opens > now), default=None)


def event(p: Prebooking, projected: int | None = None, quota: int | None = None) -> CalendarEvent:
    opens = dt_util.as_local(p.opens).strftime("%d/%m/%Y %H:%M")
    lines = [f"Séance n°{p.session_id}", f"Réservation automatique le {opens}"]
    if projected is not None:
        line = f"À l'ouverture : {projected}{f'/{quota}' if quota else ''} réservations prévues"
        if quota and projected > quota:
            line += " ⚠ quota atteint, réservée dès qu'une place se libère"
        lines.append(line)
    if p.blocked:
        lines.append(f"Bloquée : {p.blocked} – nouvel essai à chaque rafraîchissement")
    return CalendarEvent(
        start=p.start,
        end=p.end,
        summary=f"{p.summary} (pré-réservation{' – bloquée' if p.blocked else ''})",
        location=p.location,
        uid=f"prebooking-{p.session_id}",
        description="\n".join(lines),
    )


def labels(sessions: Iterable[Any], exclude: set[int]) -> dict[str, int]:
    """Picker option → session id, for `coordinator.Session` rows (sorted by start).

    No occupancy in the text so the option list only changes when a session changes state."""
    out: dict[str, int] = {}
    for s in sessions:
        if s.id in exclude or s.state not in ("available", "full", "opening"):
            continue
        local = dt_util.as_local(s.start)
        label = f"{DAYS[local.weekday()]} {local:%d/%m %H:%M} {s.summary}"
        if s.state == "opening":
            label += f" – ouvre le {dt_util.as_local(s.opens):%d/%m %H:%M}"
        elif s.state == "full":
            label += " – complet"
        if label in out:  # same title, same instant (two rooms): keep both selectable
            label += f" (n°{s.id})"
        out[label] = s.id
    return out
