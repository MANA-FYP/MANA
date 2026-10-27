"""Tests for the SANPO subset fetcher. A fake bucket stands in for Google's, so they run offline."""

import json
from urllib.parse import parse_qs, unquote, urlparse

from tools import fetch_sanpo as fs

SESSION = "abc-123"
MASKS = f"{fs.PREFIX}/sanpo-real/{SESSION}/camera_chest/left/segmentation_masks/"


class FakeBucket:
    """Answers listing and file URLs from a dict of object name -> bytes, two names per page."""

    def __init__(self, files):
        self.files, self.calls = files, []

    def __call__(self, url):
        self.calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("/o"):
            q = parse_qs(parsed.query)
            names = sorted(n for n in self.files if n.startswith(q["prefix"][0]))
            start = int(q.get("pageToken", ["0"])[0])
            page = {"items": [{"name": n} for n in names[start : start + 2]]}
            if start + 2 < len(names):
                page["nextPageToken"] = str(start + 2)
            return json.dumps(page).encode()
        return self.files[unquote(parsed.path).removeprefix(f"/{fs.BUCKET}/")]


def test_listing_follows_every_page():
    names = [f"{MASKS}{i:06d}.png" for i in range(5)]
    assert fs.list_names(MASKS, FakeBucket(dict.fromkeys(names, b"x"))) == names


def test_session_ids_ignore_blank_lines_and_spaces():
    assert fs.parse_ids("a\n\n b \nc\n") == ["a", "b", "c"]


def test_pick_takes_the_first_sessions_and_minus_one_takes_all():
    assert fs.pick(["a", "b", "c"], 2) == ["a", "b"]
    assert fs.pick(["a", "b", "c"], -1) == ["a", "b", "c"]


def test_frames_are_the_masked_ones_in_order_one_in_every():
    names = [f"{MASKS}{i:06d}.png" for i in (4, 0, 2, 1, 3)] + [f"{MASKS}notes.json"]
    assert fs.frame_ids(names, 2) == ["000000", "000002", "000004"]
    assert fs.frame_ids(names, 0) == ["000000", "000001", "000002", "000003", "000004"]


def test_each_frame_brings_its_image_and_its_mask_to_the_split_folder(tmp_path):
    names = fs.session_files(SESSION, "camera_chest", ["000007"])
    assert [n.split("/")[-2] for n in names] == ["video_frames", "segmentation_masks"]
    dest = fs.local_path(tmp_path, "train", names[0])
    assert dest == tmp_path / "train" / SESSION / "camera_chest" / "video_frames" / "000007.png"


def test_a_file_already_on_disk_is_not_downloaded_again(tmp_path):
    bucket = FakeBucket({"f.png": b"data"})
    dest = tmp_path / "f.png"
    assert fs.download(fs.file_url("f.png"), dest, bucket) is True
    assert fs.download(fs.file_url("f.png"), dest, bucket) is False
    assert dest.read_bytes() == b"data" and len(bucket.calls) == 1
    assert not list(tmp_path.glob("*.part"))
