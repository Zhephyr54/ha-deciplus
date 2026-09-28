"""Pre-check sentences → codes → member-wide blockers, and the learned limits."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.quotas import Held, blockers, codes, learn  # noqa: E402

PARIS = timezone(timedelta(hours=2))
D1 = datetime(2026, 10, 5, 16, 30, tzinfo=PARIS)
D2 = datetime(2026, 10, 6, 9, 15, tzinfo=PARIS)
QUOTA = "This member has reached his quota"
FULL = "This booking is complete"
NOT_YET = "This booking is not available yet"
CLOSED = "This booking is no longer available"
REGISTERED = "This member is already registered to this booking"
DAY = "This member has too many bookings for this day"
ACTIVITY = "This member has reached his quota for this activity"


def held(n, start=D1, activity=(4, "Spinning")):
    return [Held(start, *activity) for _ in range(n)]


def test_codes_and_blockers():
    assert codes([FULL, QUOTA, "Something new"]) == [
        "BOOKING_COMPLETE", "MEMBER_MAX_BOOKING_QUOTA_REACHED", "Something new"
    ]
    assert blockers(codes([])) == []
    # rules about the probed session are not member-wide; unknown sentences are kept
    assert blockers(codes([FULL, NOT_YET, QUOTA, "Something new"])) == [
        "MEMBER_MAX_BOOKING_QUOTA_REACHED", "Something new"
    ]
    # the server stops at a closed window or an existing registration: nothing learned
    assert blockers(codes([CLOSED])) is None
    assert blockers(codes([REGISTERED, QUOTA])) is None


def test_learn_simultaneous():
    q = {}
    assert learn(q, held(6), D2, 9, codes([NOT_YET, QUOTA])) is True
    assert q["simultaneous"] == 6
    # holding fewer, not flagged: still 6; a cancel frees it
    assert learn(q, held(5), D2, 9, codes([])) is False and q["simultaneous"] == 6
    # club lowered it: flagged at 4
    learn(q, held(4), D2, 9, codes([QUOTA]))
    assert q["simultaneous"] == 4
    # club raised it: 4 held and not flagged → unknown again, then learned at 8
    assert learn(q, held(4), D2, 9, codes([])) is True and q["simultaneous"] is None
    learn(q, held(8), D2, 9, codes([QUOTA]))
    assert q["simultaneous"] == 8
    # flagged with nothing held (e.g. queue entries count): nothing to learn, no crash
    learn(q, [], D2, 9, codes([QUOTA]))
    assert q["simultaneous"] == 8
    # a short-circuited answer changes nothing
    assert learn(q, held(1), D2, 9, codes([CLOSED, QUOTA])) is False and q["simultaneous"] == 8


def test_learn_day_and_activity():
    q = {}
    # two bookings on D1, one on D2; probe on D1 flagged for the day and for Spinning
    mine = held(2, D1) + held(1, D2, (7, "Body Pump"))
    learn(q, mine, D1, 4, codes([DAY, ACTIVITY]))
    assert q["per_day"] == 2
    assert q["per_activity"] == {"Spinning": 2}
    # probe on D2 (one booking, other activity): nothing contradicts
    learn(q, mine, D2, 7, codes([]))
    assert q["per_day"] == 2 and q["per_activity"] == {"Spinning": 2}
    # a D1 probe not flagged any more: the club raised both
    learn(q, mine, D1, 4, codes([]))
    assert q["per_day"] is None and q["per_activity"] == {}
    # an activity never booked is keyed by id, harmlessly
    learn(q, mine, D1, 99, codes([ACTIVITY]))
    assert q["per_activity"] == {}


if __name__ == "__main__":
    test_codes_and_blockers()
    test_learn_simultaneous()
    test_learn_day_and_activity()
    print("quotas OK")
