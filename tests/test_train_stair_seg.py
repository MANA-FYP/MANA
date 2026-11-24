"""tools/train_stair_seg.py: the dim-light version of a frame, the fixed dark validation copy,
and scoring saved weights on both, on a tiny SANPO-shaped folder. Needs torch."""

import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import train_stair_seg as tss  # noqa: E402


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
