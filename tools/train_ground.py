"""Train the MobileNetV2 ground classifier on StairNet, test it, and export it to ONNX (#38).

    python3 tools/train_ground.py --data datasets/stairnet --out models/ground
    python3 tools/train_ground.py --index stairnet.csv --epochs 10 --batch 64 --device cuda

The four classes are the demo's STAIR_CLASSES, in the same order (level, to_stairs, stairs,
to_level for StairNet's LG, LG-IS, IS and IS-LG), so the exported model drops into
`python -m demo.server --stairs-model models/ground/ground.onnx` without a change.

Images are found either from --index, a CSV file with the columns path, label and video, or from
--data, where an image's class is the nearest folder named after a StairNet class and its video is
the folder under that (or, with the images straight in the class folder, the file name up to its
last frame number). The same folder name under two classes is one video, so a recording that goes
from level ground to stairs stays in one part. The split is **by video**, 70 / 15 / 15 with a fixed
seed, because neighbouring frames are nearly the same picture: a random split of frames leaks them
into the test set (see docs/datasets.md). The videos in each part are written next to the model, so
the split can be repeated. Before training it prints the number of videos and images per video,
and warns when the ids look wrong (very few videos, about one per frame, or one holding most
images): then give the ids with --index.

The input is the demo's: the whole frame resized to 224 x 224 (no crop) with ImageNet
normalisation, as demo/vision.py's preprocess() does. The classes are very unbalanced (86 % level
ground), so the loss weighs each class by the inverse of its share, and the epoch kept is the one
with the best mean per-class accuracy on the validation videos, not the best plain accuracy.

Written to --out: ground.onnx, ground.pt (the weights), split.json (the videos of each part) and
results.json (per-class accuracy, the confusion matrix and the settings).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.vision import STAIR_CLASSES  # noqa: E402

# StairNet's folder names, and the demo's class each stands for
STAIRNET = {"LG": "level", "LG-IS": "to_stairs", "IS": "stairs", "IS-LG": "to_level"}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
SIZE = 224


def class_of(name: str) -> str | None:
    """The demo's class for a folder name: StairNet's (LG-IS, lg_is, ...) or the demo's own."""
    key = name.upper().replace("_", "-")
    if key in STAIRNET:
        return STAIRNET[key]
    return name if name in STAIR_CLASSES else None


def video_of(path: Path, class_dir: Path) -> str:
    """The video an image is from: the folder under its class folder, or the file name up to its
    last number (frame_000123.jpg, clip7-0042.jpg)."""
    rel = path.relative_to(class_dir)
    if len(rel.parts) > 1:
        return rel.parts[0]
    stem = re.sub(r"[-_ ]*\d+$", "", path.stem)
    return stem or path.stem


def index_folder(root: Path) -> list[tuple[str, str, str]]:
    """(path, class, video) of every image under root, by the nearest folder named after a class."""
    items = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        for parent in path.parents:
            if parent == root.parent:
                break
            label = class_of(parent.name)
            if label is not None:
                items.append((str(path), label, video_of(path, parent)))
                break
    return items


def index_csv(path: Path) -> list[tuple[str, str, str]]:
    """(path, class, video) from a CSV file with those columns; class may be StairNet's name."""
    base = path.parent
    items = []
    with path.open(newline="", encoding="utf-8-sig") as f:  # a BOM from Excel too
        for row in csv.DictReader(f):
            label = class_of(row["label"].strip())
            if label is None:
                raise SystemExit(f"{path}: unknown class {row['label']!r}")
            image = Path(row["path"])
            items.append((str(image if image.is_absolute() else base / image), label, row["video"]))
    return items


def split_by_video(items, seed: int = 0, parts=(0.7, 0.15, 0.15)) -> dict[str, list[str]]:
    """The videos of each part (train, val, test), shuffled with seed. Every part gets at least one
    video when there are three or more."""
    videos = sorted({video for _, _, video in items})
    random.Random(seed).shuffle(videos)
    n = len(videos)
    n_val = max(1, round(n * parts[1])) if n >= 3 else 0
    n_test = max(1, round(n * parts[2])) if n >= 3 else 0
    n_train = n - n_val - n_test
    return {
        "train": sorted(videos[:n_train]),
        "val": sorted(videos[n_train : n_train + n_val]),
        "test": sorted(videos[n_train + n_val :]),
    }


