"""Client for the Deciplus members API, mirroring the member-app frontend calls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Any

import aiohttp

from .const import API_BASE, GLOBAL, HEADERS, MEMBERS, PUBLIC

# API `message` → rule code, for responses without `data.rules` (frontend constants.js).
# The codes the integration branches on, plus the booking refusals a user is likely to see.
MESSAGE_CODES = {
    "This booking is complete": "BOOKING_COMPLETE",
    "This place is not available": "PLACE_NOT_AVAILABLE",
    "This booking is not available yet": "BOOKING_NOT_AVAILABLE_AFTER",
    "This booking is no longer available": "BOOKING_NOT_AVAILABLE_BEFORE",
    "This member is already registered to this booking": "MEMBER_HAS_REGISTERED",
    "This member is already registered to the waiting list of this booking": "MEMBER_HAS_REGISTERED_ON_WAITING_LIST",
    "Members cannot add member to booking queue": "CANNOT_ADD_TO_BOOKING_QUEUE",
}


class DeciplusError(Exception):
    """Network or server error."""


class DeciplusAuthError(DeciplusError):
    """Credentials or token rejected."""


class DeciplusApiError(DeciplusError):
    """4xx/5xx with a business error (rule code and/or message)."""

    def __init__(self, status: int, code: str | None, message: str) -> None:
        super().__init__(f"HTTP {status} {code or ''} {message}".strip())
        self.status = status
        self.code = code
        self.message = message


class DeciplusClient:
    """Logs in with club credentials and keeps the club-scoped token fresh."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        domain: str,
        email: str,
        password: str,
        token: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self.domain = domain
        self._email = email
        self._password = password
        self.token = token
        self._on_token = on_token
        self._login_lock = asyncio.Lock()  # gather() must not trigger parallel logins

    # ---- transport -------------------------------------------------------

    async def _raw(
        self, method: str, path: str, *, params=None, json=None, headers=None
    ) -> tuple[int, Any, str | None]:
        """Returns (status, json body or None, rotated token from the response header)."""
        try:
            async with (
                asyncio.timeout(15),
                self._session.request(
                    method,
                    f"{API_BASE}{path}",
                    params=params,
                    json=json,
                    headers={**HEADERS, **(headers or {})},
                ) as resp,
            ):
                try:
                    body = await resp.json(content_type=None)
                except ValueError:  # empty or HTML body: status is what matters
                    body = None
                # resp.headers is case-insensitive; a plain dict() would not be
                return resp.status, body, resp.headers.get("x-access-token")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DeciplusError(str(err)) from err

    def _set_token(self, token: str) -> None:
        if token and token != self.token:
            self.token = token
            if self._on_token:
                self._on_token(token)

    async def async_login(self, stale: str | None = None) -> str:
        """Club login first, global (Xplor) account as fallback. Returns the club token.

        `stale` is the token that just got a 401: if a concurrent request already
        replaced it while we waited for the lock, reuse that instead of logging in again.
        """
        async with self._login_lock:
            if self.token and self.token != stale:
                return self.token
            status, body, _ = await self._raw(
                "POST",
                f"{MEMBERS}/authenticate",
                json={"login": self._email, "password": self._password, "domain": self.domain},
            )
            data = body if isinstance(body, dict) else {}
            token = data.get("token") if status < 400 else None
            # a clear rejection is 400/401 or a JSON error body; a bare 403/404/HTML page
            # (outage, WAF) must not be read as bad credentials and trigger a reauth
            rejected = status in (400, 401) or (status >= 400 and bool(data))
            if not token:
                status, body, _ = await self._raw(
                    "POST",
                    f"{GLOBAL}/authenticate",
                    json={
                        "email": self._email,
                        "password": self._password,
                        "domain": self.domain,
                        "limitToDomain": True,
                    },
                )
                data = body if isinstance(body, dict) else {}
                clubs = (data.get("tokens") or {}).get("clubs") or {}
                token = (clubs.get(self.domain) or [{}])[0].get("token") if status < 400 else None
                rejected = rejected or status in (400, 401) or (status >= 400 and bool(data))
            if not token:
                if not rejected:  # outage or unexpected payload, not bad credentials: retry next poll
                    raise DeciplusError(f"Login HTTP {status}")
                raise DeciplusAuthError("Login rejected")
            self._set_token(token)
            return token

    async def _request(self, method: str, path: str, *, params=None, json=None) -> Any:
        if not self.token:
            await self.async_login()
        for attempt in (1, 2):
            used = self.token
            status, body, rotated = await self._raw(
                method, path, params=params, json=json, headers={"x-access-token": used}
            )
            data = body if isinstance(body, dict) else {}
            # 401 = token; 403 is also used for business refusals (rights), which carry
            # a JSON body — only a bare 403 means the token itself is rejected
            token_rejected = status == 401 or (status == 403 and not data)
            if token_rejected:
                if attempt == 2:
                    if status == 401:
                        raise DeciplusAuthError(f"HTTP {status}")
                    # a fresh login just succeeded, so credentials are fine: this 403 is
                    # not about the token (rights, WAF) and a reauth would not help
                    raise DeciplusApiError(status, None, "Forbidden")
                await self.async_login(stale=used)  # expired: re-login once and retry
                continue
            if rotated:
                self._set_token(rotated)
            if status >= 400:
                rules = (data.get("data") or {}).get("rules") or []
                message = data.get("message") or ""
                # rules may be absent: the frontend then matches the message text
                code = rules[0].get("code") if rules else MESSAGE_CODES.get(message)
                raise DeciplusApiError(status, code, message)
            return body
        raise DeciplusError("unreachable")

    # ---- public (no token) -------------------------------------------------

    async def async_get_zones(self) -> list[dict[str, Any]]:
        """Clubs/sites of the domain. Raises DeciplusApiError(404) for an unknown slug."""
        status, body, _ = await self._raw("GET", f"{PUBLIC}/{self.domain}/zones")
        if status >= 400:
            raise DeciplusApiError(status, None, "zones")
        if not isinstance(body, list):
            raise DeciplusError("Unexpected zones payload")
        return body

    # ---- members -----------------------------------------------------------

    async def async_get_me(self) -> dict[str, Any]:
        return await self._request("GET", f"{MEMBERS}/me", params={"domain": self.domain})

    async def async_get_upcoming(self) -> dict[str, Any]:
        """Bookings and waiting-list registrations, all zones."""
        return await self._request("GET", f"{MEMBERS}/bookings/upcoming")

    async def async_get_sessions(
        self, zone_id: int, from_: date, to: date
    ) -> list[dict[str, Any]]:
        """Sessions of a zone in the date range, with the member's status."""
        return await self._request(
            "GET",
            f"{MEMBERS}/sessions",
            params={"zoneId": zone_id, "from": from_.isoformat(), "to": to.isoformat()},
        )

    async def async_get_resources(self, zone_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", f"{MEMBERS}/resources", params={"zoneId": zone_id})

    async def async_get_session(self, session_id: int) -> dict[str, Any]:
        data = await self._request("GET", f"{MEMBERS}/sessions/{session_id}")
        return (data or {})["booking"]

    async def async_book(
        self, session_id: int, *, place_id: int | None = None, guests: int = 0
    ) -> None:
        """Book a session; `{}` guests are anonymous invitations, `placeId` a seat-map seat."""
        body: dict[str, Any] = {"invitedMembers": [{} for _ in range(guests)]}
        if place_id is not None:
            body["placeId"] = place_id
        data = await self._request("POST", f"{MEMBERS}/booking/{session_id}/addMember", json=body)
        if ((data or {}).get("booking") or {}).get("bookingState") != "init":
            raise DeciplusApiError(200, None, "Unexpected booking state")

    async def async_join_queue(self, session_id: int) -> int | None:
        """Register on the waiting list; returns the position."""
        data = await self._request("POST", f"{MEMBERS}/booking/{session_id}/addMemberInQueue")
        waiting = ((data or {}).get("booking") or {}).get("awaitingMembers") or []
        return waiting[-1].get("order") if waiting else None

    async def async_cancel(self, session_id: int) -> None:
        """Cancel a booking or leave a waiting list (same endpoint)."""
        data = await self._request("DELETE", f"{MEMBERS}/booking/{session_id}/cancelMember")
        # the web app treats `messages[0]` as the confirmation; a 200 with an empty list
        # means nothing was cancelled
        if isinstance(data, dict) and "messages" in data and not data["messages"]:
            raise DeciplusApiError(200, None, "Cancellation not confirmed")
