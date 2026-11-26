"""Train a stair SEGMENTATION model on SANPO's own masks, and score it against the box
detector (#49, #51).

    python3 tools/train_stair_seg.py --root datasets/sanpo --epochs 30 --out runs/stair_seg.pt

Why this exists: the fine-tuned box detector (#49) tops out at about 0.23 AP50 on SANPO stairs
and finds a staircase in only two of six test walks. That is near the published ceiling for
*box* detection of stairs, which fit a box badly: a staircase is a repeating surface that fills
the lower frame, not a compact object. SANPO already labels stairs as a segmentation mask (class
id 15 in the mask's red channel, the same encoding sanpo_to_yolo.py reads), so this scores the
stairs the way they are labelled -- per pixel -- instead of forcing them into boxes. On
validation frames from the training walks this reached about 0.77 IoU, but those frames are near
copies of training ones; on SANPO's held-out test walks (--split test) it marks about a third of
the stair pixels (IoU 0.34, recall 0.36, precision 0.90). See docs/results.md, "Stairs:
segmentation beats boxes". --depth adds a depth channel (tools/fetch_sanpo_depth.py), which on the
test walks gave about 3 IoU points, less than the spread between runs.

The model is a small U-Net (about 8 MB). It trains on an equal mix of stair and non-stair frames
(so precision is measured honestly, not inflated by empty frames) drawn from SANPO's training
sessions, holding out a seeded fraction by frame (or, with --val-walks, whole walks) for
validation, and reports stair-pixel
precision, recall and IoU. It needs the SANPO frames and masks on disk (tools/fetch_sanpo.py),
laid out as <root>/<split>/<session>/<camera>/{video_frames,segmentation_masks}/*.png.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset

STAIR_ID = 15  # SANPO 'stairs' class, stored in the mask's red channel (see sanpo_to_yolo.py)
CURB_ID = 2  # SANPO 'curb': where a pavement ends at a road, up or down (--class-id 2)
TARGET_IDS = (STAIR_ID,)  # the classes learnt, as one: --class-id sets them
IMG = 256  # frames are resized to this square for training


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


def walk_of(frame: Path) -> str:
    """The walk (SANPO session) a frame is from: <split>/<session>/<camera>/video_frames/f.png."""
    return frame.parent.parent.parent.name


def list_pairs(root: Path, split: str) -> list[tuple[Path, Path]]:
    """Every (frame, mask) pair under a split of the SANPO tree."""
    pairs: list[tuple[Path, Path]] = []
    base = root / split
    if not base.is_dir():
        return pairs
    for sess in sorted(p for p in base.iterdir() if p.is_dir()):
        for cam in sess.iterdir():
            vf, sm = cam / "video_frames", cam / "segmentation_masks"
            if vf.is_dir() and sm.is_dir():
                for f in sorted(vf.glob("*.png")):
                    m = sm / f.name
                    if m.exists():
                        pairs.append((f, m))
    return pairs


def has_stairs(mask_path: Path) -> bool:
    red = np.array(Image.open(mask_path).convert("RGB"))[..., 0]
    return bool(np.isin(red, TARGET_IDS).any())


def dim(rgb: np.ndarray, rng: np.random.Generator, light=None, gamma=None, noise=None):
    """A frame as a dim scene would give it: less light, a darker gamma curve, and the sensor
    noise that grows as the light falls. Unset parts are drawn at random (training); DARK sets
    all three for a fixed dark copy of the validation frames."""
    light = rng.uniform(0.15, 1.0) if light is None else light
    gamma = rng.uniform(1.0, 2.2) if gamma is None else gamma
    noise = rng.uniform(0.0, 0.04) if noise is None else noise
    out = np.power(rgb, gamma) * light
    return np.clip(out + rng.normal(0.0, noise, out.shape), 0.0, 1.0)


DARK = {"light": 0.3, "gamma": 2.0, "noise": 0.03}  # the fixed dark copy of the validation frames
CLIP_M = 20.0  # depth is clipped to this and scaled to 0..1 as a fourth channel


def depth_of(frame: Path, folder: str) -> Path:
    """Where a frame's depth is kept: <camera>/<folder>/<frame>.npy, in metres."""
    return frame.parent.parent / folder / (frame.stem + ".npy")


def depth_channel(path: Path) -> np.ndarray:
    d = np.nan_to_num(np.load(path).astype(np.float32), nan=0.0, posinf=CLIP_M, neginf=0.0)
    d = np.clip(d, 0.0, CLIP_M) / CLIP_M
    img = Image.fromarray((d * 255).astype(np.uint8)).resize((IMG, IMG))
    return np.asarray(img, dtype=np.float32)[None] / 255.0