def check_videos(items) -> tuple[str, list[str]]:
    """A summary of the videos found, and warnings when the video ids look wrong. The split is by
    video, so ids that merge every video into one, or give each frame its own, quietly bring back
    the leak the split is there to stop."""
    per_video = Counter(video for _, _, video in items)
    sizes = sorted(per_video.values())
    n, images = len(per_video), len(items)
    summary = (
        f"{images} images in {n} videos; images per video: least {sizes[0]}, "
        f"median {sizes[len(sizes) // 2]}, most {sizes[-1]}"
    )
    warnings = []
    hint = "check the video ids, or give them with --index"
    if n < 10:
        warnings.append(f"only {n} videos: the split by video has little to split; {hint}")
    if n > 1 and n >= 0.5 * images:
        warnings.append(f"{n} videos for {images} images, about one per frame: {hint}")
    if n > 1 and sizes[-1] > 0.5 * images:
        warnings.append(f"one video holds {sizes[-1]} of {images} images: {hint}")
    return summary, warnings


def per_class(confusion: list[list[int]]) -> dict:
    """Accuracy of each class (its recall), the mean of those, and the plain accuracy."""
    rows = [sum(r) for r in confusion]
    recall = {
        c: (confusion[i][i] / rows[i] if rows[i] else None) for i, c in enumerate(STAIR_CLASSES)
    }
    seen = [v for v in recall.values() if v is not None]
    total = sum(rows)
    return {
        "per_class": recall,
        "mean_per_class": sum(seen) / len(seen) if seen else None,
        "accuracy": sum(confusion[i][i] for i in range(len(rows))) / total if total else None,
        "images": dict(zip(STAIR_CLASSES, rows, strict=True)),
    }


# --- everything below needs torch and torchvision (requirements-ml.txt) ---------------------------


def build_model(pretrained: bool = True):
    from torch import nn
    from torchvision.models import MobileNet_V2_Weights, mobilenet_v2

    model = mobilenet_v2(weights=MobileNet_V2_Weights.IMAGENET1K_V2 if pretrained else None)
    model.classifier[1] = nn.Linear(model.last_channel, len(STAIR_CLASSES))
    return model


def transforms(train: bool):
    from torchvision import transforms as T

    # the whole frame, bicubic as PIL's resize in the demo's preprocess(): the same input as live
    steps = [T.Resize((SIZE, SIZE), interpolation=T.InterpolationMode.BICUBIC)]
    if train:
        steps += [
            T.RandomAffine(degrees=5, translate=(0.05, 0.05), scale=(0.9, 1.1)),
            T.ColorJitter(0.3, 0.3, 0.2),
        ]
    return T.Compose(steps + [T.ToTensor(), T.Normalize(MEAN, STD)])


class Images:
    """A torch dataset of (path, class) pairs."""

    def __init__(self, items, train: bool):
        self.items, self.tf = items, transforms(train)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        from PIL import Image

        path, label = self.items[i]
        with Image.open(path) as image:
            return self.tf(image.convert("RGB")), STAIR_CLASSES.index(label)


def confusion_on(model, loader, device) -> list[list[int]]:
    import torch

    k = len(STAIR_CLASSES)
    confusion = [[0] * k for _ in range(k)]
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            pred = model(x.to(device)).argmax(1).cpu()
            for t, p in zip(y.tolist(), pred.tolist(), strict=True):
                confusion[t][p] += 1
    return confusion


def export_onnx(model, path: Path) -> None:
    """The model as ONNX: input 'image', N x 3 x 224 x 224, output 'logits', N x 4."""
    import torch

    model = model.cpu().eval()
    torch.onnx.export(
        model,
        torch.zeros(1, 3, SIZE, SIZE),
        str(path),
        input_names=["image"],
        output_names=["logits"],
        dynamic_axes={"image": {0: "n"}, "logits": {0: "n"}},
        opset_version=17,
        dynamo=False,
    )


