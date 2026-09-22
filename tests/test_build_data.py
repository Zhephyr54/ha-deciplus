"""Single check on the payload → events conversion. Needs `homeassistant` importable."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))  # repo root, for plain python or pytest

from custom_components.deciplus.coordinator import DeciplusData, build_data  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PARIS = timezone(timedelta(hours=2))
# fixture sessions open between 07/09 and 13/09: two of them are still closed at this instant
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=PARIS)


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_build_data():
    sessions = _load("sessions.json")
    # malformed items must be skipped, not fail the refresh
    sessions += [
        {"id": 1, "status": {"status": "available"}, "description": "x"},  # missing keys
        {"id": 2, "status": None, "startDate": "nope"},  # null status
        {**sessions[3], "id": 3, "startDate": "not-a-date"},  # bad date
    ]
    data = build_data(_load("upcoming.json"), sessions, _load("resources.json"), 1, NOW)

    # zone 2 booking (id 2001) is dropped, the rest sorted by start
    assert [e.uid for e in data.bookings] == [
        "booking-1003", "booking-1011", "booking-1014", "booking-1010", "booking-1012"
    ]
    assert data.bookings[0].summary == "Body Pump"
    assert data.bookings[0].location == "Main Studio"
    assert data.bookings[0].end - data.bookings[0].start == timedelta(minutes=45)
    assert "Séance n°1003" in data.bookings[0].description

    assert [e.uid for e in data.waiting_list] == ["waiting-1005", "waiting-1006"]
    assert data.waiting_list[0].summary == "Bootcamp (en attente – 13)"
    assert data.wait_positions == {1005: 13, 1006: 2}

    assert [e.uid for e in data.available_sessions] == ["session-1004", "session-1008", "session-1009"]
    assert data.available_sessions[0].summary == "Yoga 22/26"
    assert data.available_sessions[0].location == "Main Studio"  # resolved from /resources

    assert {e.uid for e in data.full_sessions} == {"session-1001", "session-1002", "session-1007"}

    # not yet bookable → one event at bookable_at, in the openings calendar only
    assert [e.uid for e in data.booking_openings] == ["opening-1013", "opening-1015"]
    assert data.booking_openings[0].start == datetime(2026, 9, 12, 14, 0, tzinfo=PARIS)
    assert data.booking_openings[0].summary.startswith("Ouverture : ")
    assert "Séance n°1013" in data.booking_openings[0].description

    # registered / registered-waiting-list never leak into the session calendars
    session_uids = {e.uid for e in data.available_sessions + data.full_sessions + data.booking_openings}
    assert not {"session-1003", "session-1005", "session-1011", "session-1014"} & session_uids

    for events in data.lists():
        assert [e.start for e in events] == sorted(e.start for e in events)

    # empty 200 bodies arrive as None and must not break the refresh
    assert build_data(None, None, None, 1, NOW).lists() == DeciplusData().lists()

    # full/available follows the head-count, not the server's status label
    contradicting = {**sessions[3], "id": 4, "status": {"status": "waiting-list"}}  # 22/26
    assert "session-4" in {e.uid for e in build_data({}, [contradicting], [], 1, NOW).available_sessions}

    # a naive startDate is localised instead of producing an unorderable event
    naive = {**_load("upcoming.json")["bookings"][0]}
    naive["booking"] = {**naive["booking"], "startDate": "2026-09-21T11:15:00"}
    assert build_data({"bookings": [naive]}, [], [], 1, NOW).bookings[0].start.tzinfo is not None

    # a null item in any list is skipped, not fatal
    assert build_data({"bookings": [None]}, [None], [None], 1, NOW).lists() == DeciplusData().lists()
    # a list that is not a list means the whole payload is suspect
    for bad in ({"bookings": {"1003": {}}}, {"waitingBookings": "x"}):
        try:
            build_data(bad, [], [], 1, NOW)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")

    # null booking window: open now, bookable until the start (vendor UI ignores a null bookable_at)
    unbounded = {**sessions[3], "id": 5, "bookable_at": None, "bookable_until": None}
    assert "session-5" in {e.uid for e in build_data({}, [unbounded], [], 1, NOW).available_sessions}


if __name__ == "__main__":  # no pytest needed: python tests/test_build_data.py
    test_build_data()
    print("build_data OK")
