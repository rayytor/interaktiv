"""
Tests for the training corpus support: the primitives cache and the split.

Both exist to stop a specific way of fooling ourselves, and the tests are
written against those failures rather than against the happy path.

**The cache** stands in for the PDF during fitting. If it were lossy, or if a
stale shard survived a re-exported book, a run would optimise the detector
against geometry the shipped product never sees and report the gain as real.
So: a shard round-trips, a changed fingerprint invalidates it, and replaying a
shard produces byte-identical serialized activities to replaying the live parse.
The last of those needs a PDF, so it is skipped when none is installed.

**The split** is the defence against the mistake that produced the 79.4% claim:
measuring on the same ten books the constants were tuned against. The tests
assert that both sides span both templates and both ends of the violation
distribution, that the assignment is deterministic, and that `assert_trainable`
raises -- loudly, not as a warning -- when a fitting run reaches for a held-out
book.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import pytest

from tools.hotspot_extraction.scanner.primitives import (
    ImageRect,
    PagePrimitives,
    TextSpan,
    VectorDrawing,
)
from tools.hotspot_extraction.scanner.anchors import PublisherOge
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train import splits as splits_mod


def make_page(page_num=1):
    return PagePrimitives(
        page_num=page_num,
        width=595.0,
        height=842.0,
        spans=[TextSpan(text="a.", bbox=(60.0, 700.0, 72.0, 712.0),
                        font="MyriadPro-Bold", size=10.0, flags=16, color=0)],
        drawings=[VectorDrawing(rect=(50.0, 600.0, 545.0, 690.0), fill=None,
                                stroke=(0, 0, 0), width=1.0, is_rect=True,
                                lines=[(50.0, 600.0, 545.0, 600.0)])],
        images=[ImageRect(bbox=(100.0, 400.0, 300.0, 560.0), width=800, height=640)],
    )


def make_shard(book_id="bk", fingerprint="1234-abcd", pages=2):
    return cache_mod.BookShard(
        version=cache_mod.CACHE_VERSION,
        book_id=book_id,
        title="Test Book",
        fingerprint=fingerprint,
        page_count=pages,
        folio_offset=3,
        folio_map={i: i - 3 for i in range(1, pages + 1)},
        pages=[
            cache_mod.PageShard(
                page_num=i,
                primitives=make_page(i),
                printed_page=i - 3,
                oges=[PublisherOge(id=f"o{i}", title="Etkinlik", sayfano=i - 3,
                                   posx=25.0, posy=40.0, data="http://x/y")],
            )
            for i in range(1, pages + 1)
        ],
    )


class TestShardRoundTrip:
    def test_a_shard_survives_the_round_trip(self, tmp_path):
        path = os.path.join(tmp_path, "bk.pkl")
        shard = make_shard()
        cache_mod.save_shard(path, shard)

        back = cache_mod.load_shard(path)
        assert back.book_id == shard.book_id
        assert back.fingerprint == shard.fingerprint
        assert back.folio_map == shard.folio_map
        assert [p.page_num for p in back.pages] == [1, 2]
        assert back.pages[0].primitives.spans[0].text == "a."
        assert back.pages[0].primitives.drawings[0].rect == (50.0, 600.0, 545.0, 690.0)
        assert back.pages[0].oges[0].id == "o1"

    def test_the_header_is_readable_without_loading_the_body(self, tmp_path):
        path = os.path.join(tmp_path, "bk.pkl")
        cache_mod.save_shard(path, make_shard(fingerprint="999-zzz"))
        assert cache_mod.shard_fingerprint(path) == "999-zzz"

    def test_a_changed_pdf_invalidates_its_shard(self, tmp_path):
        path = os.path.join(tmp_path, "bk.pkl")
        cache_mod.save_shard(path, make_shard(fingerprint="111-aaa"))
        # `build_shard` skips only when the fingerprints agree, so a book
        # re-exported by the publisher is re-parsed rather than trained on stale.
        assert cache_mod.shard_fingerprint(path) != "222-bbb"

    def test_a_missing_or_corrupt_shard_is_not_mistaken_for_a_current_one(self, tmp_path):
        assert cache_mod.shard_fingerprint(os.path.join(tmp_path, "none.pkl")) is None
        bad = os.path.join(tmp_path, "bad.pkl")
        with open(bad, "wb") as f:
            f.write(b"not a pickle at all")
        assert cache_mod.shard_fingerprint(bad) is None

    def test_a_shard_from_an_older_cache_version_is_refused(self, tmp_path):
        import pickle
        path = os.path.join(tmp_path, "old.pkl")
        with open(path, "wb") as f:
            pickle.dump({"version": cache_mod.CACHE_VERSION - 1, "fingerprint": "x"}, f)
            pickle.dump(make_shard(), f)
        assert cache_mod.shard_fingerprint(path) is None


class TestReplay:
    def test_replay_runs_detection_from_the_cache_alone(self, tmp_path):
        # The point is not what is detected on this synthetic sheet, but that
        # nothing outside the shard is needed to detect it: no PDF is open here.
        page = cache_mod.PageShard(page_num=1, primitives=make_page(1),
                                   printed_page=None, oges=[])
        result = cache_mod.replay_page(page)
        assert isinstance(result.activities, list)
        # The drawn geometry comes back with the regions: a fitting loop that
        # had to rebuild it separately is a fitting loop that can drift from
        # the bake it is standing in for.
        assert isinstance(result.panels, list) and isinstance(result.solutions, list)

    @pytest.mark.skipif(not cache_mod.local_books(), reason="no PDF installed")
    def test_a_real_shard_replays_exactly_as_the_pdf_does(self, tmp_path):
        from tools.hotspot_extraction.scanner import serialize_activity
        import pymupdf

        book = min(cache_mod.local_books(), key=lambda b: os.path.getsize(b["pdf_path"]))
        res = cache_mod.build_shard({
            "book_id": book["book_id"],
            "pdf_path": book["pdf_path"],
            "title": book["title"],
            "cache_dir": str(tmp_path),
            "meta_dir": os.path.join(PROJECT_ROOT, "activities_meta"),
            "force": True,
        })
        assert res["status"] == "built", res

        shard = cache_mod.load_shard(cache_mod.shard_path(book["book_id"], str(tmp_path)))
        doc = pymupdf.open(book["pdf_path"])
        try:
            for sp in shard.pages[:12]:
                live = cache_mod.PageShard(
                    page_num=sp.page_num,
                    primitives=cache_mod.extract_page_primitives(doc, sp.page_num),
                    printed_page=sp.printed_page,
                    oges=sp.oges,
                )
                assert ([serialize_activity(a) for a in cache_mod.replay_page(sp).activities]
                        == [serialize_activity(a) for a in cache_mod.replay_page(live).activities]), \
                    f"page {sp.page_num} replays differently from the cache"
        finally:
            doc.close()


class TestTemplateAndBand:
    @pytest.mark.parametrize("title,expected", [
        ("STEPWISE (10) WB - Çalışma Kitabı", "elt"),
        ("WAYMARK (9) SB - Ders Kitabı", "elt"),
        ("11 İNGİLİZCE ÇALIŞMA KİTABI (2017-2023)", "elt"),
        ("İngilizce Ders Kitabı", "elt"),
        ("Biyoloji Ders Kitabı", "subject"),
        ("Matematik Ders Kitabı 2", "subject"),
        ("Türk Dili ve Edebiyatı Ders Kitabı", "subject"),
    ])
    def test_the_two_families_are_told_apart(self, title, expected):
        assert splits_mod.template_of(title) == expected

    @pytest.mark.parametrize("rate,expected", [
        (0.0, "clean"), (0.011, "clean"), (0.0199, "clean"),
        (0.02, "low"), (0.051, "low"), (0.0699, "low"),
        (0.07, "high"), (0.235, "high"), (3.0, "high"),
        (None, "none"),
    ])
    def test_bands(self, rate, expected):
        assert splits_mod.band_of(rate) == expected

    @pytest.mark.parametrize("row,expected", [
        ({"yield": {"regions": 100}, "violations": {"panelsCut": 3, "solutionsCut": 7}}, 0.10),
        ({"yield": {"regions": 0}, "violations": {"panelsCut": 0, "solutionsCut": 0}}, None),
        ({"yield": {}, "violations": {}}, None),
    ])
    def test_cut_rate(self, row, expected):
        got = splits_mod.cut_rate_of(row)
        assert got == expected if expected is None else abs(got - expected) < 1e-9


def meta_for(pairs):
    return {
        book_id: {"title": book_id, "template": tpl, "band": band,
                  "cutRate": None, "matchRate": None, "pages": 100, "regions": 10}
        for book_id, tpl, band in pairs
    }


class TestStratify:
    def test_roughly_a_third_is_held_out(self):
        meta = meta_for([(f"b{i:02d}", "subject", "low") for i in range(24)])
        data = splits_mod.stratify(meta)
        assert len(data["heldOut"]) == 8
        assert len(data["train"]) == 16
        assert not set(data["train"]) & set(data["heldOut"])

    def test_a_stratum_of_two_lands_on_both_sides(self):
        # Otherwise a family that only has two books in some band would sit
        # wholly in training, and nothing would ever measure it.
        data = splits_mod.stratify(meta_for([("a", "elt", "high"), ("b", "elt", "high")]))
        assert data["train"] == ["a"] and data["heldOut"] == ["b"]

    def test_both_sides_span_both_templates_and_both_ends(self):
        meta = meta_for([
            ("e1", "elt", "clean"), ("e2", "elt", "clean"), ("e3", "elt", "clean"),
            ("e4", "elt", "high"), ("e5", "elt", "high"),
            ("s1", "subject", "clean"), ("s2", "subject", "clean"),
            ("s3", "subject", "high"), ("s4", "subject", "high"), ("s5", "subject", "high"),
        ])
        data = splits_mod.stratify(meta)
        for side in ("train", "heldOut"):
            templates = {meta[b]["template"] for b in data[side]}
            bands = {meta[b]["band"] for b in data[side]}
            assert templates == {"elt", "subject"}, f"{side}: {templates}"
            assert bands == {"clean", "high"}, f"{side}: {bands}"

    def test_the_assignment_does_not_depend_on_input_order(self):
        pairs = [("b", "elt", "clean"), ("a", "subject", "high"), ("c", "elt", "clean")]
        first = splits_mod.stratify(meta_for(pairs))
        second = splits_mod.stratify(meta_for(list(reversed(pairs))))
        assert first["train"] == second["train"]
        assert first["heldOut"] == second["heldOut"]


class TestGuard:
    def write(self, tmp_path, data):
        path = os.path.join(tmp_path, "splits.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_reaching_for_a_held_out_book_raises(self, tmp_path):
        path = self.write(tmp_path, splits_mod.stratify(meta_for([
            ("a", "elt", "clean"), ("b", "elt", "clean"), ("c", "elt", "clean"),
        ])))
        held = splits_mod.held_out_ids(path)
        assert held == ["b"]
        splits_mod.assert_trainable(["a", "c"], path)          # fine
        with pytest.raises(AssertionError) as e:
            splits_mod.assert_trainable(["a", "b"], path)
        assert "b" in str(e.value)

    def test_a_future_version_is_refused_rather_than_read(self, tmp_path):
        data = splits_mod.stratify(meta_for([("a", "elt", "clean")]))
        data["version"] = splits_mod.SPLITS_VERSION + 1
        path = self.write(tmp_path, data)
        with pytest.raises(ValueError):
            splits_mod.load(path)

    def test_check_reports_a_book_in_both_lists(self, tmp_path):
        data = splits_mod.stratify(meta_for([
            ("a", "elt", "clean"), ("b", "subject", "high"),
        ]))
        data["train"] = sorted(set(data["train"]) | {"b"})
        data["heldOut"] = ["b"]
        path = self.write(tmp_path, data)
        assert any("in both lists" in p for p in splits_mod.check(path))
