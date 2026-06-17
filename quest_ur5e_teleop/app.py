from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .controller import TeleopController


def create_app(config: AppConfig, controller: TeleopController, project_root: Path) -> FastAPI:
    app = FastAPI(title="Quest UR5e Teleoperation Gateway")
    app.state.config = config
    app.state.controller = controller

    @app.on_event("startup")
    async def _startup() -> None:
        controller.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        controller.stop()

    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(controller.status())

    @app.post("/api/enable")
    async def enable() -> JSONResponse:
        controller.enable()
        return JSONResponse(controller.status())

    @app.post("/api/disable")
    async def disable() -> JSONResponse:
        controller.disable()
        return JSONResponse(controller.status())

    @app.post("/api/calibrate")
    async def calibrate() -> JSONResponse:
        if not controller.calibrate():
            raise HTTPException(status_code=409, detail=controller.status().get("last_error"))
        return JSONResponse(controller.status())

    @app.post("/api/reset-calibration")
    async def reset_calibration() -> JSONResponse:
        controller.reset_calibration()
        return JSONResponse(controller.status())

    @app.post("/api/recording/start")
    async def start_recording(payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
        try:
            controller.start_recording(task=payload.get("task"))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(controller.status())

    @app.post("/api/recording/stop")
    async def stop_recording(payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
        try:
            controller.stop_recording(success=bool(payload.get("success", True)))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(controller.status())

    @app.post("/api/recording/discard")
    async def discard_recording() -> JSONResponse:
        try:
            controller.discard_recording()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(controller.status())

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        controller.client_connected()
        await websocket.send_json({"type": "hello", "status": controller.status()})
        frame_counter = 0
        try:
            while True:
                message: dict[str, Any] = await websocket.receive_json()
                msg_type = message.get("type")
                if msg_type == "pose":
                    controller.update_pose(message)
                    if message.get("calibrate"):
                        controller.calibrate()
                elif msg_type == "control":
                    action = message.get("action")
                    try:
                        if action == "enable":
                            controller.enable()
                        elif action == "disable":
                            controller.disable()
                        elif action == "calibrate":
                            controller.calibrate()
                        elif action == "reset-calibration":
                            controller.reset_calibration()
                        elif action == "start-recording":
                            controller.start_recording(task=message.get("task"))
                        elif action == "stop-recording":
                            controller.stop_recording(success=bool(message.get("success", True)))
                        elif action == "discard-recording":
                            controller.discard_recording()
                        else:
                            await websocket.send_json({"type": "error", "message": f"Unknown action {action!r}"})
                            continue
                    except RuntimeError as exc:
                        await websocket.send_json({"type": "error", "message": str(exc), "status": controller.status()})
                        continue
                else:
                    await websocket.send_json({"type": "error", "message": f"Unknown message type {msg_type!r}"})
                    continue

                frame_counter += 1
                if frame_counter % 10 == 0 or msg_type == "control":
                    await websocket.send_json({"type": "status", "status": controller.status()})
        except WebSocketDisconnect:
            pass
        finally:
            controller.client_disconnected()

    static_dir = (project_root / config.server.static_dir).resolve()
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
