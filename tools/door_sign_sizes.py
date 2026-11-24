"""How small a door's PUSH or PULL sign can be and still be read (demo/doorsign.py).

    python3 tools/door_sign_sizes.py --root datasets/gsds

TextOCR's photos with the words are mostly book pages, phones and packets, not doors, so this
measures the case itself, and says plainly that the signs are drawn on: GSD-S's test photos where
a door fills at least a tenth of the picture (its semantic map, class 5), shrunk to the phone's
640 pixels wide, get a sign at the door's handle height. The sign is a plate in one of four
common colourings, with "PUSH" or "PULL" (or, to count false readings, "EXIT", "TOILET",
"OFFICE" or "ROOM 204") in DejaVu Sans Bold, letters from 8 to 40 pixels tall, turned up to 4
degrees, softly blurred and saved as a JPEG as the phone sends it. --hard also turns the sign
20 to 50 degrees away, wears its contrast down, dims the light to as little as 35 percent and
smears the frame sideways by 3 to 7 pixels, as walking does. Each is read as the demo
does: the door's box grown by a quarter each way. A letter height in pixels is turned into the
distance from which a 5 cm letter (a typical door sign's) looks that tall to the phone's camera.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.doorsign import DoorSign  # noqa: E402

DOOR = 5  # GSD-S's semantic class for a door
WIDTH = 640  # the phone's frame
FOV_DEG = 66.0  # its camera across the long side (demo.depth.Mount)
LETTER_M = 0.05  # a typical door sign's letters
SIZES = (5, 6, 8, 10, 12, 16, 20, 28, 40)
COLOURS = (("white", "black"), ("black", "white"), ("#c8c8c8", "black"), ("#1a7f37", "white"))
WAYS, OTHERS = ("PUSH", "PULL"), ("EXIT", "TOILET", "OFFICE", "ROOM 204")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def metres(px: int) -> float:
    f = (WIDTH / 2) / math.tan(math.radians(FOV_DEG) / 2)
    return f * LETTER_M / px


def door_box(seg: np.ndarray):
    """The box of the door pixels, or None when a door fills under a tenth of the picture."""
    door = seg == DOOR
    if door.mean() < 0.1:
        return None
    rows, cols = np.nonzero(door)
    return cols.min(), rows.min(), cols.max(), rows.max()


def with_sign(
    photo: Image.Image, box, word: str, px: int, rnd: random.Random, hard: bool = False
) -> bytes:
    """The photo with a sign on the door, as the phone's JPEG; hard: seen at an angle, worn, dim
    and smeared by walking."""
    font = ImageFont.truetype(FONT, max(6, round(px / 0.72)))  # cap height is about 0.72 em
    plate, ink = rnd.choice(COLOURS)
    x1, y1, x2, y2 = box
    left, top, right, bottom = font.getbbox(word)
    pad = px // 2
    sign = Image.new("RGB", (right - left + 2 * pad, bottom - top + 2 * pad), plate)
    ImageDraw.Draw(sign).text((pad - left, pad - top), word, font=font, fill=ink)
    sign = sign.rotate(rnd.uniform(-4, 4), expand=True, fillcolor=plate)
    if hard:
        # seen 20 to 50 degrees off its face: narrower, and its top and bottom edges slant
        squeeze = math.cos(math.radians(rnd.uniform(20, 50)))
        sign = sign.resize((max(1, round(sign.width * squeeze)), sign.height))
        shear = rnd.uniform(-0.15, 0.15)
        sign = sign.transform(
            (sign.width, sign.height + round(abs(shear) * sign.width)),
            Image.AFFINE,
            (1, 0, 0, shear, 1, -min(0.0, shear) * sign.width),
            fillcolor=plate,
        )
        # a worn or glossy sign: less contrast between its letters and its plate
        grey = Image.new("RGB", sign.size, (128, 128, 128))
        sign = Image.blend(sign, grey, rnd.uniform(0.0, 0.4))
    cx = (x1 + x2) // 2 - sign.width // 2
    cy = round(y1 + 0.45 * (y2 - y1)) - sign.height // 2  # handle height on a door
    out = photo.copy()
    out.paste(sign, (max(0, cx), max(0, cy)))
    out = out.filter(ImageFilter.GaussianBlur(rnd.uniform(0.3, 0.9)))
    if hard:
        # walking: the phone sweeps sideways while the frame is exposed (3 to 7 pixels), in a
        # dim corridor (35 to 100 percent of the light)
        arr = np.asarray(out, dtype=np.float32)
        k = rnd.randint(3, 7)
        c = np.cumsum(np.pad(arr, ((0, 0), (k, 0), (0, 0)), mode="edge"), axis=1)
        arr = (c[:, k:] - c[:, :-k]) / k * rnd.uniform(0.35, 1.0)
        out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path("datasets/gsds"))
    ap.add_argument("--split", default="test")
    ap.add_argument("--hard", action="store_true", help="signs at an angle, worn, dim, smeared")
    a = ap.parse_args(argv)
    reader = DoorSign.load()
    if reader is None:
        return 1
    doors = []
    for seg_path in sorted((a.root / a.split / "seg").glob("*.png")):
        photo = Image.open(a.root / a.split / "images" / f"{seg_path.stem}.jpg").convert("RGB")
        seg = np.asarray(Image.open(seg_path).resize(photo.size, Image.NEAREST))
        scale = WIDTH / photo.width
        photo = photo.resize((WIDTH, round(photo.height * scale)))
        seg = np.asarray(Image.fromarray(seg).resize(photo.size, Image.NEAREST))
        box = door_box(seg)
        if box is not None:
            doors.append((photo, box))
    print(
        f"door photos: {len(doors)}; letters {LETTER_M * 100:.0f} cm, {FOV_DEG:.0f} degree camera"
    )
    rnd = random.Random(0)
    print("letter px | about m | PUSH/PULL read right | wrong way | other signs read as one")
    for px in SIZES:
        right = wrong = false = 0
        for photo, box in doors:
            x1, y1, x2, y2 = box
            for word in WAYS:
                got, _ = reader.read(with_sign(photo, box, word, px, rnd, a.hard), (x1, y1, x2, y2))
                right += got == word.lower()
                wrong += got is not None and got != word.lower()
            got, _ = reader.read(with_sign(photo, box, rnd.choice(OTHERS), px, rnd, a.hard), box)
            false += got is not None
        n = 2 * len(doors)
        print(
            f"{px:9d} | {metres(px):7.1f} | {right:4d} of {n} ({right / n:4.0%})"
            f" | {wrong:9d} | {false} of {len(doors)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
