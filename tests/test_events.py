"""Lifecycle events derived from two consecutive polls, and the next-opening helper."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.const import (  # noqa: E402
    EVENT_BOOKING_CANCELLED,
    EVENT_BOOKING_CONFIRMED,
    EVENT_SESSION_AVAILABLE,
    EVENT_WAITING_POSITION_CHANGED,
)
from custom_components.deciplus.coordinator import build_data, diff_events, next_opening  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PARIS = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=PARIS)


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _data(upcoming, sessions):
    return build_data(upcoming, sessions, _load("resources.json"), 1, NOW)


def test_events():
    upcoming, sessions = _load("upcoming.json"), _load("sessions.json")
    old = _data(upcoming, sessions)
    assert old.wait_positions == {1005: 13, 1006: 2}
    assert diff_events(None, old, 1, NOW, set()) == []  # first poll: nothing to compare
    assert diff_events(old, old, 1, NOW, set()) == []  # nothing changed

    # 1005: waiting #13 → booked (promotion); 1006: waiting #2 → #1; 1010 booking vanished
    # (before its start); 1001 full → available
    new_up = json.loads(json.dumps(upcoming))
    promoted = next(w for w in new_up["waitingBookings"] if w["booking"]["id"] == 1005)
    new_up["waitingBookings"].remove(promoted)
    new_up["bookings"].append({**upcoming["bookings"][0], "booking": promoted["booking"]})
    next(w for w in new_up["waitingBookings"] if w["booking"]["id"] == 1006)["waitIndex"] = 1
    new_up["bookings"] = [b for b in new_up["bookings"] if b["booking"]["id"] != 1010]
    new_sessions = [
        {**s, "bookedMembers": 20} if s["id"] == 1001 else s for s in sessions
    ]
    events = dict(diff_events(old, _data(new_up, new_sessions), 1, NOW, set()))

    assert events[EVENT_SESSION_AVAILABLE]["session_id"] == 1001
    assert events[EVENT_BOOKING_CONFIRMED]["session_id"] == 1005
    assert events[EVENT_BOOKING_CONFIRMED]["previous_position"] == 13
    assert events[EVENT_BOOKING_CANCELLED]["session_id"] == 1010
    assert events[EVENT_BOOKING_CANCELLED]["kind"] == "booking"
    assert events[EVENT_WAITING_POSITION_CHANGED] == {
        "session_id": 1006, "zone_id": 1, "summary": "Bootcamp (en attente – 1)",
        "start": "2026-09-28T16:30:00+02:00", "position": 1, "previous_position": 2,
    }

    # cancelled through our own service → no event; consumed once gone from the data
    ignore = {1010}
    kinds = [e for e, _ in diff_events(old, _data(new_up, new_sessions), 1, NOW, ignore)]
    assert EVENT_BOOKING_CANCELLED not in kinds

    # a booking that simply started is not a cancellation
    after_start = datetime(2026, 10, 1, 17, 0, tzinfo=PARIS)
    kinds = [e for e, _ in diff_events(old, _data(new_up, new_sessions), 1, after_start, set())]
    assert EVENT_BOOKING_CANCELLED not in kinds

    # leaving the waiting list by hand is reported with kind=waiting_list
    gone = json.loads(json.dumps(upcoming))
    gone["waitingBookings"] = []
    cancelled = [d for e, d in diff_events(old, _data(gone, sessions), 1, NOW, set()) if e == EVENT_BOOKING_CANCELLED]
    assert {d["session_id"] for d in cancelled} == {1005, 1006}
    assert {d["kind"] for d in cancelled} == {"waiting_list"}

    # a booking that fails to parse is unreadable, not cancelled
    broken = json.loads(json.dumps(upcoming))
    broken["bookings"][0]["booking"]["startDate"] = None
    degraded = _data(broken, sessions)
    assert degraded.unparsed == {upcoming["bookings"][0]["booking"]["id"]}
    assert EVENT_BOOKING_CANCELLED not in [e for e, _ in diff_events(old, degraded, 1, NOW, set())]


def test_next_opening():
    data = _data(_load("upcoming.json"), _load("sessions.json"))
    assert next_opening(data, NOW) == datetime(2026, 9, 12, 14, 0, tzinfo=PARIS)
    assert next_opening(data, datetime(2026, 9, 13, 12, 0, tzinfo=PARIS)) is None


if __name__ == "__main__":
    test_events()
    test_next_opening()
    print("events OK")
