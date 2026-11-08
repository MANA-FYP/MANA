"""mana_perception.ground_classifier, the code classifier_node runs: the classes and their ground,
the model's input (the same as the phone demo's and the training's), and the model wrapper."""

import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from demo import vision  # noqa: E402
from mana_perception import ground_classifier as gc  # noqa: E402

# GroundReport's ground codes, read from the message itself
MSG = (ROOT / "src" / "mana_msgs" / "msg" / "GroundReport.msg").read_text()
GROUND = {name: int(v) for name, v in re.findall(r"^uint8 (\w+)=(\d+)", MSG, re.M)}


def test_the_classes_are_the_phone_demos_in_the_same_order():
    assert gc.CLASSES == vision.STAIR_CLASSES


def test_only_level_ground_is_flat():
    assert gc.to_ground("level") == GROUND["FLAT"] == gc.FLAT
    for label in ("to_stairs", "stairs", "to_level"):
        assert gc.to_ground(label) == GROUND["STAIRS_UP"] == gc.STAIRS_UP


def test_the_input_is_the_phone_demos_input():
    from PIL import Image

    rng = np.random.default_rng(2)
    rgb = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    ours = gc.preprocess(rgb)
    assert ours.shape == (1, 3, 224, 224) and ours.dtype == np.float32
    assert np.allclose(ours, vision.preprocess(Image.fromarray(rgb)), atol=1e-6)


class Session:
    """Stands in for an onnxruntime session: fixed logits, and the input it was given."""

    class Input:
        name = "image"

    def __init__(self, logits):
        self.logits, self.fed = logits, []

    def get_inputs(self):
        return [self.Input()]

    def run(self, _outputs, feed):
        self.fed.append(feed)
        return [np.array([self.logits], dtype=np.float32)]


def test_the_model_gives_the_most_likely_class_and_its_probability():
    session = Session([0.0, 0.0, 2.0, 0.0])
    label, p = gc.GroundModel(session).classify(np.zeros((48, 64, 3), dtype=np.uint8))
    assert label == "stairs"
    assert p == pytest.approx(np.exp(2) / (np.exp(2) + 3))
    assert session.fed[0]["image"].shape == (1, 3, 224, 224)


def test_a_missing_model_file_is_none_not_a_crash(tmp_path):
    assert gc.GroundModel.load(str(tmp_path / "missing.onnx")) is None
