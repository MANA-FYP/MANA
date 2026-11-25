"""tools/eval_glass_in_path.py: which photos count as glass ahead or as without glass, and the
demo's own rule deciding them. Needs torch."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# a mark, not a module-level importorskip (see test_train_stair_seg.py)
HAVE = importlib.util.find_spec("torch") is not None
pytestmark = pytest.mark.skipif(not HAVE, reason="needs torch")
if HAVE:
    import torch

    from demo import vision
    from tools import eval_glass_in_path as eg

    class Everywhere(torch.nn.Module):
        """Calls every pixel glass, or none."""

        def __init__(self, glass: bool):
            super().__init__()
            self.glass = glass

        def forward(self, x):
            return torch.full(x[:, :1].shape, 10.0 if self.glass else -10.0)


def photo(path: Path) -> Path:
    Image.fromarray(np.full((60, 90, 3), 120, np.uint8)).save(path)
    return path


def test_glass_counts_as_ahead_only_in_the_middle_third(tmp_path):
    middle, side = np.zeros((90, 90), np.uint8), np.zeros((90, 90), np.uint8)
    middle[:, 40:50] = 255
    side[:, :25] = 255
    Image.fromarray(middle).save(tmp_path / "m.png")
    Image.fromarray(side).save(tmp_path / "s.png")
    assert eg.shows_glass_ahead(tmp_path / "m.png")
    assert not eg.shows_glass_ahead(tmp_path / "s.png")


def test_ade_photos_with_any_glassy_label_are_left_out(tmp_path):
    ade = tmp_path / "ade"
    (ade / "annotations/validation").mkdir(parents=True)
    (ade / "images/validation").mkdir(parents=True)
    labels = {"a": 1, "b": 9, "c": 1}  # wall; a window; wall
    for stem, label in labels.items():
        ann = ade / f"annotations/validation/{stem}.png"
        Image.fromarray(np.full((8, 8), label, np.uint8)).save(ann)
        photo(ade / f"images/validation/{stem}.jpg")
    (ade / "sceneCategories.txt").write_text("a bedroom\nb living_room\nc street\n")
    clear, indoor = eg.without_glass(ade)
    assert [p.stem for p in clear] == ["a", "c"] and [p.stem for p in indoor] == ["a"]


def test_the_demo_rule_decides_glass_ahead(tmp_path):
    p = photo(tmp_path / "p.jpg")
    assert eg.glass_ahead(vision.GlassSegmenter(Everywhere(True), "cpu"), p)
    assert not eg.glass_ahead(vision.GlassSegmenter(Everywhere(False), "cpu"), p)
