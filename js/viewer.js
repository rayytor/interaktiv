import * as pdfjsLib from "./pdf.min.mjs";
import { linkInteractiveOges, unplacedAnchors, hitTestRegion } from "./interactive-links.js";
import { DashboardApp } from "./dashboard.js";

// `activities.js` -- the live detector -- is deliberately *not* imported here.
// A packaged book carries its regions in the bundle, so the reader can answer
// every page without loading (let alone running) a 3.5k-line typographic
// analyser; it is pulled in with a dynamic import only when a document turns
// out to have no bake.

/**
 * The bake format this reader understands. `tools/hotspot_extraction/scan.py` writes
 * the same number; a bake from an older detector is ignored rather than trusted,
 * and the book falls back to live detection.
 */
const BAKE_VERSION = 2;

window.addEventListener("error", (e) => {
  console.error("Interaktiv PDF Error:", e.message, e.filename, e.lineno, e.error);
});

window.addEventListener("unhandledrejection", (e) => {
  console.error("Interaktiv PDF Unhandled Rejection:", e.reason);
});

// Set local worker
pdfjsLib.GlobalWorkerOptions.workerSrc = "./js/pdf.worker.mjs";

window.__INTERAKTIV_PERF__ = window.__INTERAKTIV_PERF__ || {
  runs: [],
  lastRun: null,
};

class PDFViewerApp {
  constructor() {
    this.pdfDoc = null;
    this.currentPage = 1;
    this.totalPages = 0;
    this.currentSpreadPages = [1];
    this.renderGeneration = 0;
    this.pageViewportCache = new Map();
    this.viewMode = "book"; // "book" (default), "single", "scroll"
    this.zoomMode = "fit-page"; // "fit-page", "fit-width", or numeric (1.0, 1.5...)
    this.customZoom = 1.0;
    this.rotation = 0;
    this.theme = localStorage.getItem("interaktiv_theme") || "dark";
    this.isRendering = false;
    this.renderTasks = new Map(); // pageNum -> renderTask
    this.pageDimensions = null; // { width, height } base pt dimensions
    this.docName = null;

    // Search state
    this.searchQuery = "";
    this.searchResults = []; // array of { pageNum, matchIndex }
    this.currentSearchMatchIndex = -1;

    // Cache of page text for search
    this.pageTextCache = new Map();

    // Cache of rendered thumbnail canvases & active observer
    this.thumbCache = new Map();
    this.thumbPromiseCache = new Map();
    this.thumbObserver = null;

    // Activity detection / focus state
    this.activityDetector = null;
    this.activityDetectorRequest = null;   // in-flight dynamic import
    this.liveDetectionAllowed = true;       // false when the edition has no detector module
    this.activitiesEnabled = true;
    this.activityDebug = new URLSearchParams(location.search).get("debug") === "activities";
    this.focusedActivity = null;   // { activity, subIndex, partIndex }
    this.focusRenderTask = null;
    this.focusScale = 1;
    this.focusZoomBias = 1;
    this.activityListBuilt = false;
    this.activityListBuilding = false;

    // Which build this is. "school" is the packaged, board-facing edition: it
    // reads a ready-made library, never scans a document, and gets the large
    // touch controls in school.css. "full" is the authoring reader.
    this.edition = "full";
    this.libraryMode = false;
    this.features = {};

    // Interactive activities fetched on demand: which ones are being fetched and
    // which are already cached, for the session.
    this.activityJit = new Map();
    this.activityLoadTimer = null;
    this.activityFrameLoaded = false;

    // DOM Elements
    this.dom = {
      app: document.getElementById("app"),
      docTitle: document.getElementById("doc-title"),
      loadingSpinner: document.getElementById("loading-spinner"),
      loadingText: document.getElementById("loading-text"),
      viewerContainer: document.getElementById("viewer-container"),
      viewerPages: document.getElementById("viewer-pages"),
      btnToggleSidebar: document.getElementById("btn-toggle-sidebar"),
      btnCloseSidebar: document.getElementById("btn-close-sidebar"),
      sidebar: document.getElementById("sidebar"),
      thumbnailsList: document.getElementById("thumbnails-list"),
      outlineList: document.getElementById("outline-list"),
      tabThumbnails: document.getElementById("tab-thumbnails"),
      tabOutline: document.getElementById("tab-outline"),
      thumbnailsPanel: document.getElementById("thumbnails-panel"),
      outlinePanel: document.getElementById("outline-panel"),
      btnPrevPage: document.getElementById("btn-prev-page"),
      btnNextPage: document.getElementById("btn-next-page"),
      pageNumInput: document.getElementById("page-num-input"),
      pageTotalCount: document.getElementById("page-total-count"),
      modeBook: document.getElementById("mode-book"),
      modeSingle: document.getElementById("mode-single"),
      modeScroll: document.getElementById("mode-scroll"),
      btnZoomOut: document.getElementById("btn-zoom-out"),
      btnZoomIn: document.getElementById("btn-zoom-in"),
      zoomSelect: document.getElementById("zoom-select"),
      btnRotate: document.getElementById("btn-rotate"),
      btnSearchToggle: document.getElementById("btn-search-toggle"),
      searchBar: document.getElementById("search-bar"),
      searchInput: document.getElementById("search-input"),
      searchMatchCount: document.getElementById("search-match-count"),
      searchPrev: document.getElementById("search-prev"),
      searchNext: document.getElementById("search-next"),
      searchClose: document.getElementById("search-close"),
      btnThemeToggle: document.getElementById("btn-theme-toggle"),
      btnFullscreen: document.getElementById("btn-fullscreen"),
      fileInput: document.getElementById("file-input"),
      btnHelp: document.getElementById("btn-help"),
      helpModal: document.getElementById("help-modal"),
      btnCloseModal: document.getElementById("btn-close-modal"),
      statusPageSize: document.getElementById("status-pagesize"),
      statusRenderTime: document.getElementById("status-rendertime"),
      statusZoomLabel: document.getElementById("status-zoom-label"),
      statusModeLabel: document.getElementById("status-mode-label"),
      tabActivities: document.getElementById("tab-activities"),
      activitiesPanel: document.getElementById("activities-panel"),
      activitiesList: document.getElementById("activities-list"),
      btnActivityToggle: document.getElementById("btn-activity-toggle"),
      focusOverlay: document.getElementById("focus-overlay"),
      focusStage: document.getElementById("focus-stage"),
      focusCanvasHost: document.getElementById("focus-canvas-host"),
      focusLabel: document.getElementById("focus-label"),
      focusHeadline: document.getElementById("focus-headline"),
      focusCounter: document.getElementById("focus-counter"),
      focusPrev: document.getElementById("focus-prev"),
      focusNext: document.getElementById("focus-next"),
      focusClose: document.getElementById("focus-close"),
      focusSubPrev: document.getElementById("focus-sub-prev"),
      focusSubNext: document.getElementById("focus-sub-next"),
      focusSubLabel: document.getElementById("focus-sub-label"),
      btnOpenDashboard: document.getElementById("btn-open-dashboard"),
      btnPreviewInstall: document.getElementById("btn-preview-install"),
      activityModal: document.getElementById("activity-modal"),
      activityModalTitle: document.getElementById("activity-modal-title"),
      activityLoader: document.getElementById("activity-loader"),
      activityIframe: document.getElementById("activity-iframe"),
      btnActivityOpenTab: document.getElementById("btn-activity-open-tab"),
      btnActivityFullscreen: document.getElementById("btn-activity-fullscreen"),
      btnCloseActivityModal: document.getElementById("btn-close-activity-modal"),
    };

    this.bookOges = [];
    this.ogesByPrintedPage = new Map();
    this.isActivityModalOpen = false;

    this.dashboard = new DashboardApp(this);
    this.initTheme();
    this.bindEvents();
    this.initHeartbeat();
    this.dashboard.init();
    this.loadInitialDocument();
  }

  showDashboard() {
    if (this.dashboard) {
      this.dashboard.show();
    }
    if (this.dom.app) {
      this.dom.app.style.display = "none";
    }
    document.title = "Interaktiv - Kitaplık";
  }

  hideDashboard() {
    if (this.dashboard) {
      this.dashboard.hide();
    }
    if (this.dom.app) {
      this.dom.app.style.display = "flex";
    }
  }

