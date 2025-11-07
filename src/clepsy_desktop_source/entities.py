from pydantic import BaseModel, ConfigDict
from PIL import Image
from datetime import datetime
from typing import Literal, Optional
from datetime import timedelta
from uuid import UUID, uuid4


class Bbox(BaseModel):
    left: int
    top: int
    width: int
    height: int


class WindowInfo(BaseModel):
    title: str
    app_name: str
    bbox: Bbox
    monitor_names: list[str]


class DesktopCheck(BaseModel):
    id: UUID = uuid4()
    screenshot: Image.Image  # Required field
    active_window: WindowInfo
    timestamp: datetime
    time_since_last_user_activity: timedelta
    bbox: Bbox

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AfkStart(BaseModel):
    id: UUID = uuid4()
    timestamp: datetime
    time_since_last_user_activity: timedelta


class AppState:
    def __init__(self):
        self.last_heartbeat_timestamp: Optional[datetime] = None
        self.last_heartbeat_status: Literal["Success", "Fail"] | None = None
        self.last_data_sent_timestamp: Optional[datetime] = None
        self.last_data_sent_status: Literal["Success", "Fail"] | None = None
