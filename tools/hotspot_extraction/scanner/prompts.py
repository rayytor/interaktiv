"""
Marker-Free Question Detection.

`markers.py` finds an activity by the label printed in front of it -- a, b, c,
or 1, 2, 3 -- and validates the run as a sequence. That is the right rule where
a page enumerates its questions, and no rule at all where it does not, which in
these books is most of them: a titled box ("Konuya Başlarken", "Etkinlik"), a
single instruction over a diagram, a worked task with no number anywhere on the
sheet. Those pages came back empty, and the material that should have been one
hotspot was either missed entirely or picked up in pieces by whatever lettered
activity happened to sit above it.

What marks a question when nothing enumerates it is its grammar. Turkish
textbooks set instructions in the formal imperative -- a verb ending in -ınız,
-iniz, -unuz or -ünüz at the close of the sentence -- or in the exhortative
-alım / -elim; English coursebooks open on a bare imperative verb. Beside that
sits a small closed vocabulary of rubric headings the houses use to title an
exercise. This module reads the sheet in paragraphs and takes the paragraphs
that speak that way as the openings of questions.

It is deliberately additive and deferential: where `markers.py` has found a run
of labels, that run owns the page and nothing here is emitted over it. A prompt
is raised only for material no marker reaches.
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .layout import (
    COLUMN_TOLERANCE,
    Column,
    PageLayout,
    _text_lines,
)
from .markers import DetectedMarker, detect_markers, detect_subquestions
from .primitives import PagePrimitives, TextSpan
from .trace import GrowthTrace

PARA_LEADING = 1.55      # multiple of leading that opens a new paragraph
PARA_INDENT = 18.0       # pt: left-edge shift that opens a new paragraph
SIZE_SHIFT = 1.12        # ratio between two lines' type sizes that opens a new paragraph
SHORT_LINE = 12.0        # pt short of the measure at which a line ends its paragraph
PROMPT_MIN_CHARS = 12    # a question says more than a word or two
PROMPT_MAX_LINES = 8     # lines of one opening paragraph worth reading for cues
HEADING_RATIO = 1.12     # size over body at which a line is a title, not prose
RUBRIC_MAX_CHARS = 48    # a rubric heading is a title, not a sentence
QUESTION_MAX_CHARS = 140 # a paragraph short enough for a mid-text "?" to be its point
RUBRIC_REACH = 90.0      # pt: gap past which a rubric stops gathering its own instruction
SCOPE_MIN = 20.0         # pt: shortest scope a prompt can own and still be one
NEAR_MARKER = 6.0        # pt: slack when asking whether a line is a marker's own

# A line that closes its sentence, and a paragraph that opens in lower case --
# the two signals that tell a new thought from the continuation of one.
SENTENCE_END_RE = re.compile(r"[.!?:;»”\"')\]]\s*$", re.UNICODE)
LOWER_START_RE = re.compile(r"^[a-zçğıöşü]", re.UNICODE)

# The formal imperative that closes a Turkish instruction: "...okuyunuz.",
# "...işaretleyiniz.", "...yazmayınız."
#
# Two things separate it from endings that look identical. The first is
# position: the imperative closes its sentence, where the second person plural
# possessive that shares its shape sits mid-sentence all over ordinary prose --
# "verdiğiniz karara göre" is not an instruction, "kararınızı veriniz." is. The
# second is the person marker "s" in front of it, which the aorist, the present
# and the future all carry and the imperative never does: "gelirsiniz." tells
# the reader what will happen to them, "geliniz." tells them to come. Refusing
# the "s" costs only imperatives built on a stem ending in one, which is a
# handful of verbs no textbook gives an instruction with.
TR_IMPERATIVE_RE = re.compile(
    r"\w{2,}(?<![sS])(?:ınız|iniz|unuz|ünüz)\s*[.!?:;]",
    re.IGNORECASE | re.UNICODE,
)
TR_IMPERATIVE_END_RE = re.compile(
    r"\w{2,}(?<![sS])(?:ınız|iniz|unuz|ünüz)\s*$",
    re.IGNORECASE | re.UNICODE,
)
# "...inceleyelim.", "...yanıtlayalım." -- the same instruction in the first
# person plural, which the primary-school books prefer.
TR_EXHORT_RE = re.compile(
    r"\w{3,}(?:alım|elim)\s*[.!?:]|\w{3,}(?:alım|elim)\s*$",
    re.IGNORECASE | re.UNICODE,
)
# The bare second-person imperative of the middle-school books: "...okuyun.",
# "...eşleştirin." Only the forms built on a vowel stem are admitted. The bare
# "-ın / -in" is also the genitive, which ends a great many ordinary Turkish
# sentences ("...kitabın."), and no position test tells the two apart; the
# linking "y" does, at the cost of the consonant-stem imperatives, which these
# books almost always set in the formal "-ınız" above anyway.
TR_SHORT_IMPERATIVE_RE = re.compile(
    r"\w{2,}(?:ayın|eyin|yın|yin|yun|yün)\s*[.!?]",
    re.IGNORECASE | re.UNICODE,
)

# What the houses title an exercise. Matched at the head of a paragraph only.
RUBRIC_RE = re.compile(
    r"^\s*(?:"
    r"etkinlik|uygulama|alıştırma|alistirma|çalışma\s+kağıdı|"
    r"konuya\s+başlarken|hazırlık\s+çalışmaları|hazırlanalım|"
    r"sıra\s+sizde|sıra\s+sende|kendimizi\s+değerlendirelim|"
    r"ölçme\s+ve\s+değerlendirme|değerlendirme\s+soruları|"
    r"neler\s+öğrendik|ne\s+öğrendik|araştıralım|düşünelim|tartışalım|"
    r"yanıtlayalım|cevaplayalım|uygulayalım|inceleyelim|"
    r"bunları\s+yapalım|birlikte\s+yapalım|"
    r"\d{0,2}\s*\.?\s*soru|problem|proje\s+ödevi|performans\s+görevi|"
    r"activity|exercise|task|practice|warm[\s-]?up|let'?s\s+\w+|"
    r"your\s+turn|check\s+yourself|self[\s-]?assessment|study\s+skills"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# A reading passage / stem introducing a group of questions (e.g. "1-5. soruları...", "59-61. soruları...")
# is the stem for that group, not an individual activity prompt.
STEM_RANGE_RE = re.compile(
    r"^\s*\d+\s*[-–—]\s*\d+\.\s*sorular",
    re.IGNORECASE | re.UNICODE,
)

# English coursebook instructions open on the verb.
EN_IMPERATIVE_RE = re.compile(
    r"^\s*(?:now\s+)?(?:read|write|match|complete|fill|choose|answer|listen|"
    r"look|circle|underline|tick|number|put|order|rewrite|discuss|talk|work|"
    r"ask|tell|find|label|draw|colour|color|say|repeat|practise|practice|"
    r"decide|guess|compare|describe|explain|make|use|watch|study|check|"
    r"correct|replace|translate|sort|classify)\b",
    re.IGNORECASE | re.UNICODE,
)

# A label the page has set inside the instruction rather than out in the gutter
# -- "a) Hangileri ..." as one run of type. `markers.py` cannot see these: it
# reads a label from a span that holds nothing but the label, and here there is
# no such span. They are enumerated questions all the same.
INLINE_LABEL_RE = re.compile(
    r"^\(?\s*([a-zçğıöşü]|\d{1,2})\s*[.)\]]\s+(?=\S)",
    re.UNICODE,
)

# A question opens on a word or a number. Anything else at the head of a
# paragraph is ornament -- a bullet, a dingbat, an arrow, a rule -- and what it
# introduces is an item of a list whose own stem is the question.
OPENS_ON_WORD_RE = re.compile(
    r"^[(\[\"'«‘“]?\s*[0-9A-Za-zÇĞİÖŞÜÂÎÛçğıöşüâîû]",
    re.UNICODE,
)

# A line that is only furniture: a folio, a running head, a caption label.
FURNITURE_RE = re.compile(
    r"^\s*(?:görsel|şekil|tablo|grafik|harita|resim|çizelge|figure|fig\.?|table)\s*[\d.]*\s*$",
    re.IGNORECASE | re.UNICODE,
)


@dataclass
class Paragraph:
    """A run of lines the page sets as one block of type."""
    lines: List[dict]
    column: int
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    text: str
    opens_sentence: bool = True   # nothing runs into it from the line above


def _line_text(line: dict) -> str:
    return " ".join(s.text for s in line["spans"]).strip()


def _line_size(line: dict) -> float:
    return max((s.size for s in line["spans"]), default=0.0)


def merge_rows(lines: List[dict]) -> List[dict]:
    """
    Join the fragments of one printed line back into a line.

    `_text_lines` breaks a row wherever the gap between two words exceeds a
    column channel, which is right for telling two columns apart and wrong for
    justified type, where a loose line's word spacing reaches the same width.
    Paragraph grouping then saw the second fragment as a line at a wildly
    different left edge and broke the paragraph across it, so a prompt opened
    in the middle of its own sentence and the hotspot began there too.
    """
    rows: List[dict] = []
    for line in sorted(lines, key=lambda l: -(l["y0"] + l["y1"]) / 2.0):
        mid = (line["y0"] + line["y1"]) / 2.0
        if rows and abs(rows[-1]["y"] - mid) < 2.5:
            row = rows[-1]
            row["x0"] = min(row["x0"], line["x0"])
            row["x1"] = max(row["x1"], line["x1"])
            row["y0"] = min(row["y0"], line["y0"])
            row["y1"] = max(row["y1"], line["y1"])
            row["spans"].extend(line["spans"])
        else:
            rows.append({
                "x0": line["x0"], "x1": line["x1"],
                "y0": line["y0"], "y1": line["y1"],
                "y": mid, "spans": list(line["spans"]),
            })
    for row in rows:
        row["spans"].sort(key=lambda sp: sp.bbox[0])
    return rows


def _column_of(x0: float, x1: float, columns: Sequence[Column]) -> int:
    """The column a line belongs to, by its left edge and then by nearness."""
    cx = (x0 + x1) / 2.0
    best, best_dist = 0, float("inf")
    for col in columns:
        if col.x0 - COLUMN_TOLERANCE <= x0 <= col.x1:
            return col.index
        dist = min(abs(cx - col.x0), abs(cx - col.x1))
        if dist < best_dist:
            best, best_dist = col.index, dist
    return best


def group_paragraphs(
    lines: List[dict],
    columns: Sequence[Column],
    body_font_size: float,
) -> List[Paragraph]:
    """
    Read the page's lines as paragraphs, column by column.

    A paragraph breaks where the page breaks it: a vertical gap wider than the
    leading, a shift of the left edge, or a change of type size. The unit
    matters because a question is a paragraph, not a line -- its instruction
    may run three lines before the verb that reveals it is an instruction at
    all, and its opening line is where the hotspot has to start.
    """
    by_column: Dict[int, List[dict]] = {}
    for ln in merge_rows(lines):
        text = _line_text(ln)
        if not text or FURNITURE_RE.match(text):
            continue
        by_column.setdefault(_column_of(ln["x0"], ln["x1"], columns), []).append(ln)

    paragraphs: List[Paragraph] = []
    for col_index, col_lines in by_column.items():
        col_lines.sort(key=lambda l: -(l["y0"] + l["y1"]) / 2.0)

        # The leading this column is actually set on, so the break test does not
        # read a page-wide statistic against a block of captions.
        gaps = sorted(
            ((col_lines[i]["y0"] + col_lines[i]["y1"]) / 2.0
             - (col_lines[i + 1]["y0"] + col_lines[i + 1]["y1"]) / 2.0)
            for i in range(len(col_lines) - 1)
        )
        gaps = [g for g in gaps if g > 1.0]
        leading = gaps[len(gaps) // 2] if gaps else 1.3 * body_font_size

        # The measure this column is set to. A line ending well short of it has
        # ended its paragraph, which is the oldest signal in typesetting and the
        # one that tells a wrapped line from a new thought when the page sets
        # neither punctuation nor extra leading between them.
        edges = sorted(l["x1"] for l in col_lines)
        measure = edges[int(len(edges) * 0.75)] if edges else 0.0

        current: List[dict] = []
        opens = True
        for ln in col_lines:
            if current:
                prev = current[-1]
                gap = ((prev["y0"] + prev["y1"]) / 2.0
                       - (ln["y0"] + ln["y1"]) / 2.0)
                # A change of type size breaks a paragraph, but only a real
                # one. Comparing raw points broke a hyphenated line off its own
                # sentence whenever the two rows differed by a hair, and the
                # prompt then opened on the second half of a word.
                hi = max(_line_size(ln), _line_size(prev))
                lo = max(0.1, min(_line_size(ln), _line_size(prev)))
                size_shift = hi / lo > SIZE_SHIFT
                indent_shift = abs(ln["x0"] - current[0]["x0"]) > PARA_INDENT
                spaced = gap > PARA_LEADING * leading
                # A label opens a block, whatever the leading does. A page that
                # sets "a) ... b) ... c) ..." one under another at its ordinary
                # leading is enumerating, and reading the run as a single
                # paragraph loses every question in it but the first.
                labelled = bool(INLINE_LABEL_RE.match(_line_text(ln)))
                if spaced or size_shift or indent_shift or labelled:
                    paragraphs.append(_make_paragraph(current, col_index, opens))
                    # Whether what follows begins a sentence of its own. A
                    # paragraph broken off by indent or type size alone, from a
                    # line that did not close its sentence, is the same
                    # sentence still running -- a wrapped bullet, a hyphenated
                    # word, a line the page justified loosely. A prompt may not
                    # open there, because the hotspot would open there too,
                    # halfway through the instruction it is supposed to carry.
                    opens = (
                        spaced
                        or labelled
                        or bool(SENTENCE_END_RE.search(_line_text(prev)))
                        or prev["x1"] < measure - SHORT_LINE
                        or _line_size(prev) > _line_size(ln) * SIZE_SHIFT
                    )
                    current = []
            current.append(ln)
        if current:
            paragraphs.append(_make_paragraph(current, col_index, opens))

    paragraphs.sort(key=lambda p: (p.column, -p.y1))
    return paragraphs


def _make_paragraph(
    lines: List[dict],
    column: int,
    opens_sentence: bool = True,
) -> Paragraph:
    text = " ".join(_line_text(l) for l in lines[:PROMPT_MAX_LINES]).strip()
    return Paragraph(
        lines=list(lines),
        column=column,
        x0=min(l["x0"] for l in lines),
        y0=min(l["y0"] for l in lines),
        x1=max(l["x1"] for l in lines),
        y1=max(l["y1"] for l in lines),
        size=max(_line_size(l) for l in lines),
        text=re.sub(r"\s+", " ", text),
        opens_sentence=opens_sentence,
    )


def reads_as_rubric(para: Paragraph, body_font_size: float) -> bool:
    """Whether this paragraph is the printed title of an exercise."""
    text = para.text.strip()
    if not RUBRIC_RE.match(text):
        return False
    # A rubric is a title: short, and usually set larger or bolder than the
    # prose around it. The same words inside a running sentence are not one.
    if len(text) <= RUBRIC_MAX_CHARS:
        return True
    return para.size >= body_font_size * HEADING_RATIO


def inline_label(para: Paragraph) -> Optional[str]:
    """The enumeration label the page set inside this paragraph's first line."""
    m = INLINE_LABEL_RE.match(para.text.strip())
    return m.group(1) if m else None


