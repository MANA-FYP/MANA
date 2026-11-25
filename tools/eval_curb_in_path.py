"""Curb ahead, per frame, on SANPO walks the curb model never trained on: how often it finds a
curb in the walking path within 3 m, and how often it calls one on frames with no curb in the path,
the false alarms a person walking would hear.

    python3 tools/eval_curb_in_path.py --root datasets/sanpo runs/curb_pre_0.pt [more weights]

The walks are the ones `train_stair_seg.py --class-id 2 --val-walks` holds out (rebuilt the same
way), and every frame of them is scored, not only the balanced ones it validated on. The path is
the middle third of the picture's lower half. A frame has a curb ahead when SANPO labels at least
200 curb pixels there and SANPO's stereo depth puts the nearest tenth of them within 3 m; it is
clear when no curb pixel is labelled there; the rest (a curb further off, or a sliver) is left out.
The model calls a curb ahead when its curb pixels cover at least SHARE of the path (several shares
are reported), and IN_A_ROW asks for that on 3 frames of a walk in a row, as the demo's glass does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_stair_seg as T  # noqa: E402

SHARES = (0.005, 0.01, 0.02, 0.05)
NEAR_M = 3.0


def held_walks(root: Path, cap: int = 4000) -> tuple[set[str], list[tuple[Path, Path]]]:
    """The walks train_stair_seg --val-walks holds out, and every frame of the train split."""
    allp = T.list_pairs(root, "train")
    rng = np.random.default_rng(0)
    rng.shuffle(allp)
    target, other = [], []
    for fp, mp in allp:
        (target if T.has_stairs(mp) else other).append((fp, mp))
        if len(target) >= cap and len(other) >= cap:
            break
    data = target + other[: len(target)]
    walks = sorted({T.walk_of(fp) for fp, _ in data})
    np.random.default_rng(0).shuffle(walks)
    return set(walks[: max(1, len(walks) // 6)]), allp


def truth(mp: Path, fp: Path) -> str | None:
    """'curb' (a curb in the path within NEAR_M), 'clear' (none in the path), or None."""
    red = np.asarray(Image.open(mp).convert("RGB"))[..., 0]
    h, w = red.shape
    path = red[h // 2 :, w // 3 : 2 * w // 3] == T.CURB_ID
    if not path.any():
        return "clear"
    if path.sum() < 200:
        return None
    dp = T.depth_of(fp, "depth_npy")
    if not dp.exists():
        return None
    d = np.asarray(Image.fromarray(np.load(dp).astype(np.float32)).resize((w, h), Image.NEAREST))
    z = d[h // 2 :, w // 3 : 2 * w // 3][path]
    z = z[np.isfinite(z) & (z > 0)]
    return "curb" if z.size and np.percentile(z, 10) <= NEAR_M else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("weights", type=Path, nargs="+")
    a = ap.parse_args(argv)
    T.TARGET_ID = T.CURB_ID
    held, allp = held_walks(a.root)
    frames = sorted((fp, mp) for fp, mp in allp if T.walk_of(fp) in held)
    labels = [truth(mp, fp) for fp, mp in frames]
    keep = [i for i, t in enumerate(labels) if t is not None]
    n_curb = sum(labels[i] == "curb" for i in keep)
    left_out = len(frames) - len(keep)
    print(f"held-out walks {len(held)}, frames {len(frames)}: curb ahead {n_curb}, "
          f"clear {len(keep) - n_curb}, left out {left_out}", flush=True)  # fmt: skip
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    data = T.StairSeg([frames[i] for i in keep])
    s0, s1 = T.IMG // 2, (T.IMG // 3, 2 * (T.IMG // 3))
    for wpath in a.weights:
        model = T.Pretrained(pretrained=False).to(dev)
        model.load_state_dict(torch.load(wpath, map_location=dev))
        model.eval()
        share = []
        with torch.no_grad():
            for b in range(0, len(data), 32):
                x = torch.stack([data[j][0] for j in range(b, min(b + 32, len(data)))]).to(dev)
                p = (torch.sigmoid(model(x)) > 0.5).float()[:, 0, s0:, s1[0] : s1[1]]
                share += p.mean(dim=(1, 2)).cpu().tolist()
        share = np.array(share)
        is_curb = np.array([labels[i] == "curb" for i in keep])
        walk = [T.walk_of(frames[i][0]) + str(frames[i][0].parent.parent.name) for i in keep]
        for s in SHARES:
            hit = share >= s
            row = np.zeros_like(hit)  # 3 frames of one walk and camera in a row
            for k in range(2, len(hit)):
                row[k] = hit[k] and hit[k - 1] and hit[k - 2] and walk[k] == walk[k - 2]
            print(f"{wpath.name} share {s:.3f}: found {hit[is_curb].mean():.1%} of {is_curb.sum()} "
                  f"curb-ahead frames, false on {hit[~is_curb].mean():.1%} of {(~is_curb).sum()} "
                  f"clear | 3 in a row: found {row[is_curb].mean():.1%}, false "
                  f"{row[~is_curb].mean():.1%}", flush=True)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
