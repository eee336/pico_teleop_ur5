from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config
from .controller import TeleopController
from .robot import RTDERobot, SimRobot


def _default_config_path(project_root: Path) -> Path | None:
    preferred = project_root / "config" / "teleop.yaml"
    if preferred.exists():
        return preferred
    example = project_root / "config" / "teleop.example.yaml"
    return example if example.exists() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Quest to UR5e teleoperation gateway.")
    parser.add_argument("--config", type=Path, default=None, help="Path to teleop YAML configuration.")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8080, help="HTTP bind port.")
    parser.add_argument("--real", action="store_true", help="Connect to the configured UR5e and send RTDE servoL commands.")
    parser.add_argument("--certfile", type=Path, default=None, help="TLS certificate for HTTPS/WebXR over LAN.")
    parser.add_argument("--keyfile", type=Path, default=None, help="TLS private key for HTTPS/WebXR over LAN.")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return parser


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    args = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config_path = args.config or _default_config_path(project_root)
    config = load_config(config_path)

    if args.real and not config.robot.enabled:
        raise SystemExit(
            "Refusing real robot control because config robot.enabled is false. "
            "Copy config/teleop.example.yaml to config/teleop.yaml, review all limits, set robot.enabled: true, then run again."
        )

    robot = RTDERobot(config) if args.real else SimRobot(config)
    controller = TeleopController(config, robot, real_robot=args.real)
    app = create_app(config, controller, project_root)

    scheme = "https" if args.certfile and args.keyfile else "http"
    logging.getLogger(__name__).info("Serving Quest UI at %s://%s:%s", scheme, args.host, args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        ssl_certfile=str(args.certfile) if args.certfile else None,
        ssl_keyfile=str(args.keyfile) if args.keyfile else None,
    )


if __name__ == "__main__":
    main()

