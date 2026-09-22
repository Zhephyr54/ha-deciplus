"""Pure helpers of services.py: seat picking and session id coercion."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.services import BOOK_SCHEMA, _opens_soon, free_seat  # noqa: E402
import voluptuous as vol  # noqa: E402  after HA, which may swap in its own implementation

PLACES = [
    {"id": 1, "type": "object", "isAvailable": "O"},
    {"id": 2, "type": "coach", "isAvailable": "O"},
    {"id": 3, "type": "seat", "isAvailable": "N"},
    {"id": 4, "type": "seat", "isAvailable": "O", "isBooked": True},
    {"id": 5, "type": "seat", "isAvailable": "O", "isBooked": False},
    {"id": 6, "type": "seat", "isAvailable": "O"},
]


def test_free_seat():
    assert free_seat({"map": {"places": PLACES}}) == 5
    assert free_seat({"map": {"places": PLACES[:4]}}) is None
    assert free_seat({"map": None}) is None
    assert free_seat({}) is None


def test_book_schema():
    for raw in ("1013", 1013, "session-1013", "opening-1013", "booking-1013", "waiting-1013"):
        data = BOOK_SCHEMA({"device_id": "dev", "session_id": raw})
        assert data["session_id"] == 1013
        assert data["device_id"] == ["dev"]
        assert data["waiting_list_fallback"] is True and data["guests"] == 0
    data = BOOK_SCHEMA({"device_id": ["a", "b"], "session_id": "7", "guests": "2", "place_id": "42", "area_id": "x"})
    assert data["guests"] == 2 and data["place_id"] == 42 and data["device_id"] == ["a", "b"]
    # a pasted description must not collapse into one long number
    for bad in ("Séance n°1013\nInscrits : 22/26", "1013-1014", "", "abc"):
        try:
            BOOK_SCHEMA({"session_id": bad})
        except vol.Invalid:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")


def test_opens_soon_tolerates_naive_and_missing_dates():
    assert _opens_soon({}) is True
    assert _opens_soon({"bookable_at": "garbage"}) is True
    assert _opens_soon({"bookable_at": "2000-01-01T10:00:00"}) is True  # naive, in the past
    assert _opens_soon({"bookable_at": "2999-01-01T10:00:00+0200"}) is False


if __name__ == "__main__":
    test_free_seat()
    test_book_schema()
    test_opens_soon_tolerates_naive_and_missing_dates()
    print("services helpers OK")