  /* --------------------------------------------------------------------------
     Heartbeat & Auto-Shutdown Watchdog Integration
     -------------------------------------------------------------------------- */
  initHeartbeat() {
    this.sessionId = "sess-" + Math.random().toString(36).slice(2, 10) + "-" + Date.now();

    const sendHeartbeat = () => {
      fetch(`/api/heartbeat?session=${this.sessionId}`, { method: "GET", keepalive: true }).catch(() => {});
    };

    // Send initial heartbeat immediately
    sendHeartbeat();

    // Pulse heartbeat every 2.5 seconds
    this.heartbeatTimer = setInterval(sendHeartbeat, 2500);

    // Immediately refresh heartbeat on window focus or when tab becomes visible again
    window.addEventListener("focus", sendHeartbeat);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        sendHeartbeat();
      }
    });

    // Notify backend when this specific window/tab is closed
    const onLeave = () => {
      const url = `/api/pagehide?session=${this.sessionId}`;
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url);
      } else {
        fetch(url, { method: "POST", keepalive: true }).catch(() => {});
      }
    };

    window.addEventListener("pagehide", onLeave);
    window.addEventListener("beforeunload", onLeave);
  }

  /* --------------------------------------------------------------------------
     Theme Management
     -------------------------------------------------------------------------- */
  initTheme() {
    document.documentElement.setAttribute("data-theme", this.theme);
  }

  cycleTheme() {
    const themes = ["dark", "light", "sepia", "inverted"];
    const currentIndex = themes.indexOf(this.theme);
    this.theme = themes[(currentIndex + 1) % themes.length];
    document.documentElement.setAttribute("data-theme", this.theme);
    localStorage.setItem("interaktiv_theme", this.theme);
  }

  /* --------------------------------------------------------------------------
     Document Loading via Byte-Range Streaming
     -------------------------------------------------------------------------- */
  async loadInitialDocument() {
    try {
      // Fetch default config from backend
      const res = await fetch("/api/config");
      let defaultPdf = null;
      let defaultName = null;
      let defaultBookId = null;
      let showDashboard = true;
      if (res.ok) {
        const data = await res.json();
        if (data.default_pdf) defaultPdf = data.default_pdf;
        if (data.filename) defaultName = data.filename;
        if (data.book_id) defaultBookId = data.book_id;
        if (data.default_view_mode) this.viewMode = data.default_view_mode;
        if (typeof data.show_dashboard === "boolean") showDashboard = data.show_dashboard;
        this.applyEdition(data);
      }

      const urlParams = new URLSearchParams(location.search);
      const queryPdf = urlParams.get("pdf");
      if (queryPdf) {
        defaultPdf = queryPdf;
        defaultName = queryPdf.split("/").pop();
        defaultBookId = null;
        showDashboard = false;
      }

      this.updateViewModeUI();

      if (showDashboard || !defaultPdf) {
        this.showDashboard();
      } else {
        this.hideDashboard();
        await this.openDocument(
          defaultPdf,
          defaultName || defaultPdf.split("/").pop(),
          defaultBookId ? { bookId: defaultBookId } : {}
        );
      }
    } catch (err) {
      console.warn("Could not fetch /api/config, falling back to dashboard", err);
      this.showDashboard();
    }
  }

  /**
   * Adopt the edition the server reported.
   *
   * One codebase serves two builds and this is the only place that knows which
   * it is. The CSS hook `edition-school` brings in the large board controls;
   * `features.live_detection` decides whether a document with no bake may be
   * scanned in the browser -- it may in the authoring edition, while in the
   * school edition every packaged book already carries its regions and the
   * detector module is not even shipped.
   */
  applyEdition(config) {
    config = config || {};
    this.edition = config.edition === "school" ? "school" : "full";
    this.libraryMode = !!config.library_mode;
    this.features = config.features || {};
    if (this.features.live_detection === false) {
      this.liveDetectionAllowed = false;
    }
    document.body.classList.toggle("edition-school", this.edition === "school");
    document.body.classList.toggle("library-mode", this.libraryMode);
    if (this.dashboard) this.dashboard.setEdition(config);
  }

  /**
   * The live detector, loaded on first use.
   *
   * Nothing about a baked book needs it: regions, folio and hit-testing all
   * come from the bundle. It is imported dynamically and at most once, so the
   * school edition never pays for a module it will not use -- and if it is
   * missing (school builds exclude it) the reader simply has no live fallback
   * rather than failing to start.
   *
   * @returns {Promise<object|null>} the detector, or null when unavailable
   */
  async ensureActivityDetector() {
    if (this.activityDetector) return this.activityDetector;
    if (!this.liveDetectionAllowed || !this.pdfDoc) return null;
    if (!this.activityDetectorRequest) {
      this.activityDetectorRequest = (async () => {
        try {
          const { ActivityDetector } = await import("./activities.js");
          const detector = new ActivityDetector(this.pdfDoc, this.docName);
          this.activityDetector = detector;
          detector.checkCache();
          await detector.loadOverrides();
          return detector;
        } catch (err) {
          this.liveDetectionAllowed = false;
          console.warn("Live activity detection is unavailable in this build:", err?.message || err);
          return null;
        }
      })();
    }
    return this.activityDetectorRequest;
  }

  async openDocument(urlOrData, docName = "full_pdf.pdf", options = {}) {
    const t_open_start = performance.now();
    const perf = {
      docName,
      timestamps: { openStart: t_open_start },
      durations: {},
      subdurations: {},
      isComplete: false,
    };
    this.currentPerf = perf;

    this.hideDashboard();
    this.showLoading(true, `Loading ${docName}...`);
    this.dom.docTitle.textContent = docName;
    document.title = `${docName} - Interaktiv PDF Reader`;
    this.docName = docName;
    this.currentBookId = options.bookId || this.resolveBookId(urlOrData, docName);
    this.loadBookActivitiesMeta(this.currentBookId);
    this.bakedRegions = null;

    // Handle preview install button
    if (this.dom.btnPreviewInstall) {
      if (options.isPreview && options.bookId) {
        this.dom.btnPreviewInstall.classList.remove("hidden");
        this.dom.btnPreviewInstall.onclick = async () => {
          this.dom.btnPreviewInstall.disabled = true;
          await this.dashboard.startInstall(options.bookId);
          this.dom.btnPreviewInstall.classList.add("hidden");
          this.dom.btnPreviewInstall.disabled = false;
        };
      } else {
        this.dom.btnPreviewInstall.classList.add("hidden");
      }
    }

    // Cancel existing render tasks
    this.cancelAllRenders();

    try {
      const t_getdoc_0 = performance.now();
      const loadingTask = pdfjsLib.getDocument({
        url: typeof urlOrData === "string" ? urlOrData : undefined,
        data: typeof urlOrData !== "string" ? urlOrData : undefined,
        rangeChunkSize: 262144, // 256 KB chunks for high performance streaming
        disableAutoFetch: true, // Only fetch ranges as requested
        disableStream: true,    // Force byte-range requests instead of downloading full stream
        cMapUrl: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/cmaps/",
        cMapPacked: true,
      });

      this.pdfDoc = await loadingTask.promise;
      perf.durations.pdfjs_get_document = performance.now() - t_getdoc_0;

      this.totalPages = this.pdfDoc.numPages;
      this.dom.pageTotalCount.textContent = this.totalPages;
      this.dom.pageNumInput.max = this.totalPages;

      this.pageViewportCache = new Map();
      this.pageTextCache = new Map();
      if (this.thumbCache) this.thumbCache.clear();
      this.thumbCache = new Map();
      if (this.thumbPromiseCache) this.thumbPromiseCache.clear();
      this.thumbPromiseCache = new Map();

      // Activity regions: a packaged book answers from its bake, which arrives
      // asynchronously, so the live detector is asked for only after that fetch
      // has come back empty -- and never at all in the school edition. Nothing
      // here waits on the bake: the reader goes straight to the first page and
      // the hotspots appear when the regions land.
      this.exitFocus(true);
      this.activityDetector = null;
      this.activityDetectorRequest = null;
      this.activityListBuilt = false;
      if (this.dom.activitiesList) {
        this.dom.activitiesList.innerHTML = '<div class="outline-empty">Open this tab to scan the document for activities.</div>';
      }
      const t_over_0 = performance.now();
      const bakePromise = this.loadBakedRegions(this.currentBookId);
      const overridesPromise = bakePromise
        .then(() => (this.bakedRegions ? null : this.ensureActivityDetector()))
        .then(() => {
          perf.durations.load_overrides = performance.now() - t_over_0;
          this.updateActivityAvailability();
        })
        .catch((err) => {
          console.warn("Could not prepare activity regions:", err);
        });

      this.currentPage = 1;
      this.currentSpreadPages = [1];
      this.dom.pageNumInput.value = 1;

      // Sample first page for status bar initial display
      const t_vp_0 = performance.now();
      const vp1 = await this.getPageViewport(1);
      perf.durations.sample_page1_viewport = performance.now() - t_vp_0;

      this.pageDimensions = { width: vp1.width, height: vp1.height };
      this.dom.statusPageSize.textContent = `${Math.round(vp1.width)} × ${Math.round(vp1.height)} pt`;

      this.updateNavButtonsState();

      // Setup Thumbnails & Outline
      const t_th_0 = performance.now();
      this.initThumbnails();
      perf.durations.init_thumbnails_dom = performance.now() - t_th_0;

      const t_out_0 = performance.now();
      const outlinePromise = this.loadOutline().then(() => {
        perf.durations.load_outline = performance.now() - t_out_0;
      });

      // Render Active View
      const t_renderview_0 = performance.now();
      await this.renderCurrentView();
      perf.durations.render_current_view = performance.now() - t_renderview_0;

      this.showLoading(false);
      perf.timestamps.initialRenderComplete = performance.now();
      perf.durations.time_to_initial_render = perf.timestamps.initialRenderComplete - t_open_start;
      window.__INTERAKTIV_PERF__.lastRun = perf;
      console.log("[INITIAL_RENDER_DONE]", JSON.stringify(perf));
      fetch("/api/client_perf?stage=initial_render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(perf),
      }).catch(() => {});

      const t_idle_sched = performance.now();
      const runIdle = (fn) => setTimeout(fn, 60);
      runIdle(async () => {
        perf.durations.idle_wait_delay = performance.now() - t_idle_sched;
        // Regions first: a packaged book is answered by its bake, and only a
        // document without one needs the live detector calibrated at all. The
        // bake fetch and the detector import were both started before the first
        // render, so this waits on them rather than on the clock.
        await Promise.allSettled([overridesPromise, outlinePromise]);
        const detector = this.activityDetector;
        if (detector && !this.bakedRegions) {
          const t_cal_0 = performance.now();
          try {
            await detector.calibrate();
            perf.durations.activity_calibration = performance.now() - t_cal_0;
            if (detector.calibrateStats) {
              perf.calibrationStats = detector.calibrateStats;
            }
            if (this.activityDetector === detector) {
              this.updateActivityAvailability();
              const t_ref_0 = performance.now();
              this.refreshActivityLayers();
              perf.durations.refresh_activity_layers_post_calib = performance.now() - t_ref_0;
              if (detector.analyzeStats && detector.analyzeStats.has(1)) {
                perf.subdurations.page1_activity_analysis = detector.analyzeStats.get(1);
              }
            }
          } catch (err) {
            console.warn("Calibration error in perf run:", err);
          }
        }
        perf.durations.total_process_time = performance.now() - t_open_start;
        perf.isComplete = true;
        window.__INTERAKTIV_PERF__.lastRun = perf;
        window.__INTERAKTIV_PERF__.runs.push(perf);
        console.log("[PERF_METRICS_READY]", JSON.stringify(perf));
        fetch("/api/client_perf?stage=complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(perf),
        }).catch(() => {});
      });
    } catch (err) {
      console.error("Error loading PDF document:", err);
      fetch("/api/client_perf?stage=error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ error: String(err), stack: err ? err.stack : null }),
      }).catch(() => {});
      this.showLoading(false);
      alert(`Error loading PDF document: ${err.message}`);
    }
  }

  showLoading(show, text = "Loading...") {
    if (show) {
      this.dom.loadingText.textContent = text;
      this.dom.loadingSpinner.classList.remove("hidden");
    } else {
      this.dom.loadingSpinner.classList.add("hidden");
    }
  }

  /* --------------------------------------------------------------------------
     View Mode Management
     -------------------------------------------------------------------------- */
  setViewMode(mode) {
    if (this.viewMode === mode) return;
    const prevMode = this.viewMode;
    this.viewMode = mode;
    this.updateViewModeUI();

    const wasBook = prevMode === "book";
    const isBook = this.viewMode === "book";
    if (wasBook !== isBook && this.pdfDoc) {
      this.initThumbnails();
    }

    this.renderCurrentView();
  }

  updateViewModeUI() {
    this.dom.modeBook.classList.toggle("active", this.viewMode === "book");
    this.dom.modeSingle.classList.toggle("active", this.viewMode === "single");
    this.dom.modeScroll.classList.toggle("active", this.viewMode === "scroll");

    const modeLabels = {
      book: "Two-Page Book Spread",
      single: "Single Page",
      scroll: "Continuous Vertical Scroll",
    };
    this.dom.statusModeLabel.textContent = modeLabels[this.viewMode] || this.viewMode;
  }

  async getPageViewport(pageNum) {
    const cacheKey = `${pageNum}_${this.rotation}`;
    if (this.pageViewportCache && this.pageViewportCache.has(cacheKey)) {
      return this.pageViewportCache.get(cacheKey);
    }
    const page = await this.pdfDoc.getPage(pageNum);
    const vp = page.getViewport({ scale: 1.0, rotation: this.rotation });
    if (!this.pageViewportCache) this.pageViewportCache = new Map();
    this.pageViewportCache.set(cacheKey, vp);
    return vp;
  }

  updateNavButtonsState() {
    const isFirst = this.currentPage <= 1;
    let isLast = false;
    if (this.viewMode === "book") {
      isLast = this.currentSpreadPages.includes(this.totalPages) || (this.currentPage === 1 && this.totalPages <= 1);
    } else {
      isLast = this.currentPage >= this.totalPages;
    }
    this.dom.btnPrevPage.disabled = isFirst;
    this.dom.btnNextPage.disabled = isLast;
  }

  goToPage(pageNum) {
    pageNum = Math.max(1, Math.min(pageNum, this.totalPages));

    if (this.viewMode === "scroll") {
      this.currentPage = pageNum;
      this.dom.pageNumInput.value = this.currentPage;
      const targetEl = document.getElementById(`page-wrapper-${this.currentPage}`);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      this.updateThumbnailsActiveState();
      this.updateNavButtonsState();
      return;
    }

    // In book mode, avoid redundant re-renders if already displaying the spread
    if (this.viewMode === "book" && this.currentSpreadPages.includes(pageNum)) {
      return;
    }

    if (this.viewMode === "single" && pageNum === this.currentPage) {
      return;
    }

    this.currentPage = pageNum;
    this.dom.pageNumInput.value = this.currentPage;
    this.renderCurrentView();
  }

  nextPage() {
    if (this.viewMode === "book") {
      if (this.currentPage === 1) {
        if (this.totalPages >= 2) this.goToPage(2);
      } else {
        const nextTarget = this.currentPage + 2;
        if (nextTarget <= this.totalPages) {
          this.goToPage(nextTarget);
        } else if (this.currentSpreadPages.includes(this.totalPages)) {
          return;
        } else {
          this.goToPage(this.totalPages);
        }
      }
    } else {
      if (this.currentPage < this.totalPages) {
        this.goToPage(this.currentPage + 1);
      }
    }
  }

  prevPage() {
    if (this.viewMode === "book") {
      if (this.currentPage <= 1) {
        return;
      } else if (this.currentPage <= 3) {
        this.goToPage(1);
      } else {
        this.goToPage(this.currentPage - 2);
      }
    } else {
      if (this.currentPage > 1) {
        this.goToPage(this.currentPage - 1);
      }
    }
  }

  /* --------------------------------------------------------------------------
     Zoom Calculation
     -------------------------------------------------------------------------- */
  setZoom(modeOrValue) {
    if (typeof modeOrValue === "number") {
      this.zoomMode = "custom";
      this.customZoom = modeOrValue;
      this.dom.zoomSelect.value = String(modeOrValue);
      this.dom.statusZoomLabel.textContent = `${Math.round(modeOrValue * 100)}%`;
    } else {
      this.zoomMode = modeOrValue;
      this.dom.zoomSelect.value = modeOrValue;
      this.dom.statusZoomLabel.textContent = modeOrValue === "fit-width" ? "Fit Width" : "Fit Page";
    }
    this.renderCurrentView();
  }

  calculateScale(targetWidth, targetHeight) {
    if (this.zoomMode === "custom") {
      return this.customZoom;
    }

    const containerW = Math.max(320, (this.dom.viewerContainer.clientWidth || window.innerWidth - 260) - 48);
    const containerH = Math.max(320, (this.dom.viewerContainer.clientHeight || window.innerHeight - 80) - 48);

    if (this.zoomMode === "fit-width") {
      return Math.max(0.2, containerW / targetWidth);
    }

    // Default "fit-page"
    const scaleX = containerW / targetWidth;
    const scaleY = containerH / targetHeight;
    return Math.max(0.2, Math.min(scaleX, scaleY));
  }

  /* --------------------------------------------------------------------------
     Core Rendering Logic
     -------------------------------------------------------------------------- */
  cancelAllRenders() {
    for (const [pageNum, task] of this.renderTasks.entries()) {
      try {
        task.cancel();
      } catch (_) {}
    }
    this.renderTasks.clear();
  }

  async renderCurrentView() {
    if (!this.pdfDoc) return;
    const startTime = performance.now();
    this.cancelAllRenders();

    const renderId = ++this.renderGeneration;
    this.dom.viewerPages.className = `viewer-pages mode-${this.viewMode}`;

    if (this.viewMode === "book") {
      await this.renderBookMode(renderId);
    } else if (this.viewMode === "single") {
      await this.renderSingleMode(renderId);
    } else if (this.viewMode === "scroll") {
      await this.renderScrollMode(renderId);
    }

    if (this.renderGeneration !== renderId) return;

    const renderMs = Math.round(performance.now() - startTime);
    this.dom.statusRenderTime.textContent = `Rendered in ${renderMs}ms`;
    this.updateThumbnailsActiveState();
    this.updateNavButtonsState();
  }

  /**
   * Mode: Two-Page Book Spread
   * Cover page 1 displayed alone or facing pages (2 & 3, 4 & 5, etc.)
   */
  async renderBookMode(renderId) {
    const isCover = this.currentPage === 1;
    let pageNum1 = this.currentPage;
    let pageNum2 = null;

    if (!isCover) {
      // Align to facing pair: even on left, odd on right (e.g. 2 & 3)
      if (pageNum1 % 2 !== 0) {
        pageNum1 = pageNum1 - 1;
      }
      if (pageNum1 + 1 <= this.totalPages) {
        pageNum2 = pageNum1 + 1;
      }
      this.currentPage = pageNum1;
      this.dom.pageNumInput.value = pageNum1;
    }

    this.currentSpreadPages = pageNum2 ? [pageNum1, pageNum2] : [pageNum1];

    // Fetch viewports for facing pages in parallel
    const [vp1, vp2] = await Promise.all([
      this.getPageViewport(pageNum1),
      pageNum2 ? this.getPageViewport(pageNum2) : Promise.resolve(null),
    ]);

    if (this.renderGeneration !== renderId) return;

    // Calculate ACTUAL spread dimensions from visible page(s)
    const spreadWidth = vp2 ? (vp1.width + vp2.width) : vp1.width;
    const spreadHeight = vp2 ? Math.max(vp1.height, vp2.height) : vp1.height;
    const scale = this.calculateScale(spreadWidth, spreadHeight);

    // Update status page size accurately
    if (vp2) {
      this.dom.statusPageSize.textContent = `${Math.round(vp1.width)} × ${Math.round(vp1.height)} pt (Spread: ${Math.round(spreadWidth)} × ${Math.round(spreadHeight)} pt)`;
    } else {
      this.dom.statusPageSize.textContent = `${Math.round(spreadWidth)} × ${Math.round(spreadHeight)} pt`;
    }

    const t_layout_0 = performance.now();
    const spreadContainer = document.createElement("div");
    spreadContainer.className = "book-spread";

    if (isCover || !pageNum2) {
      // Standalone single cover / last page
      const pageWrapper = this.createPageElementSync(pageNum1, vp1, scale, "cover-single");
      spreadContainer.appendChild(pageWrapper);

      if (this.renderGeneration !== renderId) return;
      this.dom.viewerPages.replaceChildren(spreadContainer);
      if (this.currentPerf && !this.currentPerf.durations.book_layout_and_dom_setup) {
        this.currentPerf.durations.book_layout_and_dom_setup = performance.now() - t_layout_0;
      }

      await this.renderPageCanvas(pageNum1, pageWrapper, scale, renderId);
    } else {
      // Left and Right facing pages
      const leftWrapper = this.createPageElementSync(pageNum1, vp1, scale, "page-left");
      const rightWrapper = this.createPageElementSync(pageNum2, vp2, scale, "page-right");

      spreadContainer.appendChild(leftWrapper);
      spreadContainer.appendChild(rightWrapper);

      if (this.renderGeneration !== renderId) return;
      this.dom.viewerPages.replaceChildren(spreadContainer);

      // Render both facing pages in parallel
      await Promise.all([
        this.renderPageCanvas(pageNum1, leftWrapper, scale, renderId),
        this.renderPageCanvas(pageNum2, rightWrapper, scale, renderId),
      ]);
    }
  }

  /**
   * Mode: Single Page
   */
  async renderSingleMode(renderId) {
    this.currentSpreadPages = [this.currentPage];
    const vp = await this.getPageViewport(this.currentPage);
    if (this.renderGeneration !== renderId) return;

    const scale = this.calculateScale(vp.width, vp.height);
    this.dom.statusPageSize.textContent = `${Math.round(vp.width)} × ${Math.round(vp.height)} pt`;

    const pageWrapper = this.createPageElementSync(this.currentPage, vp, scale, "page-single");
    this.dom.viewerPages.replaceChildren(pageWrapper);

    await this.renderPageCanvas(this.currentPage, pageWrapper, scale, renderId);
  }

  /**
   * Mode: Continuous Vertical Scroll with IntersectionObserver Virtualization
   */
  async renderScrollMode(renderId) {
    this.currentSpreadPages = [this.currentPage];
    const vpSample = await this.getPageViewport(this.currentPage);
    if (this.renderGeneration !== renderId) return;

    const scale = this.calculateScale(vpSample.width, vpSample.height);
    this.dom.statusPageSize.textContent = `${Math.round(vpSample.width)} × ${Math.round(vpSample.height)} pt`;

    const observerOptions = {
      root: this.dom.viewerContainer,
      rootMargin: "300px 0px 300px 0px", // Preload buffer
      threshold: 0.01,
    };

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const pageWrapper = entry.target;
        const pageNum = parseInt(pageWrapper.dataset.pageNum, 10);

        if (entry.isIntersecting) {
          // Mount canvas if not already rendered
          if (!pageWrapper.dataset.rendered) {
            this.renderPageCanvas(pageNum, pageWrapper, scale, renderId);
          }
        } else {
          // Virtualize: unmount offscreen canvas to save memory
          if (pageWrapper.dataset.rendered) {
            this.unmountPageCanvas(pageNum, pageWrapper);
          }
        }
      }

      // Update current page input based on most visible element
      this.updateDominantScrollPage();
    }, observerOptions);

    const wrappers = [];
    for (let p = 1; p <= this.totalPages; p++) {
      const pageWrapper = await this.createPageElement(p, scale, "page-scroll");
      if (this.renderGeneration !== renderId) return;
      wrappers.push(pageWrapper);
      observer.observe(pageWrapper);
    }

    this.dom.viewerPages.replaceChildren(...wrappers);

    // Scroll to active page
    const targetEl = document.getElementById(`page-wrapper-${this.currentPage}`);
    if (targetEl) {
      setTimeout(() => targetEl.scrollIntoView({ block: "start" }), 50);
    }
  }

  updateDominantScrollPage() {
    const wrappers = this.dom.viewerPages.querySelectorAll(".pdf-page-wrapper");
    const containerRect = this.dom.viewerContainer.getBoundingClientRect();
    let bestPage = this.currentPage;
    let maxVisible = -Infinity;

    wrappers.forEach((el) => {
      const rect = el.getBoundingClientRect();
      const visibleHeight = Math.min(rect.bottom, containerRect.bottom) - Math.max(rect.top, containerRect.top);
      if (visibleHeight > maxVisible) {
        maxVisible = visibleHeight;
        bestPage = parseInt(el.dataset.pageNum, 10);
      }
    });

    if (bestPage !== this.currentPage && bestPage >= 1 && bestPage <= this.totalPages) {
      this.currentPage = bestPage;
      this.dom.pageNumInput.value = bestPage;
      this.updateThumbnailsActiveState();
      this.updateNavButtonsState();
    }
  }

  /* --------------------------------------------------------------------------
     DOM Page Element Creation & Rendering
     -------------------------------------------------------------------------- */
  createPageElementSync(pageNum, unscaledViewport, scale, extraClass = "") {
    const widthPx = Math.floor(unscaledViewport.width * scale);
    const heightPx = Math.floor(unscaledViewport.height * scale);

    const wrapper = document.createElement("div");
    wrapper.id = `page-wrapper-${pageNum}`;
    wrapper.dataset.pageNum = pageNum;
    wrapper.className = `pdf-page-wrapper ${extraClass}`;
    wrapper.style.width = `${widthPx}px`;
    wrapper.style.height = `${heightPx}px`;
    wrapper.style.setProperty("--scale-factor", scale);

    // Skeleton placeholder while page canvas is decoding
    const skeleton = document.createElement("div");
    skeleton.className = "page-skeleton";
    skeleton.innerHTML = `
      <div class="page-skeleton-spinner"></div>
      <span class="page-skeleton-label">Page ${pageNum}</span>
    `;
    wrapper.appendChild(skeleton);

    return wrapper;
  }

  async createPageElement(pageNum, scale, extraClass = "") {
    const vp = await this.getPageViewport(pageNum);
    return this.createPageElementSync(pageNum, vp, scale, extraClass);
  }

  async renderPageCanvas(pageNum, wrapper, scale, renderId = null) {
    if (renderId && this.renderGeneration !== renderId) return;
    if (this.renderTasks.has(pageNum)) return;

    let currentTask = null;
    try {
      const t_gp_0 = performance.now();
      const page = await this.pdfDoc.getPage(pageNum);
      const pageFetchMs = performance.now() - t_gp_0;
      if (renderId && this.renderGeneration !== renderId) return;

      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale, rotation: this.rotation });

      // Set scale factor for text layer positioning
      wrapper.style.setProperty("--scale-factor", viewport.scale);

      // Create or get canvas
      let canvas = wrapper.querySelector("canvas");
      if (!canvas) {
        canvas = document.createElement("canvas");
        wrapper.appendChild(canvas);
      }

      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      const ctx = canvas.getContext("2d", { alpha: false });
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";

      const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null;

      const renderContext = {
        canvasContext: ctx,
        viewport: viewport,
        transform: transform,
      };

      const t_cr_0 = performance.now();
      currentTask = page.render(renderContext);
      this.renderTasks.set(pageNum, currentTask);

      await currentTask.promise;
      const canvasRenderMs = performance.now() - t_cr_0;
      if (this.renderTasks.get(pageNum) === currentTask) {
        this.renderTasks.delete(pageNum);
      }
      wrapper.dataset.rendered = "true";

      // Hide skeleton loader smoothly
      const skeleton = wrapper.querySelector(".page-skeleton");
      if (skeleton) {
        skeleton.classList.add("hidden");
        setTimeout(() => skeleton.remove(), 250);
      }

      // Render Synchronized Text Layer for selection and search
      const t_tl_0 = performance.now();
      await this.renderTextLayer(page, viewport, wrapper);
      const textLayerMs = performance.now() - t_tl_0;

      // Detect and paint clickable activity regions for this page
      const t_act_0 = performance.now();
      this.buildActivityLayer(pageNum, wrapper, viewport);
      const initialActLayerMs = performance.now() - t_act_0;

      if (this.currentPerf && !this.currentPerf.durations.page1_canvas_render) {
        this.currentPerf.durations.page1_get_page = pageFetchMs;
        this.currentPerf.durations.page1_canvas_render = canvasRenderMs;
        this.currentPerf.durations.page1_text_layer = textLayerMs;
        this.currentPerf.durations.page1_initial_activity_layer = initialActLayerMs;
      }
    } catch (err) {
      if (err.name !== "RenderingCancelledException") {
        console.error(`Render error for page ${pageNum}:`, err);
      }
      if (currentTask && this.renderTasks.get(pageNum) === currentTask) {
        this.renderTasks.delete(pageNum);
      }
    }
  }

  async renderTextLayer(page, viewport, wrapper) {
    try {
      let textLayerDiv = wrapper.querySelector(".textLayer");
      if (!textLayerDiv) {
        textLayerDiv = document.createElement("div");
        textLayerDiv.className = "textLayer";
        wrapper.appendChild(textLayerDiv);
      } else {
        textLayerDiv.innerHTML = "";
      }

      textLayerDiv.style.width = `${Math.floor(viewport.width)}px`;
      textLayerDiv.style.height = `${Math.floor(viewport.height)}px`;
      textLayerDiv.style.setProperty("--scale-factor", viewport.scale);

      const t_tc_0 = performance.now();
      const textContent = await page.getTextContent();
      const tcFetchMs = performance.now() - t_tc_0;

      // The page carries its own number; taking it from the proxy keeps this
      // free of a parameter the two call sites would have to agree on.
      if (this.activityDetector && page.pageNumber) {
        this.activityDetector.setTextContent(page.pageNumber, textContent);
      }

      const t_tldom_0 = performance.now();
      const textLayer = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textLayerDiv,
        viewport: viewport,
      });

      await textLayer.render();
      const tlDomMs = performance.now() - t_tldom_0;

      if (this.currentPerf && !this.currentPerf.subdurations.page1_get_text_content) {
        this.currentPerf.subdurations.page1_get_text_content = tcFetchMs;
        this.currentPerf.subdurations.page1_text_layer_dom_render = tlDomMs;
      }

      // If active search query, highlight matches
      if (this.searchQuery) {
        this.highlightMatchesInTextLayer(textLayerDiv, this.searchQuery);
      }
    } catch (err) {
      console.warn("Text layer render skipped:", err);
    }
  }

  unmountPageCanvas(pageNum, wrapper) {
    if (this.renderTasks.has(pageNum)) {
      try {
        this.renderTasks.get(pageNum).cancel();
      } catch (_) {}
      this.renderTasks.delete(pageNum);
    }

    const canvas = wrapper.querySelector("canvas");
    if (canvas) canvas.remove();

    const textLayer = wrapper.querySelector(".textLayer");
    if (textLayer) textLayer.remove();

    const activityLayer = wrapper.querySelector(".activityLayer");
    if (activityLayer) activityLayer.remove();
    wrapper._activityViewport = null;
    wrapper._activityData = null;

    delete wrapper.dataset.rendered;
  }

  /* --------------------------------------------------------------------------
     Activity Detection: Hotspots, Focus Mode & Navigation
     -------------------------------------------------------------------------- */

  updateActivityAvailability() {
    const available = this.bakedEnabled || !!(this.activityDetector && this.activityDetector.enabled);
    if (this.dom.btnActivityToggle) {
      this.dom.btnActivityToggle.disabled = !available;
      this.dom.btnActivityToggle.classList.toggle("active", available && this.activitiesEnabled);
      this.dom.btnActivityToggle.title = available
        ? "Toggle Activity Hotspots (A)"
        : "No lettered activities detected in this document";
    }
    if (this.dom.tabActivities) {
      this.dom.tabActivities.disabled = !available;
    }
    document.body.classList.toggle("activities-off", !this.activitiesEnabled);
  }

  toggleActivities() {
    if (!this.bakedEnabled && !(this.activityDetector && this.activityDetector.enabled)) return;
    this.activitiesEnabled = !this.activitiesEnabled;
    this.updateActivityAvailability();
    this.refreshActivityLayers();
  }

  refreshActivityLayers() {
    const wrappers = this.dom.viewerPages.querySelectorAll(".pdf-page-wrapper");
    wrappers.forEach((wrapper) => {
      const pageNum = parseInt(wrapper.dataset.pageNum, 10);
      if (wrapper._activityViewport) {
        this.buildActivityLayer(pageNum, wrapper, wrapper._activityViewport);
      }
    });
  }

  /**
   * Which catalogue book the open document is, from how it was addressed. The
   * caller's explicit `bookId` wins over this; what is left to read here is the
   * id in a stream URL or in the file's own name.
   */
  resolveBookId(urlOrData, docName) {
    if (typeof urlOrData === "string") {
      const mStream = urlOrData.match(/[?&]id=([a-f0-9\-]{36})/i);
      if (mStream) return mStream[1];
      const mGuid = urlOrData.match(/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/i);
      if (mGuid) return mGuid[1];
    }
    if (typeof docName === "string") {
      const mGuid = docName.match(/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/i);
      if (mGuid) return mGuid[1];
    }
    // No book identified: an unrelated PDF gets no interactive activities rather
    // than another book's, which would hang the wrong content off its pages.
    return null;
  }

  /**
   * The regions this book was baked with, if the server has a bake for it.
   *
   * Detection is the same code either way -- `tools/bake_activities.mjs` runs
   * `ActivityDetector` over the whole book on the server -- so this is not a
   * second implementation to keep in step, only the same answer computed once
   * instead of per reader. It also makes the folio map deterministic: live,
   * `printedPageFor()` votes from whichever sheets the reader has visited, so
   * the join key to the publisher's manifest can differ between sessions.
   *
   * A book with no bake, or a bake made from different bytes, simply falls back
   * to live detection, which is also what a PDF opened from the file picker gets.
   */
  async loadBakedRegions(bookId) {
    this.bakedRegions = null;
    if (!bookId) return;
    try {
      const res = await fetch(`/api/activities/regions?book_id=${encodeURIComponent(bookId)}`);
      if (!res.ok) return;  // 404 is the ordinary "not baked" answer
      const baked = await res.json();
      if (!baked || (baked.version !== 1 && baked.version !== BAKE_VERSION) || !baked.pages) return;
      if (this.pdfDoc && baked.pageCount !== this.pdfDoc.numPages) {
        // A different edition under the same catalogue id: the page ordinals
        // would not line up, so the rects would land on the wrong sheets.
        console.warn(`Baked regions are for a ${baked.pageCount}-page edition, this file has ${this.pdfDoc.numPages}; using live detection`);
        return;
      }
      this.bakedRegions = baked;
      if (this.activityDetector && typeof baked.folio?.offset === "number") {
        this.activityDetector.folioOffset = baked.folio.offset;
      }
      this.updateActivityAvailability();
      this.refreshActivityLayers();
    } catch (err) {
      console.warn("Could not load baked activity regions:", err);
    }
  }

  /**
   * The number this sheet prints on itself, which is how the publisher's
   * manifest indexes its activities.
   *
   * The baked map is preferred because every sheet in the book voted for it;
   * the live detector's is voted only from the sheets that have been analysed
   * so far, so it can still be wrong, or absent, early in a session.
   */
  printedPageFor(pageNum) {
    const baked = this.bakedRegions && this.bakedRegions.folio;
    if (baked) {
      const printed = baked.byPage[String(pageNum)];
      if (typeof printed === "number") return printed;
      if (typeof baked.offset === "number") return pageNum - baked.offset;
    }
    return this.activityDetector ? this.activityDetector.printedPageFor(pageNum) : null;
  }

  /**
   * This page's activities, from the bake when there is one and from the live
   * detector otherwise. Returns null if neither can answer.
   */
  async activityDataFor(pageNum) {
    const baked = this.bakedPage(pageNum);
    if (baked) return baked;
    const detector = await this.ensureActivityDetector();
    if (!detector || !detector.enabled) return null;
    return this.analyzeWithAnchors(pageNum);
  }

  /**
   * What to call an activity in the chrome.
   *
   * A lettered region is named by its letter. A region grown from the
   * publisher's icon has no letter to be named by -- that is the whole reason
   * it was anchored -- so it goes by the headline read off its own first line.
   */
  activityName(act) {
    if (act.label) return `Activity ${act.label}`;
    return act.headline || "Activity";
  }

  /** The publisher's interactive entries sitting on one sheet. */
  interactiveOgesFor(pageNum) {
    const printed = this.printedPageFor(pageNum);
    const list =
      (printed !== null && this.ogesByPrintedPage
        ? this.ogesByPrintedPage.get(printed)
        : null) || [];
    return list.filter((o) => o.ogeturu === 1 && o.data);
  }

  /**
   * The live detector's answer for one sheet, with the publisher's leftover
   * entries anchored into it.
   *
   * Entries that no label on the sheet accounts for get a region grown from
   * where the publisher hung the icon, rather than a pin in the corner. The
   * anchors are read off the labelled answer and the sheet is then analysed
   * again with them in hand, so an anchor can never change which letters were
   * accepted -- the same order the baker uses, which is why a baked book comes
   * out of the server already carrying these and never goes through here.
   *
   * A sheet is only put through the second pass once: after it, its anchors are
   * recorded and analysing it again already includes them.
   */
  async analyzeWithAnchors(pageNum) {
    const detector = await this.ensureActivityDetector();
    if (!detector) return null;
    const data = await detector.analyzePage(pageNum, pdfjsLib);
    if (!data) return data;
    if (detector.anchorsByPage && detector.anchorsByPage.has(pageNum)) return data;
    const oges = this.interactiveOgesFor(pageNum);
    if (!oges.length) return data;
    const anchors = unplacedAnchors(data, oges);
    if (!anchors.length) return data;
    if (!detector.anchorsByPage) detector.anchorsByPage = new Map();
    detector.anchorsByPage.set(
      pageNum,
      anchors.map((a) => ({ id: a.id, posx: a.posx, posy: a.posy }))
    );
    detector.releasePage(pageNum, { keepText: true });
    return detector.analyzePage(pageNum, pdfjsLib);
  }

  /** True when the bake, not the live detector, says this book has activities. */
  get bakedEnabled() {
    return !!(this.bakedRegions && this.bakedRegions.calibration && this.bakedRegions.calibration.enabled);
  }

  /**
   * One baked page in the shape `analyzePage` returns, so every caller
   * downstream -- hotspots, linking, focus, the activity list -- is unchanged.
   */
  bakedPage(pageNum) {
    const page = this.bakedRegions && this.bakedRegions.pages[String(pageNum)];
    if (!page) return null;
    const toRect = (r) => ({ x0: r[0], y0: r[1], x1: r[2], y1: r[3] });
    return {
      pageNum,
      pageWidth: page.pageWidth,
      pageHeight: page.pageHeight,
      columns: (page.columns || []).map(([x0, x1]) => ({ x0, x1 })),
      baked: true,
      activities: page.activities.map((a) => ({
        id: a.id,
        pageNum,
        label: a.label,
        column: a.column,
        headline: a.headline || "",
        rect: toRect(a.rect),
        // Kept as separate pieces, never unioned: see serializeActivity().
        parts: (a.parts || [a.rect]).map(toRect),
        items: (a.items || []).map((q) => ({
          id: q.id,
          label: q.label,
          number: q.number,
          partIndex: q.partIndex ?? 0,
          pageNum,
          rect: toRect(q.rect),
          text: q.text || "",
        })),
      })),
    };
  }

  async loadBookActivitiesMeta(bookId) {
    if (!bookId) return;
    this.bookOges = [];
    this.ogesByPrintedPage = new Map();
    try {
      const res = await fetch(`/api/activities/meta?book_id=${encodeURIComponent(bookId)}`);
      if (res.ok) {
        const data = await res.json();
        this.bookOges = data.oges || [];
        // The manifest counts in printed page numbers, which are not the sheets'
        // ordinals in the file (see ActivityDetector.printedPageFor).
        for (const oge of this.bookOges) {
          // `sayfaustuoge` is the publisher's own flag for an entry that sits on
          // a page. The ones without it carry sayfano 0 and posx/posy 0 -- they
          // are book-level activities belonging to no sheet, and indexing them
          // by sayfano would pin them all to the top-left corner of sheet 1.
          if (!oge.sayfaustuoge || !oge.sayfano) continue;
          const p = oge.sayfano;
          if (!this.ogesByPrintedPage.has(p)) this.ogesByPrintedPage.set(p, []);
          this.ogesByPrintedPage.get(p).push(oge);
        }
        // If current pages are rendered, refresh activity layers
        if (this.pdfDoc) {
          this.dom.viewerPages.querySelectorAll(".pdf-page-wrapper").forEach((wrapper) => {
            const pageNum = parseInt(wrapper.dataset.pageNum, 10);
            if (pageNum && wrapper._activityViewport) {
              this.buildActivityLayer(pageNum, wrapper, wrapper._activityViewport);
            }
          });
        }
      }
    } catch (err) {
      console.warn("Could not load book activities metadata:", err);
    }
  }

  openActivityModal(act) {
    if (!this.dom.activityModal || !this.dom.activityIframe) return;

    const guid = act.interactiveGuid || act.data;
    if (!guid) return;

    const printed = act.printedPage != null
      ? act.printedPage
      : this.printedPageFor(act.pageNum);
    const where = printed != null ? printed : act.pageNum;
    const title = act.label
      ? `Page ${where} \u00b7 Activity ${act.label}`
      : (act.headline || "Interactive Activity");
    if (this.dom.activityModalTitle) {
      this.dom.activityModalTitle.textContent = title;
    }

    if (this.dom.activityLoader) {
      this.dom.activityLoader.classList.remove("hidden");
    }
    this.setActivityOffline(false);

    // Interactive activities are never shipped with a packaged book -- they are
    // the biggest part of the publisher's material and they change independently
    // of the textbooks. So each one is fetched the first time it is opened, on
    // demand, and kept in this edition's cache; the next open is served from
    // disk and needs no network.
    const localUrl = `/activities/${guid}/index.html`;
    const cdnUrl = `https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama/${guid}/index.html`;

    if (act.interactiveInstalled) {
      this.dom.activityIframe.src = localUrl;
    } else {
      // Open from the CDN straight away -- the activity is usable while the copy
      // for next time is being written -- and cache it in the background.
      this.dom.activityIframe.src = cdnUrl;
      this.jitFetchActivity(guid, act);
    }

    // A board that has lost the network shows an empty frame and says nothing;
    // if the frame has not come up within a few seconds, say so.
    if (this.activityLoadTimer) clearTimeout(this.activityLoadTimer);
    this.activityLoadTimer = setTimeout(() => {
      this.activityLoadTimer = null;
      if (!this.isActivityModalOpen) return;
      if (!this.activityFrameLoaded) this.setActivityOffline(true);
    }, 7000);

    this.activityFrameLoaded = false;
    this.dom.activityIframe.onload = () => {
      this.activityFrameLoaded = true;
      if (this.activityLoadTimer) {
        clearTimeout(this.activityLoadTimer);
        this.activityLoadTimer = null;
      }
      this.setActivityOffline(false);
      if (this.dom.activityLoader) this.dom.activityLoader.classList.add("hidden");
    };

    if (this.dom.btnActivityOpenTab) {
      this.dom.btnActivityOpenTab.onclick = () => {
        window.open(this.dom.activityIframe.src, "_blank", "noopener,noreferrer");
      };
    }

    this.dom.activityModal.showModal();
    this.isActivityModalOpen = true;
  }

  /**
   * Fetch one interactive activity in the background so the next open is local.
   *
   * `only_html=true` caches the page and its scripts and rewrites the media
   * references back to the CDN, so the cache stays small and an activity that
   * needs a picture or a clip still plays. The flag is recorded on the activity
   * for this session either way, because the file it was just fetched from is
   * also the one it is running from.
   */
  async jitFetchActivity(guid, act) {
    if (this.features.jit_activities === false) return;
    const known = this.activityJit || (this.activityJit = new Map());
    if (known.get(guid) === "done") {
      if (act) act.interactiveInstalled = true;
      return;
    }
    if (known.get(guid) === "pending") return;
    known.set(guid, "pending");
    try {
      const res = await fetch(`/api/activities/fetch?guid=${encodeURIComponent(guid)}&only_html=true`);
      const data = await res.json();
      known.set(guid, data && data.success ? "done" : "failed");
      if (data && data.success && act) act.interactiveInstalled = true;
      else if (!data || !data.success) this.setActivityOffline(true);
    } catch (err) {
      known.set(guid, "failed");
      this.setActivityOffline(true);
    }
  }

  /** Show or clear the "this activity needs a connection" note. */
  setActivityOffline(offline) {
    if (!this.dom.activityModal) return;
    this.dom.activityModal.classList.toggle("offline", !!offline);
    if (offline && this.dom.activityLoader) {
      // The spinner would sit over the note forever; the frame has given up.
      this.dom.activityLoader.classList.add("hidden");
    }
  }

  closeActivityModal() {
    if (!this.dom.activityModal) return;
    this.dom.activityModal.close();
    if (this.activityLoadTimer) {
      clearTimeout(this.activityLoadTimer);
      this.activityLoadTimer = null;
    }
    this.setActivityOffline(false);
    if (this.dom.activityIframe) {
      this.dom.activityIframe.src = "about:blank";
    }
    this.isActivityModalOpen = false;
  }

  toggleActivityModalFullscreen() {
    if (!this.dom.activityModal) return;
    if (!document.fullscreenElement) {
      this.dom.activityModal.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  /**
   * Detect the activities on a page and paint one hotspot rect per activity.
   * The layer itself is pointer-events:none — clicks are hit-tested against the
   * PDF-space rects instead, so the text layer keeps working for selection/search.
   */
  async buildActivityLayer(pageNum, wrapper, viewport) {
    wrapper._activityViewport = viewport;

    let layer = wrapper.querySelector(".activityLayer");
    const baked = this.activitiesEnabled ? this.bakedPage(pageNum) : null;
    if (!this.activitiesEnabled || (!baked && !(this.activityDetector && this.activityDetector.enabled))) {
      if (layer) layer.remove();
      wrapper._activityData = null;
      return;
    }

    // The publisher's manifest indexes its activities by the number the page
    // prints on itself, not by the sheet's ordinal in the file, and the two
    // differ by however much front matter the book carries.
    const printedPage = this.printedPageFor(pageNum);
    const interactiveOges = this.interactiveOgesFor(pageNum);

    // The server already ran this page through the same detector, anchors and
    // all, so use its answer; only a book with no bake is analysed here.
    let data = baked;
    if (!data) {
      try {
        data = await this.analyzeWithAnchors(pageNum);
      } catch (err) {
        console.warn(`Activity detection failed for page ${pageNum}:`, err);
        return;
      }
      // No bake and no detector: this build has no live fallback, so the page
      // simply carries no hotspots.
      if (!data) return;
    }

    // Page may have been re-rendered (or unmounted) while we were analysing.
    if (wrapper._activityViewport !== viewport || !wrapper.isConnected) return;
    wrapper._activityData = data;

    if (!layer) {
      layer = document.createElement("div");
      layer.className = "activityLayer";
      wrapper.appendChild(layer);
    }
    layer.innerHTML = "";
    layer.style.width = `${Math.floor(viewport.width)}px`;
    layer.style.height = `${Math.floor(viewport.height)}px`;
    layer.classList.toggle("debug", this.activityDebug);

    if (!data.activities.length && !interactiveOges.length) return;

    const frag = document.createDocumentFragment();
    const links = linkInteractiveOges(data, interactiveOges);
    // A region grown from an entry's own icon carries that entry's id inside
    // its own, so it is matched by construction rather than by the drift join.
    // Claiming it here is what stops the same activity being drawn twice --
    // once as its region and again as the corner pin below.
    const ogeById = new Map(interactiveOges.map((o) => [String(o.id), o]));
    for (const act of data.activities) {
      const m = /-oge-(.+)$/.exec(act.id);
      const fromAnchor = m ? ogeById.get(m[1]) : null;
      if (fromAnchor) links.set(act.id, fromAnchor);
    }
    const matchedOgeIds = new Set([...links.values()].map((o) => o.id));

    for (const act of data.activities) {
      const matchingOge = links.get(act.id) || null;
      act.interactiveGuid = matchingOge ? matchingOge.data : null;
      act.interactiveInstalled = matchingOge ? matchingOge.is_installed : false;
      act.interactiveOge = matchingOge;

      // An activity that flows into a second column is painted as one rect per
      // piece, and the pieces are never merged: each stays inside its own column
      // instead of a single bounding box covering — and stealing clicks from —
      // its neighbours.
      const rects = act.parts && act.parts.length ? act.parts : [act.rect];
      rects.forEach((rect, i) => {
        const box = this.rectToViewport(rect, viewport);
        const el = document.createElement("div");
        el.className = "activity-hotspot";
        if (act.interactiveGuid) {
          el.classList.add("has-interactive");
        }
        el.dataset.activityId = act.id;
        el.dataset.partIndex = String(i);
        el.style.left = `${box.left}px`;
        el.style.top = `${box.top}px`;
        el.style.width = `${box.width}px`;
        el.style.height = `${box.height}px`;

        // Each piece carries the letter and the count of the questions that
        // live inside it, because a piece is a region in its own right.
        const chip = document.createElement("span");
        chip.className = "activity-chip";
        // A region found from the publisher's icon has no letter to show, so it
        // is named by the headline read off its own first line instead.
        const name = act.label || act.headline || "";
        chip.textContent = (act.interactiveGuid ? `\u26a1 ${name}` : name).trim();
        if (act.interactiveGuid) {
          chip.title = "Click to open interactive activity";
        }
        el.appendChild(chip);

        const partItems = act.items.filter(
          (item) => (item.partIndex ?? 0) === i
        ).length;
        if (partItems) {
          const count = document.createElement("span");
          count.className = "activity-count";
          count.textContent = `${partItems} question${partItems === 1 ? "" : "s"}`;
          el.appendChild(count);
        }

        frag.appendChild(el);
      });
    }

    // An entry that belongs to no lettered region -- a whole-section task such as
    // an in-theme activity, a warm-up or a consolidation -- keeps the pin at the
    // position the publisher placed it at, so it is still reachable.
    for (const oge of interactiveOges) {
      if (matchedOgeIds.has(oge.id)) continue;
      if (oge.posx == null || oge.posy == null) continue;
      const btn = document.createElement("div");
      btn.className = "standalone-hotspot";
      btn.style.left = `${oge.posx}%`;
      btn.style.top = `${oge.posy}%`;
      btn.textContent = "\u26a1";
      const name = oge.baslik || `Page ${printedPage} activity`;
      btn.title = `Interactive Activity: ${name}`;
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.openActivityModal({
          interactiveGuid: oge.data,
          interactiveInstalled: oge.is_installed,
          headline: name,
          pageNum,
          printedPage,
        });
      });
      frag.appendChild(btn);
    }

    layer.appendChild(frag);
  }

  rectToViewport(rect, viewport) {
    const [ax, ay, bx, by] = viewport.convertToViewportRectangle([rect.x0, rect.y0, rect.x1, rect.y1]);
    const left = Math.min(ax, bx);
    const top = Math.min(ay, by);
    return {
      left,
      top,
      width: Math.abs(bx - ax),
      height: Math.abs(by - ay),
    };
  }

  /** Hit test a pointer event against the activities of the page under the cursor. */
  activityAtPointer(e) {
    const wrapper = e.target.closest ? e.target.closest(".pdf-page-wrapper") : null;
    if (!wrapper || !wrapper._activityData || !wrapper._activityViewport) return null;
    const canvas = wrapper.querySelector("canvas");
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;
    const [px, py] = wrapper._activityViewport.convertToPdfPoint(x, y);
    // Straight off the page data: a baked book hit-tests without the detector
    // module being present at all.
    const hit = hitTestRegion(wrapper._activityData, px, py);
    return hit ? { activity: hit.activity, partIndex: hit.partIndex, wrapper } : null;
  }

  handleActivityHover(e) {
    if (!this.activitiesEnabled || this.focusedActivity) return;
    const hit = this.activityAtPointer(e);
    const layerHost = e.target.closest ? e.target.closest(".pdf-page-wrapper") : null;

    const hitSelector = hit
      ? `[data-activity-id="${hit.activity.id}"][data-part-index="${hit.partIndex}"]`
      : null;
    document.querySelectorAll(".activity-hotspot.hover").forEach((el) => {
      if (!hitSelector || !el.matches(hitSelector)) el.classList.remove("hover");
    });

    if (layerHost) {
      if (hit && hit.activity.interactiveGuid) {
        layerHost.classList.add("activity-interactive-cursor");
        layerHost.classList.remove("activity-cursor");
      } else {
        layerHost.classList.remove("activity-interactive-cursor");
        layerHost.classList.toggle("activity-cursor", !!hit);
      }
    }
    if (!hit) return;

    // Pieces are independent regions, so only the one under the pointer lights up.
    hit.wrapper.querySelectorAll(`.activity-hotspot${hitSelector}`).forEach((el) => {
      el.classList.add("hover");
      const prefix = hit.activity.interactiveGuid ? "⚡ Open Interactive: " : "";
      el.title = prefix + (hit.activity.headline || `Activity ${hit.activity.label}`);
    });
  }

  handleActivityClick(e) {
    if (!this.activitiesEnabled || this.focusedActivity) return;
    // A click that ends a text selection is a selection, not a zoom request.
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const hit = this.activityAtPointer(e);
    if (!hit) return;
    e.preventDefault();

    if (hit.activity.interactiveGuid) {
      this.openActivityModal(hit.activity);
    } else {
      this.focusActivity(hit.activity, hit.wrapper, -1, hit.partIndex);
    }
  }

  /* ---------------------------- Focus mode ---------------------------- */

  async focusActivity(activity, sourceWrapper = null, subIndex = -1, partIndex = 0) {
    if (!activity) return;
    if (!this.focusedActivity) {
      this.preFocusState = {
        viewMode: this.viewMode,
        currentPage: this.currentPage,
        zoomMode: this.zoomMode,
        customZoom: this.customZoom,
        scrollTop: this.dom.viewerContainer.scrollTop,
        scrollLeft: this.dom.viewerContainer.scrollLeft,
      };
    }
    this.focusedActivity = { activity, subIndex, partIndex };
    this.focusZoomBias = 1;

    this.dom.focusOverlay.classList.remove("hidden");
    document.body.classList.add("focus-active");
    this.dom.statusModeLabel.textContent =
      `Focus: Page ${activity.pageNum} · ${this.activityName(activity)}`;

    // FLIP: start from the hotspot's on-screen box so the zoom reads as motion.
    if (sourceWrapper) {
      const el = sourceWrapper.querySelector(
        `.activity-hotspot[data-activity-id="${activity.id}"][data-part-index="${partIndex}"]`
      );
      if (el) {
        const from = el.getBoundingClientRect();
        this.dom.focusStage.style.setProperty("--flip-x", `${from.left + from.width / 2}px`);
        this.dom.focusStage.style.setProperty("--flip-y", `${from.top + from.height / 2}px`);
        this.dom.focusStage.classList.add("flip-in");
        setTimeout(() => this.dom.focusStage.classList.remove("flip-in"), 220);
      }
    }

    this.updateFocusChrome();
    await this.renderFocus();
    this.updateFocusChrome();
  }

  async renderFocus() {
    if (!this.focusedActivity) return;
    const { activity, subIndex, partIndex = 0 } = this.focusedActivity;
    // Parts are separate regions: zoom to the piece that was clicked rather than
    // a bounding box grouping every piece of this letter.
    const parts = activity.parts && activity.parts.length ? activity.parts : [activity.rect];
    const region = subIndex >= 0 && activity.items[subIndex]
      ? activity.items[subIndex].rect
      : parts[Math.min(partIndex, parts.length - 1)] || activity.rect;

    if (this.focusRenderTask) {
      try { this.focusRenderTask.cancel(); } catch (_) {}
      this.focusRenderTask = null;
    }

    const page = await this.pdfDoc.getPage(activity.pageNum);
    const vpUnit = page.getViewport({ scale: 1, rotation: this.rotation });
    const unit = this.rectToViewport(region, vpUnit);

    const stageRect = this.dom.focusStage.getBoundingClientRect();
    const availW = Math.max(240, stageRect.width - 56);
    const availH = Math.max(240, stageRect.height - 56);

    let scale = Math.min(availW / unit.width, availH / unit.height) * this.focusZoomBias;
    scale = Math.max(0.4, Math.min(6, scale));
    this.focusScale = scale;

    const vpScaled = page.getViewport({ scale, rotation: this.rotation });
    const box = this.rectToViewport(region, vpScaled);

    // Crop viewport: the canvas covers only the region, so canvas memory stays
    // bounded by the viewport size no matter how deep the zoom goes.
    const viewport = page.getViewport({
      scale,
      rotation: this.rotation,
      offsetX: -box.left,
      offsetY: -box.top,
    });

    const cssW = Math.max(1, Math.floor(box.width));
    const cssH = Math.max(1, Math.floor(box.height));

    let dpr = window.devicePixelRatio || 1;
    const MAX_PIXELS = 8e6;
    while (cssW * cssH * dpr * dpr > MAX_PIXELS && dpr > 1) dpr -= 0.25;

    const host = this.dom.focusCanvasHost;
    host.style.width = `${cssW}px`;
    host.style.height = `${cssH}px`;

    let canvas = host.querySelector("canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      host.appendChild(canvas);
    }
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;

    const ctx = canvas.getContext("2d", { alpha: false });
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const task = page.render({
      canvasContext: ctx,
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    });
    this.focusRenderTask = task;

    try {
      await task.promise;
    } catch (err) {
      if (err.name !== "RenderingCancelledException") console.error("Focus render error:", err);
      return;
    }
    if (this.focusRenderTask === task) this.focusRenderTask = null;

    // Text layer so selection and Ctrl+F highlighting keep working while zoomed.
    let textLayerDiv = host.querySelector(".textLayer");
    if (!textLayerDiv) {
      textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      host.appendChild(textLayerDiv);
    }
    textLayerDiv.innerHTML = "";
    textLayerDiv.style.width = `${cssW}px`;
    textLayerDiv.style.height = `${cssH}px`;
    textLayerDiv.style.setProperty("--scale-factor", scale);
    host.style.setProperty("--scale-factor", scale);

    try {
      const textContent = await page.getTextContent();
      const textLayer = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textLayerDiv,
        viewport,
      });
      await textLayer.render();
      if (this.searchQuery) this.highlightMatchesInTextLayer(textLayerDiv, this.searchQuery);
    } catch (_) {
      // Selection is a nicety here; a failure must not break focus mode.
    }
  }

  updateFocusChrome() {
    if (!this.focusedActivity) return;
    const { activity, subIndex } = this.focusedActivity;
    this.dom.focusLabel.textContent =
      `Page ${activity.pageNum} · ${this.activityName(activity)}`;
    this.dom.focusHeadline.textContent = activity.headline || "";

    const hasItems = activity.items.length > 0;
    this.dom.focusSubPrev.disabled = !hasItems || subIndex <= -1;
    this.dom.focusSubNext.disabled = !hasItems || subIndex >= activity.items.length - 1;
    this.dom.focusSubLabel.textContent = !hasItems
      ? "No numbered questions"
      : subIndex >= 0
        ? `Question ${activity.items[subIndex].label} of ${activity.items.length}`
        : `${activity.items.length} questions`;
    this.dom.focusCounter.textContent = `${Math.round(this.focusScale * 100)}%`;
  }

  async stepActivity(dir) {
    if (!this.focusedActivity) return;
    const next = await this.stepActivityFrom(this.focusedActivity.activity, dir);
    if (!next) return;
    this.focusedActivity = { activity: next, subIndex: -1, partIndex: 0 };
    this.focusZoomBias = 1;
    if (next.pageNum !== this.currentPage) this.goToPage(next.pageNum);
    this.updateFocusChrome();
    await this.renderFocus();
    this.updateFocusChrome();
  }

  /**
   * Next / previous activity across page boundaries, in reading order. dir is
   * +1 or -1.
   *
   * Asked of `activityDataFor`, not of the detector, so stepping works on a
   * baked book too -- and steps through exactly the regions that are drawn,
   * anchors included, rather than through a second opinion of them.
   */
  async stepActivityFrom(activity, dir) {
    const pageData = await this.activityDataFor(activity.pageNum);
    const list = (pageData && pageData.activities) || [];
    const idx = list.findIndex((a) => a.id === activity.id);
    const target = idx + dir;
    if (idx !== -1 && target >= 0 && target < list.length) return list[target];

    for (let p = activity.pageNum + dir; p >= 1 && p <= this.totalPages; p += dir) {
      const data = await this.activityDataFor(p);
      const acts = (data && data.activities) || [];
      if (acts.length) return dir > 0 ? acts[0] : acts[acts.length - 1];
    }
    return null;
  }

  async stepSubItem(dir) {
    if (!this.focusedActivity) return;
    const { activity, subIndex, partIndex = 0 } = this.focusedActivity;
    if (!activity.items.length) return;
    const next = Math.max(-1, Math.min(activity.items.length - 1, subIndex + dir));
    if (next === subIndex) return;
    // Stepping into a question that lives in another piece moves the focus there.
    const nextPart = next >= 0 && activity.items[next]
      ? activity.items[next].partIndex ?? partIndex
      : partIndex;
    this.focusedActivity = { activity, subIndex: next, partIndex: nextPart };
    this.focusZoomBias = 1;
    this.updateFocusChrome();
    await this.renderFocus();
    this.updateFocusChrome();
  }

  async nudgeFocusZoom(delta) {
    if (!this.focusedActivity) return;
    this.focusZoomBias = Math.max(0.5, Math.min(3, this.focusZoomBias + delta));
    await this.renderFocus();
    this.updateFocusChrome();
  }

  exitFocus(silent = false) {
    if (this.focusRenderTask) {
      try { this.focusRenderTask.cancel(); } catch (_) {}
      this.focusRenderTask = null;
    }
    if (!this.focusedActivity) {
      if (this.dom.focusOverlay) this.dom.focusOverlay.classList.add("hidden");
      document.body.classList.remove("focus-active");
      return;
    }
    this.focusedActivity = null;
    if (this.dom.focusCanvasHost) this.dom.focusCanvasHost.innerHTML = "";
    this.dom.focusOverlay.classList.add("hidden");
    document.body.classList.remove("focus-active");
    this.updateViewModeUI();

    const prev = this.preFocusState;
    this.preFocusState = null;
    if (silent || !prev) return;

    const needsRerender = prev.viewMode !== this.viewMode || prev.currentPage !== this.currentPage;
    if (needsRerender) {
      this.viewMode = prev.viewMode;
      this.currentPage = prev.currentPage;
      this.dom.pageNumInput.value = prev.currentPage;
      this.updateViewModeUI();
      this.renderCurrentView().then(() => {
        this.dom.viewerContainer.scrollTop = prev.scrollTop;
        this.dom.viewerContainer.scrollLeft = prev.scrollLeft;
      });
    } else {
      this.dom.viewerContainer.scrollTop = prev.scrollTop;
      this.dom.viewerContainer.scrollLeft = prev.scrollLeft;
    }
  }

  /* ------------------------ Sidebar activity list ---------------------- */

  async buildActivityList() {
    if (this.activityListBuilt || this.activityListBuilding) return;
    this.activityListBuilding = true;
    try {
      if (!this.bakedEnabled) {
        // Live detection: the list can only be built once the document has been
        // calibrated, which is also what decides whether it has activities at
        // all. A packaged book skips straight past this -- its bake already
        // says so.
        const detector = await this.ensureActivityDetector();
        if (!detector) return;
        await detector.calibrate();
        this.updateActivityAvailability();

        if (!detector.enabled) {
          this.dom.activitiesList.innerHTML =
            '<div class="outline-empty">No lettered activities detected in this document.</div>';
          this.activityListBuilt = true;
          return;
        }
      } else {
        this.updateActivityAvailability();
      }
    } finally {
      this.activityListBuilding = false;
    }

    this.activityListBuilt = true;
    this.dom.activitiesList.innerHTML = '<div class="outline-empty" id="activity-scan-status">Scanning pages…</div>';
    const status = document.getElementById("activity-scan-status");
    const list = document.createElement("div");
    list.className = "activity-index";
    this.dom.activitiesList.appendChild(list);

    let found = 0;
    // A timeout keeps the scan progressing even when the main thread never goes
    // properly idle (it competes with page rendering).
    const idle = window.requestIdleCallback
      ? (fn) => window.requestIdleCallback(fn, { timeout: 400 })
      : (fn) => setTimeout(() => fn({ timeRemaining: () => 8 }), 16);

    const scanChunk = async (startPage) => {
      const chunkEnd = Math.min(this.totalPages, startPage + 7);
      for (let p = startPage; p <= chunkEnd; p++) {
        let data;
        try {
          data = await this.activityDataFor(p);
        } catch (_) {
          continue;
        }
        if (!data || !data.activities.length) continue;
        const group = document.createElement("div");
        group.className = "activity-group";
        const head = document.createElement("div");
        head.className = "activity-group-head";
        head.textContent = `Page ${p}`;
        group.appendChild(head);

        for (const act of data.activities) {
          found++;
          const row = document.createElement("button");
          row.className = "activity-row";
          row.innerHTML = `<span class="activity-row-label">${act.label || "\u26a1"}</span>` +
            `<span class="activity-row-text"></span>`;
          row.querySelector(".activity-row-text").textContent =
            act.headline || this.activityName(act);
          row.addEventListener("click", () => {
            if (this.currentPage !== act.pageNum) this.goToPage(act.pageNum);
            this.focusActivity(act);
          });
          group.appendChild(row);
        }
        list.appendChild(group);
      }

      if (status) status.textContent = `Scanned ${chunkEnd} / ${this.totalPages} pages · ${found} activities`;

      if (chunkEnd < this.totalPages) {
        idle(() => scanChunk(chunkEnd + 1));
      } else if (status) {
        if (found === 0) {
          status.textContent = "No activities detected in this document.";
        } else {
          status.textContent = `${found} activities across ${this.totalPages} pages`;
          status.classList.add("activity-scan-done");
        }
      }
    };

    idle(() => scanChunk(1));
  }

  /** Debug helper: window.pdfApp.dumpActivities() prints per-page detection. */
  async dumpActivities(from = 1, to = null) {
    const detector = await this.ensureActivityDetector();
    if (detector) await detector.calibrate();
    const end = to || this.totalPages;
    const rows = [];
    for (let p = from; p <= end; p++) {
      const data = await this.activityDataFor(p);
      if (!data || !data.activities.length) continue;
      rows.push({
        page: p,
        labels: data.activities.map((a) => a.label).join(""),
        questions: data.activities.map((a) => a.items.length).join(","),
      });
    }
    console.table(rows);
    return rows;
  }

  /* --------------------------------------------------------------------------
     Sidebar: Thumbnails & Outline
     -------------------------------------------------------------------------- */
  async getThumbnailCanvas(pageNum) {
    if (this.thumbCache && this.thumbCache.has(pageNum)) {
      return this.thumbCache.get(pageNum);
    }
    if (this.thumbPromiseCache && this.thumbPromiseCache.has(pageNum)) {
      return this.thumbPromiseCache.get(pageNum);
    }
    if (!this.thumbPromiseCache) this.thumbPromiseCache = new Map();

    const promise = (async () => {
      const page = await this.pdfDoc.getPage(pageNum);
      const unscaledVp = page.getViewport({ scale: 1.0, rotation: this.rotation });
      const thumbWidth = 160;
      const scale = thumbWidth / unscaledVp.width;
      const vp = page.getViewport({ scale, rotation: this.rotation });

      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);

      const ctx = canvas.getContext("2d", { alpha: false });
      await page.render({ canvasContext: ctx, viewport: vp }).promise;

      if (!this.thumbCache) this.thumbCache = new Map();
      this.thumbCache.set(pageNum, canvas);
      this.thumbPromiseCache.delete(pageNum);
      return canvas;
    })();

    this.thumbPromiseCache.set(pageNum, promise);
    return promise;
  }

  async renderThumbnailIntoWrapper(pageNum, item) {
    try {
      const wrapper = item.querySelector(`.thumb-page-wrapper[data-page-num="${pageNum}"]`);
      if (!wrapper) return;
      if (wrapper.dataset.rendered === "true") return;

      const t_th_0 = performance.now();
      const cachedCanvas = await this.getThumbnailCanvas(pageNum);
      const thRenderMs = performance.now() - t_th_0;
      if (this.currentPerf && pageNum === 1 && !this.currentPerf.durations.page1_thumbnail_render) {
        this.currentPerf.durations.page1_thumbnail_render = thRenderMs;
      }
      if (!item.isConnected) return;
      if (wrapper.dataset.rendered === "true") return;

      const displayCanvas = document.createElement("canvas");
      displayCanvas.className = "thumb-canvas";
      displayCanvas.width = cachedCanvas.width;
      displayCanvas.height = cachedCanvas.height;

      const ctx = displayCanvas.getContext("2d", { alpha: false });
      ctx.drawImage(cachedCanvas, 0, 0);

      wrapper.innerHTML = "";
      wrapper.appendChild(displayCanvas);
      wrapper.dataset.rendered = "true";
    } catch (err) {
      console.warn(`Thumbnail render page ${pageNum} error:`, err);
    }
  }

  initThumbnails() {
    if (!this.pdfDoc || this.totalPages <= 0) return;
    this.dom.thumbnailsList.innerHTML = "";
    if (this.thumbObserver) {
      this.thumbObserver.disconnect();
    }

    this.thumbObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const item = entry.target;
          if (!item.dataset.thumbRendered) {
            item.dataset.thumbRendered = "true";
            const pages = (item.dataset.pages || "")
              .split(",")
              .map((s) => parseInt(s.trim(), 10))
              .filter((n) => !isNaN(n));
            pages.forEach((p) => this.renderThumbnailIntoWrapper(p, item));
          }
        }
      }
    }, { root: this.dom.thumbnailsPanel, rootMargin: "350px" });

    if (this.viewMode === "book") {
      this.initBookThumbnails();
    } else {
      this.initSingleThumbnails();
    }

    this.updateThumbnailsActiveState();
  }

  initBookThumbnails() {
    // Spreads: [1] (Cover), [2, 3], [4, 5], ...
    const spreads = [];
    spreads.push([1]);
    for (let p = 2; p <= this.totalPages; p += 2) {
      if (p + 1 <= this.totalPages) {
        spreads.push([p, p + 1]);
      } else {
        spreads.push([p]);
      }
    }

    const currentPages = this.currentSpreadPages && this.currentSpreadPages.length
      ? this.currentSpreadPages
      : [this.currentPage];

    for (const pages of spreads) {
      const isSingle = pages.length === 1;
      const p1 = pages[0];
      const p2 = pages[1] || null;

      const item = document.createElement("div");
      item.id = isSingle ? `thumb-spread-${p1}` : `thumb-spread-${p1}-${p2}`;
      item.className = "thumbnail-item thumbnail-spread-item";
      if (isSingle) item.classList.add("thumb-is-cover");
      item.dataset.pages = pages.join(",");
      item.dataset.startPage = p1;

      const isActive = currentPages.some((p) => pages.includes(p));
      if (isActive) item.classList.add("active");

      const container = document.createElement("div");
      container.className = "thumb-canvas-container thumb-spread-container";
      if (isSingle) container.classList.add("thumb-single-container");

      if (isSingle) {
        const pageWrapper = document.createElement("div");
        pageWrapper.className = "thumb-page-wrapper thumb-page-cover";
        pageWrapper.dataset.pageNum = p1;
        container.appendChild(pageWrapper);
      } else {
        const leftWrapper = document.createElement("div");
        leftWrapper.className = "thumb-page-wrapper thumb-page-left";
        leftWrapper.dataset.pageNum = p1;

        const rightWrapper = document.createElement("div");
        rightWrapper.className = "thumb-page-wrapper thumb-page-right";
        rightWrapper.dataset.pageNum = p2;

        container.appendChild(leftWrapper);
        container.appendChild(rightWrapper);
      }

      const numLabel = document.createElement("span");
      numLabel.className = "thumb-number";
      numLabel.textContent = isSingle ? (p1 === 1 ? "Page 1" : `Page ${p1}`) : `Pages ${p1}–${p2}`;

      item.appendChild(container);
      item.appendChild(numLabel);

      item.addEventListener("click", () => {
        this.goToPage(p1);
      });

      this.dom.thumbnailsList.appendChild(item);
      this.thumbObserver.observe(item);
    }
  }

  initSingleThumbnails() {
    for (let p = 1; p <= this.totalPages; p++) {
      const item = document.createElement("div");
      item.id = `thumb-item-${p}`;
      item.dataset.pageNum = p;
      item.dataset.pages = `${p}`;
      item.dataset.startPage = p;
      item.className = "thumbnail-item thumbnail-single-item";
      if (p === this.currentPage) item.classList.add("active");

      const container = document.createElement("div");
      container.className = "thumb-canvas-container thumb-single-container";

      const pageWrapper = document.createElement("div");
      pageWrapper.className = "thumb-page-wrapper thumb-page-single";
      pageWrapper.dataset.pageNum = p;
      container.appendChild(pageWrapper);

      const numLabel = document.createElement("span");
      numLabel.className = "thumb-number";
      numLabel.textContent = `Page ${p}`;

      item.appendChild(container);
      item.appendChild(numLabel);

      item.addEventListener("click", () => {
        this.goToPage(p);
      });

      this.dom.thumbnailsList.appendChild(item);
      this.thumbObserver.observe(item);
    }
  }

  updateThumbnailsActiveState() {
    const prevActive = this.dom.thumbnailsList.querySelectorAll(".thumbnail-item.active");
    prevActive.forEach((el) => el.classList.remove("active"));

    let activeThumb = null;
    if (this.viewMode === "book") {
      const pagesToMatch = this.currentSpreadPages && this.currentSpreadPages.length
        ? this.currentSpreadPages
        : [this.currentPage];

      for (const item of this.dom.thumbnailsList.children) {
        if (!item.dataset.pages) continue;
        const itemPages = item.dataset.pages
          .split(",")
          .map((s) => parseInt(s.trim(), 10))
          .filter((n) => !isNaN(n));
        if (pagesToMatch.some((p) => itemPages.includes(p))) {
          activeThumb = item;
          break;
        }
      }
    } else {
      activeThumb = document.getElementById(`thumb-item-${this.currentPage}`);
    }

    if (activeThumb) {
      activeThumb.classList.add("active");
      activeThumb.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  async loadOutline() {
    try {
      const outline = await this.pdfDoc.getOutline();
      this.dom.outlineList.innerHTML = "";
      if (!outline || outline.length === 0) {
        this.dom.outlineList.innerHTML = '<div class="outline-empty">No bookmarks found in this PDF.</div>';
        return;
      }
      // Populate nested outline list
      const ul = document.createElement("ul");
      ul.className = "outline-tree";
      this.renderOutlineItems(outline, ul);
      this.dom.outlineList.appendChild(ul);
    } catch (err) {
      this.dom.outlineList.innerHTML = '<div class="outline-empty">No bookmarks found in this PDF.</div>';
    }
  }

  renderOutlineItems(items, parentEl) {
    items.forEach((item) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.textContent = item.title;
      a.href = "#";
      a.addEventListener("click", async (e) => {
        e.preventDefault();
        if (item.dest) {
          const dest = typeof item.dest === "string" ? await this.pdfDoc.getDestination(item.dest) : item.dest;
          if (dest) {
            const pageIndex = await this.pdfDoc.getPageIndex(dest[0]);
            this.goToPage(pageIndex + 1);
          }
        }
      });
      li.appendChild(a);
      if (item.items && item.items.length > 0) {
        const subUl = document.createElement("ul");
        this.renderOutlineItems(item.items, subUl);
        li.appendChild(subUl);
      }
      parentEl.appendChild(li);
    });
  }

  /* --------------------------------------------------------------------------
     Search Feature
     -------------------------------------------------------------------------- */
  toggleSearch() {
    const isHidden = this.dom.searchBar.classList.contains("hidden");
    if (isHidden) {
      this.dom.searchBar.classList.remove("hidden");
      this.dom.searchInput.focus();
      this.dom.searchInput.select();
    } else {
      this.closeSearch();
    }
  }

  closeSearch() {
    this.dom.searchBar.classList.add("hidden");
    this.clearSearchHighlights();
    this.searchQuery = "";
    this.searchResults = [];
    this.currentSearchMatchIndex = -1;
  }

  async executeSearch(query) {
    this.searchQuery = query.trim().toLowerCase();
    this.clearSearchHighlights();
    this.searchResults = [];
    this.currentSearchMatchIndex = -1;

    if (!this.searchQuery || !this.pdfDoc) {
      this.dom.searchMatchCount.textContent = "0 of 0";
      return;
    }

    this.dom.searchMatchCount.textContent = "Searching...";

    for (let p = 1; p <= this.totalPages; p++) {
      let pageText = this.pageTextCache.get(p);
      if (!pageText) {
        const page = await this.pdfDoc.getPage(p);
        const textContent = await page.getTextContent();
        pageText = textContent.items.map((it) => it.str).join(" ").toLowerCase();
        this.pageTextCache.set(p, pageText);
      }

      let pos = 0;
      while ((pos = pageText.indexOf(this.searchQuery, pos)) !== -1) {
        this.searchResults.push({ pageNum: p, index: pos });
        pos += this.searchQuery.length;
      }
    }

    const count = this.searchResults.length;
    if (count > 0) {
      this.currentSearchMatchIndex = 0;
      this.dom.searchMatchCount.textContent = `1 of ${count}`;
      this.jumpToSearchMatch(0);
    } else {
      this.dom.searchMatchCount.textContent = "0 matches";
    }
  }

  jumpToSearchMatch(matchIndex) {
    if (matchIndex < 0 || matchIndex >= this.searchResults.length) return;
    this.currentSearchMatchIndex = matchIndex;
    const match = this.searchResults[matchIndex];
    this.dom.searchMatchCount.textContent = `${matchIndex + 1} of ${this.searchResults.length}`;

    const isVisible = this.viewMode === "book"
      ? this.currentSpreadPages.includes(match.pageNum)
      : match.pageNum === this.currentPage;

    if (!isVisible) {
      this.goToPage(match.pageNum);
    } else {
      this.highlightActiveMatch();
    }
  }

  nextSearchMatch() {
    if (this.searchResults.length === 0) return;
    const nextIdx = (this.currentSearchMatchIndex + 1) % this.searchResults.length;
    this.jumpToSearchMatch(nextIdx);
  }

  prevSearchMatch() {
    if (this.searchResults.length === 0) return;
    const prevIdx = (this.currentSearchMatchIndex - 1 + this.searchResults.length) % this.searchResults.length;
    this.jumpToSearchMatch(prevIdx);
  }

  highlightMatchesInTextLayer(textLayerDiv, query) {
    const spans = textLayerDiv.querySelectorAll("span");
    spans.forEach((span) => {
      const text = span.textContent;
      const lower = text.toLowerCase();
      if (lower.includes(query)) {
        const regex = new RegExp(`(${query.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")})`, "gi");
        span.innerHTML = text.replace(regex, '<mark class="highlight">$1</mark>');
      }
    });
  }

  clearSearchHighlights() {
    document.querySelectorAll(".textLayer mark.highlight").forEach((el) => {
      const parent = el.parentNode;
      parent.textContent = parent.textContent;
    });
  }

  highlightActiveMatch() {
    const marks = document.querySelectorAll(".textLayer mark.highlight");
    marks.forEach((m) => m.classList.remove("selected"));
    if (marks.length > 0) {
      marks[0].classList.add("selected");
      marks[0].scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  /**
   * Turn a button into one that repeats while it is held.
   *
   * On a smartboard a single tap per page is the wrong gesture for turning
   * twenty pages: the pointer lands, and holding it turns them. The first step
   * happens immediately on press (a tap is still one page), then repeats after
   * a short pause so that a long press does not overshoot before the teacher
   * has decided how far to go.
   *
   * The trailing `click` that follows a real press-hold is swallowed, so a hold
   * never turns one page more than the repeats asked for. Keyboard activation
   * is untouched: no pointer events fire for Enter or Space, so the click goes
   * straight through.
   *
   * @param {HTMLElement} el
   * @param {Function} fn  action for one step
   */
  bindHoldRepeat(el, fn) {
    if (!el) return;
    const REPEAT_DELAY = 350;   // ms before holding starts to repeat
    const REPEAT_INTERVAL = 110; // ms between repeats

    let delayTimer = null;
    let repeatTimer = null;
    let handledByHold = false;

    const stop = () => {
      if (delayTimer) clearTimeout(delayTimer);
      if (repeatTimer) clearInterval(repeatTimer);
      delayTimer = null;
      repeatTimer = null;
    };

    el.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      handledByHold = false;
      stop();
      fn();
      delayTimer = setTimeout(() => {
        handledByHold = true;
        repeatTimer = setInterval(fn, REPEAT_INTERVAL);
      }, REPEAT_DELAY);
    });

    ["pointerup", "pointercancel", "pointerleave", "lostpointercapture"].forEach((type) => {
      el.addEventListener(type, stop);
    });

    // A long press on a touch screen would otherwise raise the context menu and
    // cancel the pointer stream mid-turn.
    el.addEventListener("contextmenu", (e) => e.preventDefault());

    el.addEventListener("click", () => {
      if (handledByHold) {
        handledByHold = false;
        return;
      }
      fn();
    });
  }

  /* --------------------------------------------------------------------------
     Event Listeners & Keybindings
     -------------------------------------------------------------------------- */
  bindEvents() {
    // Back to Dashboard / Library
    if (this.dom.btnOpenDashboard) {
      this.dom.btnOpenDashboard.addEventListener("click", () => this.showDashboard());
    }

    // Page Navigation Buttons: press for one page, hold to keep turning.
    this.bindHoldRepeat(this.dom.btnPrevPage, () => this.prevPage());
    this.bindHoldRepeat(this.dom.btnNextPage, () => this.nextPage());

    // Page Number Input Jump
    this.dom.pageNumInput.addEventListener("change", (e) => {
      this.goToPage(parseInt(e.target.value, 10) || 1);
    });
    this.dom.pageNumInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        this.goToPage(parseInt(e.target.value, 10) || 1);
        this.dom.pageNumInput.blur();
      }
    });

    // View Mode Switcher
    this.dom.modeBook.addEventListener("click", () => this.setViewMode("book"));
    this.dom.modeSingle.addEventListener("click", () => this.setViewMode("single"));
    this.dom.modeScroll.addEventListener("click", () => this.setViewMode("scroll"));

    // Zoom Controls
    this.dom.btnZoomOut.addEventListener("click", () => {
      const current = this.zoomMode === "custom" ? this.customZoom : 1.0;
      this.setZoom(Math.max(0.3, current - 0.2));
    });
    this.dom.btnZoomIn.addEventListener("click", () => {
      const current = this.zoomMode === "custom" ? this.customZoom : 1.0;
      this.setZoom(Math.min(3.0, current + 0.2));
    });
    this.dom.zoomSelect.addEventListener("change", (e) => {
      const val = e.target.value;
      if (val === "fit-width" || val === "fit-page") {
        this.setZoom(val);
      } else {
        this.setZoom(parseFloat(val));
      }
    });

    // Rotate
    this.dom.btnRotate.addEventListener("click", () => {
      this.rotation = (this.rotation + 90) % 360;
      if (this.pageViewportCache) this.pageViewportCache.clear();
      if (this.thumbCache) this.thumbCache.clear();
      if (this.thumbPromiseCache) this.thumbPromiseCache.clear();
      this.initThumbnails();
      this.renderCurrentView();
    });

    // Theme Toggle
    this.dom.btnThemeToggle.addEventListener("click", () => this.cycleTheme());

    // Fullscreen
    this.dom.btnFullscreen.addEventListener("click", () => {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    });

    // Open Local File
    this.dom.fileInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file && file.type === "application/pdf") {
        const fileReader = new FileReader();
        fileReader.onload = () => {
          const typedArray = new Uint8Array(fileReader.result);
          this.openDocument(typedArray, file.name);
        };
        fileReader.readAsArrayBuffer(file);
      }
    });

    // Sidebar Toggle
    this.dom.btnToggleSidebar.addEventListener("click", () => {
      this.dom.sidebar.classList.toggle("collapsed");
    });
    this.dom.btnCloseSidebar.addEventListener("click", () => {
      this.dom.sidebar.classList.add("collapsed");
    });

    // Sidebar Tabs
    const tabs = [
      { btn: this.dom.tabThumbnails, panel: this.dom.thumbnailsPanel },
      { btn: this.dom.tabOutline, panel: this.dom.outlinePanel },
      { btn: this.dom.tabActivities, panel: this.dom.activitiesPanel },
    ].filter((t) => t.btn && t.panel);

    const activateTab = (target) => {
      tabs.forEach(({ btn, panel }) => {
        btn.classList.toggle("active", btn === target.btn);
        panel.classList.toggle("active", panel === target.panel);
      });
      if (target.btn === this.dom.tabActivities) this.buildActivityList();
      if (target.btn === this.dom.tabThumbnails) this.updateThumbnailsActiveState();
    };
    tabs.forEach((t) => t.btn.addEventListener("click", () => activateTab(t)));

    // Activity hotspots: toggle, hover feedback and click-to-zoom.
    if (this.dom.btnActivityToggle) {
      this.dom.btnActivityToggle.addEventListener("click", () => this.toggleActivities());
    }
    this.dom.viewerPages.addEventListener("mousemove", (e) => this.handleActivityHover(e));
    this.dom.viewerPages.addEventListener("mouseleave", () => {
      document.querySelectorAll(".activity-hotspot.hover").forEach((el) => el.classList.remove("hover"));
      document.querySelectorAll(".pdf-page-wrapper.activity-cursor").forEach((el) => el.classList.remove("activity-cursor"));
    });
    this.dom.viewerPages.addEventListener("click", (e) => this.handleActivityClick(e));

    // Focus mode chrome
    this.dom.focusClose.addEventListener("click", () => this.exitFocus());
    this.dom.focusNext.addEventListener("click", () => this.stepActivity(1));
    this.dom.focusPrev.addEventListener("click", () => this.stepActivity(-1));
    this.dom.focusSubNext.addEventListener("click", () => this.stepSubItem(1));
    this.dom.focusSubPrev.addEventListener("click", () => this.stepSubItem(-1));
    this.dom.focusOverlay.addEventListener("click", (e) => {
      if (e.target === this.dom.focusOverlay || e.target === this.dom.focusStage) this.exitFocus();
    });

    // Search Bar
    this.dom.btnSearchToggle.addEventListener("click", () => this.toggleSearch());
    this.dom.searchClose.addEventListener("click", () => this.closeSearch());
    this.dom.searchNext.addEventListener("click", () => this.nextSearchMatch());
    this.dom.searchPrev.addEventListener("click", () => this.prevSearchMatch());
    this.dom.searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        if (e.shiftKey) {
          this.prevSearchMatch();
        } else if (this.searchQuery === this.dom.searchInput.value.trim().toLowerCase()) {
          this.nextSearchMatch();
        } else {
          this.executeSearch(this.dom.searchInput.value);
        }
      } else if (e.key === "Escape") {
        this.closeSearch();
      }
    });

    // Help Modal
    this.dom.btnHelp.addEventListener("click", () => this.dom.helpModal.showModal());
    this.dom.btnCloseModal.addEventListener("click", () => this.dom.helpModal.close());
    this.dom.helpModal.addEventListener("click", (e) => {
      if (e.target === this.dom.helpModal) this.dom.helpModal.close();
    });

    // Activity Modal
    if (this.dom.btnCloseActivityModal) {
      this.dom.btnCloseActivityModal.addEventListener("click", () => this.closeActivityModal());
    }
    if (this.dom.btnActivityFullscreen) {
      this.dom.btnActivityFullscreen.addEventListener("click", () => this.toggleActivityModalFullscreen());
    }
    if (this.dom.activityModal) {
      this.dom.activityModal.addEventListener("click", (e) => {
        if (e.target === this.dom.activityModal) this.closeActivityModal();
      });
    }

    // Window Resize debounce
    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (this.focusedActivity) {
          this.renderFocus().then(() => this.updateFocusChrome());
          return;
        }
        if (this.zoomMode === "fit-page" || this.zoomMode === "fit-width") {
          this.renderCurrentView();
        }
      }, 200);
    });

    // Keyboard Shortcuts
    window.addEventListener("keydown", (e) => {
      // Activity modal takes priority for Escape
      if (this.isActivityModalOpen) {
        if (e.key === "Escape") {
          e.preventDefault();
          this.closeActivityModal();
          return;
        }
      }

      // Ignore if input/textarea is focused
      if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
        if (e.key === "Escape" && document.activeElement === this.dom.searchInput) {
          this.closeSearch();
        }
        return;
      }

      // Focus mode owns navigation while an activity is zoomed in.
      if (this.focusedActivity) {
        switch (e.key) {
          case "Escape":
            e.preventDefault();
            this.exitFocus();
            return;
          case "ArrowRight":
          case "PageDown":
          case " ":
          case "n":
          case "N":
            e.preventDefault();
            this.stepActivity(1);
            return;
          case "ArrowLeft":
          case "PageUp":
          case "p":
          case "P":
            e.preventDefault();
            this.stepActivity(-1);
            return;
          case "ArrowDown":
            e.preventDefault();
            this.stepSubItem(1);
            return;
          case "ArrowUp":
            e.preventDefault();
            this.stepSubItem(-1);
            return;
          case "+":
          case "=":
            e.preventDefault();
            this.nudgeFocusZoom(0.2);
            return;
          case "-":
          case "_":
            e.preventDefault();
            this.nudgeFocusZoom(-0.2);
            return;
          case "0":
            e.preventDefault();
            this.focusZoomBias = 1;
            this.renderFocus().then(() => this.updateFocusChrome());
            return;
          default:
            if (!(e.ctrlKey || e.metaKey)) return;
        }
      }

      switch (e.key) {
        case "ArrowRight":
        case "PageDown":
        case "j":
        case "J":
        case " ":
          e.preventDefault();
          this.nextPage();
          break;
        case "ArrowLeft":
        case "PageUp":
        case "k":
        case "K":
          e.preventDefault();
          this.prevPage();
          break;
        case "Home":
          e.preventDefault();
          this.goToPage(1);
          break;
        case "End":
          e.preventDefault();
          this.goToPage(this.totalPages);
          break;
        case "+":
        case "=":
          e.preventDefault();
          this.dom.btnZoomIn.click();
          break;
        case "-":
        case "_":
          e.preventDefault();
          this.dom.btnZoomOut.click();
          break;
        case "0":
          e.preventDefault();
          this.setZoom("fit-page");
          break;
        case "b":
        case "B":
          e.preventDefault();
          this.setViewMode("book");
          break;
        case "s":
        case "S":
          e.preventDefault();
          this.setViewMode("single");
          break;
        case "c":
        case "C":
          e.preventDefault();
          this.setViewMode("scroll");
          break;
        case "f":
        case "F":
          if (!e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            this.dom.btnFullscreen.click();
          }
          break;
        case "t":
        case "T":
          e.preventDefault();
          this.dom.btnToggleSidebar.click();
          break;
        case "m":
        case "M":
          e.preventDefault();
          this.cycleTheme();
          break;
        case "r":
        case "R":
          e.preventDefault();
          this.dom.btnRotate.click();
          break;
        case "a":
        case "A":
          e.preventDefault();
          this.toggleActivities();
          break;
        case "?":
          e.preventDefault();
          this.dom.helpModal.showModal();
          break;
        case "Escape":
          if (this.dom.helpModal.open) {
            this.dom.helpModal.close();
          } else if (!this.dom.searchBar.classList.contains("hidden")) {
            this.closeSearch();
          } else {
            this.showDashboard();
          }
          break;
      }

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        this.toggleSearch();
      }
    });

    // Mouse Wheel Zoom with Ctrl
    this.dom.focusOverlay.addEventListener("wheel", (e) => {
      if (!this.focusedActivity) return;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        this.nudgeFocusZoom(e.deltaY > 0 ? -0.15 : 0.15);
      }
    }, { passive: false });

    this.dom.viewerContainer.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.15 : 0.15;
        const current = this.zoomMode === "custom" ? this.customZoom : 1.0;
        this.setZoom(Math.max(0.3, Math.min(3.0, current + delta)));
      }
    }, { passive: false });
  }
}

// Instantiate on DOM load or immediately if DOM is already ready
function initApp() {
  if (!window.pdfApp) {
    window.pdfApp = new PDFViewerApp();
  }
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}