def augment(x: torch.Tensor, seg: torch.Tensor, rng: np.random.Generator):
    """A training frame changed the way another walk would show it: mirrored half the time, a
    random crop of 60 to 100 percent of it scaled back up, and its colour's brightness, contrast
    and saturation changed by up to 40, 30 and 40 percent. A depth channel is cropped and mirrored
    with it but not recoloured. x: C x H x W, seg: H x W."""
    if rng.random() < 0.5:
        x, seg = x.flip(-1), seg.flip(-1)
    size = x.shape[-1]
    crop = int(size * rng.uniform(0.6, 1.0))
    top, left = rng.integers(0, size - crop + 1, 2)
    x = x[:, top : top + crop, left : left + crop]
    seg = seg[top : top + crop, left : left + crop]
    x = F.interpolate(x[None], size=(size, size), mode="bilinear", align_corners=False)[0]
    seg = F.interpolate(seg[None, None], size=(size, size), mode="nearest")[0, 0]
    rgb = x[:3] * rng.uniform(0.6, 1.4)
    mean = rgb.mean()
    rgb = (rgb - mean) * rng.uniform(0.7, 1.3) + mean
    grey = rgb.mean(0, keepdim=True)
    rgb = grey + (rgb - grey) * rng.uniform(0.6, 1.4)
    return torch.cat([rgb.clamp(0, 1), x[3:]]), seg


class StairSeg(Dataset):
    """Frames and stair masks. low_light: a random dim version of each training frame half the
    time. dark: every frame darkened the same way (DARK, seeded by its index), to score with.
    depth: the folder beside video_frames that holds each frame's depth, as a fourth channel
    (the darkening touches only the colour: a depth camera does not need light)."""

    def __init__(
        self, pairs: list[tuple[Path, Path]], low_light=False, dark=False, depth=None, aug=False
    ):
        self.pairs, self.low_light, self.dark, self.depth = pairs, low_light, dark, depth
        self.aug = aug  # augment() each frame (training)

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, i: int):
        fp, mp = self.pairs[i]
        rgb = np.array(Image.open(fp).convert("RGB").resize((IMG, IMG))) / 255.0
        m = np.array(Image.open(mp).convert("RGB").resize((IMG, IMG), Image.NEAREST))
        seg = np.isin(m[..., 0], TARGET_IDS).astype(np.float32)
        if self.dark:
            rgb = dim(rgb, np.random.default_rng(i), **DARK)
        elif self.low_light and np.random.random() < 0.5:
            rgb = dim(rgb, np.random.default_rng())
        x = rgb.transpose(2, 0, 1).astype(np.float32)
        if self.depth:
            x = np.concatenate([x, depth_channel(depth_of(fp, self.depth))])
        xt, st = torch.from_numpy(x), torch.from_numpy(seg)
        if self.aug:
            xt, st = augment(xt, st, np.random.default_rng())
        return xt, st[None]


