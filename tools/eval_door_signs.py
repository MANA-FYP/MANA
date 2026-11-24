"""Score the door-sign reader (demo/doorsign.py) on TextOCR's real photos.

    python3 tools/eval_door_signs.py --ann TextOCR_0.1_train.json TextOCR_0.1_val.json \
        --images train_images --width 640

TextOCR (CC BY 4.0) labels every word in 25,000 Open Images photos. Every photo whose words say
"push" or "pull" (by the reader's own rule, demo.doorsign.way, on the labelled words) is a
positive; as many photos with no such word, drawn with a fixed seed, are negatives. For each
positive the reader's answer is right, the wrong way, or nothing; for each negative, any answer
is a false one. Positives are also split by how tall the word is in the photo, the reader's limit
(a sign seen from further away is smaller). --width shrinks each photo first, to the phone's
frame size. --sheet writes the positives as a contact sheet, to see which are door signs.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.doorsign import DoorSign, way  # noqa: E402

SIZES = ((0.0, 0.02, "under 2%"), (0.02, 0.05, "2 to 5%"), (0.05, 2.0, "5% or more"))


def truth(paths: list[Path]) -> tuple[dict, dict, dict]:
    """(image id -> push/pull/None, image id -> its file name, image id -> tallest sign word as a
    share of the photo's height) from TextOCR annotation files."""
    side, files, tall = {}, {}, {}
    for p in paths:
        d = json.loads(p.read_text())
        words: dict[str, list] = {}
        for a in d["anns"].values():
            words.setdefault(a["image_id"], []).append(a)
        for iid, img in d["imgs"].items():
            files[iid] = Path(img["file_name"]).name
            found = [(a["utf8_string"], 1.0) for a in words.get(iid, [])]
            side[iid] = way(found)[0]
            if side[iid]:
                hs = [
                    a["bbox"][3] / img["height"]
                    for a in words[iid]
                    if way([(a["utf8_string"], 1.0)])[0] == side[iid]
                ]
                tall[iid] = max(hs)
    return side, files, tall


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ann", type=Path, nargs="+", required=True)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--width", type=int, default=0, help="shrink photos to this width first")
    ap.add_argument("--sheet", type=Path, help="write the positives as a contact sheet")
    a = ap.parse_args(argv)
    import numpy as np
    from PIL import Image

    side, files, tall = truth(a.ann)
    pos = sorted(i for i, s in side.items() if s)
    rest = sorted(i for i, s in side.items() if not s)
    neg = random.Random(0).sample(rest, min(len(pos), len(rest)))
    reader = DoorSign.load()
    if reader is None:
        return 1

    def read(iid):
        im = Image.open(a.images / files[iid]).convert("RGB")
        if a.width and im.width > a.width:
            im = im.resize((a.width, round(im.height * a.width / im.width)))
        return way(reader.words(np.asarray(im))), im

    right = wrong = missed = false = 0
    by_size = {label: [0, 0] for *_, label in SIZES}  # label -> [right, of]
    tiles = []
    for iid in pos:
        (got, _), im = read(iid)
        ok = got == side[iid]
        right += ok
        wrong += got is not None and not ok
        missed += got is None
        label = next(lab for lo, hi, lab in SIZES if lo <= tall[iid] < hi)
        by_size[label][0] += ok
        by_size[label][1] += 1
        if a.sheet:
            tiles.append((im, f"{side[iid]} -> {got or '-'} ({tall[iid]:.0%})"))
    for iid in neg:
        false += read(iid)[0][0] is not None
    n = len(pos)
    print(f"photos with a push or pull word: {n}; as many without: {len(neg)}")
    print(f"read right {right} ({right / n:.0%}), wrong way {wrong}, nothing {missed}")
    print(f"photos without the words read as push or pull: {false} of {len(neg)}")
    for label, (ok, of) in by_size.items():
        print(f"  word {label} of the photo's height: right {ok} of {of}")
    if a.sheet and tiles:
        from PIL import ImageDraw

        cols, w, h = 8, 240, 200
        sheet = Image.new("RGB", (cols * w, -(-len(tiles) // cols) * h), "white")
        for k, (im, text) in enumerate(tiles):
            im.thumbnail((w, h - 20))
            x, y = (k % cols) * w, (k // cols) * h
            sheet.paste(im, (x, y))
            ImageDraw.Draw(sheet).text((x + 2, y + h - 18), text, fill="black")
        sheet.save(a.sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
