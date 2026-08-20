"""
KHOJ detector — convert a Pascal-VOC dataset (e.g. HERIDAL) to YOLO format.

HERIDAL ships bounding boxes as VOC XML. This converts them to YOLO txt labels
(single class: person), builds an images/labels train/val split, and writes a
data.yaml ready for train.py. SARD from Roboflow is ALREADY YOLO format — you
only need this for HERIDAL (or any other VOC-annotated set).

    python detector/voc_to_yolo.py \
        --images path/to/HERIDAL/images \
        --annotations path/to/HERIDAL/annotations \
        --out detector/datasets/heridal_yolo \
        --val-frac 0.15

Then: python detector/train.py --data detector/datasets/heridal_yolo/data.yaml
"""

from __future__ import annotations

import argparse
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}


def find_image(images_dir: Path, stem: str) -> Path | None:
    for ext in IMG_EXTS:
        p = images_dir / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def convert_one(xml_path: Path) -> tuple[str, list[str]] | None:
    """Return (image_stem, [yolo lines]) for one VOC xml. All objects -> class 0."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    size = root.find("size")
    if size is None:
        return None
    w = float(size.findtext("width", 0))
    h = float(size.findtext("height", 0))
    if w <= 0 or h <= 0:
        return None

    lines: list[str] = []
    for obj in root.findall("object"):
        bb = obj.find("bndbox")
        if bb is None:
            continue
        xmin = float(bb.findtext("xmin", 0))
        ymin = float(bb.findtext("ymin", 0))
        xmax = float(bb.findtext("xmax", 0))
        ymax = float(bb.findtext("ymax", 0))
        # clamp + normalize to YOLO cx,cy,w,h
        xmin, xmax = max(0, min(xmin, xmax)), min(w, max(xmin, xmax))
        ymin, ymax = max(0, min(ymin, ymax)), min(h, max(ymin, ymax))
        bw = (xmax - xmin) / w
        bh = (ymax - ymin) / h
        cx = (xmin + xmax) / 2 / w
        cy = (ymin + ymax) / 2 / h
        if bw <= 0 or bh <= 0:
            continue
        lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return (xml_path.stem, lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--annotations", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    images_dir = Path(args.images)
    ann_dir = Path(args.annotations)
    out = Path(args.out)

    xmls = sorted(ann_dir.glob("*.xml"))
    if not xmls:
        raise SystemExit(f"no .xml files in {ann_dir}")

    # build split
    random.seed(args.seed)
    random.shuffle(xmls)
    n_val = int(len(xmls) * args.val_frac)
    val_set = set(xmls[:n_val])

    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    n_ok, n_skip, n_boxes = 0, 0, 0
    for xml_path in xmls:
        res = convert_one(xml_path)
        if res is None:
            n_skip += 1
            continue
        stem, lines = res
        img = find_image(images_dir, stem)
        if img is None:
            n_skip += 1
            continue
        split = "val" if xml_path in val_set else "train"
        shutil.copy2(img, out / "images" / split / img.name)
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines))
        n_ok += 1
        n_boxes += len(lines)

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        f"path: {out.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"nc: 1\n"
        f"names: ['person']\n"
    )

    print(f"[voc->yolo] converted {n_ok} images ({n_boxes} boxes), skipped {n_skip}")
    print(f"[voc->yolo] wrote {data_yaml}")


if __name__ == "__main__":
    main()
