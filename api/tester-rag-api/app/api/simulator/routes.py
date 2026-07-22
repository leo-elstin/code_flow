import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.simulator.schemas import (
    SimulatorDevice,
    SimulatorDeviceListResponse,
    SimulatorUiElement,
    SimulatorUiTreeResponse,
    TapRequest,
    WdaStatusResponse,
)
from app.core.logging_config import get_logger
from app.services.simulator import hierarchy_parser, simctl, wda_bootstrap, wda_client
from app.services.simulator.hierarchy_parser import HierarchyParseError
from app.services.simulator.session_manager import session_manager
from app.services.simulator.simctl import SimctlError
from app.services.simulator.wda_bootstrap import WdaBootstrapError
from app.services.simulator.wda_client import WdaClientError

router = APIRouter()
logger = get_logger("api.simulator")


@router.get("/devices", response_model=SimulatorDeviceListResponse)
async def list_devices() -> SimulatorDeviceListResponse:
    try:
        devices = await asyncio.to_thread(simctl.list_devices)
    except SimctlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SimulatorDeviceListResponse(devices=[SimulatorDevice(**d) for d in devices])


@router.post("/devices/{udid}/boot")
async def boot_device(udid: str) -> dict:
    try:
        await asyncio.to_thread(simctl.boot_device, udid)
    except SimctlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/devices/{udid}/screenshot")
async def get_screenshot(udid: str) -> Response:
    try:
        png_bytes = await asyncio.to_thread(simctl.capture_screenshot, udid)
    except SimctlError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=png_bytes, media_type="image/png")


def _wda_status_response(udid: str) -> WdaStatusResponse:
    session = session_manager.get()
    if session is None or session.udid != udid:
        return WdaStatusResponse(state="not_started")
    return WdaStatusResponse(state=session.state, error=session.error)


@router.post("/devices/{udid}/wda/start", response_model=WdaStatusResponse)
async def start_wda(udid: str) -> WdaStatusResponse:
    try:
        session = await wda_bootstrap.start(udid)
    except WdaBootstrapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return WdaStatusResponse(state=session.state, error=session.error)


@router.get("/devices/{udid}/wda/status", response_model=WdaStatusResponse)
async def get_wda_status(udid: str) -> WdaStatusResponse:
    return _wda_status_response(udid)


@router.post("/devices/{udid}/wda/stop")
async def stop_wda(udid: str) -> dict:
    await wda_bootstrap.stop(udid)
    return {"ok": True}


def _require_ready_session(udid: str):
    session = session_manager.get()
    if session is None or session.udid != udid or session.state != "ready":
        raise HTTPException(
            status_code=409,
            detail="WebDriverAgent is not ready for this device — start it first",
        )
    return session


@router.get("/devices/{udid}/ui-tree", response_model=SimulatorUiTreeResponse)
async def get_ui_tree(udid: str) -> SimulatorUiTreeResponse:
    session = _require_ready_session(udid)
    try:
        if session.wda_session_id is None:
            session.wda_session_id = await wda_client.create_session()
        xml_str = await wda_client.get_source(session.wda_session_id)
        tree = hierarchy_parser.parse(xml_str)
    except (WdaClientError, HierarchyParseError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SimulatorUiTreeResponse(
        root_width=tree.root_width,
        root_height=tree.root_height,
        elements=[
            SimulatorUiElement(
                index=e.index, type=e.type, label=e.label,
                x=e.x, y=e.y, width=e.width, height=e.height,
            )
            for e in tree.elements
        ],
    )


@router.post("/devices/{udid}/tap")
async def tap_device(udid: str, body: TapRequest) -> dict:
    session = _require_ready_session(udid)
    try:
        if session.wda_session_id is None:
            session.wda_session_id = await wda_client.create_session()
        await wda_client.tap(session.wda_session_id, body.x, body.y)
    except (WdaClientError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}
