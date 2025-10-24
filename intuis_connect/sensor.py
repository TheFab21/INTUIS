from __future__ import annotations

from typing import Any, Optional
import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature, UnitOfEnergy
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import IntuisCoordinator

_LOGGER = logging.getLogger(__name__)

# ---------- Device helpers ----------
def device_info_gateway(home_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"gateway_{home_id}")},
        name="Intuis Gateway",
        manufacturer="Muller/Intuis",
        model="Gateway",
    )

def device_info_room(home_id: str, room_id: str, room_name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"room_{home_id}_{room_id}")},
        name=room_name,
        manufacturer="Muller/Intuis",
        model="Room",
        via_device=(DOMAIN, f"gateway_{home_id}"),
        suggested_area=room_name,
    )

# ---------- PLATFORM ENTRYPOINT ----------
async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Intuis sensors from a config entry."""
    data = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    coordinator: IntuisCoordinator = data.get("coordinator")
    if coordinator is None:
        _LOGGER.error("Coordinator not available; cannot create sensors")
        return

    # Ensure we have initial data
    await coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = []

    payload = coordinator.data or {}
    home = payload.get("home") or {}
    home_id = payload.get("home_id")
    status = payload.get("status") or {}
    configs = payload.get("configs") or {}
    measures = payload.get("measures") or {}

    rooms = home.get("rooms", []) if isinstance(home, dict) else []
    modules = home.get("modules", []) if isinstance(home, dict) else []
    therm_mode = home.get("therm_mode")

    # ----- Gateway device -----
    dev_info_gw = device_info_gateway(home_id or "home")

    # Energy (day)
    val = _extract_energy_day_value(measures)
    if isinstance(val, (int, float)):
        entities.append(IntuisEnergyHomeSensor(coordinator, unique=f"intuis_{home_id}_energy_today", value=float(val), device_info=dev_info_gw))

    # Global diagnostics on gateway
    entities.append(IntuisSimpleNumberSensor(coordinator, unique=f"intuis_{home_id}_rooms_count", name="Rooms Count", value=len(rooms), device_info=dev_info_gw))
    entities.append(IntuisSimpleNumberSensor(coordinator, unique=f"intuis_{home_id}_modules_count", name="Modules Count", value=len(modules), device_info=dev_info_gw))
    if isinstance(therm_mode, str):
        entities.append(IntuisSimpleTextSensor(coordinator, unique=f"intuis_{home_id}_therm_mode", name="Therm Mode", value=str(therm_mode), device_info=dev_info_gw))

    # ----- Per-room devices -----
    for room in rooms:
        room_id = str(room.get("id") or room.get("_id") or "")
        if not room_id:
            continue
        room_name = room.get("name") or f"Room {room_id}"
        dev_info_rm = device_info_room(home_id or "home", room_id, room_name)

        measured = _get_room_field(status, room_id, "therm_measured_temperature")
        if measured is None:
            measured = room.get("therm_measured_temperature")
        if isinstance(measured, (int, float)):
            entities.append(
                IntuisRoomTempSensor(
                    coordinator,
                    unique=f"intuis_{home_id}_{room_id}_measured",
                    name=f"{room_name} Measured",
                    value=float(measured),
                    device_info=dev_info_rm,
                )
            )

        setpoint = _get_room_field(status, room_id, "therm_setpoint_temperature")
        if setpoint is None:
            setpoint = _get_room_setpoint_from_configs(configs, room_id)
        if isinstance(setpoint, (int, float)):
            entities.append(
                IntuisRoomTempSensor(
                    coordinator,
                    unique=f"intuis_{home_id}_{room_id}_setpoint",
                    name=f"{room_name} Setpoint",
                    value=float(setpoint),
                    device_info=dev_info_rm,
                )
            )

        mode = _get_room_field(status, room_id, "therm_setpoint_mode")
        if isinstance(mode, str):
            entities.append(
                IntuisRoomTextSensor(
                    coordinator,
                    unique=f"intuis_{home_id}_{room_id}_mode",
                    name=f"{room_name} Mode",
                    value=mode,
                    device_info=dev_info_rm,
                )
            )

    async_add_entities(entities, True)


# ---------- Helpers ----------
def _extract_energy_day_value(measures: dict) -> Optional[float]:
    if not isinstance(measures, dict):
        return None
    arr = None
    if "measures" in measures and isinstance(measures["measures"], list):
        arr = measures["measures"]
    elif "body" in measures and isinstance(measures["body"], dict) and isinstance(measures["body"].get("measures"), list):
        arr = measures["body"]["measures"]
    if not arr:
        return None
    first = arr[0]
    for key in ("value", "energy", "kwh", "sum", "data"):
        v = first.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return float(v[0])
    return None


def _find_room_in_payload(payload: dict, room_id: str) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    body = payload.get("body")
    if isinstance(body, dict):
        home = body.get("home")
        if isinstance(home, dict):
            rooms = home.get("rooms")
            if isinstance(rooms, list):
                for r in rooms:
                    rid = str(r.get("id") or r.get("_id") or "")
                    if rid == room_id:
                        return r
    rooms = payload.get("rooms")
    if isinstance(rooms, list):
        for r in rooms:
            rid = str(r.get("id") or r.get("_id") or "")
            if rid == room_id:
                return r
    return None


def _get_room_field(payload: dict, room_id: str, field: str) -> Optional[Any]:
    room = _find_room_in_payload(payload, room_id)
    if isinstance(room, dict):
        v = room.get(field)
        if isinstance(v, (int, float, str, bool)):
            return v
    return None


def _get_room_setpoint_from_configs(configs: dict, room_id: str) -> Optional[float]:
    if not isinstance(configs, dict):
        return None
    body = configs.get("body")
    if not isinstance(body, dict):
        return None
    home = body.get("home")
    if not isinstance(home, dict):
        return None
    schedules = home.get("therm_schedules") or home.get("schedules")
    if not isinstance(schedules, list):
        return None
    selected = None
    for sch in schedules:
        if sch.get("selected") is True:
            selected = sch
            break
    if not selected:
        selected = schedules[0] if schedules else None
    if not isinstance(selected, dict):
        return None
    zones = selected.get("zones")
    if not isinstance(zones, list):
        return None
    for z in zones:
        rtemps = z.get("rooms_temp")
        if isinstance(rtemps, list):
            for rt in rtemps:
                if str(rt.get("room_id")) == str(room_id):
                    temp = rt.get("temp")
                    if isinstance(temp, (int, float)):
                        return float(temp)
    rooms = selected.get("rooms")
    if isinstance(rooms, list):
        for r in rooms:
            if str(r.get("id")) == str(room_id):
                t = r.get("therm_setpoint_temperature")
                if isinstance(t, (int, float)):
                    return float(t)
    return None


# ---------- Base & Entities ----------
class _BaseIntuisEntity(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IntuisCoordinator, unique_suffix: str, *, name: str, device_info: DeviceInfo):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_suffix
        self._dev_info = device_info

    @property
    def device_info(self):
        return self._dev_info


class IntuisEnergyHomeSensor(_BaseIntuisEntity):
    def __init__(self, coordinator, unique: str, value: float, device_info: DeviceInfo):
        super().__init__(coordinator, unique_suffix=unique, name="Energy Today", device_info=device_info)
        self._value = float(value)
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        return self._value


class IntuisSimpleNumberSensor(_BaseIntuisEntity):
    def __init__(self, coordinator, unique: str, name: str, value: float | int, device_info: DeviceInfo):
        super().__init__(coordinator, unique_suffix=unique, name=name, device_info=device_info)
        self._value = value
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self._value


class IntuisSimpleTextSensor(_BaseIntuisEntity):
    def __init__(self, coordinator, unique: str, name: str, value: str, device_info: DeviceInfo):
        super().__init__(coordinator, unique_suffix=unique, name=name, device_info=device_info)
        self._value = value
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self._value


class IntuisRoomTempSensor(_BaseIntuisEntity):
    def __init__(self, coordinator, unique: str, name: str, value: float, device_info: DeviceInfo):
        super().__init__(coordinator, unique_suffix=unique, name=name, device_info=device_info)
        self._value = float(value)
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._value


class IntuisRoomTextSensor(_BaseIntuisEntity):
    def __init__(self, coordinator, unique: str, name: str, value: str, device_info: DeviceInfo):
        super().__init__(coordinator, unique_suffix=unique, name=name, device_info=device_info)
        self._value = str(value)

    @property
    def native_value(self):
        return self._value
