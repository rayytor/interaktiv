/**
 * Detection unit tests over synthetic pages.
 *
 * The two books are the real regression suite (see converter/tests/detect.mjs), but they
 * are 140 MB and slow; these pages are hand-built so the two structures that
 * used to be got wrong can be checked in milliseconds:
 *
 *   1. a page carrying two lists that use the same labels
 *   2. a page whose right column stops halfway down, with a full-width section
 *      printed underneath it
 *   3. an activity whose numbered list is followed by a long passage the last
 *      question must not swallow
 *   4. the "N. Adım" step markers a Turkish book numbers a practical's steps in
 */
import assert from "node:assert/strict";
import { ActivityDetector } from "../../../js/activities.js";

const PAGE_W = 600;
const PAGE_H = 800;

// PDF.js hands text back as runs with a text matrix and a font id; that is all
// the detector reads, so that is all the mock provides.
const run = (str, x, y, size = 10, width = null, fontName = "f1") => ({
  str,
  transform: [size, 0, 0, size, x, y],
  width: width === null ? str.length * size * 0.5 : width,
  height: size,
  fontName,
});

const label = (str, x, y) => run(str, x, y, 11, 6);

const pdfjsStub = {
  OPS: {
    save: 1, restore: 2, transform: 3,
    constructPath: 4, fill: 5, stroke: 6, paintImageXObject: 7,
  },
};

// A page is either its text runs, or `{ items, paths, images }` where `paths`
// are the rules and panels the page draws and `images` the pictures it places.
// Both are handed back the way pdf.js hands them back -- as an operator list --
// since that is what the detector replays.
const pageParts = (page) =>
  Array.isArray(page) ? { items: page, paths: [], images: [] }
                      : { items: page.items, paths: page.paths || [], images: page.images || [] };

const rule = (x0, y0, x1, y1) => ({ x0, y0, x1, y1 });

function operatorList(page) {
  const { paths, images } = pageParts(page);
  const OPS = pdfjsStub.OPS;
  const fnArray = [];
  const argsArray = [];
  const push = (fn, args) => { fnArray.push(fn); argsArray.push(args); };
  for (const p of paths) {
    push(OPS.constructPath, [[], [], [p.x0, p.y0, p.x1, p.y1]]);
    push(OPS.fill, []);
  }
  for (const im of images) {
    push(OPS.save, []);
    push(OPS.transform, [im.x1 - im.x0, 0, 0, im.y1 - im.y0, im.x0, im.y0]);
    push(OPS.paintImageXObject, []);
    push(OPS.restore, []);
  }
  return { fnArray, argsArray };
}

function mockDoc(pages) {
  return {
    numPages: pages.length,
    fingerprints: ["synthetic"],
    getPage: async (n) => ({
      getViewport: () => ({ width: PAGE_W, height: PAGE_H }),
      getTextContent: async () => ({
        items: pageParts(pages[n - 1]).items,
        styles: {
          f1: { fontFamily: "sans-serif", ascent: 1, descent: -0.25 },
          // A second face at the same metrics: the bold the book sets its
          // headings in. fontKey() reads family and metrics, so f1 and f2 are
          // one style to calibration and two faces to a single page -- which is
          // exactly how PDF.js reports a roman and its bold.
          f2: { fontFamily: "sans-serif", ascent: 1, descent: -0.25 },
        },
      }),
      getOperatorList: async () => operatorList(pages[n - 1]),
    }),
  };
}

/** Two columns, each running its own a / b / c list. */
function twoListsPage() {
  const items = [];
  const ys = [700, 650, 600];
  ["a", "b", "c"].forEach((letter, i) => {
    items.push(label(letter, 50, ys[i]));
    items.push(run(`Left column instruction ${letter}`, 68, ys[i], 10, 182));
    items.push(label(letter, 320, ys[i]));
    items.push(run(`Right column instruction ${letter}`, 338, ys[i], 10, 182));
  });
  return items;
}

/**
 * A boxed exercise in the right column (d, e, f) with nothing beside it, and a
 * full-width section below it running its own list (a, b, c) right across the
 * page -- the shape of matematik.pdf page 16.
 */
