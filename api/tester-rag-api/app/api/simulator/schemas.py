from pydantic import BaseModel


class SimulatorDevice(BaseModel):
    udid: str
    name: str
    state: str
    runtime: str


class SimulatorDeviceListResponse(BaseModel):
    devices: list[SimulatorDevice]


class TapRequest(BaseModel):
    x: float
    y: float


class WdaStatusResponse(BaseModel):
    state: str  # not_started | building | ready | failed
    error: str | None = None


class SimulatorUiElement(BaseModel):
    index: int
    type: str
    label: str | None = None
    x: float
    y: float
    width: float
    height: float


class SimulatorUiTreeResponse(BaseModel):
    root_width: float
    root_height: float
    elements: list[SimulatorUiElement]
