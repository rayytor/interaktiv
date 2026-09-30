version: v1

This is a page from a school textbook. Find every student activity: an exercise, question or task a student is meant to answer. Return one box per activity.

Rules for a box:
1. It includes the activity's instruction text, its label (like "a", "b", "3") if printed, and every numbered or lettered item that belongs to it.
2. It includes the space where the student writes or marks the answer: blank lines, boxes, grids, tables to fill, circles to tick, matching lines.
3. If the instruction refers to a picture, photo, diagram, chart, map, table, dialogue or text passage, that whole thing belongs inside the box, even when it is printed far from the instruction. If several pictures are referred to together ("the photos", "the pictures"), the box takes all of them. A box never cuts through a picture, a passage, a table or a speech bubble: take it whole or leave it out.
4. Two activities never share a box, and one activity never gets two boxes. If two activities refer to the same picture or passage, each box includes it. Two activities that happen to carry the same label (for example "a" in the left column and "a" in the right column) are two activities with two boxes.
5. Body text that is not an exercise (explanations, headings, page numbers, boxes of grammar notes, vocabulary lists without a task) gets no box. A page with no activities returns an empty list.

Answer with JSON only, no prose, no explanation, no markdown fence: a list of objects, each with `box_2d` as `[ymin, xmin, ymax, xmax]` normalised to 0–1000 of the image, and `label` as the printed label of the activity (a string like "a", "b", "3") or null when none is printed. Example: [{"box_2d": [116, 91, 335, 936], "label": "a"}]