function deadColumnPage() {
  const items = [];
  [700, 660, 620].forEach((y, i) => {
    items.push(label("def"[i], 320, y));
    items.push(run(`Boxed exercise line ${i}`, 338, y, 10, 182));
  });
  items.push(run("A full width line of the section below", 50, 560, 10, 470));
  [500, 400, 300].forEach((y, i) => {
    items.push(label("abc"[i], 50, y));
    items.push(run(`Full width instruction ${i}`, 68, y, 10, 452));
  });
  return items;
}

/**
 * One activity: a two-line instruction, a 1..4 list of one-word answers, and a
 * reading passage printed underneath the list -- the shape of full_pdf.pdf
 * page 47, where the last question used to run all the way down to the foot of
 * the activity.
 */
function listAbovePassagePage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Read the passage and tick the words you find in it.", 68, 700, 10, 230));
  [660, 640, 620, 600].forEach((y, i) => {
    items.push(run(String(i + 1), 68, y, 10, 5));
    items.push(run(["homepage", "mind map", "project", "battery"][i], 95, y, 10, 60));
  });
  // The passage: same type size as the list, set solid, starting a clear
  // paragraph break below the last answer.
  for (let i = 0; i < 12; i++) {
    items.push(run(`Passage line ${i} of the text that follows the list`, 68, 550 - i * 13, 10, 400));
  }
  return items;
}

/**
 * One two-line instruction in the left column, with a photograph beside it and a
 * full-width dialogue set underneath -- the shape of full_pdf.pdf page 33, where
 * the region used to cover the whole top half of the page.
 */
function figureAndDialoguePage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Work in groups. Look at the", 68, 700, 10, 140));
  items.push(run("photos below and talk about them.", 68, 686, 10, 150));
  // The dialogue: same type size, set right across the page, close underneath.
  for (let i = 0; i < 12; i++) {
    items.push(run("Lucia: a line of the dialogue below the question", 60, 660 - i * 14, 10, 440));
  }
  // The two columns resume under the dialogue, which is what makes the page two
  // columns at the height of the question above it.
  ["b", "c"].forEach((letter, i) => {
    items.push(label(letter, 50, 460 - i * 60));
    items.push(run(`Left column instruction ${letter}`, 68, 460 - i * 60, 10, 182));
  });
  ["d", "e"].forEach((letter, i) => {
    items.push(label(letter, 320, 460 - i * 60));
    items.push(run(`Right column instruction ${letter}`, 338, 460 - i * 60, 10, 182));
  });
  return items;
}

/**
 * The same shape again, but with the dialogue set inside the speech bubble the
 * book draws round it -- full_pdf.pdf page 33. The frame says where the block
 * begins and ends, so the question that runs into it takes all of it: covering
 * whichever speeches happen to be short enough to fit the column is the one
 * answer the page does not admit.
 */
function framedDialoguePage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Work in groups. Look at the", 68, 700, 10, 140));
  items.push(run("photos below and talk about them.", 68, 686, 10, 150));
  // A dialogue is set to the page, not to a column, so its turns are as long or
  // as short as what the speaker says: some fit the measure of the question
  // above and some break right out of it. That is what used to leave the region
  // covering a ragged half of it.
  for (let i = 0; i < 12; i++) {
    const short = i % 5 === 1;
    items.push(run(
      short ? "Ali: Yes, he is." : "Lucia: a long line of the dialogue below the question",
      60, 660 - i * 13, 10, short ? 90 : 440
    ));
  }
  // One column under the bubble: what is being measured here is how wide the
  // region grows across the page, and test 6 already covers a bubble with a
  // column of its own beside it.
  ["b", "c"].forEach((letter, i) => {
    items.push(label(letter, 50, 460 - i * 60));
    items.push(run(`Instruction ${letter} right across the page`, 68, 460 - i * 60, 10, 420));
  });
  return { items, paths: [rule(40, 470, 520, 672)] };
}

/**
 * An activity whose questions are followed by the card they are answered in: a
 * panel of ruled writing lines with an illustration standing over it -- the
 * shape of full_pdf.pdf pages 32 and 36. The region has to reach the card, and
 * the picture in the way is part of the card rather than a wall.
 */
