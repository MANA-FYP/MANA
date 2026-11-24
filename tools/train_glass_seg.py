"""Train a glass SEGMENTATION model (glass walls, doors, windows) on GSD-S, and score it on the
dataset's own test split.

    python3 tools/train_glass_seg.py --root datasets/gsds --epochs 40 --out runs/glass_seg.pt

Why this exists: a 3D camera's light mostly passes through glass, so a glass wall or door ahead
can read as open space (docs/coverage.md). The phone's depth, estimated from the image, mostly
does not: it put a surface where the glass is in 85% of 474 glass regions of GSD-S's test
photos. But neither says it is glass, and the detector's "glass door" name is the only cue
today. Glass is a surface, like stairs, so it is scored per pixel, with the small U-Net of
tools/train_stair_seg.py or its ImageNet-pretrained model (--arch pretrained).

GSD-S ("Exploiting Semantic Relations for Glass Surface Detection", NeurIPS 2022; licence
bsd-3-clause-non-commercial) has 3,911 training and 608 test photos with a binary glass mask.
Lay it out as <root>/{train,test}/{images/<id>.jpg, masks/<id>.png} (mask 255 = glass), for
example with the datasets package:

    for split in ("train", "test"):
        for r in datasets.load_dataset("garrying/GSD-S", split=split):
            r["image"].convert("RGB").save(f"{root}/{split}/images/{r['image_id']}.jpg")
            r["mask"].convert("L").save(f"{root}/{split}/masks/{r['image_id']}.png")

GSD-S is nearly all glass (575 of its 608 test photos show some), so a model trained on it alone
learns to see glass everywhere: on ADE20K photos with nothing glassy labelled it called glass
ahead in about 80%. --clear gives a folder of photos without glass (for example ADE20K training
photos with no window, door, mirror, screen, display case, vehicle, shower or drinking glass),
trained on with an empty mask.

The saved model is the epoch best on a seeded sixth of the training photos (and of the clear
ones); the numbers reported are on the test photos, which play no part in choosing it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.train_stair_seg import Pretrained, UNet, score  # noqa: E402

IMG = 320  # photos are resized to this square
GLASS_SHARE = 0.02  # a photo with at least this much glass counts as showing glass
FOUND = 0.3  # and the glass in it counts as found when the model marks this much of it


def clear_pairs(folder: Path | None) -> list[tuple[Path, None]]:
    """Photos without glass, each with no mask: all of it is not glass."""
    return [] if folder is None else [(p, None) for p in sorted(folder.glob("*.jpg"))]


def list_pairs(root: Path, split: str) -> list[tuple[Path, Path]]:
    """Every (photo, mask) pair of a split."""
    masks = root / split / "masks"
    return [
        (p, masks / f"{p.stem}.png")
        for p in sorted((root / split / "images").glob("*.jpg"))
        if (masks / f"{p.stem}.png").exists()
    ]


class GlassSeg(Dataset):
    """Photos and glass masks (None: no glass); flip: mirror half the photos left to right."""

    def __init__(self, pairs: list[tuple[Path, Path | None]], flip: bool = False):
        self.pairs, self.flip = pairs, flip

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, i: int):
        fp, mp = self.pairs[i]
        rgb = np.asarray(Image.open(fp).convert("RGB").resize((IMG, IMG)), np.float32) / 255.0
        if mp is None:
            m = np.zeros((IMG, IMG), bool)
        else:
            m = np.asarray(Image.open(mp).convert("L").resize((IMG, IMG), Image.NEAREST)) > 127
        if self.flip and np.random.random() < 0.5:
            rgb, m = rgb[:, ::-1], m[:, ::-1]
        x = torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))
        return x, torch.from_numpy(np.ascontiguousarray(m, dtype=np.float32))[None]


def photos_found(model, dl: DataLoader, dev: str) -> tuple[int, int, int, int]:
    """(photos showing glass, of them with their glass found, photos without glass, of them
    marked as having some): how often a user would be told about glass, there or not, per photo."""
    model.eval()
    shown = found = clear = false = 0
    with torch.no_grad():
        for x, y in dl:
            p = (torch.sigmoid(model(x.to(dev))) > 0.5).float().cpu()
            for pi, yi in zip(p, y, strict=True):
                share = float(yi.mean())
                if share >= GLASS_SHARE:
                    shown += 1
                    found += float((pi * yi).sum() / yi.sum()) >= FOUND
                else:
                    clear += 1
                    false += float(pi.mean()) >= GLASS_SHARE
    return shown, found, clear, false


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--root", type=Path, default=Path("datasets/gsds"))
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0, help="the run's seed (weights, batches)")
    ap.add_argument(
        "--arch",
        choices=("unet", "pretrained"),
        default="unet",
        help="the small U-Net from scratch, or LR-ASPP on an ImageNet backbone",
    )
    ap.add_argument("--clear", type=Path, help="a folder of photos without glass (.jpg)")
    ap.add_argument("--out", type=Path, default=Path("runs/glass_seg.pt"))
    ap.add_argument("--eval", type=Path, metavar="WEIGHTS", help="only score these on the test")
    a = ap.parse_args(argv)
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    torch.set_num_threads(4)  # the model runs on the GPU: a shared PC's other cores stay free
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    train, test = list_pairs(a.root, "train"), list_pairs(a.root, "test")
    if not train or not test:
        print(f"no photos under {a.root}/train or test (see the docstring)", file=sys.stderr)
        return 1
    np.random.default_rng(0).shuffle(train)  # the split stays the same whatever the seed
    nval = max(1, len(train) // 6)
    va, tr = train[:nval], train[nval:]
    clear = clear_pairs(a.clear)
    np.random.default_rng(0).shuffle(clear)
    va, tr = va + clear[: len(clear) // 6], tr + clear[len(clear) // 6 :]
    print(f"train {len(tr)} val {len(va)} test {len(test)}; without glass {len(clear)}", flush=True)
    vl = DataLoader(GlassSeg(va), batch_size=16, num_workers=4)
    tel = DataLoader(GlassSeg(test), batch_size=16, num_workers=4)
    model = (Pretrained(not a.eval) if a.arch == "pretrained" else UNet(3)).to(dev)

    def report(name: str) -> None:
        p, r, i = score(model, tel, dev)
        shown, found, clear, false = photos_found(model, tel, dev)
        print(
            f"{name} test: precision {p:.3f} recall {r:.3f} IoU {i:.3f}; glass found in "
            f"{found} of {shown} photos showing it; {false} of {clear} without glass marked",
            flush=True,
        )

    if a.eval:
        model.load_state_dict(torch.load(a.eval, map_location=dev))
        report(a.eval.name)
        return 0
    tl = DataLoader(
        GlassSeg(tr, flip=True), batch_size=16, shuffle=True, num_workers=8, drop_last=True
    )
    opt = torch.optim.Adam(model.parameters(), 1e-3)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    best = -1.0
    for ep in range(a.epochs):
        model.train()
        loss = torch.tensor(0.0)
        for x, y in tl:
            opt.zero_grad()
            loss = F.binary_cross_entropy_with_logits(model(x.to(dev)), y.to(dev))
            loss.backward()
            opt.step()
        p, r, i = score(model, vl, dev)
        if i > best:
            best = i
            torch.save(model.state_dict(), a.out)
        print(f"ep{ep + 1} loss {loss.item():.3f} val P {p:.3f} R {r:.3f} IoU {i:.3f}", flush=True)
    model.load_state_dict(torch.load(a.out, map_location=dev))
    print(f"best val IoU {best:.3f}; saved {a.out}")
    report(a.out.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
