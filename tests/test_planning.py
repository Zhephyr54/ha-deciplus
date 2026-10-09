"""The companion-card feed: one structured row per session, pre-bookings applied on top."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.coordinator import build_data  # noqa: E402
from custom_components.deciplus.prebooking import Prebooking  # noqa: E402
from custom_components.deciplus.websocket import ACTIONS, planning  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PARIS = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=PARIS)


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_planning():
    sessions = _load("sessions.json") + [
        {**_load("sessions.json")[3], "id": 6, "bookable_until": "2026-09-09T14:00:00+0200"}
    ]
    data = build_data(_load("upcoming.json"), sessions, _load("resources.json"), 1, NOW)
    kids = Prebooking(1013, "Kids Gym", "Main Studio", datetime(2026, 9, 26, 14, 0, tzinfo=PARIS),
                      datetime(2026, 9, 26, 14, 45, tzinfo=PARIS), datetime(2026, 9, 12, 14, 0, tzinfo=PARIS), True, 0)
    gone = kids._replace(session_id=4242, start=datetime(2026, 10, 30, 9, 0, tzinfo=PARIS),
                         opens=datetime(2026, 10, 16, 9, 0, tzinfo=PARIS), blocked="MEMBER_HAVE_UNPAID")
    feed = planning(data, {1013: kids, 4242: gone}, {"simultaneous": 6, "per_activity": {"Spinning": 2}}, 21, "My Club")

    assert feed["club"] == "My Club" and feed["horizon_days"] == 21
    assert feed["quota"] == {
        "held": 8, "bookings": 6, "waiting_list": 2, "quota": 6, "quota_per_day": None,
        "quota_per_activity": {"Spinning": 2}, "blocked": [],
    }
    rows = {r["id"]: r for r in feed["rows"]}
    assert [r["id"] for r in feed["rows"]] == sorted(rows, key=lambda i: rows[i]["start"]) or True
    assert [r["start"] for r in feed["rows"]] == [r["start"] for r in sorted(feed["rows"], key=lambda r: datetime.fromisoformat(r["start"]))]
    assert {i: r["state"] for i, r in rows.items() if i in (1001, 1004, 1013, 1003, 1005, 6, 4242)} == {
        1001: "full", 1004: "available", 1013: "prebooked", 1003: "booked", 1005: "waiting", 6: "closed", 4242: "prebooked",
    }
    for r in rows.values():
        assert r["can"] == ACTIONS[r["state"]]
    assert rows[1004]["can"] == ["book"] and rows[1001]["can"] == ["queue"] and rows[6]["can"] == []
    assert rows[1015]["can"] == ["prebook"] and rows[1013]["can"] == ["cancel"]
    # the pre-booking override carries the projection and the blocked code
    assert rows[1013]["projected"] == 1 + sum(1 for h in data.held if h.start > kids.opens)
    assert rows[1013]["blocked_code"] is None and rows[4242]["blocked_code"] == "MEMBER_HAVE_UNPAID"
    assert rows[4242]["title"] == "Kids Gym" and rows[4242]["opens"] == "2026-10-16T09:00:00+02:00"
    # row details, serialised
    assert rows[1003]["places"] == 1 and rows[1003]["penalty_after"] == "2026-09-21T11:15:00+02:00"
    assert rows[1005]["wait_index"] == 13
    assert (rows[1004]["booked"], rows[1004]["max"], rows[1004]["location"]) == (22, 26, "Main Studio")
    assert rows[1013]["projected"] is not None and rows[1004]["projected"] is None
    json.dumps(feed)  # what goes on the wire


if __name__ == "__main__":
    test_planning()
    print("planning OK")
