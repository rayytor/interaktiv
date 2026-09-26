# Kitap Sunumları — teacher slide decks as free question-region labels

Turkish English teachers publish "kitap sunumları" (book presentations): one
slide deck per unit of the MEB coursebook, built by cropping every exercise
out of the page and pasting the crop on a slide, usually with the printed page
number on the slide.  Each crop is a human-drawn activity region, which is the
label the hotspot detector cannot get any other way without hand labelling.

## Sources (third-party, text-navigable — no browser automation needed)

| site | where | what |
|---|---|---|
| `aogultegin` | https://highschool.aogultegin.com → *Grade N → Unit M → Book Presentations* | The content pages list ~75 decks (WAYMARK 9, STEPWISE 10, SPICE UP 11, NOTIFIER 12, PASİFİK, YILDIRIM, SUNSHINE, COUNT ME IN, ERKAD, the author's own ACTIVITY BOOK), but only the current 2026-2027 decks still exist on `slides.aogultegin.com/<slug>/`; the 2025-2026 and older ones return 404 and are flagged `available: false` in `sources.json`. Two old decks were recovered from their Google Drive "DOWNLOAD" zips (SPICE UP 11 Unit 10; a YILDIRIM 11 Unit 8 flipbook). |
| `ingilizceciyiz` | https://www.ingilizceciyiz.com/ingilizce-kitap-sunumlari/ → *N. Sınıf Kitap Sunumları* | 11 decks for 2026-2027 (Revision 1/2 + Theme/Unit 1 for grades 9–12). Same iSpring format under `wp-content/uploads/ingilizcederskitabisunumlari/`. More units are announced ("yakında"). |

The "DOWNLOAD" entries on aogultegin point at Google Drive zips of the same
HTML5 exports; most are dead. The two that survive were unpacked by hand into
`kitap_sunumlari/aogultegin/` (audio, video and fonts dropped) and their
manifests built by pointing the fetcher at a `file://` URL.

## What was installed (2026-09-26)

21 decks, 1109 slides, ~4000 images, 538 MB. Per site: 11 ingilizceciyiz
(2026-2027: WAYMARK 9 R1/R2/T1, STEPWISE 10 R1/R2/T1, SPICE UP 11 U1,
YILDIRIM 11 U1, LİDER 12 U1, NOTIFIER 12 U1 SB+WB) and 10 aogultegin
(WAYMARK 9 R+T1, STEPWISE 10 R+T1, SPICE UP 11 U1+U10, YILDIRIM 11 U1+U8,
NOTIFIER 12 U1, SUNSHINE 12 U1). `kitap_sunumlari/index.json` is the list.

Verified by eye on WAYMARK 9 Theme 1: the ingilizceciyiz deck shows the full
page (printed page 26 = PDF index 27 of `0a3fbb41…`) and then one crop per
activity with the page number in the slide text. The aogultegin decks mix
book crops with the teacher's own rubric/grammar cards, so their images must
be matched against the PDF before being trusted as labels.

## Layout on disk

```
kitap_sunumlari/
  index.json                         # one row per deck (tracked in git)
  <site>/<slug>/
    index.html                       # the iSpring page (holds presInfo)
    presinfo.json                    # decoded presInfo: slide order, per-slide text
    manifest.json                    # what we use: slides → images with placement
    data/slideN.js                   # slide HTML (small)
    data/imgN.png|jpg                # the crops
```

`manifest.json` has, per slide, the slide's visible text (`text`, usually
containing the printed page number) and each `<img>` with its absolute
`x, y, w, h` on the slide (slide size in `slide_size`), `background=true` for
the full-slide template image.  `images` maps each file to pixel size, bytes,
sha1 and how many slides use it.  Navigation thumbnails, icons and logos are
small and reused across slides; the page crops are the large single-use images.

## Matching installed books

| deck book | installed PDF |
|---|---|
| WAYMARK 9 | `0a3fbb41…` (SB), `c9f63718…` (WB) |
| STEPWISE 10 | `0e966773…` (SB), `550e601a…` (WB) |
| SPICE UP 11 | `11941059…` (SB), `3d372038…` (WB) |
| NOTIFIER 12 | check `a7a7886a…` / `ad3f3275…` (cover pages carry no text) |

The other series (PASİFİK, YILDIRIM, SUNSHINE, COUNT ME IN, ERKAD, LİDER) are
distributed books whose PDFs are not in the EBA catalogue under those names.

## Refetching

```
python3 tools/kitap_sunumlari/fetch_ispring.py            # everything, resumable
python3 tools/kitap_sunumlari/fetch_ispring.py --site ingilizceciyiz
python3 tools/kitap_sunumlari/fetch_ispring.py --only WAYMARK --only STEPWISE
```

One deck at a time, six connections, existing files skipped, a pause between
decks. Audio, video, fonts and CSS are not fetched.
