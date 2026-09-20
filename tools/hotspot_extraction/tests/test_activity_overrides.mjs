import assert from "node:assert/strict";
import { ActivityDetector } from "../../../js/activities.js";

// Mock pdfDoc
function createMockDoc({ fingerprints = ["2a8bee1f65cd405a896aa16824498e7d"], fingerprint = null } = {}) {
  return {
    numPages: 100,
    fingerprints,
    fingerprint,
  };
}

console.log("Running ActivityDetector overrides test suite...\n");

// 1. Basename resolution
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "full_pdf.pdf");
  const data = {
    "full_pdf.pdf": {
      "33": { "b": { "rect": [10, 20, 30, 40] }, "drop": ["c"] },
    },
    "matematik.pdf": {
      "33": { "drop": ["a"] },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {
    "33": { "b": { "rect": [10, 20, 30, 40] }, "drop": ["c"] },
  });
  console.log("✔ Test 1 passed: Scoped by document basename");
}

// 2. Basename without extension resolution
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "full_pdf.pdf");
  const data = {
    "full_pdf": {
      "33": { "b": { "rect": [10, 20, 30, 40] } },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {
    "33": { "b": { "rect": [10, 20, 30, 40] } },
  });
  console.log("✔ Test 2 passed: Scoped by document basename without extension");
}

// 3. Fingerprint resolution
{
  const mockDoc = createMockDoc({ fingerprints: ["fp_abc_123"] });
  // Notice docName is different or renamed!
  const detector = new ActivityDetector(mockDoc, "renamed_book.pdf");
  const data = {
    "fp_abc_123": {
      "42": { "headline": "New Headline" },
    },
    "other_fp": {
      "42": { "drop": ["x"] },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {
    "42": { "headline": "New Headline" },
  });
  console.log("✔ Test 3 passed: Scoped by PDF fingerprint (survives renaming)");
}

// 4. Isolation across documents
{
  const mockDoc = createMockDoc({ fingerprints: ["fp_matematik"] });
  const detector = new ActivityDetector(mockDoc, "matematik.pdf");
  const data = {
    "full_pdf.pdf": {
      "33": { "b": { "rect": [10, 20, 30, 40] }, "drop": ["c"] },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {});
  console.log("✔ Test 4 passed: Isolation across documents (overrides do not leak)");
}

// 5. Merging basename and fingerprint overrides
{
  const mockDoc = createMockDoc({ fingerprints: ["fp_hybrid"] });
  const detector = new ActivityDetector(mockDoc, "hybrid.pdf");
  const data = {
    "hybrid.pdf": {
      "10": { "a": { "headline": "From Name" }, "drop": ["z"] },
      "11": { "drop": ["x"] },
    },
    "fp_hybrid": {
      "10": { "a": { "headline": "From Fingerprint" } },
      "12": { "b": { "rect": [1, 2, 3, 4] } },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {
    "10": { "a": { "headline": "From Fingerprint" }, "drop": ["z"] },
    "11": { "drop": ["x"] },
    "12": { "b": { "rect": [1, 2, 3, 4] } },
  });
  console.log("✔ Test 5 passed: Precedence and merging (fingerprint overrides basename on conflict)");
}

// 6. Backwards compatibility with legacy flat format
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "any_file.pdf");
  const legacyFlatData = {
    "33": { "b": { "rect": [43, 222, 261, 322] }, "drop": ["c"] },
    "50": { "drop": ["a"] },
  };

  const resolved = detector.resolveDocumentOverrides(legacyFlatData);
  assert.deepEqual(resolved, legacyFlatData);
  console.log("✔ Test 6 passed: Backwards compatibility with legacy flat shape");
}

// 7. applyOverrides behavior
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "full_pdf.pdf");
  const overrides = {
    "full_pdf.pdf": {
      "33": {
        "drop": ["c"],
        "b": {
          "rect": [43, 222, 261, 322],
          "headline": "Overridden Headline",
        },
      },
    },
  };

  detector.loadOverrides(overrides);

  const initialActivities = [
    { label: "a", rect: { x0: 10, y0: 10, x1: 50, y1: 50 }, parts: [{ x0: 10, y0: 10, x1: 50, y1: 50 }], headline: "Orig A" },
    { label: "b", rect: { x0: 10, y0: 60, x1: 50, y1: 100 }, parts: [{ x0: 10, y0: 60, x1: 50, y1: 100 }], headline: "Orig B" },
    { label: "c", rect: { x0: 10, y0: 110, x1: 50, y1: 150 }, parts: [{ x0: 10, y0: 110, x1: 50, y1: 150 }], headline: "Orig C" },
  ];

  const res33 = detector.applyOverrides(33, initialActivities);
  assert.equal(res33.length, 2, "Activity c should be dropped");
  assert.equal(res33[0].label, "a");
  assert.equal(res33[0].headline, "Orig A");

  assert.equal(res33[1].label, "b");
  assert.deepEqual(res33[1].rect, { x0: 43, y0: 222, x1: 261, y1: 322 });
  assert.deepEqual(res33[1].parts, [{ x0: 43, y0: 222, x1: 261, y1: 322 }]);
  assert.equal(res33[1].headline, "Overridden Headline");

  // Page with no overrides returns activities unchanged
  const res34 = detector.applyOverrides(34, initialActivities);
  assert.equal(res34.length, 3);
  console.log("✔ Test 7 passed: applyOverrides modifies rect/headline and drops activities");
}

// 8. Case insensitivity and path cleanup
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "/var/pdf/Full_Pdf.PDF?query=1#frag");
  const data = {
    "full_pdf.pdf": {
      "5": { "drop": ["a"] },
    },
  };

  const resolved = detector.resolveDocumentOverrides(data);
  assert.deepEqual(resolved, {
    "5": { "drop": ["a"] },
  });
  console.log("✔ Test 8 passed: Path parsing and case-insensitive filename matching");
}

// 9. setDocName updates resolved overrides
{
  const mockDoc = createMockDoc({ fingerprints: [] });
  const detector = new ActivityDetector(mockDoc);
  const data = {
    "docA.pdf": { "1": { "drop": ["x"] } },
    "docB.pdf": { "2": { "drop": ["y"] } },
  };

  detector.loadOverrides(data);
  assert.deepEqual(detector.overrides, {});

  detector.setDocName("docA.pdf");
  assert.deepEqual(detector.overrides, { "1": { "drop": ["x"] } });

  detector.setDocName("docB.pdf");
  assert.deepEqual(detector.overrides, { "2": { "drop": ["y"] } });
  console.log("✔ Test 9 passed: setDocName updates resolved overrides");
}

// 10. Empty or invalid overrides
{
  const mockDoc = createMockDoc();
  const detector = new ActivityDetector(mockDoc, "full_pdf.pdf");
  assert.deepEqual(detector.resolveDocumentOverrides({}), {});
  assert.deepEqual(detector.resolveDocumentOverrides(null), {});
  assert.deepEqual(detector.resolveDocumentOverrides("invalid"), {});
  console.log("✔ Test 10 passed: Empty and invalid overrides safely handled");
}

console.log("\nAll 10 tests passed successfully! 🎉");
