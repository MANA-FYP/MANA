"""tools/train_ground.py: finding StairNet's images, the split by video, the scores, and (with
torchvision and onnxruntime installed) a short training run whose ONNX model the demo loads."""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import train_ground as tg  # noqa: E402

from demo import vision  # noqa: E402


def test_the_classes_are_the_demos_in_stairnets_order():
    assert [tg.STAIRNET[k] for k in ("LG", "LG-IS", "IS", "IS-LG")] == list(vision.STAIR_CLASSES)


@pytest.mark.parametrize(
    "name, label",
    [("LG", "level"), ("lg_is", "to_stairs"), ("IS", "stairs"), ("IS-LG", "to_level"),
     ("to_level", "to_level"), ("frames", None)],
)  # fmt: skip
def test_a_class_folder_by_stairnets_name_or_the_demos(name, label):
    assert tg.class_of(name) == label


def write_images(root: Path, layout: dict[str, dict[str, int]], size=(64, 48)):
    """layout: class folder -> video -> frames; a video named '' puts frames straight in the class
    folder, as clip<k>_<n>.jpg."""
    from PIL import Image

    rng = np.random.default_rng(0)
    for folder, videos in layout.items():
        for video, frames in videos.items():
            where = root / folder / video if video else root / folder
            where.mkdir(parents=True, exist_ok=True)
            for n in range(frames):
                pixels = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
                name = f"frame_{n:05d}.jpg" if video else f"clip{n % 2}_{n:04d}.jpg"
                Image.fromarray(pixels).save(where / name)


def test_index_finds_class_and_video_from_the_folders(tmp_path):
    write_images(tmp_path, {"LG": {"v1": 2, "v2": 1}, "IS": {"v3": 2}, "LG-IS": {"": 4}})
    (tmp_path / "ReadMe.txt").write_text("not an image")
    items = tg.index_folder(tmp_path)
    assert len(items) == 9
    got = {(Path(p).parent.name if lab != "to_stairs" else "", lab, v) for p, lab, v in items}
    assert ("v1", "level", "v1") in got and ("v3", "stairs", "v3") in got
    assert {v for _, lab, v in items if lab == "to_stairs"} == {"clip0", "clip1"}


def test_index_from_a_csv_file_relative_to_it(tmp_path):
    (tmp_path / "list.csv").write_text("path,label,video\na/1.jpg,IS-LG,v9\n/abs/2.jpg,level,v8\n")
    items = tg.index_csv(tmp_path / "list.csv")
    assert items[0] == (str(tmp_path / "a" / "1.jpg"), "to_level", "v9")
    assert items[1][1:] == ("level", "v8") and Path(items[1][0]).is_absolute()


def test_split_is_by_video_repeatable_and_70_15_15():
    items = [(f"{v}/{n}.jpg", "level", f"v{v:02d}") for v in range(20) for n in range(5)]
    split = tg.split_by_video(items, seed=3)
    assert [len(split[k]) for k in ("train", "val", "test")] == [14, 3, 3]
    assert set().union(*split.values()) == {f"v{v:02d}" for v in range(20)}
    assert not set(split["train"]) & set(split["test"])  # no video in two parts
    assert split == tg.split_by_video(items, seed=3)
    assert split != tg.split_by_video(items, seed=4)


def test_per_class_scores_each_class_by_its_own_images():
    confusion = [[90, 0, 0, 10], [0, 5, 5, 0], [0, 0, 0, 0], [0, 0, 1, 3]]
    s = tg.per_class(confusion)
    assert s["per_class"] == {"level": 0.9, "to_stairs": 0.5, "stairs": None, "to_level": 0.75}
    assert s["mean_per_class"] == pytest.approx((0.9 + 0.5 + 0.75) / 3)
    assert s["accuracy"] == pytest.approx(98 / 114)


def test_training_input_is_the_demos_input(tmp_path):
    pytest.importorskip("torchvision")
    from PIL import Image

    rng = np.random.default_rng(1)
    image = Image.fromarray(rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8))
    ours = tg.transforms(train=False)(image).numpy()
    demo = vision.preprocess(image)[0]
    assert ours.shape == demo.shape == (3, 224, 224)
    assert np.abs(ours - demo).max() < 0.05  # uint8 rounding in PIL, after normalising


