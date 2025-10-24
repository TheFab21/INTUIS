from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    INTUIS_CLIENT_ID,
    INTUIS_CLIENT_SECRET,
    INTUIS_SCOPE,
    INTUIS_USER_PREFIX,
)
from .api import IntuisApi
from .coordinator import IntuisCoordinator

PLATFORMS: Final = ["sensor", "climate"]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Intuis Connect from a config entry."""
    session = async_get_clientsession(hass)

    username: str = entry.data["username"]
    password: str = entry.data["password"]

    api = IntuisApi(
        session,
        username=username,
        password=password,
        client_id=INTUIS_CLIENT_ID,
        client_secret=INTUIS_CLIENT_SECRET,
        scope=INTUIS_SCOPE,              # "read_muller write_muller"
        user_prefix=INTUIS_USER_PREFIX,  # "muller"
    )

    coordinator = IntuisCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.entry_id in hass.data.get(DOMAIN, {}):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
