import json

from quest_ur5e_teleop.config import RecordingConfig
from quest_ur5e_teleop.recording import EpisodeRecorder, RecordingSample
from quest_ur5e_teleop.tools.export_lerobot import build_features, iter_raw_episodes


def sample(monotonic_s: float) -> RecordingSample:
    return RecordingSample(
        monotonic_s=monotonic_s,
        wall_time_s=1_700_000_000.0 + monotonic_s,
        observation_tcp_pose=[0.4, 0.0, 0.35, 0.0, 3.14, 0.0],
        target_tcp_pose=[0.41, 0.0, 0.35, 0.0, 3.14, 0.0],
        quest_position=[0.0, 1.2, -0.3],
        quest_orientation=[0.0, 0.0, 0.0, 1.0],
        handedness="right",
        buttons={"deadman": True},
        active=True,
        operator_enabled=True,
        calibrated=True,
        stale=False,
    )


def test_episode_recorder_writes_metadata_frames_and_manifest(tmp_path):
    recorder = EpisodeRecorder(RecordingConfig(root_dir="raw", fps=10), tmp_path)
    recorder.start(task="pick the cube")
    recorder.record(sample(1.0))
    recorder.record(sample(1.2))
    summary = recorder.stop()

    episode_dir = tmp_path / "raw" / summary["session_id"] / "episode_000000"
    metadata = json.loads((episode_dir / "metadata.json").read_text(encoding="utf-8"))
    frames = (episode_dir / "frames.jsonl").read_text(encoding="utf-8").strip().splitlines()
    manifest = (tmp_path / "raw" / summary["session_id"] / "manifest.jsonl").read_text(encoding="utf-8")

    assert metadata["task"] == "pick the cube"
    assert metadata["frame_count"] == 2
    assert metadata["success"] is True
    assert len(frames) == 2
    first_frame = json.loads(frames[0])
    assert first_frame["observation.state"] == [0.4, 0.0, 0.35, 0.0, 3.14, 0.0, 0.0]
    assert first_frame["action.absolute_tcp"] == [0.41, 0.0, 0.35, 0.0, 3.14, 0.0, 0.0]
    assert "episode_000000" in manifest

    episodes = iter_raw_episodes(tmp_path / "raw")
    assert len(episodes) == 1
    assert episodes[0].task == "pick the cube"


def test_lerobot_features_require_images_by_default():
    frame = {"images": {}}
    try:
        build_features(frame, action_mode="absolute_tcp", allow_no_images=False)
    except ValueError as exc:
        assert "No images" in str(exc)
    else:
        raise AssertionError("Expected no-image export to fail")


def test_lerobot_features_include_camera_shape():
    frame = {"images": {"front": {"width": 640, "height": 480}}}
    features = build_features(frame, action_mode="absolute_tcp", allow_no_images=False)
    assert features["observation.state"]["shape"] == (7,)
    assert features["action"]["shape"] == (7,)
    assert features["observation.images.front"]["shape"] == (480, 640, 3)

