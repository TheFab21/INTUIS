from __future__ import annotations
from typing import Optional
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .coordinator import IntuisCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntuisCoordinator = hass.data[DOMAIN][entry.entry_id]
    rooms = coordinator.data.get("rooms", [])
    entities = []
    for room in rooms:
        if room.get("window_open") is not None:
            entities.append(IntuisRoomWindowSensor(coordinator, room))
        if room.get("presence") is not None:
            entities.append(IntuisRoomPresenceSensor(coordinator, room))
    async_add_entities(entities, True)

class IntuisRoomWindowSensor(CoordinatorEntity[IntuisCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(self, coordinator, room: dict):
        super().__init__(coordinator)
        self._room = room
        self._attr_name = f"{room.get('name')} Window Open"
        self._attr_unique_id = f"{DOMAIN}_room_{room.get('id')}_window"

    @property
    def is_on(self) -> Optional[bool]:
        return bool(self._room.get("window_open"))

class IntuisRoomPresenceSensor(CoordinatorEntity[IntuisCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator, room: dict):
        super().__init__(coordinator)
        self._room = room
        self._attr_name = f"{room.get('name')} Presence"
        self._attr_unique_id = f"{DOMAIN}_room_{room.get('id')}_presence"

    @property
    def is_on(self) -> Optional[bool]:
        return bool(self._room.get("presence"))
