"""Client transport checks with a stub aiohttp session: re-login, rotation, error codes."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys

from multidict import CIMultiDict

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.deciplus.api import (  # noqa: E402
    DeciplusApiError,
    DeciplusAuthError,
    DeciplusClient,
    DeciplusError,
)


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status = status
        self._body = body
        self.headers = CIMultiDict(headers or {})

    async def json(self, content_type=None):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class StubSession:
    """Maps (method, path) → list of responses, consumed in order; records calls."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []
        self.bodies = []

    @asynccontextmanager
    async def request(self, method, url, params=None, json=None, headers=None):
        path = url.split("api.deciplus.pro", 1)[1]
        self.calls.append((method, path, headers.get("x-access-token")))
        self.bodies.append(json)
        queue = self.routes[(method, path)]
        await asyncio.sleep(0)  # let concurrent requests interleave
        yield queue.pop(0) if len(queue) > 1 else queue[0]


LOGIN = ("POST", "/members/v1/authenticate")
ME = ("GET", "/members/v1/me")


def run(coro):
    return asyncio.run(coro)


def test_concurrent_401_logs_in_once():
    session = StubSession(
        {
            LOGIN: [Resp(200, {"token": "new"})],
            ME: [Resp(401), Resp(401), Resp(200, {"id": 1})],
        }
    )
    tokens = []
    client = DeciplusClient(session, "myclub", "e", "p", token="old", on_token=tokens.append)

    async def both():
        return await asyncio.gather(client.async_get_me(), client.async_get_me())

    assert run(both()) == [{"id": 1}, {"id": 1}]
    assert [c for c in session.calls if c[:2] == LOGIN] == [("POST", LOGIN[1], None)]
    assert tokens == ["new"]


def test_rotated_token_header_is_case_insensitive():
    session = StubSession({ME: [Resp(200, {"id": 1}, {"X-Access-Token": "rotated"})]})
    tokens = []
    client = DeciplusClient(session, "myclub", "e", "p", token="t", on_token=tokens.append)
    run(client.async_get_me())
    assert client.token == "rotated" and tokens == ["rotated"]


def test_error_code_from_rules_or_message():
    session = StubSession(
        {
            ("POST", "/members/v1/booking/7/addMember"): [
                Resp(400, {"message": "This booking is complete"}),
            ],
            ("POST", "/members/v1/booking/8/addMember"): [
                Resp(400, {"message": "x", "data": {"rules": [{"code": "MEMBER_HAVE_UNPAID"}]}}),
            ],
            ("POST", "/members/v1/booking/9/addMember"): [
                Resp(400, {"message": "This place is not available"}),
            ],
        }
    )
    client = DeciplusClient(session, "myclub", "e", "p", token="t")
    for sid, code in ((7, "BOOKING_COMPLETE"), (8, "MEMBER_HAVE_UNPAID"), (9, "PLACE_NOT_AVAILABLE")):
        try:
            run(client.async_book(sid))
        except DeciplusApiError as err:
            assert err.code == code
        else:
            raise AssertionError("expected DeciplusApiError")


def test_login_outage_is_not_auth_failure():
    global_login = ("POST", "/deciplus-members/v1/authenticate")
    for statuses, exc in (((503, 503), DeciplusError), ((401, 404), DeciplusAuthError)):
        session = StubSession({LOGIN: [Resp(statuses[0])], global_login: [Resp(statuses[1])]})
        client = DeciplusClient(session, "myclub", "e", "p")
        try:
            run(client.async_login())
        except DeciplusError as err:
            assert type(err) is exc, f"expected {exc.__name__}, got {type(err).__name__}"
        else:
            raise AssertionError(f"expected {exc.__name__}")
    # only 400/401 or a JSON error body prove bad credentials; a non-dict body (HTML from a
    # WAF, unexpected payload) or a bare 403 is an outage and must not trigger a reauth
    for club, glob in ((Resp(200, ["unexpected"]), Resp(404, [])), (Resp(403), Resp(403))):
        session = StubSession({LOGIN: [club], global_login: [glob]})
        try:
            run(DeciplusClient(session, "myclub", "e", "p").async_login())
        except DeciplusError as err:
            assert type(err) is DeciplusError, type(err).__name__
        else:
            raise AssertionError("expected DeciplusError")
    # a 403 carrying a JSON error body is a rejection
    session = StubSession({LOGIN: [Resp(403, {"message": "Bad credentials"})], global_login: [Resp(404)]})
    try:
        run(DeciplusClient(session, "myclub", "e", "p").async_login())
    except DeciplusAuthError:
        pass
    else:
        raise AssertionError("expected DeciplusAuthError")


def test_403_with_body_is_a_business_error_not_expiry():
    session = StubSession(
        {
            ("POST", "/members/v1/booking/9/addMember"): [
                Resp(403, {"message": "This member has unpaid", "data": {"rules": [{"code": "MEMBER_HAVE_UNPAID"}]}}),
            ],
            ME: [Resp(403), Resp(403)],
            LOGIN: [Resp(200, {"token": "new"})],
        }
    )
    client = DeciplusClient(session, "myclub", "e", "p", token="t")
    try:
        run(client.async_book(9))
    except DeciplusApiError as err:
        assert err.code == "MEMBER_HAVE_UNPAID"
    else:
        raise AssertionError("expected DeciplusApiError")
    assert not [c for c in session.calls if c[:2] == LOGIN], "business 403 must not re-login"
    # a bare 403 (no body) triggers one re-login; if it persists with a fresh token the
    # credentials are fine, so it is an API error (rights/WAF), not an auth failure
    try:
        run(client.async_get_me())
    except DeciplusApiError as err:
        assert err.status == 403
    else:
        raise AssertionError("expected DeciplusApiError")
    assert len([c for c in session.calls if c[:2] == LOGIN]) == 1


def test_cancel_requires_confirmation_when_messages_present():
    path = ("DELETE", "/members/v1/booking/5/cancelMember")
    for body, ok in (({"messages": ["Cancelled"]}, True), (None, True), ({"messages": []}, False)):
        client = DeciplusClient(StubSession({path: [Resp(200, body)]}), "myclub", "e", "p", token="t")
        try:
            run(client.async_cancel(5))
        except DeciplusApiError:
            assert not ok, body
        else:
            assert ok, body


def test_book_body_with_seat_and_guests():
    ok = Resp(200, {"booking": {"bookingState": "init"}})
    session = StubSession({("POST", "/members/v1/booking/5/addMember"): [ok]})
    client = DeciplusClient(session, "myclub", "e", "p", token="t")
    run(client.async_book(5))
    run(client.async_book(5, place_id=42, guests=2))
    assert session.bodies == [
        {"invitedMembers": []},
        {"invitedMembers": [{}, {}], "placeId": 42},
    ]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("api OK")
