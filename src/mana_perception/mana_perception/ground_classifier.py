"""The camera's ground classifier: level ground or stairs from one colour frame (#38).

The model is MobileNetV2 trained on StairNet by tools/train_ground.py and exported to ONNX. It
takes the whole frame resized to 224 x 224 (bicubic) with ImageNet normalisation, as it was
trained, and gives four classes in this order, the phone demo's STAIR_CLASSES:

  level      StairNet LG     level ground                                -> FLAT
  to_stairs  StairNet LG-IS  level ground, stairs coming up ahead        -> STAIRS_UP
  stairs     StairNet IS     on the stairs going up                      -> STAIRS_UP
  to_level   StairNet IS-LG  top of the stairs, the last steps still ahead -> STAIRS_UP

Only level ground is flat. StairNet has stairs going up only, and no ramps, curbs or drops:
those come from the 3D camera (depth.py). Needs numpy, and PIL and onnxruntime for a real model.
"""

from __future__ import annotations

import importlib
import math

CLASSES = ("level", "to_stairs", "stairs", "to_level")
SIZE = 224
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

# GroundReport's codes, so this module needs no ROS
FLAT, STAIRS_UP = 0, 2


def to_ground(label: str) -> int:
    """GroundReport's ground code for a class: FLAT for level ground, else STAIRS_UP."""
    return FLAT if label == "level" else STAIRS_UP


def preprocess(rgb):
    """An HxWx3 uint8 RGB array as the model's 1x3x224x224 float32 input."""
    import numpy as np
    from PIL import Image

    image = Image.fromarray(np.asarray(rgb, dtype=np.uint8)).resize((SIZE, SIZE), Image.BICUBIC)
    x = np.asarray(image, dtype=np.float32) / 255.0
    x = (x - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)
    return x.transpose(2, 0, 1)[None, ...].astype(np.float32)


def softmax(logits) -> list[float]:
    top = max(logits)
    e = [math.exp(float(v) - top) for v in logits]
    return [v / sum(e) for v in e]


class GroundModel:
    """The exported classifier, run with onnxruntime on the CPU."""

    def __init__(self, session, classes=CLASSES):
        self.session, self.classes = session, classes

    @classmethod
    def load(cls, path: str, classes=CLASSES, log=None) -> GroundModel | None:
        """The model, or None when onnxruntime or the file is missing, the file is not a model,
        or the model does not give one output per class. log, if given, is told why."""
        try:
            ort = importlib.import_module("onnxruntime")
            session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        except Exception as e:  # onnxruntime missing, no such file, or not a model
            if log is not None:
                log(f"cannot load the ground classifier {path!r}: {type(e).__name__}: {e}")
            return None
        return cls.checked(session, classes, path, log)

    @classmethod
    def checked(cls, session, classes=CLASSES, path="model", log=None) -> GroundModel | None:
        """The model on session, or None when its output is not one score per class: a model for
        other classes would otherwise fail on the first frame."""
        outputs = session.get_outputs()[0].shape[-1]
        if outputs != len(classes):
            if log is not None:
                log(f"{path!r} gives {outputs} outputs, not one for each of {len(classes)} classes")
            return None
        return cls(session, classes)

    def classify(self, rgb) -> tuple[str, float]:
        """The most likely class of an RGB frame and its probability."""
        name = self.session.get_inputs()[0].name
        logits = self.session.run(None, {name: preprocess(rgb)})[0][0]
        probs = softmax(logits)
        best = max(range(len(probs)), key=probs.__getitem__)
        return self.classes[best], probs[best]
