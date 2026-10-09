"""Pre-booking queue helpers: storage round-trip, settlement, calendar event, picker labels."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.coordinator import build_data  # noqa: E402
from custom_components.deciplus.prebooking import (  # noqa: E402
    Prebooking,
    dump,
    event,
    labels,
    load,
    next_opens,
    projected,
    retryable,
    split,
    still_blocked,
)
from custom_components.deciplus.quotas import Held  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PARIS = timezone(timedelta(hours=2))
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=PARIS)


def _p(sid, start, opens, **kw):
    return Prebooking(
        sid, f"Séance {sid}", "Main Studio", start, start + timedelta(minutes=45), opens,
        kw.get("fallback", True), kw.get("guests", 0),
    )


# 1013 opens 12/09 14:00 (after NOW), 1015 opened on 01/09 (before NOW), both start later
KIDS = _p(1013, datetime(2026, 9, 26, 14, 0, tzinfo=PARIS), datetime(2026, 9, 12, 14, 0, tzinfo=PARIS))
HIIT = _p(1015, datetime(2026, 9, 27, 10, 15, tzinfo=PARIS), datetime(2026, 9, 1, 10, 15, tzinfo=PARIS), guests=1)
QUEUE = {1013: KIDS, 1015: HIIT}


def test_store_roundtrip():
    raw = json.loads(json.dumps(dump(QUEUE)))  # what the Store writes, then reads back
    assert load(raw) == QUEUE
    assert load(None) == {} and load([]) == {}
    blocked = HIIT._replace(blocked="MEMBER_MAX_BOOKING_QUOTA_REACHED")
    assert load(json.loads(json.dumps(dump({1015: blocked})))) == {1015: blocked}
    assert load([{k: v for k, v in raw[0].items() if k != "blocked"}])[1013].blocked is None  # older store
    # an unreadable item is dropped, the others survive
    junk = [None, {"session_id": "x"}, {**raw[0], "opens": "nope"}, {**raw[0], "start": "2026-09-26T14:00:00"}]
    assert load(raw + junk) == QUEUE
    assert load({"1013": {}}) == {}  # not even a list


def test_split():
    due, expired, done = split(QUEUE, set(), NOW)
    assert (due, expired, done) == ([HIIT], [], [])  # 1015 is open, 1013 not yet
    # booked by hand in the meantime: reported, not booked again
    assert split(QUEUE, {1015}, NOW) == ([], [], [HIIT])
    # the sessions started while nobody could book them (HIIT starts right at `later`)
    later = datetime(2026, 9, 27, 10, 15, tzinfo=PARIS)
    assert split(QUEUE, set(), later) == ([], [KIDS, HIIT], [])
    assert split(QUEUE, set(), later - timedelta(minutes=1)) == ([HIIT], [KIDS], [])
    # at the opening instant itself 1013 is due
    assert split(QUEUE, set(), KIDS.opens) == ([KIDS, HIIT], [], [])
    assert split({}, set(), NOW) == ([], [], [])


def test_next_opens():
    assert next_opens(QUEUE, NOW) == KIDS.opens
    assert next_opens(QUEUE, KIDS.opens) is None  # nothing strictly after
    assert next_opens({}, NOW) is None


def test_event():
    ev = event(KIDS)
    assert ev.uid == "prebooking-1013"
    assert ev.summary == "Séance 1013 (pré-réservation)"
    assert (ev.start, ev.end, ev.location) == (KIDS.start, KIDS.end, "Main Studio")
    assert ev.description.startswith("Séance n°1013\nRéservation automatique le ")
    assert "prévues" not in ev.description
    # the projection, with and without a known quota, warned when over it
    assert event(KIDS, 5, None).description.endswith("\nÀ l'ouverture : 5 réservations prévues")
    assert event(KIDS, 5, 6).description.endswith("\nÀ l'ouverture : 5/6 réservations prévues")
    assert event(KIDS, 7, 6).description.endswith(" 7/6 réservations prévues ⚠ quota atteint, réservée dès qu'une place se libère")
    blocked = event(KIDS._replace(blocked="MEMBER_HAVE_UNPAID"), 6, 6)
    assert blocked.summary == "Séance 1013 (pré-réservation – bloquée)"
    assert blocked.description.endswith("\nBloquée : MEMBER_HAVE_UNPAID – nouvel essai à chaque rafraîchissement")


def test_projected():
    # held: two classes after KIDS opens (12/09 14:00), one before; HIIT opens before KIDS
    held = [
        Held(datetime(2026, 9, 12, 18, 0, tzinfo=PARIS), 1, "a"),
        Held(datetime(2026, 9, 20, 9, 0, tzinfo=PARIS), 1, "a"),
        Held(datetime(2026, 9, 12, 9, 0, tzinfo=PARIS), 1, "a"),
        Held(None, None, None),
    ]
    assert projected(KIDS, held, QUEUE) == 1 + 2 + 1
    assert projected(HIIT, held, QUEUE) == 1 + 3  # KIDS opens after HIIT: not counted
    assert projected(KIDS, [], {1013: KIDS}) == 1


def test_blocking_rules():
    assert retryable("api_error", "MEMBER_MAX_BOOKING_QUOTA_REACHED")
    assert retryable("api_error", "MEMBER_HAVE_UNPAID")
    assert retryable("api_error", "")  # no answer at all (outage at the opening)
    assert not retryable("api_error", "BOOKING_COMPLETE")
    assert not retryable("api_error", "BOOKING_NOT_AVAILABLE_BEFORE")
    assert not retryable("session_full", None) and not retryable("no_free_seat", None)
    quota = "MEMBER_MAX_BOOKING_QUOTA_REACHED"
    assert still_blocked(["BOOKING_COMPLETE", quota]) == quota
    assert still_blocked(["BOOKING_NOT_AVAILABLE_AFTER"]) is None  # book() retries that one
    assert still_blocked(["BOOKING_COMPLETE"]) is None  # full: book() queues or fails for good
    assert still_blocked([]) is None
    assert still_blocked(["MEMBER_HAS_REGISTERED"]) is None  # settled as already_registered


def test_labels():
    fixtures = {n: json.loads((FIXTURES / f"{n}.json").read_text(encoding="utf-8")) for n in ("upcoming", "sessions", "resources")}
    data = build_data(fixtures["upcoming"], fixtures["sessions"], fixtures["resources"], 1, NOW)
    got = labels(data.sessions, set())
    assert list(got.values()) == [1001, 1002, 1004, 1007, 1008, 1009, 1013, 1015]  # by session start
    by_id = {v: k for k, v in got.items()}
    # the hour is rendered in HA's local zone (UTC in the test container): check the rest
    assert by_id[1001].startswith("lun. 21/09 ") and by_id[1001].endswith(" Spinning – complet")
    assert by_id[1004].startswith("lun. 21/09 ") and by_id[1004].endswith(" Yoga")
    assert by_id[1013].startswith("sam. 26/09 ") and " Kids Gym – ouvre le 12/09 " in by_id[1013]
    assert "/" not in by_id[1004].split(" ", 2)[2]  # no occupancy in the text
    # queued or excluded ids disappear
    assert 1013 not in labels(data.sessions, {1013}).values()
    # two rooms, same title and instant: both stay selectable
    twin = next(s for s in data.sessions if s.id == 1004)._replace(id=9999)
    got = labels([*data.sessions, twin], set())
    assert got[by_id[1004]] == 1004 and got[f"{by_id[1004]} (n°9999)"] == 9999


if __name__ == "__main__":
    test_store_roundtrip()
    test_split()
    test_next_opens()
    test_event()
    test_projected()
    test_blocking_rules()
    test_labels()
    print("prebooking OK")
