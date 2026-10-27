#!/usr/bin/env bash
# Fetch the datasets the MANA models train and test on. See docs/datasets.md for what each one is.
# Run it from the repository root. The data goes to datasets/ (or $MANA_DATA), which git ignores.
#
#   tools/fetch_datasets.sh                 # every dataset
#   tools/fetch_datasets.sh coco speech     # only the ones named
#   tools/fetch_datasets.sh --list          # names, sizes and what needs a login
#
# Each step returns on failure itself: set -e does not apply inside a function called with ||.
# Names: stairnet, sanpo, coco, speech, urbansound. A dataset already on disk is skipped, and a
# stopped download picks up where it left off. SANPO_TRAIN, SANPO_TEST and SANPO_EVERY set the
# SANPO subset (default 40 train sessions, 10 test sessions, one annotated frame in 10).
set -euo pipefail

ROOT=${MANA_DATA:-datasets}
DOWNLOADS=$ROOT/downloads

usage() {
  cat <<'EOF'
name        size on disk           how
stairnet    92 GB zip              manual: needs an IEEE DataPort login, then the script unpacks it
sanpo       about 9 GB (default)   automatic: public Google bucket, a subset of SANPO-Real
coco        1.1 GB                 automatic: val2017 images and the 2017 annotations
speech      2.4 GB                 automatic: Speech Commands v2 and its fixed test set
urbansound  6.0 GB                 automatic: UrbanSound8K from Zenodo
EOF
}

fetch() {  # fetch <url> <file>: download with resume, into the downloads folder
  mkdir -p "$DOWNLOADS"
  echo "  downloading $(basename "$2")"
  curl -fL# --retry 3 -C - -o "$DOWNLOADS/$2" "$1"
}

check_zip() {  # check_zip <file>: every member's CRC, so a cut-off download is never unpacked
  echo "  checking $(basename "$1")"
  unzip -tq "$1" >/dev/null || { echo "  $1 is damaged; delete it and fetch again" >&2; return 1; }
}

check_gz() {  # check_gz <file> [md5]: the gzip stream, and the checksum when the owner gives one
  echo "  checking $(basename "$1")"
  gzip -t "$1" || { echo "  $1 is damaged; delete it and fetch again" >&2; return 1; }
  [ -z "${2:-}" ] && return 0
  local sum
  sum=$(python3 -c 'import hashlib,sys;h=hashlib.md5()
with open(sys.argv[1],"rb") as f:
    for b in iter(lambda:f.read(1<<20),b""): h.update(b)
print(h.hexdigest())' "$1") || return 1
  [ "$sum" = "$2" ] || { echo "  $1: md5 $sum, expected $2" >&2; return 1; }
}

done_mark() { [ -f "$ROOT/$1/.done" ]; }
mark_done() { touch "$ROOT/$1/.done"; echo "  ready in $ROOT/$1"; }

stairnet() {
  done_mark stairnet && { echo "stairnet: already here"; return; }
  local zip=$DOWNLOADS/StairNet.zip
  if [ ! -f "$zip" ]; then
    cat <<EOF
stairnet: needs a manual download (an IEEE DataPort subscription; IEEE student members have one)
  1. Log in at https://ieee-dataport.org/documents/stairnet-computer-vision-dataset-stair-recognition
  2. Download StairNet.zip (92 GB) and its ReadMe, and put the zip at $zip
  3. Run tools/fetch_datasets.sh stairnet again to unpack it
EOF
    return 1
  fi
  echo "stairnet: checking and unpacking (the check reads all 92 GB once)"
  check_zip "$zip" || return 1
  mkdir -p "$ROOT/stairnet" || return 1
  unzip -q -n "$zip" -d "$ROOT/stairnet" || return 1
  mark_done stairnet
}

sanpo() {
  done_mark sanpo && { echo "sanpo: already here"; return; }
  echo "sanpo: ${SANPO_TRAIN:-40} train and ${SANPO_TEST:-10} test sessions"
  python3 tools/fetch_sanpo.py --root "$ROOT/sanpo" --train "${SANPO_TRAIN:-40}" \
    --test "${SANPO_TEST:-10}" --every "${SANPO_EVERY:-10}" || return 1
  mark_done sanpo
}

coco() {
  done_mark coco && { echo "coco: already here"; return; }
  echo "coco: val2017 subset"
  fetch http://images.cocodataset.org/zips/val2017.zip val2017.zip || return 1
  fetch http://images.cocodataset.org/annotations/annotations_trainval2017.zip annotations_2017.zip || return 1
  check_zip "$DOWNLOADS/val2017.zip" || return 1  # COCO is plain http only
  check_zip "$DOWNLOADS/annotations_2017.zip" || return 1
  mkdir -p "$ROOT/coco" || return 1
  unzip -q -n "$DOWNLOADS/val2017.zip" -d "$ROOT/coco" || return 1
  unzip -q -n "$DOWNLOADS/annotations_2017.zip" -d "$ROOT/coco" || return 1
  mark_done coco
}

speech() {
  done_mark speech_commands && { echo "speech: already here"; return; }
  echo "speech: Speech Commands v2"
  local base=https://storage.googleapis.com/download.tensorflow.org/data
  fetch "$base/speech_commands_v0.02.tar.gz" speech_commands_v0.02.tar.gz || return 1
  fetch "$base/speech_commands_test_set_v0.02.tar.gz" speech_commands_test_set_v0.02.tar.gz || return 1
  check_gz "$DOWNLOADS/speech_commands_v0.02.tar.gz" || return 1
  check_gz "$DOWNLOADS/speech_commands_test_set_v0.02.tar.gz" || return 1
  mkdir -p "$ROOT/speech_commands/test_set" || return 1
  tar -xzf "$DOWNLOADS/speech_commands_v0.02.tar.gz" -C "$ROOT/speech_commands" || return 1
  tar -xzf "$DOWNLOADS/speech_commands_test_set_v0.02.tar.gz" -C "$ROOT/speech_commands/test_set" || return 1
  mark_done speech_commands
}

urbansound() {
  done_mark UrbanSound8K && { echo "urbansound: already here"; return; }
  echo "urbansound: UrbanSound8K"
  fetch "https://zenodo.org/records/1203745/files/UrbanSound8K.tar.gz" UrbanSound8K.tar.gz || return 1
  check_gz "$DOWNLOADS/UrbanSound8K.tar.gz" 9aa69802bbf37fb986f71ec1483a196e || return 1  # Zenodo's
  tar -xzf "$DOWNLOADS/UrbanSound8K.tar.gz" -C "$ROOT" || return 1  # makes $ROOT/UrbanSound8K
  mark_done UrbanSound8K
}

if [ "${1:-}" = "--list" ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

names=("$@")
[ ${#names[@]} -eq 0 ] && names=(stairnet sanpo coco speech urbansound)
for name in "${names[@]}"; do  # every name is checked before anything is downloaded
  case $name in
    stairnet | sanpo | coco | speech | urbansound) ;;
    *) echo "unknown dataset: $name (try --list)" >&2; exit 2 ;;
  esac
done
mkdir -p "$ROOT"
failed=()
for name in "${names[@]}"; do
  "$name" || failed+=("$name")
done
if [ ${#failed[@]} -gt 0 ]; then
  echo "not fetched: ${failed[*]}" >&2
  exit 1
fi
