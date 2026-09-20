/**
 * A fuse on a single book's memory, checked between pages.
 *
 * The heap cap passed to a child is V8's own limit and says nothing about what
 * the process costs the machine: pdf.js holds fonts and operator lists outside
 * the JS heap, so resident memory runs ahead of it. A sweep that goes wrong is
 * therefore capable of taking the desktop into swap while V8 still believes it
 * has room, which is exactly what happened once here.
 *
 * So the number that matters is RSS, and it is read where a book's memory is
 * actually released -- between sheets. A book over the limit aborts itself and
 * takes only itself down; the run continues with the next one, and the report
 * says which book failed and how large it got.
 *
 * The limit is 3 GB rather than something tighter because of one measured page:
 * sheet 164 of 1cc573f6 embeds a 318x208 pt illustration stored at 15071x9830
 * pixels, which pdf.js decodes to roughly 590 MB of RGBA and which takes that
 * one page to about 2.8 GB resident. The detector wants only the image's
 * bounding box, but the public API gives no way to ask for it without the
 * pixels: `maxImageSize` drops the image op altogether, and that image covers
 * 14.5% of the sheet, so discarding it would quietly change the layout.
 * 3 GB leaves that page room while still fitting one book at a time inside a
 * 5 GB budget -- which is why books are scored one short-lived child at a time
 * and never swept together in one process.
 */
export const RSS_LIMIT_MB = 3000;

export class MemoryWatchdog {
  constructor(limitMb = RSS_LIMIT_MB) {
    this.limitMb = limitMb;
    this.peakMb = 0;
  }

  /** Call between pages. Throws once, when the book has grown too large. */
  check(where) {
    const mb = Math.round(process.memoryUsage().rss / (1024 * 1024));
    if (mb > this.peakMb) this.peakMb = mb;
    if (mb > this.limitMb) {
      throw new Error(
        `aborted at ${where}: ${mb} MB resident, over the ${this.limitMb} MB limit`
      );
    }
    return mb;
  }
}
