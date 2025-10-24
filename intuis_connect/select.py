from __future__ import annotations

from homeassistant.components.select import SelectEntity
from .const import DOMAIN
from .coordinator import IntuisCoordinator
from .api import IntuisApi

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    api: IntuisApi = data.get("api")
    coordinator: IntuisCoordinator = data.get("coordinator")
    if not api or not coordinator:
        return
    await coordinator.async_config_entry_first_refresh()

    home = coordinator.data.get("home", {})
    home_id = coordinator.data.get("home_id")
    schedules = home.get("therm_schedules") or home.get("schedules") or []
    async_add_entities([IntuisScheduleSelect(api, coordinator, home_id, schedules)], True)

class IntuisScheduleSelect(SelectEntity):
    def __init__(self, api, coordinator, home_id, schedules):
        self._api = api
        self._coordinator = coordinator
        self._home_id = home_id
        self._schedules = schedules
        self._attr_name = "Intuis Active Schedule"
        self._options = [s.get("name") for s in schedules if isinstance(s, dict)]
        self._ids = [s.get("id") for s in schedules if isinstance(s, dict)]

    @property
    def options(self):
        return self._options

    @property
    def current_option(self):
        for s in self._schedules:
            if s.get("selected"):
                return s.get("name")
        return None

    async def async_select_option(self, option: str):
        if option in self._options:
            idx = self._options.index(option)
            schedule_id = self._ids[idx]
            await self._api.switch_home_schedule(self._home_id, schedule_id)
            await self._coordinator.async_request_refresh()
