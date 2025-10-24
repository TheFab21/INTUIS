from __future__ import annotations

from typing import Any, Dict
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, INTUIS_CLIENT_ID, INTUIS_CLIENT_SECRET, INTUIS_SCOPE, INTUIS_USER_PREFIX
from .api import IntuisApi

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required("email"): str, vol.Required("password"): str})

class IntuisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            email = user_input["email"].strip()
            password = user_input["password"]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            api = IntuisApi(session, username=email, password=password,
                            client_id=INTUIS_CLIENT_ID, client_secret=INTUIS_CLIENT_SECRET,
                            scope=INTUIS_SCOPE, user_prefix=INTUIS_USER_PREFIX)
            try:
                await api.async_authenticate()
                await api.get_home_id()
            except Exception as exc:
                _LOGGER.exception("Login failed: %s", exc)
                msg = str(exc).lower()
                errors["base"] = "invalid_auth" if any(k in msg for k in ("invalid_grant","auth","401","403")) else "cannot_connect"
            else:
                return self.async_create_entry(title=f"Intuis ({email})",
                                               data={"username": email, "password": password},
                                               options={"update_interval": 90, "measure_scale": "1day"})
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)
