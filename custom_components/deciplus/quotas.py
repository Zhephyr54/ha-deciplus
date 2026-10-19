"""Booking limits, read off the server's pre-check.

`GET sessions/{id}` returns `messages`: the rules that would refuse the calling member on
that session, right now. The API never states the limits themselves, so the maxima are
learned: a "reached" rule means the max is what the member holds at that moment (the server
never lets a member exceed it online). Only a refusal ever sets a value, so a displayed max
is always one that was hit. A rule *not* reached while holding as much as the learned value
contradicts it: either the club raised the limit (unknown again, relearned at the next
refusal), or the server had counted a unit we do not (a cancelled class until its start, seen
live) and pulled the value below the real one; the highest value ever hit is kept for that
case and restored when it is still consistent with what the member holds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple

from homeassistant.util import dt as dt_util

from .api import MESSAGE_CODES

QUOTA = "MEMBER_MAX_BOOKING_QUOTA_REACHED"  # simultaneous registrations (bookings + queue entries)
QUOTA_DAY = "MEMBER_HAS_TOO_MANY_BOOKING_FOR_THE_DAY"
QUOTA_ACTIVITY = "MEMBER_MAX_ACTIVITY_BOOKING_QUOTA_REACHED"

# observed live: with one of these the server stops evaluating, so the answer says nothing
# about the member
UNEVALUATED = {
    "BOOKING_NOT_AVAILABLE_BEFORE",
    "BOOKING_NOT_FOUND",
    "BOOKING_HAS_BEEN_CANCELLED",
    "BOOKING_HAS_BEEN_CANCELLED_NOT_FOUND",
    "MEMBER_HAS_REGISTERED",
    "MEMBER_HAS_REGISTERED_ON_WAITING_LIST",
}
# about the probed session, or the member relative to it: not a member-wide refusal
SESSION_RULES = {
    "BOOKING_COMPLETE",
    "BOOKING_NOT_AVAILABLE_AFTER",
    "PLACE_NOT_AVAILABLE",
    "BOOKING_ON_SAME_TIME_SLOT",
    "MEMBER_BOOKING_TOO_EARLY",
    QUOTA_DAY,
    QUOTA_ACTIVITY,
}


class Held(NamedTuple):
    """One upcoming registration of the member (any zone): a booking or a waiting-list
    entry, both count the same (tested live), each until its class starts."""

    start: datetime | None
    activity_id: int | None
    activity_name: str | None


def codes(messages: list[str]) -> list[str]:
    """Rule codes; an unknown sentence is kept verbatim so a new rule still surfaces."""
    return [MESSAGE_CODES.get(m, m) for m in messages if isinstance(m, str)]


def blockers(found: list[str]) -> list[str] | None:
    """Member-wide refusals, or None when the server did not evaluate them."""
    if UNEVALUATED & set(found):
        return None
    return [c for c in found if c not in SESSION_RULES]


def _fit(known: int | None, seen: int | None, reached: bool, count: int) -> tuple[int | None, int | None]:
    """(learned value, highest value ever hit) after one observation."""
    if reached:
        if count == 0:  # 0 held yet "reached": nothing to learn
            return known, seen
        return count, max(seen or 0, count)
    if known is not None and count >= known:  # contradicted
        return (seen, seen) if seen and seen > count else (None, None)
    return known, seen


def learn(
    quotas: dict[str, Any],
    held: list[Held],
    start: datetime,
    activity_id: int | None,
    found: list[str],
) -> bool:
    """Update the learned maxima in place from one pre-check; True when something changed.

    `start` / `activity_id` are the probed session's: the day and activity rules are
    evaluated against them.
    """
    if UNEVALUATED & set(found):
        return False
    day = dt_util.as_local(start).date()
    on_day = sum(1 for h in held if h.start and dt_util.as_local(h.start).date() == day)
    same = [h for h in held if activity_id is not None and h.activity_id == activity_id]

    seen = quotas.get("seen") or {}
    simultaneous, seen_simultaneous = _fit(quotas.get("simultaneous"), seen.get("simultaneous"), QUOTA in found, len(held))
    per_day, seen_per_day = _fit(quotas.get("per_day"), seen.get("per_day"), QUOTA_DAY in found, on_day)
    new = {
        "simultaneous": simultaneous,
        "per_day": per_day,
        "per_activity": dict(quotas.get("per_activity") or {}),
        # highest values ever hit, the fallback of a contradicted value (see _fit)
        "seen": {
            "simultaneous": seen_simultaneous,
            "per_day": seen_per_day,
            "per_activity": dict(seen.get("per_activity") or {}),
        },
    }
    if activity_id is not None:
        # keyed by the activity name (what a user reads), from the bookings held for it
        name = next((h.activity_name for h in same if h.activity_name), str(activity_id))
        values = _fit(new["per_activity"].get(name), new["seen"]["per_activity"].get(name), QUOTA_ACTIVITY in found, len(same))
        for table, value in zip((new["per_activity"], new["seen"]["per_activity"]), values):
            if value is None:
                table.pop(name, None)
            else:
                table[name] = value
    changed = new != quotas
    quotas.clear()
    quotas.update(new)
    return changed
