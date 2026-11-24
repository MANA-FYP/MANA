"""tools/train_stair_seg.py: the dim-light version of a frame, the fixed dark validation copy,
and scoring saved weights on both, on a tiny SANPO-shaped folder. Needs torch."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# A mark, not pytest.importorskip at module level: in CI, ROS's launch_testing plugin imports each
# test module itself, and a skip raised there skipped the whole session (0 tests collected).
HAVE = importlib.util.find_spec("torch") is not None
pytestmark = pytest.mark.skipif(not HAVE, reason="needs torch")
if HAVE:
    import torch

    from tools import train_stair_seg as tss


def test_a_dim_frame_is_darker_and_stays_an_image():
    rgb = np.full((8, 8, 3), 0.6)
    out = tss.dim(rgb, np.random.default_rng(0))
    assert out.min() >= 0.0 and out.max() <= 1.0
    assert out.mean() < rgb.mean()


def test_the_dark_copy_is_the_same_every_time():
    rgb = np.random.default_rng(1).random((8, 8, 3))
    a = tss.dim(rgb, np.random.default_rng(5), **tss.DARK)
    b = tss.dim(rgb, np.random.default_rng(5), **tss.DARK)
    assert np.array_equal(a, b)
    assert a.mean() < 0.5 * rgb.mean()  # light 0.3 with gamma 2: well under half as bright


def sanpo_tree(root: Path, frames: int = 12) -> Path:
    """Frames and masks as tools/fetch_sanpo.py lays them out; stairs (red channel 15) in the
    lower half of every other frame."""
    cam = root / "train" / "s1" / "camera_chest"
    (cam / "video_frames").mkdir(parents=True)
    (cam / "segmentation_masks").mkdir(parents=True)
    for k in range(frames):
        Image.new("RGB", (64, 48), (90 + k, 120, 140)).save(cam / "video_frames" / f"{k:06}.png")
        mask = np.zeros((48, 64, 3), np.uint8)
        if k % 2 == 0:
            mask[24:, :, 0] = tss.STAIR_ID
        Image.fromarray(mask).save(cam / "segmentation_masks" / f"{k:06}.png")
    return root


def test_the_dark_validation_frames_are_darker_than_the_plain_ones(tmp_path):
    pairs = tss.list_pairs(sanpo_tree(tmp_path), "train")
    plain, _ = tss.StairSeg(pairs)[0]
    dark, _ = tss.StairSeg(pairs, dark=True)[0]
    assert dark.mean() < plain.mean()


def test_saved_weights_are_scored_plain_and_dark(tmp_path, capsys):
    root = sanpo_tree(tmp_path / "sanpo")
    weights = tmp_path / "seg.pt"
    torch.save(tss.UNet(3).state_dict(), weights)
    assert tss.main(["--root", str(root), "--cap", "6", "--eval", str(weights)]) == 0
    out = capsys.readouterr().out
    assert "seg.pt plain: precision" in out and "seg.pt dark: precision" in out


def with_depth(root: Path, every: int = 1) -> Path:
    """Depth (metres) for every `every`-th frame, as tools/fetch_sanpo_depth.py saves it."""
    cam = root / "train" / "s1" / "camera_chest"
    (cam / "depth_npy").mkdir(exist_ok=True)
    for k, f in enumerate(sorted((cam / "video_frames").glob("*.png"))):
        if k % every == 0:
            np.save(cam / "depth_npy" / f"{f.stem}.npy", np.full((48, 64), 2.5, np.float16))
    return root


def test_depth_is_a_fourth_channel_and_the_dark_copy_leaves_it_alone(tmp_path):
    pairs = tss.list_pairs(with_depth(sanpo_tree(tmp_path)), "train")
    x, _ = tss.StairSeg(pairs, depth="depth_npy")[0]
    xd, _ = tss.StairSeg(pairs, dark=True, depth="depth_npy")[0]
    assert x.shape[0] == 4
    assert torch.allclose(x[3], torch.full_like(x[3], 2.5 / tss.CLIP_M), atol=0.01)
    assert torch.equal(x[3], xd[3])  # a depth camera needs no light
    assert xd[:3].mean() < x[:3].mean()


def test_only_frames_with_depth_are_used_when_asked(tmp_path, capsys):
    root = with_depth(sanpo_tree(tmp_path / "sanpo"), every=2)  # stairs frames only
    weights = tmp_path / "seg.pt"
    torch.save(tss.UNet(3).state_dict(), weights)
    tss.main(["--root", str(root), "--eval", str(weights)])
    assert "stair 6  balanced 12" in capsys.readouterr().out  # all frames
    tss.main(["--root", str(root), "--only-with", "depth_npy", "--eval", str(weights)])
    assert "stair 6  balanced 6" in capsys.readouterr().out  # no level frame has depth


def test_a_sanpo_depth_file_decodes_to_metres():
    import gzip

    from tools.fetch_sanpo_depth import decode

    depth = np.arange(6, dtype=np.float16).reshape(2, 3)
    raw = gzip.compress(np.concatenate([np.array([2, 3], np.float16), depth.ravel()]).tobytes())
    assert np.array_equal(decode(raw), depth)
