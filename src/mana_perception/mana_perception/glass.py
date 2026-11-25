"""Glass in the path, from the camera image alone (M1 Vision).

A 3D camera's light and a lidar's mostly pass through glass, so a glass wall or a closed glass door
can read as open way. A segmentation model marks the glass pixel by pixel (tools/train_glass_seg.py
--arch pretrained: LR-ASPP on an ImageNet MobileNetV3, trained on GSD-S with photos without glass;
docs/results.md "Glass walls and doors"). Glass counts when it covers enough of the middle third of
the picture, where the device is heading, for GLASS_FRAMES frames in a row; it is placed where its
bottom edge meets the floor, from the camera's height and tilt, since depth may see through it. The
phone demo does the same (demo/vision.GlassSegmenter, demo/server.glass_ahead).

Pure Python and numpy; torch and torchvision only to load and run the model (load()).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SIZE = 320  # the model's input, a square
MIN_SHARE = 0.02  # of the middle third that must be glass
GLASS_FRAMES = 3  # frames in a row: a glossy surface or a screen up close flickers (results.md)


@dataclass(frozen=True)
class Ahead:
    """Glass in the middle third: its mean probability, and its bottom edge and centre as fractions
    of the picture's height and width."""

    confidence: float
    bottom: float
    centre: float


def in_path(mask: np.ndarray, min_share: float = MIN_SHARE) -> Ahead | None:
    """The glass of a probability mask (rows x cols, 0 to 1) in the middle third, or None. Its
    bottom is the 98th percentile of its rows, so a few stray pixels do not move it."""
    rows, cols = mask.shape
    third = cols // 3
    mid = mask[:, third : 2 * third]
    glass = mid > 0.5
    if glass.mean() < min_share:
        return None
    r, c = np.nonzero(glass)
    return Ahead(
        confidence=float(mid[glass].mean()),
        bottom=float((np.percentile(r, 98) + 1) / rows),
        centre=float((np.mean(c) + third + 0.5) / cols),
    )


def floor_distance(row_px: float, fy: float, cy: float, height_m: float, pitch_rad: float):
    """How far ahead along the floor the floor seen at this image row is, from the camera's height
    and tilt (positive looks down); None at or above the horizon. As demo/depth.floor_at."""
    yn = (row_px - cy) / fy
    c, s = math.cos(pitch_rad), math.sin(pitch_rad)
    down = yn * c + s  # metres down per metre along the ray
    if down <= 0.02:
        return None
    return height_m * (c - yn * s) / down


class InARow:
    """Counts frames in a row with glass ahead; true from the GLASS_FRAMES-th."""

    def __init__(self, frames: int = GLASS_FRAMES):
        self.frames, self.count = frames, 0

    def see(self, glass: bool) -> bool:
        self.count = self.count + 1 if glass else 0
        return self.count >= self.frames


def load(path: str, device: str | None = None):
    """The glass model as a function from an RGB image (rows x cols x 3, uint8) to a SIZE x SIZE
    probability mask; None when torch, torchvision or the weights are missing."""
    try:
        import torch
        from PIL import Image
        from torchvision.models.segmentation import lraspp_mobilenet_v3_large

        class GlassNet(torch.nn.Module):  # tools/train_stair_seg.Pretrained, the same weights
            def __init__(self):
                super().__init__()
                self.net = lraspp_mobilenet_v3_large(weights_backbone=None, num_classes=1)
                self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
                self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

            def forward(self, x):
                return self.net((x - self.mean) / self.std)["out"]

        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        model = GlassNet()
        model.load_state_dict(torch.load(path, map_location="cpu"))
        model = model.eval().to(dev)
    except Exception:  # torch, torchvision or the weights missing, or other weights
        return None

    def run(image: np.ndarray) -> np.ndarray:
        small = np.asarray(Image.fromarray(image).resize((SIZE, SIZE)), dtype=np.float32) / 255.0
        x = torch.from_numpy(small.transpose(2, 0, 1)[None].copy()).to(dev)
        with torch.inference_mode():
            return torch.sigmoid(model(x))[0, 0].float().cpu().numpy()

    return run