def test_a_short_run_writes_a_model_the_demo_loads(tmp_path):
    pytest.importorskip("torchvision")
    pytest.importorskip("onnx")  # torch.onnx.export needs it
    pytest.importorskip("onnxruntime")
    data = tmp_path / "stairnet"
    write_images(
        data, {c: {f"{c}-{v}": 3 for v in range(3)} for c in ("LG", "LG-IS", "IS", "IS-LG")}
    )
    out = tmp_path / "model"
    results = tg.train(tg.index_folder(data), out, epochs=1, batch=8, pretrained=False,
                       workers=0, log=lambda *_: None)  # fmt: skip
    for name in ("ground.onnx", "ground.pt", "split.json", "results.json"):
        assert (out / name).is_file()
    split = json.loads((out / "split.json").read_text())
    assert sum(len(v) for v in split.values()) == 12
    assert results["classes"] == list(vision.STAIR_CLASSES)
    assert results["test"]["accuracy"] is not None

    classifier = vision.GroundClassifier.load(str(out / "ground.onnx"))
    assert classifier is not None
    from PIL import Image

    jpeg = io.BytesIO()
    Image.new("RGB", (1280, 720), (120, 120, 120)).save(jpeg, "JPEG")
    label, confidence = classifier.classify(jpeg.getvalue())
    assert label in vision.STAIR_CLASSES and 0.0 <= confidence <= 1.0


def test_the_same_recording_under_two_classes_is_one_video(tmp_path):
    write_images(tmp_path, {"LG": {"walk7": 2}, "LG-IS": {"walk7": 2}, "IS": {"walk8": 1}})
    items = tg.index_folder(tmp_path)
    assert {v for _, lab, v in items if lab in ("level", "to_stairs")} == {"walk7"}
    split = tg.split_by_video(items + [("x", "level", "w9")], seed=0)
    assert sum("walk7" in part for part in split.values()) == 1


def test_an_index_saved_by_excel_with_a_bom_is_read(tmp_path):
    (tmp_path / "list.csv").write_bytes("path,label,video\na.jpg,LG,v1\n".encode("utf-8-sig"))
    assert tg.index_csv(tmp_path / "list.csv")[0][1:] == ("level", "v1")


def items_of(videos: dict[str, int]):
    return [(f"{v}/{n}.jpg", "level", v) for v, count in videos.items() for n in range(count)]


def test_video_ids_that_look_right_give_no_warning():
    summary, warnings = tg.check_videos(items_of({f"v{i}": 50 + i for i in range(20)}))
    assert summary.startswith("1190 images in 20 videos") and warnings == []


@pytest.mark.parametrize(
    "videos, says",
    [
        ({"IMG": 300, "v2": 5, "v3": 5}, "one video holds 300"),  # IMG_0012.jpg -> IMG
        ({f"{n:06d}": 1 for n in range(40)}, "about one per frame"),  # 000123.jpg -> 000123
        ({"a": 30, "b": 30, "c": 30}, "only 3 videos"),
    ],
)
def test_video_ids_that_look_wrong_are_warned_about(videos, says):
    _, warnings = tg.check_videos(items_of(videos))
    assert any(says in w for w in warnings) and all("--index" in w for w in warnings)


def test_fewer_than_three_videos_stop_before_training(tmp_path):
    items = [(f"{n}.jpg", "level", f"v{n % 2}") for n in range(6)]
    with pytest.raises(ValueError, match="at least 3"):
        tg.train(items, tmp_path / "out")


def test_without_validation_videos_the_last_epoch_is_kept(tmp_path, monkeypatch):
    pytest.importorskip("torchvision")
    pytest.importorskip("onnx")
    data = tmp_path / "stairnet"
    write_images(data, {c: {f"{c}-{v}": 2 for v in range(2)} for c in ("LG", "IS")})
    items = tg.index_folder(data)
    videos = sorted({v for *_, v in items})
    monkeypatch.setattr(
        tg, "split_by_video", lambda *_: {"train": videos[:-1], "val": [], "test": videos[-1:]}
    )
    results = tg.train(items, tmp_path / "out", epochs=2, batch=4, pretrained=False, workers=0,
                       log=lambda *_: None)  # fmt: skip
    assert results["best_epoch"] == 2
