# Phase 9 — unboxed siblings, eight more books (2026-10-03)

Follows `phase8-HANDOFF.md`. Branch `vision-student`. The user gave a yes to each of: the 35-book re-bake
and its installation, eight downloads, 320 teacher requests through the Antigravity CLI, one GPU retrain.
The rules bake was not re-made (it changes nothing in what ships).

## 1. The report: Edebiyat 9, printed page 19 (`7a92f6d0`, sheet 20)

Questions 3, 4, 6 b), 6 c) and 7 had a hotspot; 5 and 6 a) had none. The student draws nothing at 5 at any
confidence and 0.125 at 6 a): there was no box for the bake to admit.

**`snap.between_siblings`** (step 6 of `vision_page`, on by default). A printed label the student left
unboxed between two labels it boxed is a question of the same list. It gets a region when all of this
holds, and the page keeps its hole otherwise:

- the label next above and the label next below in the column each head one of the student's regions;
- at most `SIBLING_RUN` = 2 unboxed labels stand between them, and they count on from the one above
  (4 | 5 6) or up to the one below (1 | 2); a numbered step ("2. Adım") is never filled in;
- the stretch from the label down to the next label holds only lines hanging under the label's own text
  and answer rules: a panel or a picture in it, or a line starting further left, refuses it;
- the region touches no other region and is not taller than a hotspot may be.

The region is the label's lines and the answer rules under them. All of a run or none of it.

Measured on the 26 training and validation books before installing: 79 new regions. On labelled pages 24
of them: 21 answer a teacher box (IoU ≥ 0.5), 1 is a question the teacher boxed without its answer line
(`1903e365` p169, q12), 2 are steps of an activity the student had boxed a part of (`afec0d89` p32: the
student's mistake, repeated). No new region on the validation pages.

**`data/vision/bake-7/`** = `bake-6` (student-8 at 0.7, `--icons bind`, admit) + siblings, 35 books, 225 s
at 4 workers, 0.8 GB. Installed in `activities/books/`; GTK tests and the 164 detector tests pass.
`runs/phase9-compare-bake7-as-rules-vs-bake6.json` (its "rules" rows are the installed `bake-7`, its
"vision" rows `bake-6`),
`runs/phase9-accuracy-{valid,train,heldout}-bake7.json`.

| | Regions | Cuts/region | Overlaps, slivers, tall | Match | Found (IoU ≥ 0.5) | Same content | Answering no teacher box |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9), `bake-6` | 2742 | 0.021 | 0 | 0.455 | 0.591 | 0.569 | 77 of 559 (13.8 %) |
| held-out (9), `bake-7` | 2770 | 0.022 | 0 | 0.455 | 0.592 | 0.570 | 77 of 560 (13.8 %) |
| train (24 in the dataset), `bake-6` | | | | | 0.880 | 0.814 | 63 of 3,713 (1.7 %) |
| train (24), `bake-7` | | | | | 0.885 | 0.819 | 66 of 3,737 (1.8 %) |
| validation (2), `bake-6` and `bake-7` | | | | | 0.850 | 0.767 | 11 of 124 (8.9 %) |

The rule fills holes; it does not make the student see unseen layouts. Held-out moved by one box.

