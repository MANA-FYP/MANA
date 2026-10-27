# Datasets

The datasets the MANA models are trained and tested on: where each one comes from, its licence, its size, and the split we use. None of the data is kept in git. One script fetches all of it into `datasets/` (git ignores that folder):

```bash
tools/fetch_datasets.sh            # every dataset
tools/fetch_datasets.sh --list     # names, sizes and which one needs a login
tools/fetch_datasets.sh coco speech
MANA_DATA=/mnt/big/mana tools/fetch_datasets.sh   # put the data somewhere else
```

A dataset that is already on disk is skipped, and a stopped download picks up where it left off. Only StairNet needs a manual step (see below). The full licence list is in [`NOTICE`](../NOTICE).

| Dataset | Used for | Licence | Size we fetch | How |
|---|---|---|---|---|
| [StairNet](#stairnet) | MobileNetV2 ground classifier (#38) | Not stated by the authors; access through IEEE DataPort | 92 GB zip, about 515,000 images | Manual download, then the script unpacks it |
| [SANPO](#sanpo) | YOLO26s and YOLOv8s fine-tuning, YOLOE-26 zero-shot test (#49) | CC BY 4.0 | About 9 GB, about 2,300 frames (a subset) | Automatic |
| [COCO 2017 val](#coco) | Check of the pretrained COCO detector (YOLO26n) | Annotations CC BY 4.0; images under their Flickr licences | 1.1 GB, 5,000 images | Automatic |
| [Speech Commands v2](#speech-commands-v2) | BC-ResNet-8 command words (#46) | CC BY 4.0 | 2.4 GB, 105,829 clips | Automatic |
| [UrbanSound8K](#urbansound8k) | EfficientAT-mn10 siren and horn detection (#47) | CC BY-NC 4.0 (non-commercial) | 6.0 GB, 8,732 clips | Automatic |

EfficientAT-mn10 starts from the authors' weights, which are already trained on AudioSet, so we do not download AudioSet. We only fine-tune and test on UrbanSound8K.

## StairNet

- **Source:** Kurbis, Kuzmenko, Ivanyuk-Skulskiy, Mihailidis and Laschowski, *StairNet: visual recognition of stairs for human–robot locomotion*, BioMedical Engineering OnLine 23:20, 2024. Data on [IEEE DataPort](https://ieee-dataport.org/documents/stairnet-computer-vision-dataset-stair-recognition), DOI 10.21227/12jm-e336.
- **Access:** the download needs an IEEE DataPort subscription. IEEE student members have one, and the university library may too. Log in, download `StairNet.zip` (91.96 GB) and the ReadMe, put the zip at `datasets/downloads/StairNet.zip`, then run `tools/fetch_datasets.sh stairnet` to unpack it into `datasets/stairnet/`.
- **Licence:** the DataPort page and the paper do not name a licence for the data (the paper itself is CC BY 4.0). We use it for academic research only, cite the paper, and never put the images in this repository or in a release.
- **Contents:** about 515,000 egocentric images from a chest-mounted phone, 1280 × 720 at 5 frames per second, indoors and outdoors, taken from the ExoNet dataset and relabelled. Four classes, very unbalanced:

  | Class | Meaning | Share of images | MANA ground type |
  |---|---|---|---|
  | LG | level ground | about 86 % | flat |
  | LG-IS | level ground, stairs coming up ahead | about 3 % | stairs |
  | IS | on incline stairs (going up) | about 9 % | stairs |
  | IS-LG | stairs ending, level ground ahead | about 2 % | flat |

  These are the demo's `STAIR_CLASSES` (`level`, `to_stairs`, `stairs`, `to_level`) in `demo/vision.py`, in the same order.
- **Split:** the dataset has no fixed split. The authors found that a random split of frames leaks near-identical neighbouring frames into the test set (98.4 % test accuracy with a random split, 97.2 % with a split by video). We split **by video**: 70 % of the videos for training, 15 % for validation and 15 % for testing, with a fixed seed. The training script for #38 writes the exact list of videos in each part next to the model, so the split can be repeated.
- **Limits:** StairNet has only stairs going **up** and has no ramps. Stairs going down, drops and ramps come from the 3D camera (#39), not from this classifier.

## SANPO

- **Source:** Waghmare et al., *SANPO: A Scene Understanding, Accessibility, Navigation, Pathfinding, Obstacle Avoidance Dataset*, arXiv:2309.12172, 2023. Data in the public Google Cloud bucket `gs://gresearch/sanpo_dataset/v0`, [code and documentation](https://github.com/google-research-datasets/sanpo_dataset).
- **Licence:** CC BY 4.0. Faces and number plates are already blurred by the authors.
- **Contents:** SANPO-Real has 701 walking sessions filmed from a chest camera and a head camera (stereo pairs, 2208 × 1242 PNG frames), with depth maps and segmentation masks on the left image (in the sessions we checked, only one of the two cameras has masks). SANPO-Synthetic adds 113,000 rendered frames. The whole dataset is about 6 TB.
- **What we fetch:** `tools/fetch_sanpo.py` (called by the main script) uses plain HTTPS, so no Google tools are needed. For each chosen session it keeps only the left frames that have a mask, and one of every 10 of those (frames next to each other are almost the same picture). By default that is 40 training sessions and 10 test sessions, about 2,300 frames and 9 GB. Change it with `SANPO_TRAIN`, `SANPO_TEST` and `SANPO_EVERY`, for example `SANPO_EVERY=5` for twice the frames. Depth maps, the right lens and SANPO-Synthetic are left out.
- **Layout:** `datasets/sanpo/<train|test>/<session>/<camera>/video_frames/<frame>.png`, with the matching mask under `segmentation_masks/`, plus `labelmap.json`, `labeltype.json` and `splits/`.
- **Labels:** 31 classes in `labelmap.json`. Masks are RGB PNG files: in the frames we checked, the red channel holds the class id and the blue channel holds the instance id. SANPO has masks, not boxes, so #49 turns each instance of a "thing" class (`labeltype.json` marks them `panoptic`, for example `stairs`, `pedestrian`, `vehicle`, `pole`, `opening-door`, `obstacle`) into a YOLO box before training.
- **Split:** the authors' own lists, `splits/train_session_ids.txt` (560 sessions) and `splits/test_session_ids.txt` (141 sessions), so a test walk is never seen in training. We take the first sessions of each list, so every member gets the same ones, and hold out 10 % of the training sessions (by session) for validation.

## COCO

- **Source:** Lin et al., *Microsoft COCO: Common Objects in Context*, ECCV 2014, [cocodataset.org](https://cocodataset.org).
- **Licence:** the annotations are CC BY 4.0. Each image keeps its own Flickr licence, which is listed in the annotation file. We do not redistribute the images.
- **What we fetch:** only the 2017 validation set (5,000 images, 815 MB) and the 2017 annotations (253 MB), into `datasets/coco/val2017/` and `datasets/coco/annotations/`. The pretrained YOLO26n was trained on COCO train2017, so val2017 is the fair set to check it on. It is also a source of extra person, bicycle and car examples if SANPO alone is not enough.
- **Split:** val2017 is used only as a test set.

## Speech Commands v2

- **Source:** Warden, *Speech Commands: A Dataset for Limited-Vocabulary Speech Recognition*, arXiv:1804.03209, 2018.
- **Licence:** CC BY 4.0.
- **Contents:** 105,829 one-second 16 kHz WAV clips of 35 words from 2,618 speakers (2.4 GB), plus background-noise recordings. MANA's ten command words (`COMMAND_WORDS` in `demo/speech.py`: stop, go, yes, no, left, right, up, down, on, off) are among them.
- **Split:** the official one. `validation_list.txt` and `testing_list.txt` inside the archive name the validation and test clips (split by speaker, so a speaker is in one part only); every other clip is training. The separate official test set (`speech_commands_test_set_v0.02`, 113 MB) goes to `datasets/speech_commands/test_set/` and is the one we report.

## UrbanSound8K

- **Source:** Salamon, Jacoby and Bello, *A Dataset and Taxonomy for Urban Sound Research*, ACM Multimedia 2014, [Zenodo record 1203745](https://zenodo.org/records/1203745).
- **Licence:** CC BY-NC 4.0, as listed on the Zenodo record: free for research and teaching, **not for commercial use**. A model fine-tuned on it inherits that limit, which is fine for this project but must be noted if MANA is ever sold.
- **Contents:** 8,732 labelled clips of up to 4 seconds in 10 classes (6.0 GB). MANA uses `siren` and `car_horn` against the other eight.
- **Split:** the 10 official folds. The authors ask that results are reported with 10-fold cross-validation on these folds and never with a random split (clips from the same recording sit in the same fold). We report the mean over the 10 folds.
