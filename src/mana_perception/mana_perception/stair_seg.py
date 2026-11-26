"""Stairs found pixel by pixel on a camera frame: the segmentation models of
tools/train_stair_seg.py (a small U-Net, or LR-ASPP on an ImageNet MobileNetV3), and the rule the
phone demo's StairSegmenter uses to call a frame "stairs" (demo/vision.py). Here so that the
chair's camera (classifier_node, segmenter parameter) can run the same models as the phone. Needs
torch, and torchvision for the pretrained model.
"""

from __future__ import annotations

import torch
import torch.nn as nn

SIZE = 256  # frames are resized to this square, as in training
MIN_SHARE = 0.02  # of the picture's pixels: fewer stair pixels is level ground (demo/vision.py)


def conv(i: int, o: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(i, o, 3, padding=1),
        nn.BatchNorm2d(o),
        nn.ReLU(inplace=True),
        nn.Conv2d(o, o, 3, padding=1),
        nn.BatchNorm2d(o),
        nn.ReLU(inplace=True),
    )


class UNet(nn.Module):
    """A small U-Net that outputs one stair/not-stair logit per pixel."""

    def __init__(self, in_ch: int = 3):
        super().__init__()
        self.e1 = conv(in_ch, 32)
        self.e2 = conv(32, 64)
        self.e3 = conv(64, 128)
        self.b = conv(128, 256)
        self.u3 = nn.ConvTranspose2d(256, 128, 2, 2)
        self.d3 = conv(256, 128)
        self.u2 = nn.ConvTranspose2d(128, 64, 2, 2)
        self.d2 = conv(128, 64)
        self.u1 = nn.ConvTranspose2d(64, 32, 2, 2)
        self.d1 = conv(64, 32)
        self.out = nn.Conv2d(32, 1, 1)
        self.p = nn.MaxPool2d(2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        e1 = self.e1(x)
        e2 = self.e2(self.p(e1))
        e3 = self.e3(self.p(e2))
        b = self.b(self.p(e3))
        d = self.d3(torch.cat([self.u3(b), e3], 1))
        d = self.d2(torch.cat([self.u2(d), e2], 1))
        d = self.d1(torch.cat([self.u1(d), e1], 1))
        return self.out(d)


class Pretrained(nn.Module):
    """torchvision's LR-ASPP on a MobileNetV3-Large backbone pretrained on ImageNet (BSD-3), with
    one stair logit per pixel: a backbone that has seen a million photos, against the U-Net's
    from-scratch features, for frames from walks it never saw. RGB only."""

    def __init__(self, pretrained: bool = True):
        super().__init__()
        from torchvision.models import MobileNet_V3_Large_Weights
        from torchvision.models.segmentation import lraspp_mobilenet_v3_large

        weights = MobileNet_V3_Large_Weights.IMAGENET1K_V1 if pretrained else None
        self.net = lraspp_mobilenet_v3_large(weights_backbone=weights, num_classes=1)
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net((x - self.mean) / self.std)["out"]


class StairSegModel:
    """classify(rgb) -> ("stairs" or "level", probability), as ground_classifier.GroundModel
    says it, from one or more weights files (a pixel is stairs when any model says so)."""

    def __init__(self, models, min_share: float = MIN_SHARE, size: int = SIZE):
        self.models, self.min_share, self.size = models, min_share, size

    @classmethod
    def load(cls, path: str, log=None) -> StairSegModel | None:
        """path: one weights file, or several separated by commas. None if one cannot be loaded."""
        try:
            models = []
            for one in path.split(","):
                state = torch.load(one.strip(), map_location="cpu")
                if any(k.startswith("net.") for k in state):  # --arch pretrained
                    model = Pretrained(pretrained=False)
                else:
                    model = UNet(int(state["e1.0.weight"].shape[1]))
                model.load_state_dict(state)
                models.append(model.eval())
            return cls(models)
        except Exception as e:  # torch, torchvision or the weights missing
            if log:
                log(f"stairs segmentation not loaded ({path}): {e}")
            return None

    def classify(self, rgb) -> tuple[str, float]:
        """rgb: an HxWx3 uint8 array. Colour-only models; one trained with depth is not run."""
        import numpy as np
        from PIL import Image

        small = np.asarray(Image.fromarray(rgb).resize((self.size, self.size)), dtype="float32")
        x = torch.from_numpy((small / 255.0).transpose(2, 0, 1)[None].copy())
        with torch.inference_mode():
            probs = [
                torch.sigmoid(m(x))[0, 0]
                for m in self.models
                if not hasattr(m, "e1") or m.e1[0].in_channels == 3
            ]
        mask = torch.stack(probs).max(0).values.numpy() if probs else np.zeros((1, 1))
        stairs = mask > 0.5
        if stairs.mean() < self.min_share:
            return "level", float(1.0 - mask.mean())
        return "stairs", float(mask[stairs].mean())