Not fixed, seen on the same sheet: the region of 6 b) stops one answer rule short (the student's box did).

## 2. Eight more books, student-9, `bake-8` (installed)

No Fen Lisesi book is left outside held-out (four exist: three held out, one trained on), so the science
books added are the nearest layouts. New, all `train`: `991f1bb8` Uptone 9 SB, `f2dd55aa` Uptone 9 WB,
`d5bf2536` Progress 10 SB, `44af382c` Progress 10 WB, `aa676ba4` Progress 11 SB (English; not in
`kitap_pdf_linkleri.txt`, so the reader does not list them), `2e1f772b` Fizik 12, `3e42e66e` Kimya 11,
`95f41768` Biyoloji 11. 1.04 GB of PDFs in `books/`, 1,469 pages rendered (78 s).

- Selection `data/vision/select/round4.json`, 40 pages a book, 320 pages; merged for the dataset as
  `round1+3+4.json`.
- Teacher: `teacher_all_routes.py --route agy` copied in under a temporary name and deleted again, prompt
  v2, no hint, `labels/agy/v2-round4-g38`: 320 labelled, 1,000 boxes, 380 requests, 659 s (828 of the CLI's
  1,500 used that day). `qa.py --allow-miss`: 311 of 320 trusted (`runs/phase9-qa-round4-agy.json`).
- Dataset: train 2,050 pages, 5,314 boxes, 32 books (was 1,739 / 4,345 / 24); validation and held-out
  unchanged. The previous `dataset.json` is `data/vision/dataset-student8.json`.
- `train.py --run student-9 --init student-8 --resolution 864 --epochs 7`: 897 s, 6.45 GB VRAM, RAM 11 of
  14 GB at peak, best epoch 3, val mAP50-95 0.888 (student-8: 0.882).
- `infer.py --run student-9 --resolution 864 --all --conf 0.3 --out data/vision/pred-s9-low`: 43 books.

Confidence, on validation (two books, 133 boxes a hotspot may hold):

| Bake | Found (IoU ≥ 0.5) | IoU ≥ 0.85 | Same content | Regions answering no teacher box |
| --- | ---: | ---: | ---: | ---: |
| `bake-7`: student-8 at 0.7 | 0.850 | 0.789 | 0.767 | 11 of 124 (8.9 %) |
| student-9 at 0.6 | 0.872 | 0.827 | 0.812 | 18 of 134 (13.4 %) |
| student-9 at 0.7 | 0.850 | 0.805 | 0.797 | 14 of 127 (11.0 %) |
| student-9 at 0.75 | 0.842 | 0.797 | 0.789 | 13 of 125 (10.4 %) |
| student-9 at 0.8 | 0.812 | 0.767 | 0.759 | 11 of 119 (9.2 %) |

On validation student-9 is not better than student-8: tighter boxes, three more hotspots on nothing at the
same number found. 0.7 was kept. **`data/vision/bake-8/`** = student-9 at 0.7, `--icons bind`, admit,
siblings, 43 books, 226 s at 4 workers. Held-out scored once, after the choice
(`runs/phase9-accuracy-*-bake8-student9.json`, `runs/phase9-compare-bake7-as-rules-vs-bake8-student9.json`;
in that file "rules" is `bake-7` for 35 books and the rules bake for the eight new ones):

| Held-out, 9 books | Regions | Cuts/region | Violations | Match | Found (IoU ≥ 0.5) | IoU ≥ 0.85 | Same content | Answering no teacher box |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `bake-5` (student-7) | 2818 | 0.023 | 0 | 0.473 | 0.623 | 0.559 | 0.607 | 93 of 603 (15.4 %) |
| `bake-7` (student-8 + siblings) | 2770 | 0.022 | 0 | 0.455 | 0.592 | 0.531 | 0.570 | 77 of 560 (13.8 %) |
| `bake-8` (student-9 + siblings) | 2826 | 0.025 | 0 | 0.464 | 0.640 | 0.576 | 0.615 | 73 of 595 (12.3 %) |

Per held-out book, found, `bake-7` → `bake-8`: `11941059` (English 11) 0.444 → 0.594; `c9f63718` (Waymark WB)
0.748 → 0.860; `a7a7886a` (English 12) 0.782 → 0.769; `6e4f65dc` (Fen Lisesi Kimya) 0.429 → 0.482;
`3a4479c7` (Fen Lisesi Biyoloji) 0.556 → 0.597; **`7fad03e9` (Fen Lisesi Fizik) 0.293 → 0.293**;
`cb558332` (Fizik) 0.816 → 0.851; `79cbecfa` (Biyoloji) 0.465 → 0.493; `51cdbbce` (Matematik) 0.651 → 0.613.

This time more books helped on unseen books, and where they were aimed: the English held-out books gained
most. Fen Lisesi Fizik did not move at all; its pages are not like the Fizik 12 added.

**Installed.** `activities/books/` holds `bake-8` for 43 books; `scan.py`'s `VISION_PRED` names
`pred-s9-low`; `scan.py --all` finds all 43 current; GTK tests and the 164 detector tests pass.
`data/vision/bake-7/` is the student-8 bake with siblings, should it be wanted back (copy it over and set
`VISION_PRED` to `pred-s8-low`).

## 3. The reported sheet under student-9, and a rule that was tried and removed

`7a92f6d0` sheet 20 now carries 4, 5, 6 (a), b), c) and 7 b). 5 comes from `between_siblings`; 6 a) the
student now draws itself (0.92). **It lost 3** (student-9 scores it 0.699, under 0.7) **and 7 shrank to
7 b)** (student-9 draws 7 a) and 7 b) apart, at 0.36 and 0.68; the publisher's icon admits 7 b)). Under
`bake-7` the sheet had 3, 4, 5, 6, b), c), 7. One sheet against the held-out table above.

Tried for it: `admit_in_sequence`, a doubtful box stands when it opens with the label printed next to a
sure box's label and one off it in the count. On validation it added 5 regions, 2 on a teacher box and 3 on
none (with a floor of 0.5; without a floor 4 and 4); on training pages 74 and 19. More wrong than right
where it counts. Removed, code and tests; `bake-8` was made without it.

## 4. A side effect, undone

`extract_activities.py --book <id> --meta-only` for a book with no manifest runs the catalogue sync, which
rewrites the manifest of every book in `kitap_pdf_linkleri.txt`. The publisher lists more entries now
(`11941059`: 33 on read pages → 96), so the 57 tracked files changed and every match rate with them.
`git checkout -- activities_meta` put the committed ones back before anything was baked or scored for
this handoff's tables (only `round4.json`'s choice of pages for the three science books read the
refreshed ones); the refreshed set is kept in `data/vision/meta-refresh-2026-10-03/` (untracked).
Only the five new English manifests are new files. Taking the refreshed manifests is a decision of its
own: it changes the reader's pins and the icon admits, and needs a re-bake.

## 5. Open

1. Fen Lisesi Fizik (`7fad03e9`, found 0.293) and the two biology books under 0.6: nothing added so far
   resembles them. No unlabelled book of their kind is left; the lever is more of their own kind of page
   from books that are not held out (`753fcdb0` Fen Lisesi Matematik has 40 labelled pages of 407), or
   looking at what the teacher boxes there that the student does not.
2. Validation is two books and 133 boxes; a three-box difference decides the confidence. A third
   validation book would make that choice less of a coin toss.
3. The publisher's refreshed manifests (§4).
4. A region that stops one answer rule short of the student's question (sheet 20, 6 b)).
5. The rules bake was not re-made with the corrected block detector.
