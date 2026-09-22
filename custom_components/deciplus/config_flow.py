"""Config flow: club slug + credentials, zone picker, reauth."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_DOMAIN, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import DeciplusApiError, DeciplusAuthError, DeciplusClient, DeciplusError
from .const import (
    CONF_DAYS_AHEAD,
    CONF_MEMBER_ID,
    CONF_SCAN_MINUTES,
    CONF_ZONE_ID,
    CONF_ZONE_NAME,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DOMAIN): str,
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): PASSWORD,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD})
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES): NumberSelector(
            NumberSelectorConfig(min=2, max=60, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_DAYS_AHEAD, default=DEFAULT_DAYS_AHEAD): NumberSelector(
            NumberSelectorConfig(min=7, max=28, step=1, unit_of_measurement="d", mode=NumberSelectorMode.BOX)
        ),
    }
)


def _unique_id(domain: str, member_id: Any, zone_id: Any) -> str:
    return f"{domain}_{member_id}_{zone_id}"


def _slug(value: str) -> str:
    """Accept `myclub` or any member-area URL (`https://member-app.deciplus.pro/myclub/calendar`).

    Kept verbatim (no lowercasing): the vendor app uses the slug as typed in the URL.
    """
    value = value.strip().strip("/")
    if "://" in value or "." in value.split("/")[0]:  # a URL, with or without scheme
        value = urlparse(value if "://" in value else f"https://{value}").path
    return value.strip("/").split("/")[0]


class DeciplusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DeciplusOptionsFlow()

    _creds: dict[str, Any]
    _zones: list[dict[str, Any]]
    _me: dict[str, Any]
    _token: str

    async def _login(self, domain: str, email: str, password: str) -> dict[str, str]:
        """Fill _zones/_me/_token; return form errors."""
        client = DeciplusClient(async_get_clientsession(self.hass), domain, email, password)
        try:
            zones = await client.async_get_zones()
        except DeciplusApiError as err:
            if err.status == 404:
                return {CONF_DOMAIN: "invalid_domain"}
            return {"base": "cannot_connect"}
        except DeciplusError:
            return {"base": "cannot_connect"}
        # prefer sites shown online; fall back to all rather than an empty picker
        self._zones = [z for z in zones if z.get("isVisibleOnline", True)] or zones
        if not self._zones:
            return {CONF_DOMAIN: "invalid_domain"}
        try:
            await client.async_login()
            self._me = await client.async_get_me()
            self._token = client.token  # after /me: it may have rotated the token
        except DeciplusAuthError:
            return {"base": "invalid_auth"}
        except DeciplusError:
            return {"base": "cannot_connect"}
        if not isinstance(self._me, dict) or "id" not in self._me:  # unexpected /me payload
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_DOMAIN] = _slug(user_input[CONF_DOMAIN])
            if not user_input[CONF_DOMAIN]:  # e.g. the member-app root URL, without a club
                errors = {CONF_DOMAIN: "invalid_domain"}
            else:
                errors = await self._login(
                    user_input[CONF_DOMAIN], user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
            if not errors:
                self._creds = user_input
                if len(self._zones) == 1:
                    return await self._create(self._zones[0])
                return await self.async_step_zone()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the club/site when the domain has several."""
        if user_input is not None:
            zone = next((z for z in self._zones if str(z["id"]) == user_input[CONF_ZONE_ID]), None)
            if zone is not None:  # a StopIteration here would kill the flow ("Unknown error")
                return await self._create(zone)
        options = [
            SelectOptionDict(
                value=str(z["id"]),
                label=" – ".join(filter(None, (z.get("clubName"), z.get("city")))),
            )
            for z in self._zones
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE_ID): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(
            step_id="zone",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_ZONE_ID: str(self._me.get("zone", ""))}
            ),
        )

    async def _create(self, zone: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(
            _unique_id(self._creds[CONF_DOMAIN], self._me["id"], zone["id"])
        )
        self._abort_if_unique_id_configured()
        name = zone.get("clubName") or self._creds[CONF_DOMAIN]
        return self.async_create_entry(
            title=name,
            data={
                **self._creds,
                CONF_ZONE_ID: zone["id"],
                CONF_ZONE_NAME: name,
                CONF_MEMBER_ID: self._me["id"],
                CONF_ACCESS_TOKEN: self._token,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._login(
                entry.data[CONF_DOMAIN], entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if CONF_DOMAIN in errors:  # no domain field on this form: surface it anyway
                errors = {"base": "cannot_connect"}
            if not errors:
                await self.async_set_unique_id(
                    _unique_id(entry.data[CONF_DOMAIN], self._me["id"], entry.data[CONF_ZONE_ID])
                )
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: self._token,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                CONF_EMAIL: entry.data[CONF_EMAIL],
                CONF_DOMAIN: entry.data[CONF_DOMAIN],
            },
        )


class DeciplusOptionsFlow(OptionsFlow):
    """Poll interval and sessions horizon; the entry reloads on save."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={k: int(v) for k, v in user_input.items()})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, self.config_entry.options),
        )