function answerCardPage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Work in pairs. Think of the answers to the questions.", 68, 700, 10, 230));
  [680, 660, 640].forEach((y, i) => {
    items.push(run(String(i + 1), 68, y, 10, 5));
    items.push(run(`Question ${i + 1} of the list`, 95, y, 10, 120));
  });
  // A second activity underneath, so the card sits inside a band with a floor.
  items.push(label("b", 50, 350));
  items.push(run("Make a poster about your school.", 68, 350, 10, 180));
  return {
    items,
    images: [{ x0: 60, y0: 480, x1: 250, y1: 560 }],
    paths: [
      rule(55, 380, 260, 470),           // the card the lines are set in
      rule(70, 440, 250, 441),
      rule(70, 420, 250, 421),
      rule(70, 400, 250, 401),
    ],
  };
}

/**
 * An activity set as a table: each numbered row asks in its first column and is
 * answered in the empty columns beside it -- the shape of full_pdf.pdf page 32,
 * activity h. Each question has to take its own blank cells and no other row's.
 */
function tableRowsPage() {
  const items = [];
  items.push(label("a", 50, 730));
  items.push(run("Read the table and complete it.", 68, 730, 10, 160));
  [685, 665, 645].forEach((y, i) => {
    items.push(run(String(i + 1), 65, y, 10, 5));
    items.push(run(`Row ${i + 1} description`, 80, y, 10, 100));
  });
  const paths = [];
  for (const y of [700, 680, 660, 640]) paths.push(rule(60, y, 280, y + 1));
  for (const x of [60, 190, 280]) paths.push(rule(x, 640, x + 1, 700));
  return { items, paths };
}

/**
 * An activity at the foot of a section, with the next section printed under it:
 * its heading, its prose and its own body copy -- the shape of the Turkish
 * textbooks, where a region used to run from the last question all the way down
 * the page. The heading is given both of the ways a book sets one, since either
 * alone has to end the flow: the rubric banner is the question's own size in
 * the bold the book reserves for headings and stands flush to the column, while
 * the section title is display type. A question's runover is neither: it hangs
 * off the label, indented clear of it.
 */
function sectionBelowPage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Answer the question in the space below.", 68, 700, 10, 200));
  items.push(run("Write your answer on the lines.", 68, 686, 10, 150));
  items.push(run("Assessment", 50, 660, 10, 70, "f2"));
  items.push(run("The Measurable Properties of Pure Substances", 90, 634, 15, 330, "f2"));
  for (let i = 0; i < 10; i++) {
    items.push(run(`Body line ${i} of the section that follows the activity`, 90, 606 - i * 13, 10, 400));
  }
  return items;
}

/**
 * The same activity, but with the question set in two faces the way a book
 * routinely sets one -- the instruction in roman, the question it asks in bold
 * on the line under it, indented off the label. That second line is the
 * question's own, and a heading test that reads type alone drops it.
 */
function boldQuestionPage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Discuss the answer with your group.", 68, 700, 10, 190));
  items.push(run("What does a plant use and produce?", 68, 686, 10, 190, "f2"));
  items.push(run("Assessment", 50, 640, 10, 70, "f2"));
  for (let i = 0; i < 10; i++) {
    items.push(run(`Body line ${i} of the section that follows the activity`, 50, 610 - i * 13, 10, 400));
  }
  return items;
}

/**
 * The steps of a practical, the way a Turkish book numbers them: "1. Adım",
 * "2. Adım", "3. Adım" down a column, each with its instruction at the hanging
 * indent. The marker is a number and a word in one run, so no bare-label
 * grammar ever read it; the casing and the space vary across books ("2.adım",
 * "3. ADIM:"), and a colon after the word is routine.
 */
function stepMarkersPage() {
  const items = [];
  const steps = ["1. Adım", "2. Adım", "3. Adım"];
  const ys = [700, 640, 580];
  steps.forEach((l, i) => {
    items.push(run(l, 50, ys[i], 11, 40));
    items.push(run(`Instruction for step ${i + 1}`, 100, ys[i], 10, 180));
  });
  return items;
}

/**
 * A page carrying both lists at once: plain digit questions down the left
 * column and Adım steps down the right, plus prose that merely uses the word
 * "adım" mid-sentence. The two numbering schemes must each read as its own
 * complete list, and the word alone must open nothing.
 */
function digitsAndStepsPage() {
  const items = [];
  [700, 640].forEach((y, i) => {
    items.push(label(`${i + 1}.`, 50, y));
    items.push(run(`Question ${i + 1} about the passage`, 68, y, 10, 182));
  });
  ["1.Adım", "2. ADIM"].forEach((l, i) => {
    const y = [700, 640][i];
    items.push(run(l, 320, y, 11, 40));
    items.push(run(`Do what step ${i + 1} asks, üç adım atlayın`, 380, y, 10, 160));
  });
  return items;
}