def score(model: nn.Module, dl: DataLoader, dev: str) -> tuple[float, float, float]:
    model.eval()
    tp = fp = fn = 0
    with torch.no_grad():
        for x, y in dl:
            p = (torch.sigmoid(model(x.to(dev))) > 0.5).float().cpu()
            tp += int(((p == 1) & (y == 1)).sum())
            fp += int(((p == 1) & (y == 0)).sum())
            fn += int(((p == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    iou = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    return prec, rec, iou


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument(
        "--seed",
        type=int,
        default=0,
        help="the training run's seed (weights, batches); the frames and their split stay fixed",
    )
    ap.add_argument("--cap", type=int, default=4000, help="max stair frames (+ equal non-stair)")
    ap.add_argument("--out", type=Path, default=Path("runs/stair_seg.pt"))
    ap.add_argument(
        "--low-light",
        action="store_true",
        help="train on dim versions of half the frames too, and keep the model best on the "
        "plain and the dark validation frames together",
    )
    ap.add_argument(
        "--eval",
        type=Path,
        metavar="WEIGHTS",
        help="only score these weights on the validation frames, plain and dark",
    )
    ap.add_argument(
        "--depth",
        metavar="FOLDER",
        help="add each frame's depth as a fourth channel, from <camera>/FOLDER/<frame>.npy "
        "(SANPO's stereo depth as tools/fetch_sanpo_depth.py saves it: depth_npy)",
    )
    ap.add_argument(
        "--only-with",
        metavar="FOLDER",
        help="use only frames that have depth in FOLDER (so an RGB run and a depth run are on "
        "the same frames); --depth implies its own folder",
    )
    ap.add_argument(
        "--split",
        default="train",
        help="with --eval, score on every stairs frame of this split and as many without, "
        "instead of the validation frames: test scores on SANPO's held-out walks. The "
        "validation frames come from the training walks (near-copies of training frames) and "
        "choose the saved epoch, so they flatter a model",
    )
    ap.add_argument(
        "--val-walks",
        action="store_true",
        help="hold out whole walks (a sixth of them, seeded) for validation, not a sixth of the "
        "frames: neighbouring frames of one walk are near-copies, so frame-held-out validation "
        "flatters the epoch it picks",
    )
    ap.add_argument(
        "--augment",
        action="store_true",
        help="mirror, crop and recolour the training frames, so the model learns stairs rather "
        "than its training walks: scored on SANPO's held-out test walks, models without it found "
        "only about a third of the stair pixels",
    )
    ap.add_argument(
        "--arch",
        choices=("unet", "pretrained"),
        default="unet",
        help="unet: the small U-Net from scratch; pretrained: LR-ASPP on an ImageNet MobileNetV3 "
        "(colour only)",
    )
    ap.add_argument(
        "--class-id",
        type=int,
        nargs="+",
        default=[STAIR_ID],
        help="the SANPO classes to learn as one, from the mask's red channel: 15 stairs (the "
        "default), 2 curb (where the pavement ends: a road end for someone walking), 1 2 road "
        "or curb (the walkway has ended)",
    )
    a = ap.parse_args(argv)
    global TARGET_IDS
    TARGET_IDS = tuple(a.class_id)
    only = a.only_with or a.depth
    if a.arch == "pretrained" and a.depth:
        ap.error("--arch pretrained takes colour only, not --depth")
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    torch.set_num_threads(4)  # the model runs on the GPU: a shared PC's other cores stay free
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    split = a.split if a.eval else "train"
    allp = list_pairs(a.root, split)
    if not allp:
        print(f"no frames under {a.root}/{split} (run tools/fetch_sanpo.py first)", file=sys.stderr)
        return 1
    rng = np.random.default_rng(0)
    rng.shuffle(allp)
    stair: list[tuple[Path, Path]] = []
    nonstair: list[tuple[Path, Path]] = []
    for fp, mp in allp:
        if only and not depth_of(fp, only).exists():
            continue
        (stair if has_stairs(mp) else nonstair).append((fp, mp))
        if len(stair) >= a.cap and len(nonstair) >= a.cap:
            break
    data = stair + nonstair[: len(stair)]
    rng.shuffle(data)
    nval = len(data) if split != "train" else max(1, len(data) // 6)  # a held-out split: all
    va, tr = data[:nval], data[nval:]
    if a.val_walks and split == "train":
        walks = sorted({walk_of(fp) for fp, _ in data})
        np.random.default_rng(0).shuffle(walks)
        held = set(walks[: max(1, len(walks) // 6)])
        va = [p for p in data if walk_of(p[0]) in held]
        tr = [p for p in data if walk_of(p[0]) not in held]
    print(f"stair {len(stair)}  balanced {len(data)}  train {len(tr)} val {len(va)}", flush=True)

    vl = DataLoader(StairSeg(va, depth=a.depth), batch_size=16, num_workers=4)
    dark = DataLoader(StairSeg(va, dark=True, depth=a.depth), batch_size=16, num_workers=4)
    if a.arch == "pretrained":
        model = Pretrained(pretrained=not a.eval).to(dev)
    else:
        model = UNet(4 if a.depth else 3).to(dev)
    if a.eval:
        model.load_state_dict(torch.load(a.eval, map_location=dev))
        for name, dl in (("plain", vl), ("dark", dark)):
            p, r, i = score(model, dl, dev)
            print(f"{a.eval.name} {name}: precision {p:.3f} recall {r:.3f} IoU {i:.3f}")
        return 0
    train = StairSeg(tr, low_light=a.low_light, depth=a.depth, aug=a.augment)
    tl = DataLoader(train, batch_size=16, shuffle=True, num_workers=8, drop_last=True)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    best = (-1.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    for ep in range(a.epochs):
        model.train()
        loss = torch.tensor(0.0)
        for x, y in tl:
            opt.zero_grad()
            loss = F.binary_cross_entropy_with_logits(model(x.to(dev)), y.to(dev))
            loss.backward()
            opt.step()
        p, r, i = score(model, vl, dev)
        dp, dr, di = score(model, dark, dev)
        keep = (i + di) / 2 if a.low_light else i  # what the saved model is chosen on
        if keep > best[0]:
            best = (keep, (p, r, i), (dp, dr, di))
            torch.save(model.state_dict(), a.out)
        print(
            f"ep{ep + 1} loss {loss.item():.3f} val P {p:.3f} R {r:.3f} IoU {i:.3f}"
            f" | dark P {dp:.3f} R {dr:.3f} IoU {di:.3f}",
            flush=True,
        )
    (p, r, i), (dp, dr, di) = best[1], best[2]
    print(f"plain: precision {p:.3f} recall {r:.3f} IoU {i:.3f}")
    print(f"dark:  precision {dp:.3f} recall {dr:.3f} IoU {di:.3f}; saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
