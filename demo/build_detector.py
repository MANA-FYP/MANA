"""Build an open-vocabulary detector for the phone demo: YOLOE-26 told which everyday objects to
look for. It needs the internet once (the YOLOE weights and its text encoder); the saved weights
then work offline and load like any other detector:

    python -m demo.build_detector                       # writes yoloe-26n-mana.pt
    python -m demo.server --weights yoloe-26n-mana.pt --imgsz 480

The list holds objects that matter around a wheelchair or a cane user and that the model found
reliably in our phone tests; rare kitchen, sport and animal classes were left out, because each
extra name costs time and invites wrong labels. A label is only a detection; a distance is
estimated only for the classes in vision.OBSTACLE_HEIGHT_M.
"""

import argparse

NAMES = [
    # people and mobility
    "person", "wheelchair", "walking stick", "bicycle", "motorcycle", "car", "dog",
    # furniture and the building
    "chair", "couch", "table", "desk", "bench", "door", "glass door", "stairs", "pole", "pillar",
    "shelf", "cabinet", "box", "trash can", "fire extinguisher", "potted plant", "tv", "lamp",
    "traffic cone",
    # things people carry or wear
    "bottle", "cup", "laptop", "keyboard", "mouse", "cell phone", "tablet", "book", "backpack",
    "handbag", "suitcase", "umbrella", "glasses", "hat",
]  # fmt: skip


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--base", default="yoloe-26n-seg.pt", help="YOLOE weights to start from")
    p.add_argument("--out", default="yoloe-26n-mana.pt", help="where to save the detector")
    a = p.parse_args(argv)
    from ultralytics import YOLOE

    model = YOLOE(a.base)
    model.set_classes(NAMES, model.get_text_pe(NAMES))
    model.save(a.out)
    print(f"saved {a.out}: {len(NAMES)} object names")


if __name__ == "__main__":
    main()
