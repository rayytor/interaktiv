"""Phase 3 tests for dataset.py. Run with the vision venv from the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_dataset.py -q
"""
import json

from PIL import Image

from tools.hotspot_extraction.vision import dataset

W, H = 200, 300
BOOKS = {"elt-a": "train", "elt-b": "train", "sub-a": "train", "sub-b": "train", "held": "heldout"}
TEMPLATES = {"elt-a": "elt", "elt-b": "elt", "sub-a": "subject", "sub-b": "subject", "held": "subject"}


def _corpus(tmp_path, boxes_by_page):
    """A rendered corpus, label files and one verdict file; boxes_by_page: {(book, page): ([px boxes], verdict)}."""
    pages, labels = tmp_path / "pages", tmp_path / "labels"
    rows = []
    for (book, page), (boxes, verdict) in boxes_by_page.items():
        (pages / book).mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (W, H), "white").save(pages / book / f"{page:04d}.jpg")
        (labels / book).mkdir(parents=True, exist_ok=True)
        lf = labels / book / f"{page:04d}.json"
        lf.write_text(json.dumps({"book": book, "page": page,
                                  "index": {"page": page, "width_px": W, "height_px": H},
                                  "boxes": [{"label": None, "px": b} for b in boxes]}))
        rows.append({"book": book, "page": page, "verdict": verdict, "source": "primary",
                     "label_file": str(lf), "reask_file": None})
    verdicts = tmp_path / "verdicts.jsonl"
    verdicts.write_text("".join(json.dumps(r) + "\n" for r in rows))
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"books": {b: {"split": s, "pages": []} for b, s in BOOKS.items()}}))
    return pages, verdicts, selection


def _build(tmp_path, boxes_by_page, **kw):
    pages, verdicts, selection = _corpus(tmp_path, boxes_by_page)
    out = tmp_path / "dataset"
    summary = dataset.build([verdicts], out, pages, selection, templates=TEMPLATES, **kw)
    return out, summary


def test_split_is_by_book_with_one_valid_book_per_template():
    selection = {"books": {b: {"split": s} for b, s in BOOKS.items()}}
    split_of = dataset.assign_splits(selection, TEMPLATES, seed=0)
    assert split_of["held"] == "heldout"
    valid = [b for b, s in split_of.items() if s == "valid"]
    assert sorted(TEMPLATES[b] for b in valid) == ["elt", "subject"]
    assert split_of == dataset.assign_splits(selection, TEMPLATES, seed=0)      # seeded
    only = dataset.assign_splits(selection, TEMPLATES, seed=0, labelled=["elt-a", "sub-a", "sub-b"])
    assert only["elt-a"] == "train" and only["elt-b"] == "train"                # a lone labelled book stays in train
    assert sum(1 for b in ("sub-a", "sub-b") if only[b] == "valid") == 1


def test_only_trusted_pages_and_empty_pages_are_kept(tmp_path):
    out, summary = _build(tmp_path, {
        ("elt-a", 1): ([[10, 20, 110, 120]], "trusted"),
        ("elt-a", 2): ([], "trusted"),
        ("elt-a", 3): ([[10, 20, 110, 120]], "re-ask"),
        ("held", 4): ([[0, 0, 50, 60]], "trusted"),
    })
    split = summary["books"]["elt-a"]["split"]
    coco = json.loads((out / split / dataset.ANNOTATIONS).read_text())
    assert [im["page"] for im in coco["images"]] == [1, 2]
    assert [a["bbox"] for a in coco["annotations"]] == [[10.0, 20.0, 100.0, 100.0]]
    assert summary["books"]["elt-a"]["empty"] == 1
    assert summary["splits"]["heldout"]["images"] == 1
    assert dataset.check(out) == []


def test_boxes_are_clamped_and_tiny_boxes_drop_the_page(tmp_path):
    out, summary = _build(tmp_path, {
        ("held", 1): ([[-5, 280, 230, 320]], "trusted"),
        ("held", 2): ([[10, 10, 15, 100]], "trusted"),
    })
    coco = json.loads((out / "heldout" / dataset.ANNOTATIONS).read_text())
    assert [a["bbox"] for a in coco["annotations"]] == [[0.0, 280.0, 200.0, 20.0]]
    assert len(summary["dropped"]) == 1 and "p2" in summary["dropped"][0]


def test_first_verdict_file_wins(tmp_path):
    pages, verdicts, selection = _corpus(tmp_path, {("held", 1): ([[10, 10, 100, 100]], "trusted")})
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"book": "held", "page": 1, "index": {"width_px": W, "height_px": H},
                                 "boxes": [{"px": [20, 20, 60, 60]}]}))
    second = tmp_path / "second.jsonl"
    second.write_text(json.dumps({"book": "held", "page": 1, "verdict": "trusted", "source": "primary",
                                  "label_file": str(other)}) + "\n")
    assert [f for _, _, f in dataset.trusted_labels([second, verdicts])] == [other]


def test_a_label_asked_with_the_hint_is_never_trained_on(tmp_path):
    pages, verdicts, selection = _corpus(tmp_path, {("held", 1): ([[10, 10, 100, 100]], "trusted")})
    hinted = tmp_path / "hinted.json"
    hinted.write_text(json.dumps({"book": "held", "page": 1, "index": {"width_px": W, "height_px": H},
                                  "hint": "The publisher lists 2 activities; make sure each has a box.",
                                  "boxes": [{"px": [20, 20, 60, 60]}]}))
    first = tmp_path / "first.jsonl"
    first.write_text(json.dumps({"book": "held", "page": 1, "verdict": "trusted", "source": "primary",
                                 "label_file": str(hinted)}) + "\n")
    assert dataset.trusted_labels([first]) == []
    assert [f for _, _, f in dataset.trusted_labels([first, verdicts])] != [hinted]


def test_check_names_the_offending_page(tmp_path):
    out, _ = _build(tmp_path, {("held", 1): ([[10, 10, 100, 100]], "trusted"),
                               ("elt-a", 1): ([[10, 10, 100, 100]], "trusted")})
    path = out / "heldout" / dataset.ANNOTATIONS
    coco = json.loads(path.read_text())
    coco["annotations"][0]["bbox"] = [150.0, 10.0, 100.0, 100.0]
    coco["annotations"][0]["category_id"] = 1
    path.write_text(json.dumps(coco))
    problems = dataset.check(out)
    assert any("held_0001.jpg" in p and "leaves the image" in p for p in problems)
    assert any("class id 1" in p for p in problems)
