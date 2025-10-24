from __future__ import annotations
from typing import Any, Optional
import logging

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACMode,
    ClimateEntityFeature,
    PRESET_AWAY,
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import IntuisCoordinator

_LOGGER = logging.getLogger(__name__)

PRESET_HOME = "home"
PRESET_HG = "hg"


def device_info_room(home_id: str, room_id: str, room_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"room_{home_id}_{room_id}")},
        name=room_name,
        manufacturer="Muller/Intuis",
        model="Room",
        via_device=(DOMAIN, f"gateway_{home_id}"),
        suggested_area=room_name,
    )


def _find_room_in_payload(payload: dict, room_id: str) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    body = payload.get("body")
    if isinstance(body, dict):
        home = body.get("home")
        if isinstance(home, dict):
            for r in home.get("rooms", []):
                if str(r.get("id")) == str(room_id):
                    return r
    for r in payload.get("rooms", []) or []:
        if str(r.get("id")) == str(room_id):
            return r
    return None


def _get_room_setpoint_from_configs(configs: dict, room_id: str) -> Optional[float]:
    if not isinstance(configs, dict):
        return None
    home = configs.get("body", {}).get("home", {})
    schedules = home.get("therm_schedules") or home.get("schedules") or []
    selected = next((s for s in schedules if s.get("selected")), (schedules[0] if schedules else None))
    if not isinstance(selected, dict):
        return None
    for zone in selected.get("zones", []):
        for rt in zone.get("rooms_temp", []):
            if str(rt.get("room_id")) == str(room_id):
                try:
                    return float(rt.get("temp"))
                except (TypeError, ValueError):
                    pass
    for r in selected.get("rooms", []):
        if str(r.get("id")) == str(room_id):
            try:
                return float(r.get("therm_setpoint_temperature"))
            except (TypeError, ValueError):
                pass
    return None


def _extract_home_mode_from_status(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    body = payload.get("body") or {}
    home = (body.get("home") or payload.get("home") or {})
    mode = home.get("therm_mode")
    if isinstance(mode, str):
        m = mode.lower()
        if m in {"home", "away", "hg"}:
            return m
    return None


def room_mode_to_hvac_and_preset(room_mode: str, home_mode: str | None) -> tuple[str, str | None]:
    rm = (room_mode or "").lower()
    hm = (home_mode or "").lower() if home_mode else None

    if rm == "off":
        return HVACMode.OFF, None
    if rm == "manual":
        return HVACMode.HEAT, None
    if rm == "away":
        return HVACMode.AUTO, PRESET_AWAY
    if rm == "hg":
        return HVACMode.HEAT, PRESET_HG
    if rm == "home":
        if hm == "hg":
            return HVACMode.HEAT, PRESET_HG
        if hm == "away":
            return HVACMode.AUTO, PRESET_AWAY
        return HVACMode.AUTO, PRESET_HOME

    return HVACMode.AUTO, None


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    coordinator: IntuisCoordinator = data.get("coordinator")
    if coordinator is None:
        _LOGGER.error("Coordinator not available; cannot create climates")
        return
    await coordinator.async_config_entry_first_refresh()

    payload = coordinator.data or {}
    home = payload.get("home") or {}
    home_id = payload.get("home_id")
    rooms = home.get("rooms", []) if isinstance(home, dict) else []

    entities: list[ClimateEntity] = []
    for room in rooms:
        room_id = str(room.get("id") or "")
        if not room_id:
            continue
        room_name = room.get("name") or f"Room {room_id}"
        entities.append(IntuisRoomClimate(coordinator, home_id, room_id, room_name))
    async_add_entities(entities, True)


class IntuisRoomClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = 7.0
    _attr_max_temp = 30.0
    _attr_preset_modes = [PRESET_HOME, PRESET_AWAY, PRESET_HG]

    def __init__(self, coordinator: IntuisCoordinator, home_id: str, room_id: str, room_name: str):
        super().__init__(coordinator)
        self._home_id = str(home_id)
        self._room_id = str(room_id)
        self._room_name = room_name
        self._attr_name = f"{room_name} Climate"
        self._attr_unique_id = f"intuis_{home_id}_{room_id}_climate"
        self._preset_mode: Optional[str] = None

    @property
    def device_info(self) -> DeviceInfo:
        return device_info_room(self._home_id, self._room_id, self._room_name)

    @property
    def current_temperature(self) -> Optional[float]:
        status = (self.coordinator.data or {}).get("status") or {}
        room = _find_room_in_payload(status, self._room_id)
        v = room.get("therm_measured_temperature") if isinstance(room, dict) else None
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def target_temperature(self) -> Optional[float]:
        status = (self.coordinator.data or {}).get("status") or {}
        room = _find_room_in_payload(status, self._room_id)
        v = room.get("therm_setpoint_temperature") if isinstance(room, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
        configs = (self.coordinator.data or {}).get("configs") or {}
        return _get_room_setpoint_from_configs(configs, self._room_id)

    @property
    def hvac_mode(self) -> HVACMode:
        status = (self.coordinator.data or {}).get("status") or {}
        room = _find_room_in_payload(status, self._room_id)
        room_mode = (room.get("therm_setpoint_mode") or "").lower() if isinstance(room, dict) else ""
        home_mode = _extract_home_mode_from_status(status)
        hvac, preset = room_mode_to_hvac_and_preset(room_mode, home_mode)
        self._preset_mode = preset
        return hvac

    @property
    def preset_mode(self) -> Optional[str]:
        return self._preset_mode

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        api = self.coordinator.api  # type: ignore[attr-defined]
        if hvac_mode == HVACMode.OFF:
            await api.set_room_mode(self._home_id, self._room_id, "off")
        elif hvac_mode == HVACMode.HEAT:
            await api.set_room_mode(self._home_id, self._room_id, "manual")
        else:
            await api.set_room_mode(self._home_id, self._room_id, "home")
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (ATTR_TEMPERATURE not in kwargs) or (kwargs[ATTR_TEMPERATURE] is None):
            return
        temp = float(kwargs[ATTR_TEMPERATURE])
        api = self.coordinator.api  # type: ignore[attr-defined]
        # manuel permanent par défaut; pour un manuel temporaire utilise climate.set_temperature avec 'duration' en service (optionnel).
        await api.set_room_mode(self._home_id, self._room_id, "manual")
        await api.set_room_setpoint(self._home_id, self._room_id, temp)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        api = self.coordinator.api  # type: ignore[attr-defined]
        if preset_mode == PRESET_HOME:
            await api.set_room_mode(self._home_id, self._room_id, "home")
        elif preset_mode == PRESET_AWAY:
            await api.set_room_mode(self._home_id, self._room_id, "away")
        elif preset_mode == PRESET_HG:
            await api.set_room_mode(self._home_id, self._room_id, "hg")
        await self.coordinator.async_request_refresh()