/**
 * A book that puts two questions at the foot of a section and then reads on for
 * pages: two hundred pages carrying one pair of labels every seventh page. Fourteen
 * evenly spread samples land on two of those pairs and score under the bar, so
 * this is the book that used to switch the feature off for itself entirely.
 */
function thinlySpreadBook() {
  const pages = [];
  for (let p = 0; p < 200; p++) {
    const items = [];
    for (let i = 0; i < 14; i++) {
      items.push(run(`Running prose line ${i} of a page of this book`, 50, 700 - i * 16, 10, 400));
    }
    if (p % 7 === 3) {
      ["1.", "2."].forEach((l, i) => {
        items.push(label(l, 50, 420 - i * 20));
        items.push(run(`Question ${i + 1} on the passage above`, 68, 420 - i * 20, 10, 220));
      });
    }
    pages.push(items);
  }
  return pages;
}

/**
 * The heading of the section below set the way a book usually sets one: across
 * the page, wider and further left than the question it follows, so it is not a
 * line the question's own read would ever have looked at. It still ends the
 * region, because what it opens is printed under it.
 *
 * Beside it, and under the question, a reading card printed on a picture, with
 * its own title set as large as any heading. That title belongs to the card, so
 * it says nothing about where the page's sections divide -- whether the question
 * takes the card is a question about the card.
 */
function headingAcrossPage() {
  const items = [];
  items.push(label("a", 50, 700));
  items.push(run("Read the card and answer in your own words.", 68, 700, 10, 220));
  items.push(run("A Day at Sutton Hill School", 310, 660, 15, 200));
  for (let i = 0; i < 6; i++) {
    items.push(run(`Card line ${i} of the passage the question is about`, 310, 634 - i * 14, 10, 200));
  }
  items.push(run("The Measurable Properties of Pure Substances", 30, 520, 15, 400));
  for (let i = 0; i < 8; i++) {
    items.push(run(`Body line ${i} of the section that follows the activity`, 30, 494 - i * 13, 10, 420));
  }
  return { items, images: [{ x0: 300, y0: 600, x1: 530, y1: 690 }] };
}

/**
 * A sheet out of a Turkish subject book: two blocks of prose under their own
 * headings, and not a label anywhere. Nothing on it is lettered, so the whole
 * page is invisible to the label pass -- and the publisher's manifest names the
 * activities on it descriptively ("Kontrol Noktasi Cozumlu Soru 1"), which is
 * why they have to be found from the icon position instead.
 */
function unlabelledPage() {
  const items = [];
  items.push(run("Kontrol Noktasi Cozumlu Soru 1", 50, 700, 11, 200, "f2"));
  for (let i = 0; i < 6; i++) {
    items.push(run(`Soru metninin ${i}. satiri burada devam ediyor`, 50, 680 - i * 14, 10, 400));
  }
  for (let i = 0; i < 5; i++) {
    items.push(run(`Ikinci etkinligin ${i}. satiri`, 50, 420 - i * 14, 10, 400));
  }
  return items;
}

async function analyze(pages, pageNum, overrides = {}, anchors = null) {
  const detector = new ActivityDetector(mockDoc(pages), "synthetic.pdf");
  detector.loadOverrides(overrides);
  await detector.calibrate();
  assert.ok(detector.enabled, "calibration should find the synthetic label style");
  if (anchors) detector.anchorsByPage = new Map([[pageNum, anchors]]);
  return detector.analyzePage(pageNum, pdfjsStub);
}

/**
 * The publisher's icon position, as the manifest carries it: percentages of the
 * sheet measured from its top-left corner, where PDF user space measures from
 * the bottom-left.
 */
const anchorAt = (id, x, y) => ({ id, posx: (x / PAGE_W) * 100, posy: ((PAGE_H - y) / PAGE_H) * 100 });

console.log("Running ActivityDetector detection test suite...\n");

