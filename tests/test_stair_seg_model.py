"""The stairs segmentation models as the chair's camera runs them (mana_perception.stair_seg), with
the phone demo's rule: "stairs" when at least 2 percent of the picture is stairs."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="no torch")


@needs_torch
@pytest.mark.parametrize("bias, label", [(10.0, "stairs"), (-10.0, "level")])
def test_a_frame_is_stairs_when_enough_of_it_is(tmp_path, bias, label):
    import torch
    from mana_perception.stair_seg import StairSegModel, UNet

    model = UNet(3)
    with torch.no_grad():
        model.out.weight.zero_()
        model.out.bias.fill_(bias)  # every pixel stairs, or none
    path = tmp_path / "m.pt"
    torch.save(model.state_dict(), path)
    seg = StairSegModel.load(str(path))
    said, p = seg.classify(np.zeros((120, 160, 3), np.uint8))
    assert said == label and 0.9 < p <= 1.0


@needs_torch
def test_weights_that_cannot_be_loaded_give_no_model_and_say_why(tmp_path):
    from mana_perception.stair_seg import StairSegModel

    said = []
    assert StairSegModel.load(str(tmp_path / "missing.pt"), log=said.append) is None
    assert said and "missing.pt" in said[0]


@needs_torch
def test_the_training_tool_and_the_demo_use_the_same_models():
    from mana_perception import stair_seg
    from tools import train_stair_seg

    assert train_stair_seg.UNet is stair_seg.UNet
    assert train_stair_seg.Pretrained is stair_seg.Pretrained