def reads_as_question(para: Paragraph) -> bool:
    """Whether this paragraph asks the reader to do something."""
    text = para.text.strip()
    if len(text) < PROMPT_MIN_CHARS:
        return False
    if STEM_RANGE_RE.search(text):
        return False
    # A bullet opens an item, not a question. Whatever instruction the list is
    # carrying out was given above it, and that stem is the prompt.
    if not OPENS_ON_WORD_RE.match(text):
        return False
    # A sentence does not begin in lower case. Where one appears to, the page is
    # continuing a sentence the line above started -- a wrapped bullet, a
    # hyphenated word, a cell of a table read out of order -- and the paragraph
    # grouping merely failed to see it. Opening a hotspot there would start it
    # mid-instruction, which is the defect this whole module exists to fix. An
    # enumerated label is the exception: "a) Kutucuklarda ..." opens in lower
    # case and is unambiguously the head of a question.
    if LOWER_START_RE.match(text) and not INLINE_LABEL_RE.match(text):
        return False
    # A question mark at the close of the paragraph is the paragraph asking
    # something. One buried in the middle of a long block of exposition is
    # rhetorical -- "Besinlerin icerisine enerji nasil aktarilmaktadir? ..."
    # opens a page of explanation, not an exercise -- so it only counts while
    # the paragraph is short enough to be a prompt in the first place.
    if text.rstrip().endswith("?"):
        return True
    if "?" in text and len(text) <= QUESTION_MAX_CHARS:
        return True
    if TR_IMPERATIVE_RE.search(text) or TR_IMPERATIVE_END_RE.search(text):
        return True
    if TR_EXHORT_RE.search(text):
        return True
    if TR_SHORT_IMPERATIVE_RE.search(text):
        return True
    if EN_IMPERATIVE_RE.match(text):
        return True
    return False


