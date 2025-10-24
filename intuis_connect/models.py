from dataclasses import dataclass
from typing import Literal, List, Dict, Optional

Preset = Literal["comfort", "eco", "hg", "away", "schedule"]
HVAC = Literal["off", "heat"]

@dataclass
class RoomDevice:
    id: str
    name: str
    area_id: Optional[str]
    current_temp: float
    target_temp: float
    hvac_mode: HVAC
    preset_mode: Optional[Preset]
    heating: bool
    window_open: Optional[bool] = None
    presence: Optional[bool] = None
    consumption_kwh: Optional[float] = None
    schedule_name: Optional[str] = None

@dataclass
class TimeSlot:
    day: int              # 0=Mon ... 6=Sun
    start: int            # minutes since midnight
    end: int
    preset: Preset

@dataclass
class Zone:
    id: str
    name: str
    rooms: List[str]
    default_preset: Preset = "schedule"

@dataclass
class Timetable:
    name: str
    slots_by_zone: Dict[str, List[TimeSlot]]
