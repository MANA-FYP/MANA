"""tools/train_glass_seg.py: the GSD-S layout, the flip keeping photo and mask together, and the
per-photo count of glass found, on a tiny folder. Needs torch."""

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

    from tools import train_glass_seg as tgs


def gsds_tree(root: Path, photos: int = 4) -> Path:
    """Photos and masks as GSD-S is laid out; glass on the left half of every other photo."""
    for split in ("train", "test"):
        (root / split / "images").mkdir(parents=True)
        (root / split / "masks").mkdir(parents=True)
        for k in range(photos):
            rgb = np.zeros((48, 64, 3), np.uint8)
            rgb[:, :32] = 200  # bright where the glass is, so a flip can be followed
            Image.fromarray(rgb).save(root / split / "images" / f"{k:04}.jpg")
            mask = np.zeros((48, 64), np.uint8)
            if k % 2 == 0:
                mask[:, :32] = 255
            Image.fromarray(mask).save(root / split / "masks" / f"{k:04}.png")
    return root


def test_photos_pair_with_their_masks(tmp_path):
    pairs = tgs.list_pairs(gsds_tree(tmp_path), "test")
    assert [(p.stem, m.stem) for p, m in pairs] == [(f"{k:04}",) * 2 for k in range(4)]


def test_a_flip_mirrors_the_photo_and_its_mask_together(tmp_path):
    pairs = tgs.list_pairs(gsds_tree(tmp_path), "train")
    np.random.seed(0)
    sides = set()
    for _ in range(8):
        x, y = tgs.GlassSeg(pairs[:1], flip=True)[0]
        glass = y[0] > 0.5
        assert x[0][glass].mean() > 0.6 > x[0][~glass].mean()  # glass stays on the bright half
        sides.add(bool(glass[:, -1].all()))  # on the right: flipped
    assert sides == {True, False}


if HAVE:

    class Marks(torch.nn.Module):
        """A model that marks the left half of every photo as glass."""

        def forward(self, x):
            out = torch.full(x[:, :1].shape, -10.0)
            out[..., : x.shape[-1] // 2] = 10.0
            return out


def test_glass_found_and_false_marks_are_counted_per_photo(tmp_path):
    pairs = tgs.list_pairs(gsds_tree(tmp_path), "test")
    dl = torch.utils.data.DataLoader(tgs.GlassSeg(pairs), batch_size=4)
    # two photos show glass and both are found; both of the two without glass are marked anyway
    assert tgs.photos_found(Marks(), dl, "cpu") == (2, 2, 2, 2)


def test_saved_weights_are_scored_on_the_test_photos(tmp_path, capsys):
    root = gsds_tree(tmp_path / "gsds")
    weights = tmp_path / "glass.pt"
    torch.save(tgs.UNet(3).state_dict(), weights)
    assert tgs.main(["--root", str(root), "--eval", str(weights)]) == 0
    out = capsys.readouterr().out
    assert "train 3 val 1 test 4; without glass 0" in out  # a training photo is held out
    assert "glass.pt test: precision" in out and "of 2 photos showing it" in out
    assert "of 2 without glass marked" in out


def test_pretrained_weights_are_scored_too(tmp_path, capsys):
    pytest.importorskip("torchvision")
    root = gsds_tree(tmp_path / "gsds")
    weights = tmp_path / "pre.pt"
    torch.save(tgs.Pretrained(pretrained=False).state_dict(), weights)
    args = ["--root", str(root), "--arch", "pretrained", "--eval", str(weights)]
    assert tgs.main(args) == 0
    assert "pre.pt test: precision" in capsys.readouterr().out


def test_photos_without_glass_train_with_an_empty_mask(tmp_path):
    clear = tmp_path / "clear"
    clear.mkdir()
    for k in range(6):
        Image.fromarray(np.full((48, 64, 3), 90, np.uint8)).save(clear / f"c{k}.jpg")
    pairs = tgs.clear_pairs(clear)
    assert len(pairs) == 6 and all(m is None for _, m in pairs)
    x, y = tgs.GlassSeg(pairs)[0]
    assert x.shape == (3, tgs.IMG, tgs.IMG) and float(y.sum()) == 0.0
    assert tgs.clear_pairs(None) == []


def test_one_sixth_of_the_clear_photos_is_held_out_with_the_validation(tmp_path, capsys):
    root = gsds_tree(tmp_path / "gsds")
    clear = tmp_path / "clear"
    clear.mkdir()
    for k in range(6):
        Image.fromarray(np.full((48, 64, 3), 90, np.uint8)).save(clear / f"c{k}.jpg")
    weights = tmp_path / "glass.pt"
    torch.save(tgs.UNet(3).state_dict(), weights)
    assert tgs.main(["--root", str(root), "--clear", str(clear), "--eval", str(weights)]) == 0
    assert "train 8 val 2 test 4; without glass 6" in capsys.readouterr().out