// 1. Two lists with the same labels are two sets of activities, not one.
{
  const { activities } = await analyze([twoListsPage()], 1);
  assert.deepEqual(
    activities.map((a) => a.label),
    ["a", "b", "c", "a", "b", "c"]
  );
  const ids = activities.map((a) => a.id);
  assert.deepEqual(ids, ["p1-a", "p1-b", "p1-c", "p1-a-2", "p1-b-2", "p1-c-2"]);
  assert.equal(new Set(ids).size, ids.length, "ids must be unique within a page");
  console.log("✔ Test 1 passed: repeated labels get one id per region");
}

// 2. The regions of two same-labelled activities stay apart.
{
  const { activities } = await analyze([twoListsPage()], 1);
  const [left] = activities.filter((a) => a.label === "a");
  const right = activities.filter((a) => a.label === "a")[1];
  assert.ok(left.rect.x1 < right.rect.x0, "same-labelled activities must not overlap");
  console.log("✔ Test 2 passed: same-labelled activities keep separate regions");
}

// 3. A column that has ended does not own the page printed below it, and the
//    full-width section below it is not clipped to the left column.
{
  const { activities } = await analyze([deadColumnPage()], 1);
  const byLabel = Object.fromEntries(activities.map((a) => [a.label, a]));
  assert.equal(activities.length, 6, "six activities on the page");

  const f = byLabel.f;
  assert.ok(
    f.parts.every((r) => r.y0 > 560),
    `the last activity of the boxed exercise must stop above the section below it, got ${JSON.stringify(f.parts)}`
  );

  for (const letter of ["a", "b", "c"]) {
    const widest = Math.max(...byLabel[letter].parts.map((r) => r.x1));
    assert.ok(
      widest > 450,
      `full-width activity ${letter} must not be clipped to the left column, got x1 ${widest}`
    );
  }
  console.log("✔ Test 3 passed: a column band ends where its flow ends");
}

// 4. Overrides can address one of two same-labelled activities by id.
{
  const { activities } = await analyze([twoListsPage()], 1, {
    1: { "p1-a-2": { rect: [1, 2, 3, 4] }, drop: ["p1-c-2"] },
  });
  const first = activities.find((a) => a.id === "p1-a");
  const second = activities.find((a) => a.id === "p1-a-2");
  assert.deepEqual(second.rect, { x0: 1, y0: 2, x1: 3, y1: 4 });
  assert.notDeepEqual(first.rect, second.rect, "the other 'a' must be untouched");
  assert.ok(activities.some((a) => a.id === "p1-c"), "p1-c stays");
  assert.ok(!activities.some((a) => a.id === "p1-c-2"), "p1-c-2 is dropped by id");
  console.log("✔ Test 4 passed: overrides address a single region by id");
}

// 5. The last question of a list ends with its own answer, not at the foot of
//    the activity: a one-word answer stays the height of its siblings even when
//    a long passage is printed below the list.
{
  // Calibration learns the label style from the document, so the page is
  // analysed alongside a page that carries a plain list of them.
  const { activities } = await analyze([twoListsPage(), listAbovePassagePage()], 2);
  const [act] = activities;
  assert.equal(act.items.length, 4, "the 1..4 list is four questions");
  const heights = act.items.map((q) => q.rect.y1 - q.rect.y0);
  const last = heights[heights.length - 1];
  assert.ok(
    last < 1.5 * Math.min(...heights.slice(0, -1)),
    `the last question must not be taller than its siblings, got ${JSON.stringify(heights)}`
  );
  assert.ok(
    act.items[3].rect.y0 > 560,
    `the last question must stop above the passage, got y0 ${act.items[3].rect.y0}`
  );
  // The region is the question, so it ends with the list too: the passage
  // printed below it is not part of what was asked.
  assert.ok(
    Math.min(...act.parts.map((r) => r.y0)) > 560,
    `the activity region must stop above the passage, got ${JSON.stringify(act.parts)}`
  );
  console.log("✔ Test 5 passed: the last question ends with its own answer");
}

// 6. A region is the question, not the slice of page it stands on: a photo it
//    refers to, and a dialogue merely printed under it, belong to neither.
{
  const { activities } = await analyze([twoListsPage(), figureAndDialoguePage()], 2);
  const [act] = activities;
  const widest = Math.max(...act.parts.map((r) => r.x1));
  const lowest = Math.min(...act.parts.map((r) => r.y0));
  assert.ok(
    widest < 300,
    `the region must not reach over the full-width dialogue, got x1 ${widest}`
  );
  assert.ok(
    lowest > 640,
    `the region must stop at the end of its own two lines, got y0 ${lowest}`
  );
  console.log("✔ Test 6 passed: a region covers the question and nothing else");
}

