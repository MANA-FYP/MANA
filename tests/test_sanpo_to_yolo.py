"""tools/sanpo_to_yolo.py: SANPO masks become YOLO boxes, split by session, with data.yaml."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import sanpo_to_yolo as sy  # noqa: E402

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

LABELMAP = {"road": 1, "stairs": 15, "pole": 24, "pedestrian": 7}
LABELTYPE = {"road": "semantic", "stairs": "panoptic", "pole": "panoptic", "pedestrian": "panoptic"}


def test_thing_classes_are_the_panoptic_ones_in_id_order(tmp_path):
    (tmp_path / "labelmap.json").write_text(json.dumps(LABELMAP))
    (tmp_path / "labeltype.json").write_text(json.dumps(LABELTYPE))
    assert sy.thing_classes(tmp_path) == {7: "pedestrian", 15: "stairs", 24: "pole"}


def mask_with_things():
    mask = np.zeros((40, 80, 3), dtype=np.uint8)
    mask[:, :, 0] = 1  # road everywhere: stuff, no box
    mask[0:20, 0:20] = (15, 0, 1)  # stairs, instance 1
    mask[20:40, 40:80] = (15, 1, 1)  # stairs, instance 257: same blue, other green
    mask[0:5, 70:75] = (24, 0, 3)  # a 25-pixel pole: a speck, no box
    return mask


def test_each_thing_object_is_one_box_and_stuff_and_specks_are_not():
    things = {7: 0, 15: 1, 24: 2}
    found = sy.boxes(mask_with_things(), things)
    assert found == [
        (1, 10 / 80, 10 / 40, 20 / 80, 20 / 40),
        (1, 60 / 80, 30 / 40, 40 / 80, 20 / 40),
    ]
    assert len(sy.boxes(mask_with_things(), things, min_pixels=10)) == 3  # the pole now counts


def test_validation_is_held_out_by_session_and_repeatable():
    train = [f"t{i:02d}" for i in range(20)]
    parts = sy.split_sessions(train, ["x1", "x2"], val_share=0.1, seed=5)
    assert len(parts["val"]) == 2 and len(parts["train"]) == 18
    assert not set(parts["val"]) & set(parts["train"])
    assert parts["test"] == ["x1", "x2"]
    assert parts == sy.split_sessions(train, ["x1", "x2"], val_share=0.1, seed=5)


def write_sanpo(root: Path):
    root.mkdir(parents=True)
    (root / "labelmap.json").write_text(json.dumps(LABELMAP))
    (root / "labeltype.json").write_text(json.dumps(LABELTYPE))
    empty = np.zeros((40, 80, 3), dtype=np.uint8)
    empty[:, :, 0] = 1
    for split, sessions in {"train": ["a", "b", "c"], "test": ["z"]}.items():
        for session in sessions:
            base = root / split / session / "camera_chest"
            for kind in ("video_frames", "segmentation_masks"):
                (base / kind).mkdir(parents=True)
            for n, mask in enumerate((mask_with_things(), empty)):
                Image.fromarray(mask).save(base / "segmentation_masks" / f"{n:06d}.png")
                Image.new("RGB", (80, 40), (90, 90, 90)).save(
                    base / "video_frames" / f"{n:06d}.png"
                )
    # a mask without its image is skipped
    (root / "train" / "a" / "camera_chest" / "segmentation_masks" / "000009.png").write_bytes(
        (root / "train" / "a" / "camera_chest" / "segmentation_masks" / "000000.png").read_bytes()
    )


def test_convert_writes_images_labels_and_data_yaml_by_session(tmp_path):
    write_sanpo(tmp_path / "sanpo")
    out = tmp_path / "yolo"
    s = sy.convert(tmp_path / "sanpo", out, val_share=0.34, seed=0)
    assert s["names"] == ["pedestrian", "stairs", "pole"]
    assert s["sessions"]["test"] == ["z"] and len(s["sessions"]["val"]) == 1
    assert s["images"] == {"train": 4, "val": 2, "test": 2}
    assert s["objects"]["test"] == {"stairs": 2}
    val = s["sessions"]["val"][0]
    labels = sorted((out / "labels" / "val").iterdir())
    assert [p.name for p in labels] == [
        f"{val}__camera_chest__000000.txt",
        f"{val}__camera_chest__000001.txt",
    ]
    assert labels[0].read_text().splitlines()[0] == "1 0.125000 0.250000 0.250000 0.500000"
    assert labels[1].read_text() == ""  # a background image
    assert (out / "images" / "val" / f"{val}__camera_chest__000000.png").is_file()
    yaml = (out / "data.yaml").read_text()
    assert "train: images/train" in yaml and "test: images/test" in yaml
    assert "  1: stairs" in yaml
    assert json.loads((out / "parts.json").read_text())["sessions"] == s["sessions"]


def test_converting_again_reuses_what_is_there(tmp_path):
    write_sanpo(tmp_path / "sanpo")
    first = sy.convert(tmp_path / "sanpo", tmp_path / "yolo")
    assert sy.convert(tmp_path / "sanpo", tmp_path / "yolo") == first