def _opening_span(para: Paragraph) -> TextSpan:
    """
    The span a prompt hangs off: the first word of its opening line.

    Growth reads an activity from its marker -- flow starts under the marker's
    box and the headline is built from the text to its right -- so a prompt has
    to present itself the same way, as the first thing on its own first line.
    """
    first = para.lines[0]
    return min(first["spans"], key=lambda s: s.bbox[0])


def detect_prompts(
    primitives: PagePrimitives,
    layout: PageLayout,
    markers: Sequence[DetectedMarker],
    content_bottom: float = 44.0,
    trace: Optional[GrowthTrace] = None,
) -> List[DetectedMarker]:
    """
    Raise activity markers for the questions a page asks without enumerating them.

    Args:
        primitives: the page's extracted primitives.
        layout: the page's resolved layout.
        markers: the enumeration markers already detected. Their material is
            theirs; nothing is raised over it.
        content_bottom: foot of the live area.

    Returns:
        Prompt markers in reading order, each flagged `is_prompt`.
    """
    content_box = layout.content_box
    body_spans = [
        s for s in primitives.spans
        if s.bbox[1] >= content_box[1] - 2.0 and s.bbox[3] <= content_box[3] + 2.0
    ]
    if not body_spans:
        return []

    lines = _text_lines(body_spans)
    paragraphs = group_paragraphs(lines, layout.columns, layout.body_font_size)
    if not paragraphs:
        return []
    if trace is not None:
        trace.count("prompt.paragraphs", len(paragraphs))

    # Where the enumerated activities stand. A marker run owns everything from
    # its first label to the foot of its column, because that is the scope
    # growth reads it in; raising a prompt inside it would split one question's
    # instruction away from its own list.
    claimed: List[Tuple[int, float, float]] = []
    for col in layout.columns:
        col_markers = [m for m in markers if m.column_index == col.index]
        if col_markers:
            claimed.append((
                col.index,
                max(m.span.bbox[3] for m in col_markers) + NEAR_MARKER,
                content_bottom,
            ))

    def is_claimed(para: Paragraph) -> bool:
        mid = (para.y0 + para.y1) / 2.0
        for col_index, top, bottom in claimed:
            if para.column == col_index and bottom - NEAR_MARKER <= mid <= top:
                return True
        # A paragraph that merely overlaps a marker's own line is that marker's.
        for m in markers:
            if (m.span.bbox[1] - NEAR_MARKER <= para.y1
                    and para.y0 <= m.span.bbox[3] + NEAR_MARKER
                    and m.span.bbox[0] >= para.x0 - NEAR_MARKER
                    and m.span.bbox[2] <= para.x1 + NEAR_MARKER):
                return True
        return False

    # A rubric heading titles the question that follows it, so the two are one
    # opening and the hotspot starts at the title.
    openings: List[Tuple[Paragraph, Optional[str]]] = []
    skip: set = set()
    for i, para in enumerate(paragraphs):
        if id(para) in skip or is_claimed(para) or not para.opens_sentence:
            continue
        if reads_as_rubric(para, layout.body_font_size):
            openings.append((para, None))
            # The title names the whole exercise, so the instruction under it is
            # the same question continuing, however many paragraphs the page
            # sets it in. Gathering them stops where growth's own flow would
            # stop -- at a gap too wide to read across, or at the next title.
            cursor = para.y0
            for follower in paragraphs[i + 1:]:
                if follower.column != para.column or follower.y1 > para.y0:
                    continue
                if cursor - follower.y1 > RUBRIC_REACH:
                    break
                if reads_as_rubric(follower, layout.body_font_size):
                    break
                cursor = follower.y0
                if reads_as_question(follower) or inline_label(follower):
                    skip.add(id(follower))
            continue
        # An inline label decides nothing on its own. A numbered legend beside a
        # diagram -- "1. Glikoliz", "2. Krebs dongusu" -- is a run of them and
        # is not a run of questions; so is a list of flowchart exits. The label
        # only names an opening the grammar has already found.
        if reads_as_question(para):
            openings.append((para, inline_label(para)))
        elif trace is not None:
            # The paragraph opens a sentence in the body face and is not
            # claimed by any marker, but the grammar did not read it as asking
            # anything. On a sheet that ends up empty this is the bucket to
            # read: it is either genuine prose or a question phrased in a way
            # `reads_as_question` does not yet cover.
            trace.drop("prompt", "not-a-question",
                       rect=(para.x0, para.y0, para.x1, para.y1),
                       text=para.text,
                       column=para.column)

    if not openings:
        if trace is not None:
            trace.drop("prompt", "no-opening", paragraphs=len(paragraphs))
        return []
    if trace is not None:
        trace.count("prompt.openings", len(openings))

    # Two openings the page sets as one instruction -- a directive that runs
    # into its own second sentence, each read as its own paragraph -- are one
    # question. They are merged when nothing but leading separates them.
    merged: List[Tuple[Paragraph, Optional[str]]] = []
    for para, label in openings:
        if merged and not label:
            prev = merged[-1][0]
            if (prev.column == para.column
                    and abs(para.x0 - prev.x0) <= PARA_INDENT
                    and 0.0 <= prev.y0 - para.y1 <= PARA_LEADING * layout.body_font_size):
                continue
        merged.append((para, label))

    out: List[DetectedMarker] = []
    for index, (para, label) in enumerate(merged, 1):
        col = next(
            (c for c in layout.columns if c.index == para.column),
            layout.columns[0],
        )
        # What this prompt reads down to: the next prompt in its column, or the
        # foot of the sheet.
        nxt = next(
            (p for p, _ in merged if p.column == para.column and p.y1 < para.y0),
            None,
        )
        floor = nxt.y1 if nxt else content_bottom
        if para.y1 - floor < SCOPE_MIN:
            continue

        # "Yonerge", "Asagidaki sorulari cevaplayiniz." -- an instruction
        # standing over a numbered list is the stem of that list, and the
        # questions are the numbered items beneath it. Raising it as well would
        # wrap a second hotspot around every one of them.
        if any(
            m.column_index == para.column and floor <= m.span.bbox[3] <= para.y0
            for m in markers
        ):
            continue

        span = _opening_span(para)
        items = detect_subquestions(
            act_span=span,
            next_act_span=_opening_span(nxt) if nxt else None,
            body_spans=body_spans,
            col_x0=col.x0,
            col_x1=col.x1,
            bottom_limit=content_bottom,
        )

        out.append(
            DetectedMarker(
                label=label or "",
                slug=label or f"q{index}",
                normalized_value=float(index),
                span=span,
                column_index=para.column,
                is_step=False,
                items=items,
                is_prompt=True,
            )
        )

    return out


def detect_activity_markers(
    primitives: PagePrimitives,
    layout: Optional[PageLayout] = None,
    content_bottom: float = 44.0,
    trace: Optional[GrowthTrace] = None,
) -> List[DetectedMarker]:
    """
    Everything on this page that opens an activity, labelled or not.

    The single entry point growth should be fed from. `detect_markers` finds
    the enumerated runs and `detect_prompts` fills in the questions the page
    asks without enumerating them; the order matters, because the second
    defers to the first.
    """
    if layout is None:
        from .layout import detect_layout
        layout = detect_layout(primitives)

    markers = detect_markers(primitives, layout=layout, trace=trace)
    prompts = detect_prompts(primitives, layout, markers,
                             content_bottom=content_bottom, trace=trace)
    return markers + prompts