// 7. A question is asked and then answered: the region takes the ruled card the
//    answer is written on, and the illustration standing over the lines comes
//    with the card instead of walling it off.
{
  const { activities } = await analyze([twoListsPage(), answerCardPage()], 2);
  const a = activities.find((x) => x.label === "a");
  const b = activities.find((x) => x.label === "b");
  const lowest = Math.min(...a.parts.map((r) => r.y0));
  assert.ok(
    lowest <= 380,
    `the region must reach the card its answers are written on, got y0 ${lowest}`
  );
  assert.ok(
    Math.max(...a.parts.map((r) => r.x1)) >= 260,
    "the region must cover the width of the card"
  );
  assert.ok(
    Math.max(...b.parts.map((r) => r.y1)) < lowest + 1,
    "the activity below must keep its own label"
  );
  console.log("✔ Test 7 passed: a region reaches the space its answer goes in");
}

// 8. Each numbered question takes the empty cells of its own row, and stops at
//    the row below.
{
  const { activities } = await analyze([twoListsPage(), tableRowsPage()], 2);
  const [act] = activities;
  assert.equal(act.items.length, 3, "the table is three questions");
  for (const q of act.items) {
    assert.ok(
      q.rect.x1 >= 275,
      `question ${q.label} must reach its empty cells, got x1 ${q.rect.x1}`
    );
    assert.ok(
      q.rect.y1 - q.rect.y0 < 45,
      `question ${q.label} must keep to its own row, got height ${q.rect.y1 - q.rect.y0}`
    );
  }
  console.log("✔ Test 8 passed: a question takes the blank cells of its own row");
}

// 9. All or nothing: once the question's flow has read lines out of a panel the
//    page draws, the region covers that panel whole, out to its frame.
{
  const { activities } = await analyze([twoListsPage(), framedDialoguePage()], 2);
  const [act] = activities;
  const widest = Math.max(...act.parts.map((r) => r.x1));
  const lowest = Math.min(...act.parts.map((r) => r.y0));
  assert.ok(
    widest >= 520,
    `the region must reach the far edge of the bubble, got x1 ${widest}`
  );
  assert.ok(
    lowest <= 472,
    `the region must reach the foot of the bubble, got y0 ${lowest}`
  );
  const b = activities.find((x) => x.label === "b");
  assert.ok(
    Math.max(...b.parts.map((r) => r.y1)) < lowest + 1,
    "the activity below the bubble must keep its own label"
  );
  console.log("✔ Test 9 passed: a panel the question reads is covered whole");
}

// 10. A heading is not part of the question: the flow ends at the section
//     printed below, whether the heading is display type or the book's heading
//     face set at the question's own size.
{
  const { activities } = await analyze([twoListsPage(), sectionBelowPage()], 2);
  const [act] = activities;
  const lowest = Math.min(...act.parts.map((r) => r.y0));
  assert.ok(
    lowest > 640,
    `the region must stop above the rubric banner, got y0 ${lowest}`
  );
  console.log("✔ Test 10 passed: a region stops at the heading below it");
}

// 11. ... but a change of face inside the question is still the question: the
//     bold line hanging off the label is covered, and only the banner out at
//     the margin ends the flow.
{
  const { activities } = await analyze([twoListsPage(), boldQuestionPage()], 2);
  const [act] = activities;
  const lowest = Math.min(...act.parts.map((r) => r.y0));
  assert.ok(
    lowest <= 686,
    `the bold line of the question must be covered, got y0 ${lowest}`
  );
  assert.ok(
    lowest > 620,
    `the region must still stop at the rubric banner, got y0 ${lowest}`
  );
  console.log("✔ Test 11 passed: a change of face inside a question is not a heading");
}

// 12. A book whose activities are thinly spread still calibrates: the sample
//     widens until the evidence is there rather than stopping at a fixed count.
{
  const detector = new ActivityDetector(mockDoc(thinlySpreadBook()), "thin.pdf");
  detector.loadOverrides({});
  await detector.calibrate();
  assert.ok(detector.enabled, "a book with sparse activities must still calibrate");
  const { activities } = await detector.analyzePage(4, pdfjsStub);
  assert.equal(activities.length, 2, "both questions on the page are activities");
  console.log("✔ Test 12 passed: a book with thinly spread activities calibrates");
}

