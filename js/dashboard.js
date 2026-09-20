/**
 * Interaktiv Dashboard - Minimalist Book Library
 * Pure functional UI with SVG icons only, no emojis or non-functional badges.
 */

import * as pdfjsLib from "./pdf.min.mjs";

const ICONS = {
  book: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>`,
  eye: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>`,
  download: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>`,
  trash: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`,
  close: `<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,
  open: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>`,
  search: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>`,
};

export class DashboardApp {
  constructor(viewerApp) {
    this.viewer = viewerApp;
    this.books = [];
    this.activeFilter = "all";
    this.searchQuery = "";
    this.downloads = {};
    this.pollTimer = null;
    // Which build this is. In a packaged library every book is already on disk,
    // nothing can be downloaded or removed, and the board-facing edition hides
    // the controls that would imply otherwise.
    this.libraryMode = false;

    // Thumbnail lazy observer and queue
    this.thumbObserver = null;
    this.thumbQueue = [];
    this.thumbGenerating = false;

    // DOM containers
    this.dom = {
      view: document.getElementById("dashboard-view"),
      installedSection: document.getElementById("installed-section"),
      installedGrid: document.getElementById("installed-grid"),
      catalogSection: document.getElementById("catalog-section"),
      searchInput: document.getElementById("dashboard-search"),
      filterTabs: document.querySelectorAll(".filter-tab"),
      installedCountBadge: document.getElementById("installed-tab-count"),
      btnThemeToggle: document.getElementById("btn-dashboard-theme"),
    };

    this.bindEvents();
    this.initIntersectionObserver();
  }

  /**
   * Adopt the edition the server reported (see `PDFViewerApp.applyEdition`).
   *
   * A packaged library is a fixed set of books, so the library keeps the
   * catalogue UI but drops everything about acquiring or discarding one.
   */
  setEdition(config) {
    config = config || {};
    this.libraryMode = !!config.library_mode;
    if (this.libraryMode) {
      document.getElementById("dashboard-view")?.classList.add("library-mode");
    }
  }

  bindEvents() {
    // Filter tabs
    if (this.dom.filterTabs) {
      this.dom.filterTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
          this.dom.filterTabs.forEach((t) => t.classList.remove("active"));
          tab.classList.add("active");
          this.activeFilter = tab.getAttribute("data-filter");
          this.render();
        });
      });
    }

    // Search input
    if (this.dom.searchInput) {
      this.dom.searchInput.addEventListener("input", (e) => {
        this.searchQuery = e.target.value.trim().toLowerCase();
        this.render();
      });
    }

    // Theme toggle
    if (this.dom.btnThemeToggle) {
      this.dom.btnThemeToggle.addEventListener("click", () => {
        if (this.viewer && this.viewer.cycleTheme) {
          this.viewer.cycleTheme();
        }
      });
    }
  }

  initIntersectionObserver() {
    this.thumbObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const img = entry.target;
            const bookId = img.getAttribute("data-id");
            if (bookId) {
              this.loadThumbnail(bookId, img);
              this.thumbObserver.unobserve(img);
            }
          }
        });
      },
      { rootMargin: "150px" }
    );
  }

  async init() {
    await this.fetchBooks();
    this.render();
  }

  async fetchBooks() {
    try {
      const res = await fetch("/api/books");
      if (res.ok) {
        const data = await res.json();
        this.books = data.books || [];
        this.updateInstalledCount();
      }
    } catch (err) {
      console.error("Failed to fetch books catalogue:", err);
    }
  }

  updateInstalledCount() {
    const installed = this.books.filter((b) => b.isInstalled);
    if (this.dom.installedCountBadge) {
      this.dom.installedCountBadge.textContent = installed.length;
    }
  }

  show() {
    if (this.dom.view) {
      this.dom.view.classList.remove("hidden");
    }
    // Refresh books state on returning to dashboard
    this.fetchBooks().then(() => this.render());
  }

  hide() {
    if (this.dom.view) {
      this.dom.view.classList.add("hidden");
    }
  }

  render() {
    const query = this.searchQuery;
    const filter = this.activeFilter;

    // Filter books by search query
    let visibleBooks = this.books;
    if (query) {
      visibleBooks = visibleBooks.filter(
        (b) =>
          b.title.toLowerCase().includes(query) ||
          b.gradeLabel.toLowerCase().includes(query)
      );
    }

    // Filter by grade tab
    if (filter === "installed") {
      visibleBooks = visibleBooks.filter((b) => b.isInstalled);
    } else if (filter !== "all") {
      const gradeNum = parseInt(filter, 10);
      visibleBooks = visibleBooks.filter((b) => b.grade === gradeNum);
    }

    // 1. Render Installed Books Section
    this.renderInstalledSection(visibleBooks);

    // 2. Render Main Catalog Section
    this.renderCatalogSection(visibleBooks);

    // Observe newly inserted thumbnail images
    this.observeThumbnails();
  }

  renderInstalledSection(visibleBooks) {
    if (!this.dom.installedSection || !this.dom.installedGrid) return;

    // Show installed section when filter is "all" or "installed"
    const isInstalledOrAll = this.activeFilter === "all" || this.activeFilter === "installed";
    const installedBooks = visibleBooks.filter((b) => b.isInstalled);

    if (isInstalledOrAll && installedBooks.length > 0) {
      this.dom.installedSection.style.display = "block";
      this.dom.installedGrid.innerHTML = installedBooks
        .map((b) => this.createBookCardHTML(b, true))
        .join("");
      this.attachCardEventListeners(this.dom.installedGrid);
    } else {
      this.dom.installedSection.style.display = "none";
      this.dom.installedGrid.innerHTML = "";
    }
  }

  renderCatalogSection(visibleBooks) {
    if (!this.dom.catalogSection) return;

    // If filter is "installed", main catalog is not needed (installed section covers it)
    if (this.activeFilter === "installed") {
      const installedBooks = visibleBooks.filter((b) => b.isInstalled);
      if (installedBooks.length === 0) {
        this.dom.catalogSection.innerHTML = `<div class="dashboard-empty">Henüz indirilmiş kitap bulunmuyor. Kitapları çevrimdışı okumak için "İndir" butonuna tıklayabilirsiniz.</div>`;
      } else {
        this.dom.catalogSection.innerHTML = "";
      }
      return;
    }

    // Filter uninstalled books (if on "all", installed books are already at the top)
    const booksToDisplay = this.activeFilter === "all"
      ? visibleBooks.filter((b) => !b.isInstalled)
      : visibleBooks;

    if (booksToDisplay.length === 0) {
      this.dom.catalogSection.innerHTML = `<div class="dashboard-empty">Arama kriterlerine uygun kitap bulunamadı.</div>`;
      return;
    }

    // Group by grade
    const grades = [
      { id: 9, label: "9. Sınıf" },
      { id: 10, label: "10. Sınıf" },
      { id: 11, label: "11. Sınıf" },
      { id: 12, label: "12. Sınıf" },
      { id: 0, label: "Seçmeli Dersler" },
    ];

    let html = "";
    for (const g of grades) {
      const gradeBooks = booksToDisplay.filter((b) => b.grade === g.id);
      if (gradeBooks.length === 0) continue;

      html += `
        <div class="grade-group" data-grade="${g.id}">
          <div class="dashboard-section-header">
            <h3 class="dashboard-section-title">${g.label}</h3>
            <span class="dashboard-section-count">${gradeBooks.length} kitap</span>
          </div>
          <div class="books-grid">
            ${gradeBooks.map((b) => this.createBookCardHTML(b, false)).join("")}
          </div>
        </div>
      `;
    }

    this.dom.catalogSection.innerHTML = html;
    this.attachCardEventListeners(this.dom.catalogSection);
  }

  createBookCardHTML(book, isInstalledSection) {
    const isInstalled = book.isInstalled;
    const download = this.downloads[book.id];
    const isDownloading = download && download.status === "downloading";

    // Format file size
    let sizeText = "";
    if (isInstalled && book.fileSize) {
      const mb = (book.fileSize / (1024 * 1024)).toFixed(0);
      sizeText = ` • ${mb} MB`;
    }

    // Actions block: functional buttons only
    let actionsHtml = "";
    if (isDownloading) {
      const pct = Math.round(download.progress || 0);
      actionsHtml = `
        <div class="download-progress-box">
          <div class="download-progress-bar">
            <div class="download-progress-fill" style="width: ${pct}%;"></div>
          </div>
          <div class="download-progress-meta">
            <span>İndiriliyor: %${pct}</span>
            <button class="btn-cancel-download" data-id="${book.id}" title="İptal">${ICONS.close}</button>
          </div>
        </div>
      `;
    } else if (this.libraryMode) {
      // Packaged library: a book is opened, never installed or removed.
      actionsHtml = `
        <button class="btn-card-action primary btn-open-book" data-id="${book.id}">
          ${ICONS.open} <span>Aç</span>
        </button>
      `;
    } else if (isInstalled) {
      actionsHtml = `
        <button class="btn-card-action primary btn-open-book" data-id="${book.id}">
          ${ICONS.open} <span>Aç</span>
        </button>
        <button class="btn-card-icon danger btn-uninstall-book" data-id="${book.id}" title="Kitabı Kaldır">
          ${ICONS.trash}
        </button>
      `;
    } else {
      actionsHtml = `
        <button class="btn-card-action btn-preview-book" data-id="${book.id}" title="İndirmeden Görüntüle">
          ${ICONS.eye} <span>Önizle</span>
        </button>
        <button class="btn-card-icon btn-install-book" data-id="${book.id}" title="Kitabı İndir">
          ${ICONS.download}
        </button>
      `;
    }

    let confBadge = "";
    if (book.confidence) {
      const confLabels = { strong: "Güçlü", weak: "Zayıf", none: "Yok" };
      const label = confLabels[book.confidence] || book.confidence;
      confBadge = `<span class="book-confidence conf-${book.confidence}" title="Etkinlik Tespiti: ${label}">${label}</span>`;
    }

    return `
      <div class="book-card" data-id="${book.id}">
        <div class="book-cover btn-cover-click" data-id="${book.id}" data-installed="${isInstalled}">
          <div class="book-cover-placeholder">
            ${ICONS.book}
            <div class="book-cover-placeholder-title">${book.title}</div>
          </div>
          <img class="book-thumb-img${book.hasThumbnail ? " loaded" : ""}" data-id="${book.id}" alt="${book.title}" loading="lazy"${book.hasThumbnail ? ` src="/api/thumbnail?id=${book.id}"` : ""} />
        </div>
        <div class="book-info">
          <div>
            <div class="book-title btn-cover-click" data-id="${book.id}" data-installed="${isInstalled}">${book.title}</div>
            <div class="book-meta">
              <span>${book.gradeLabel}${sizeText}</span>
              ${confBadge}
            </div>
          </div>
          <div class="book-actions">
            ${actionsHtml}
          </div>
        </div>
      </div>
    `;
  }

  attachCardEventListeners(container) {
    // Open action
    container.querySelectorAll(".btn-open-book").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-id");
        this.openBook(id, false);
      });
    });

    // Preview action
    container.querySelectorAll(".btn-preview-book").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-id");
        this.openBook(id, true);
      });
    });

    // Cover / title click defaults to open if installed, preview if not
    container.querySelectorAll(".btn-cover-click").forEach((el) => {
      el.addEventListener("click", () => {
        const id = el.getAttribute("data-id");
        const isInstalled = el.getAttribute("data-installed") === "true";
        // A packaged book can only be opened; the preview path exists for a
        // catalogue book that has not been downloaded yet.
        this.openBook(id, this.libraryMode ? false : !isInstalled);
      });
    });

    // Install action
    container.querySelectorAll(".btn-install-book").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-id");
        this.startInstall(id);
      });
    });

    // Cancel download
    container.querySelectorAll(".btn-cancel-download").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-id");
        this.cancelInstall(id);
      });
    });

    // Uninstall action
    container.querySelectorAll(".btn-uninstall-book").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.getAttribute("data-id");
        this.confirmUninstall(id);
      });
    });
  }

  observeThumbnails() {
    if (!this.thumbObserver) return;
    const imgs = document.querySelectorAll(".book-thumb-img:not(.observed)");
    imgs.forEach((img) => {
      img.classList.add("observed");
      this.thumbObserver.observe(img);
    });
  }

  async loadThumbnail(bookId, imgEl) {
    const book = this.books.find((b) => b.id === bookId);
    if (!book) return;

    if (!imgEl.src || !imgEl.src.includes("/api/thumbnail")) {
      imgEl.onload = () => {
        imgEl.classList.add("loaded");
        book.hasThumbnail = true;
      };
      imgEl.onerror = () => {
        imgEl.onerror = null;
        this.enqueueThumbnailGeneration(bookId, imgEl);
      };
      imgEl.src = `/api/thumbnail?id=${bookId}`;
    }
  }

  enqueueThumbnailGeneration(bookId, imgEl) {
    if (this.thumbQueue.some((item) => item.id === bookId)) return;
    this.thumbQueue.push({ id: bookId, img: imgEl });
    this.processThumbnailQueue();
  }

  async processThumbnailQueue() {
    if (this.thumbGenerating || this.thumbQueue.length === 0) return;
    this.thumbGenerating = true;

    const { id, img } = this.thumbQueue.shift();
    try {
      await this.generateThumbnail(id, img);
    } catch (err) {
      console.warn(`Could not render thumbnail for ${id}:`, err);
    } finally {
      this.thumbGenerating = false;
      // Continue next in queue
      setTimeout(() => this.processThumbnailQueue(), 50);
    }
  }

  async generateThumbnail(bookId, imgEl) {
    const streamUrl = `/api/stream?id=${bookId}`;
    const loadingTask = pdfjsLib.getDocument({
      url: streamUrl,
      rangeChunkSize: 262144, // 256KB chunks
      disableAutoFetch: true,
      disableStream: true,
      cMapUrl: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/cmaps/",
      cMapPacked: true,
    });

    const doc = await loadingTask.promise;
    const page1 = await doc.getPage(1);

    // Render at crisp thumbnail size (~360px height)
    const baseViewport = page1.getViewport({ scale: 1.0 });
    const targetHeight = 360;
    const scale = targetHeight / baseViewport.height;
    const viewport = page1.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width);
    canvas.height = targetHeight;
    const ctx = canvas.getContext("2d");

    await page1.render({ canvasContext: ctx, viewport }).promise;

    // Crop out the back cover and spine if page 1 is a wrap-around cover spread
    let finalCanvas = canvas;
    if (canvas.width > targetHeight * 1.05) {
      const frontWidth = Math.floor(Math.min(canvas.width * 0.485, targetHeight * 0.703));
      const cropX = canvas.width - frontWidth;
      const cropCanvas = document.createElement("canvas");
      cropCanvas.width = frontWidth;
      cropCanvas.height = targetHeight;
      const cropCtx = cropCanvas.getContext("2d");
      cropCtx.drawImage(canvas, cropX, 0, frontWidth, targetHeight, 0, 0, frontWidth, targetHeight);
      finalCanvas = cropCanvas;
    }

    const dataUrl = finalCanvas.toDataURL("image/jpeg", 0.90);

    // Display immediately on image element
    if (imgEl) {
      imgEl.src = dataUrl;
      imgEl.classList.add("loaded");
    }

    // Persist to backend
    fetch(`/api/thumbnail?id=${bookId}`, {
      method: "POST",
      body: dataUrl,
    }).catch(() => {});

    // Free memory
    doc.destroy();
  }

  openBook(bookId, isPreview) {
    const book = this.books.find((b) => b.id === bookId);
    if (!book || !this.viewer) return;

    this.hide();
    const streamUrl = `/api/stream?id=${book.id}`;
    this.viewer.openDocument(streamUrl, book.title, {
      isPreview: isPreview,
      bookId: book.id,
    });
  }

  async startInstall(bookId) {
    if (this.libraryMode) return;
    try {
      this.downloads[bookId] = { status: "downloading", progress: 0 };
      this.render();
      this.startPollingDownloads();

      const res = await fetch(`/api/books/install?id=${bookId}`, { method: "POST" });
      const data = await res.json();
      if (!data.success) {
        delete this.downloads[bookId];
        this.render();
      }
    } catch (err) {
      console.error("Install failed:", err);
      delete this.downloads[bookId];
      this.render();
    }
  }

  async cancelInstall(bookId) {
    try {
      await fetch(`/api/books/cancel?id=${bookId}`, { method: "POST" });
      delete this.downloads[bookId];
      this.render();
    } catch (err) {
      console.error("Cancel failed:", err);
    }
  }

  async confirmUninstall(bookId) {
    if (this.libraryMode) return;
    const book = this.books.find((b) => b.id === bookId);
    const title = book ? book.title : "bu kitabı";
    if (!confirm(`"${title}" kitabını silmek istediğinize emin misiniz?`)) {
      return;
    }

    try {
      const res = await fetch(`/api/books/uninstall?id=${bookId}`, { method: "POST" });
      const data = await res.json();
      if (data.success) {
        if (book) {
          book.isInstalled = false;
          book.fileSize = null;
        }
        this.updateInstalledCount();
        this.render();
      }
    } catch (err) {
      console.error("Uninstall failed:", err);
    }
  }

  startPollingDownloads() {
    // Nothing can be downloaded in a packaged library, so there is nothing to poll.
    if (this.pollTimer || this.libraryMode) return;
    this.pollTimer = setInterval(async () => {
      try {
        const res = await fetch("/api/books/status");
        if (!res.ok) return;
        const data = await res.json();
        const active = data.downloads || {};

        this.downloads = active;

        // Check if any downloads just completed
        const hasActive = Object.values(active).some(
          (d) => d.status === "downloading"
        );

        if (!hasActive) {
          clearInterval(this.pollTimer);
          this.pollTimer = null;
          // Refresh full book list to get updated file sizes & status
          await this.fetchBooks();
        }

        this.render();
      } catch (err) {
        console.error("Error polling downloads:", err);
      }
    }, 800);
  }
}
