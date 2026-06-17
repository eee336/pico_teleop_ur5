from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from quest_ur5e_teleop.recording import ACTION_NAMES, DELTA_ACTION_NAMES, STATE_NAMES


@dataclass
class RawEpisode:
    path: Path
    metadata: dict[str, Any]

    @property
    def frames_path(self) -> Path:
        return self.path / "frames.jsonl"

    @property
    def task(self) -> str:
        return str(self.metadata.get("task") or "teleoperate the UR5e safely")


def iter_raw_episodes(raw_root: Path, *, only_success: bool = True) -> list[RawEpisode]:
    episodes: list[RawEpisode] = []
    for metadata_path in sorted(raw_root.rglob("metadata.json")):
        episode_dir = metadata_path.parent
        frames_path = episode_dir / "frames.jsonl"
        if not frames_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if only_success and metadata.get("success") is False:
            continue
        if int(metadata.get("frame_count") or 0) <= 0:
            continue
        episodes.append(RawEpisode(episode_dir, metadata))
    return episodes


def read_frames(episode: RawEpisode) -> Iterable[dict[str, Any]]:
    with episode.frames_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _first_frame(episodes: list[RawEpisode]) -> dict[str, Any]:
    for episode in episodes:
        for frame in read_frames(episode):
            return frame
    raise ValueError("No frames found")


def _import_lerobot_dataset():
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
    except ImportError as exc:
        raise RuntimeError(
            "LeRobot is not installed. Install it with `python -m pip install -e '.[lerobot]'` "
            "or follow the official LeRobot install instructions."
        ) from exc
    return LeRobotDataset


def build_features(first_frame: dict[str, Any], *, action_mode: str, allow_no_images: bool) -> dict[str, Any]:
    action_names = ACTION_NAMES if action_mode == "absolute_tcp" else DELTA_ACTION_NAMES
    features: dict[str, Any] = {
        "observation.state": {
            "dtype": "float32",
            "shape": (len(STATE_NAMES),),
            "names": STATE_NAMES,
        },
        "action": {
            "dtype": "float32",
            "shape": (len(action_names),),
            "names": action_names,
        },
    }

    images = first_frame.get("images") or {}
    if not images and not allow_no_images:
        raise ValueError(
            "No images were found in the raw episodes. SmolVLA training expects visual observations; "
            "enable at least one recording camera or pass --allow-no-images for a state-only export."
        )

    for camera_name, image_meta in images.items():
        width = int(image_meta["width"])
        height = int(image_meta["height"])
        features[f"observation.images.{camera_name}"] = {
            "dtype": "image",
            "shape": (height, width, 3),
            "names": ["height", "width", "channel"],
        }

    return features


def _create_dataset(repo_id: str, root: Path | None, fps: int, features: dict[str, Any], *, use_videos: bool):
    LeRobotDataset = _import_lerobot_dataset()
    kwargs: dict[str, Any] = {
        "repo_id": repo_id,
        "fps": fps,
        "features": features,
        "use_videos": use_videos,
    }
    if root is not None:
        kwargs["root"] = root

    try:
        return LeRobotDataset.create(**kwargs)
    except TypeError:
        kwargs.pop("use_videos", None)
        return LeRobotDataset.create(**kwargs)


def export_dataset(
    *,
    raw_root: Path,
    repo_id: str,
    output_root: Path | None,
    fps: int | None,
    action_mode: str,
    only_success: bool,
    allow_no_images: bool,
    use_videos: bool,
    push_to_hub: bool,
    force: bool,
) -> dict[str, Any]:
    episodes = iter_raw_episodes(raw_root, only_success=only_success)
    if not episodes:
        raise ValueError(f"No raw episodes found under {raw_root}")

    inferred_fps = fps or int(episodes[0].metadata.get("recording_fps") or 20)
    first_frame = _first_frame(episodes)
    features = build_features(first_frame, action_mode=action_mode, allow_no_images=allow_no_images)

    if output_root is not None and output_root.exists():
        if not force:
            raise FileExistsError(f"{output_root} already exists. Pass --force to replace it.")
        shutil.rmtree(output_root)

    dataset = _create_dataset(repo_id, output_root, inferred_fps, features, use_videos=use_videos)

    image_keys = [key for key in features if key.startswith("observation.images.")]
    action_key = f"action.{action_mode}"
    try:
        from PIL import Image
    except ImportError as exc:
        if image_keys:
            raise RuntimeError("Image export requires Pillow. Install with `python -m pip install Pillow`.") from exc
        Image = None  # type: ignore[assignment]

    frame_count = 0
    for episode in episodes:
        for raw_frame in read_frames(episode):
            frame = {
                "observation.state": np.asarray(raw_frame["observation.state"], dtype=np.float32),
                "action": np.asarray(raw_frame[action_key], dtype=np.float32),
                "task": raw_frame.get("task") or episode.task,
            }
            for image_key in image_keys:
                camera_name = image_key.removeprefix("observation.images.")
                image_meta = raw_frame.get("images", {}).get(camera_name)
                if image_meta is None:
                    raise ValueError(f"Frame {raw_frame.get('frame_index')} is missing image for camera {camera_name!r}")
                image_path = episode.path / image_meta["path"]
                frame[image_key] = np.asarray(Image.open(image_path).convert("RGB"))  # type: ignore[union-attr]
            dataset.add_frame(frame)
            frame_count += 1
        dataset.save_episode()

    if hasattr(dataset, "finalize"):
        dataset.finalize()
    if push_to_hub:
        dataset.push_to_hub()

    return {
        "repo_id": repo_id,
        "output_root": str(output_root) if output_root else None,
        "episode_count": len(episodes),
        "frame_count": frame_count,
        "fps": inferred_fps,
        "features": sorted(features.keys()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export raw Quest/UR5e episodes to a LeRobot dataset.")
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"), help="Raw episode root directory.")
    parser.add_argument("--repo-id", required=True, help="LeRobot dataset repo id, for example eee336/ur5e_teleop_demo.")
    parser.add_argument("--output-root", type=Path, default=None, help="Local LeRobot dataset directory.")
    parser.add_argument("--fps", type=int, default=None, help="Override dataset FPS; defaults to episode recording FPS.")
    parser.add_argument("--action-mode", choices=["absolute_tcp", "delta_tcp"], default="absolute_tcp")
    parser.add_argument("--include-failed", action="store_true", help="Include episodes stopped as unsuccessful.")
    parser.add_argument("--allow-no-images", action="store_true", help="Allow state-only export without image observations.")
    parser.add_argument("--no-videos", action="store_true", help="Disable LeRobot video encoding when supported.")
    parser.add_argument("--push-to-hub", action="store_true", help="Push the exported dataset to Hugging Face Hub.")
    parser.add_argument("--force", action="store_true", help="Delete output-root before export if it exists.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = export_dataset(
        raw_root=args.raw_root,
        repo_id=args.repo_id,
        output_root=args.output_root,
        fps=args.fps,
        action_mode=args.action_mode,
        only_success=not args.include_failed,
        allow_no_images=args.allow_no_images,
        use_videos=not args.no_videos,
        push_to_hub=args.push_to_hub,
        force=args.force,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