// 13. A heading set across the page ends the region even though the question's
//     own read never reaches it -- while a title printed over a picture is that
//     picture's, not the page's.
{
  const { activities } = await analyze([twoListsPage(), headingAcrossPage()], 2);
  const [act] = activities;
  const lowest = Math.min(...act.parts.map((r) => r.y0));
  assert.ok(
    lowest > 520,
    `the region must stop above the section heading, got y0 ${lowest}`
  );
  assert.ok(
    lowest <= 634,
    `the card's own title must not end the region, got y0 ${lowest}`
  );
  console.log("✔ Test 13 passed: a heading across the page ends a region, a card's title does not");
}

// 14. A step marker is a number with the word attached: "1. Adım" down a column
//     is a list of steps, and each step is its own clickable activity.
{
  const { activities } = await analyze([twoListsPage(), stepMarkersPage()], 2);
  assert.deepEqual(
    activities.map((a) => a.label),
    ["1. Adım", "2. Adım", "3. Adım"]
  );
  const ids = activities.map((a) => a.id);
  assert.deepEqual(ids, ["p2-1. Adım", "p2-2. Adım", "p2-3. Adım"]);
  assert.equal(new Set(ids).size, ids.length, "ids must be unique within a page");
  for (let i = 0; i + 1 < activities.length; i++) {
    const below = Math.min(...activities[i].parts.map((r) => r.y0));
    const above = Math.max(...activities[i + 1].parts.map((r) => r.y1));
    assert.ok(
      above <= below + 1,
      `step ${i + 1} must stop above step ${i + 2}, got y0 ${below} under y1 ${above}`
    );
  }
  console.log("✔ Test 14 passed: Adım steps become one activity each");
}

// 15. Steps are an alphabet of their own: a page numbering its questions "1. 2."
//     while its practical runs "1. Adım 2. Adım" keeps both lists, and prose
//     that merely uses the word opens nothing.
{
  const { activities } = await analyze([twoListsPage(), digitsAndStepsPage()], 2);
  assert.deepEqual(
    activities.map((a) => a.label).sort(),
    ["1.", "1.Adım", "2.", "2. ADIM"]
  );
  console.log("✔ Test 15 passed: digits and Adım steps stay two lists");
}

// 16. A page with no labels on it at all still yields a region, grown from the
//     position the publisher hung the icon at, covering the block that starts
//     there and stopping before the next one.
{
  const { activities } = await analyze(
    [twoListsPage(), unlabelledPage()], 2, {}, [anchorAt(77, 50, 700)]
  );
  assert.equal(activities.length, 1, "the anchor should yield exactly one region");
  const [act] = activities;
  assert.equal(act.id, "p2-oge-77", "an anchored region is named after its entry");
  assert.equal(act.label, null, "there was no label to read; that is the point");
  assert.ok(act.anchored, "the region should be marked as anchored");
  assert.ok(
    act.rect.y1 >= 700 && act.rect.y1 <= 720,
    `the region should start at the anchored line, got y1 ${act.rect.y1}`
  );
  assert.ok(
    act.rect.y0 <= 600 && act.rect.y0 > 430,
    `the region should cover its own block and stop above the next, got y0 ${act.rect.y0}`
  );
  assert.ok(act.headline.startsWith("Kontrol Noktasi"), `headline was "${act.headline}"`);
  console.log("✔ Test 16 passed: an unlabelled page yields a region from the icon anchor");
}

// 17. Two entries on one sheet are two activities, never one: each gets its own
//     id and its own rect, and the two do not overlap.
{
  const { activities } = await analyze(
    [twoListsPage(), unlabelledPage()], 2, {}, [anchorAt(77, 50, 700), anchorAt(78, 50, 420)]
  );
  assert.deepEqual(activities.map((a) => a.id), ["p2-oge-77", "p2-oge-78"]);
  const [first, second] = activities;
  assert.ok(
    second.rect.y1 <= first.rect.y0,
    `the second region must sit clear of the first, got y1 ${second.rect.y1} above y0 ${first.rect.y0}`
  );
  console.log("✔ Test 17 passed: two anchors on a sheet stay two separate activities");
}

console.log("\nAll 17 tests passed successfully! 🎉");