def train(items, out: Path, epochs=10, batch=64, lr=1e-3, seed=0, device="cpu",
          pretrained=True, workers=4, log=print) -> dict:  # fmt: skip
    import torch
    from torch.utils.data import DataLoader

    torch.manual_seed(seed)
    out.mkdir(parents=True, exist_ok=True)
    videos = len({video for _, _, video in items})
    if videos < 3:
        raise ValueError(
            f"{videos} video(s): the split by video needs at least 3 (train, val, test)"
        )
    split = split_by_video(items, seed)
    part = {
        name: [(p, c) for p, c, v in items if v in set(videos)] for name, videos in split.items()
    }
    (out / "split.json").write_text(json.dumps(split, indent=1))
    counts = Counter(c for _, c in part["train"])
    log("train images per class: " + ", ".join(f"{c} {counts[c]}" for c in STAIR_CLASSES))
    # inverse share, normalised to a mean of 1; a class missing from train gets no weight
    raw = [len(part["train"]) / counts[c] if counts[c] else 0.0 for c in STAIR_CLASSES]
    present = [w for w in raw if w]
    weights = torch.tensor([w * len(present) / sum(present) for w in raw])

    def loader(name, shuffle):
        data = Images(part[name], train=name == "train")
        return DataLoader(data, batch_size=batch, shuffle=shuffle, num_workers=workers)

    train_loader, val_loader, test_loader = (
        loader("train", True), loader("val", False), loader("test", False)
    )  # fmt: skip
    model = build_model(pretrained).to(device)
    optimiser = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, max(1, epochs))
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights.to(device))
    best, history = None, []
    for epoch in range(1, epochs + 1):
        model.train()
        started, total, seen = time.monotonic(), 0.0, 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimiser.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            optimiser.step()
            total, seen = total + loss.item() * len(y), seen + len(y)
        schedule.step()
        val = per_class(confusion_on(model, val_loader, device)) if part["val"] else None
        # no validation score: keep the last epoch (a later epoch scores higher)
        score = val["mean_per_class"] if val and val["mean_per_class"] is not None else float(epoch)
        history.append({"epoch": epoch, "loss": total / max(1, seen), "val": val})
        shown = f"{score:.3f}" if val else "-"
        took = time.monotonic() - started
        mean_loss = total / max(1, seen)
        log(f"epoch {epoch}: loss {mean_loss:.4f}, val mean per class {shown}, {took:.0f} s")
        if best is None or score > best[0]:
            best = (score, epoch)
            torch.save(model.state_dict(), out / "ground.pt")
    model.load_state_dict(torch.load(out / "ground.pt", map_location=device))
    test_confusion = confusion_on(model, test_loader, device)
    export_onnx(model, out / "ground.onnx")
    results = {
        "classes": list(STAIR_CLASSES),
        "best_epoch": best[1],
        "test": per_class(test_confusion) | {"confusion": test_confusion},
        "videos": {name: len(v) for name, v in split.items()},
        "images": {name: len(v) for name, v in part.items()},
        "settings": {
            "epochs": epochs,
            "batch": batch,
            "lr": lr,
            "seed": seed,
            "size": SIZE,
            "pretrained": pretrained,
        },  # fmt: skip
        "history": history,
    }
    (out / "results.json").write_text(json.dumps(results, indent=1))
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", type=Path, help="StairNet unpacked: one folder per class")
    src.add_argument("--index", type=Path, help="CSV file with the columns path, label, video")
    p.add_argument("--out", type=Path, default=ROOT / "models" / "ground")
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default="cpu", help="cpu, cuda or mps")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--no-pretrained", action="store_true", help="start from random weights")
    a = p.parse_args(argv)
    items = index_csv(a.index) if a.index else index_folder(a.data)
    if not items:
        raise SystemExit("no images found: see the folder layout in this script's help")
    summary, warnings = check_videos(items)
    print(summary)
    for w in warnings:
        print(f"warning: {w}")
    try:
        results = train(items, a.out, a.epochs, a.batch, a.lr, a.seed, a.device,
                        not a.no_pretrained, a.workers)  # fmt: skip
    except ValueError as e:
        raise SystemExit(str(e)) from None
    test = results["test"]

    def shown(v):
        return "-" if v is None else f"{v:.3f}"

    print(
        f"test: accuracy {shown(test['accuracy'])}, mean per class {shown(test['mean_per_class'])}"
    )
    for c in STAIR_CLASSES:
        v = test["per_class"][c]
        print(f"  {c:10} {shown(v)}  ({test['images'][c]} images)")
    print(f"written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
