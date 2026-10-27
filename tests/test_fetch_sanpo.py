"""Tests for the SANPO subset fetcher. A fake bucket stands in for Google's, so they run offline."""

import io
import json
import urllib.error
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from tools import fetch_sanpo as fs

SESSION = "abc-123"
MASKS = f"{fs.PREFIX}/sanpo-real/{SESSION}/camera_chest/left/segmentation_masks/"


class FakeBucket:
    """Answers listing and file URLs from a dict of object name -> bytes, two names per page.
    A name that is not there is Missing, as get() reports a 404."""

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
        name = unquote(parsed.path).removeprefix(f"/{fs.BUCKET}/")
        if name not in self.files:
            raise fs.Missing(f"404 {url}")
        return self.files[name]


def test_listing_follows_every_page():
    names = [f"{MASKS}{i:06d}.png" for i in range(5)]
    assert fs.list_names(MASKS, FakeBucket(dict.fromkeys(names, b"x"))) == names


def test_session_ids_ignore_blank_lines_and_spaces():
    assert fs.parse_ids("a\n\n b \nc\n") == ["a", "b", "c"]


def test_pick_spreads_the_sessions_over_the_list_and_minus_one_takes_all():
    ids = [f"s{i}" for i in range(560)]
    picked = fs.pick(ids, 40)
    assert len(picked) == 40 and len(set(picked)) == 40
    assert picked[0] == "s0" and picked[-1] == "s546"  # every 14th, to near the end
    assert fs.pick(ids, 40) == picked  # the same for every member
    assert fs.pick(["a", "b", "c"], -1) == ["a", "b", "c"]
    assert fs.pick(["a", "b", "c"], 5) == ["a", "b", "c"]
    assert fs.pick(["a", "b", "c"], 0) == []


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


class Flaky:
    """urlopen that fails with the given errors first, then answers."""

    def __init__(self, *errors):
        self.errors, self.calls = list(errors), 0

    def __call__(self, url, timeout):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return io.BytesIO(b"ok")


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


def test_a_timeout_or_server_error_is_tried_again_with_a_growing_wait(monkeypatch):
    flaky = Flaky(TimeoutError(), http_error(503), urllib.error.URLError("reset"))
    monkeypatch.setattr(fs.urllib.request, "urlopen", flaky)
    waits = []
    assert fs.get("u", sleep=waits.append) == b"ok"
    assert flaky.calls == 4 and waits == [1.0, 2.0, 4.0]


def test_a_file_that_keeps_failing_or_is_not_there_is_missing(monkeypatch):
    monkeypatch.setattr(fs.urllib.request, "urlopen", Flaky(*[http_error(500)] * 4))
    with pytest.raises(fs.Missing, match="after 4 tries"):
        fs.get("u", sleep=lambda s: None)
    flaky = Flaky(http_error(404))
    monkeypatch.setattr(fs.urllib.request, "urlopen", flaky)
    with pytest.raises(fs.Missing, match="404"):
        fs.get("u", sleep=lambda s: None)
    assert flaky.calls == 1  # a 404 is not tried again


def bucket_with(sessions, frames=4, missing=()):
    """A bucket with metadata, split lists and masked frames, minus the names in missing."""
    files = {f"{fs.PREFIX}/{m}": b"{}" for m in fs.META}
    for split, list_file in fs.SPLITS.items():
        ids = "\n".join(sessions[split]).encode()
        files[f"{fs.PREFIX}/sanpo-real/splits/{list_file}"] = ids
        for session in sessions[split]:
            for f in range(frames):
                for name in fs.session_files(session, "camera_chest", [f"{f:06d}"]):
                    files[name] = b"png"
    for name in missing:
        files.pop(name)
    return FakeBucket(files)


def test_main_fetches_both_splits_into_their_folders(tmp_path):
    sessions = {"train": ["t1", "t2"], "test": ["e1"]}
    args = ["--root", str(tmp_path), "--train", "-1", "--test", "-1", "--every", "2"]
    assert fs.main(args, fetch=bucket_with(sessions)) == 0
    assert (tmp_path / "labelmap.json").exists()
    assert (tmp_path / "splits" / "train_session_ids.txt").read_text() == "t1\nt2"
    frames = sorted(tmp_path.glob("*/*/camera_chest/video_frames/*.png"))
    masks = sorted(tmp_path.glob("*/*/camera_chest/segmentation_masks/*.png"))
    assert [p.relative_to(tmp_path).as_posix() for p in frames] == [
        "test/e1/camera_chest/video_frames/000000.png",
        "test/e1/camera_chest/video_frames/000002.png",
        "train/t1/camera_chest/video_frames/000000.png",
        "train/t1/camera_chest/video_frames/000002.png",
        "train/t2/camera_chest/video_frames/000000.png",
        "train/t2/camera_chest/video_frames/000002.png",
    ]
    assert len(masks) == len(frames)


def test_masks_only_fetches_no_images(tmp_path):
    sessions = {"train": ["t1"], "test": []}
    args = ["--root", str(tmp_path), "--train", "-1", "--test", "-1", "--masks-only"]
    assert fs.main(args, fetch=bucket_with(sessions)) == 0
    assert list(tmp_path.glob("**/segmentation_masks/*.png"))
    assert not list(tmp_path.glob("**/video_frames/*"))


def test_a_missing_file_is_skipped_the_rest_is_fetched_and_the_run_fails(tmp_path, capsys):
    sessions = {"train": ["t1"], "test": []}
    gone = fs.session_files("t1", "camera_chest", ["000001"])[0]
    args = ["--root", str(tmp_path), "--train", "-1", "--test", "-1", "--every", "1"]
    assert fs.main(args, fetch=bucket_with(sessions, missing=[gone])) == 1
    assert len(list(tmp_path.glob("train/t1/camera_chest/video_frames/*.png"))) == 3
    assert "1 skipped" in capsys.readouterr().err
    # once the file is back, a second run fills the gap and succeeds
    assert fs.main(args, fetch=bucket_with(sessions)) == 0
    assert len(list(tmp_path.glob("train/t1/camera_chest/video_frames/*.png"))) == 4


def test_count_finds_each_thing_once_and_ignores_stuff_and_specks(tmp_path):
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image")
    (tmp_path / "labelmap.json").write_text(json.dumps({"road": 1, "stairs": 15, "pole": 24}))
    (tmp_path / "labeltype.json").write_text(
        json.dumps({"road": "semantic", "stairs": "panoptic", "pole": "panoptic"})
    )
    mask = np.zeros((40, 40, 3), dtype=np.uint8)
    mask[:, :, 0] = 1  # road everywhere
    mask[0:20, 0:20] = (15, 0, 1)  # one stairs object, 400 pixels
    mask[20:40, 0:20] = (15, 1, 1)  # a second stairs object: same blue, other green
    mask[0:5, 30:35] = (24, 0, 3)  # a 25-pixel pole: too small to count
    folder = tmp_path / "train" / "s" / "camera_chest" / "segmentation_masks"
    folder.mkdir(parents=True)
    image.fromarray(mask).save(folder / "000000.png")
    objects, frames, n = fs.count_things(tmp_path)
    assert n == 1
    assert objects == {"stairs": 2} and frames == {"stairs": 1}
