"""Train a stair SEGMENTATION model on SANPO's own masks, and score it against the box
detector (#49, #51).

    python3 tools/train_stair_seg.py --root datasets/sanpo --epochs 30 --out runs/stair_seg.pt

Why this exists: the fine-tuned box detector (#49) tops out at about 0.23 AP50 on SANPO stairs
and finds a staircase in only two of six test walks. That is near the published ceiling for
*box* detection of stairs, which fit a box badly: a staircase is a repeating surface that fills
the lower frame, not a compact object. SANPO already labels stairs as a segmentation mask (class
id 15 in the mask's red channel, the same encoding sanpo_to_yolo.py reads), so this scores the
stairs the way they are labelled -- per pixel -- instead of forcing them into boxes. On the same
SANPO frames this reaches about 0.77 IoU and 0.85 recall on the stair pixels, roughly three times
the box IoU. See docs/results.md, "Stairs: segmentation beats boxes". Depth fusion is expected to
add more (an RGB-vs-RGB-D study on a RealSense set gave +6.5 IoU), but the SANPO export here has
no depth, so this model is RGB only.

The model is a small U-Net (about 8 MB). It trains on an equal mix of stair and non-stair frames
(so precision is measured honestly, not inflated by empty frames) drawn from SANPO's training
sessions, holding out a seeded fraction by frame for validation, and reports stair-pixel
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
IMG = 256      # frames are resized to this square for training


def conv(i: int, o: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
        nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
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
    return bool((red == STAIR_ID).any())


class StairSeg(Dataset):
    def __init__(self, pairs: list[tuple[Path, Path]]):
        self.pairs = pairs

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, i: int):
        fp, mp = self.pairs[i]
        rgb = np.array(Image.open(fp).convert("RGB").resize((IMG, IMG))) / 255.0
        m = np.array(Image.open(mp).convert("RGB").resize((IMG, IMG), Image.NEAREST))
        seg = (m[..., 0] == STAIR_ID).astype(np.float32)
        x = torch.from_numpy(rgb.transpose(2, 0, 1).astype(np.float32))
        return x, torch.from_numpy(seg)[None]


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
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("datasets/sanpo"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--cap", type=int, default=4000, help="max stair frames (+ equal non-stair)")
    ap.add_argument("--out", type=Path, default=Path("runs/stair_seg.pt"))
    a = ap.parse_args(argv)
    torch.manual_seed(0)
    np.random.seed(0)
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    allp = list_pairs(a.root, "train")
    if not allp:
        print(f"no frames under {a.root}/train (run tools/fetch_sanpo.py first)", file=sys.stderr)
        return 1
    rng = np.random.default_rng(0)
    rng.shuffle(allp)
    stair: list[tuple[Path, Path]] = []
    nonstair: list[tuple[Path, Path]] = []
    for fp, mp in allp:
        (stair if has_stairs(mp) else nonstair).append((fp, mp))
        if len(stair) >= a.cap and len(nonstair) >= a.cap:
            break
    data = stair + nonstair[: len(stair)]
    rng.shuffle(data)
    nval = max(1, len(data) // 6)
    va, tr = data[:nval], data[nval:]
    print(f"stair {len(stair)}  balanced {len(data)}  train {len(tr)} val {len(va)}", flush=True)

    tl = DataLoader(StairSeg(tr), batch_size=16, shuffle=True, num_workers=8, drop_last=True)
    vl = DataLoader(StairSeg(va), batch_size=16, num_workers=4)
    model = UNet(3).to(dev)
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    best = (0.0, 0.0, 0.0)
    for ep in range(a.epochs):
        model.train()
        loss = torch.tensor(0.0)
        for x, y in tl:
            opt.zero_grad()
            loss = F.binary_cross_entropy_with_logits(model(x.to(dev)), y.to(dev))
            loss.backward()
            opt.step()
        p, r, i = score(model, vl, dev)
        if i > best[2]:
            best = (p, r, i)
            torch.save(model.state_dict(), a.out)
        print(f"ep{ep + 1} loss {loss.item():.3f} val P {p:.3f} R {r:.3f} IoU {i:.3f}"
              f" (best IoU {best[2]:.3f})", flush=True)
    print(f"best stair-pixel precision {best[0]:.3f} recall {best[1]:.3f} IoU {best[2]:.3f};"
          f" saved {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
