import { useEffect, useMemo, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

export default function PdfHighlightViewer({
  pdfUrl,
  pageNumber = 1,
  pageNumbers = [],
  highlights = [],
}) {
  const hostRef = useRef(null);
  const canvasRef = useRef(null);

  const [hostWidth, setHostWidth] = useState(0);
  const [pageSize, setPageSize] = useState({
    width: 0,
    height: 0,
  });

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const availablePages = useMemo(() => {
    const pages = [
      ...pageNumbers,
      pageNumber,
      ...highlights.map((h) => h.page_number),
    ]
      .map(Number)
      .filter((n) => Number.isInteger(n) && n > 0);

    return [...new Set(pages)].sort((a, b) => a - b);
  }, [pageNumbers, pageNumber, highlights]);

  const [selectedPage, setSelectedPage] = useState(
    Number(pageNumber) || 1
  );

  useEffect(() => {
    setSelectedPage(Number(pageNumber) || 1);
  }, [pageNumber, pdfUrl]);

  useEffect(() => {
    const host = hostRef.current;

    if (!host) return undefined;

    const updateWidth = () => {
      const width = Math.floor(host.clientWidth - 24);

      if (width > 0) {
        setHostWidth(width);
      }
    };

    updateWidth();

    let observer;

    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(updateWidth);
      observer.observe(host);
    } else {
      window.addEventListener("resize", updateWidth);
    }

    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateWidth);
    };
  }, []);

  useEffect(() => {
    if (!pdfUrl || !hostWidth) return undefined;

    let cancelled = false;
    let loadingTask;
    let renderTask;

    async function renderPage() {
      setLoading(true);
      setError("");
      setPageSize({ width: 0, height: 0 });

      try {
        loadingTask = pdfjsLib.getDocument({ url: pdfUrl });

        const pdf = await loadingTask.promise;

        if (cancelled) return;

        if (selectedPage > pdf.numPages) {
          throw new Error(
            `Page ${selectedPage} does not exist in this PDF.`
          );
        }

        const page = await pdf.getPage(selectedPage);

        if (cancelled) return;

        const baseViewport = page.getViewport({ scale: 1 });

        const scale = Math.min(
          hostWidth / baseViewport.width,
          1.5
        );

        const viewport = page.getViewport({ scale });

        const outputScale = window.devicePixelRatio || 1;

        const canvas = canvasRef.current;

        if (!canvas) return;

        canvas.width = Math.ceil(
          viewport.width * outputScale
        );

        canvas.height = Math.ceil(
          viewport.height * outputScale
        );

        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;

        setPageSize({
          width: viewport.width,
          height: viewport.height,
        });

        const context = canvas.getContext("2d", {
          alpha: false,
        });

        renderTask = page.render({
          canvasContext: context,
          viewport,
          transform:
            outputScale === 1
              ? null
              : [outputScale, 0, 0, outputScale, 0, 0],
        });

        await renderTask.promise;
      } catch (err) {
        if (
          !cancelled &&
          err?.name !== "RenderingCancelledException"
        ) {
          console.error("PDF highlight viewer error:", err);

          setError(
            err?.message || "Unable to display this PDF page."
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    renderPage();

    return () => {
      cancelled = true;

      if (renderTask) {
        try {
          renderTask.cancel();
        } catch {}
      }

      if (loadingTask) {
        try {
          loadingTask.destroy();
        } catch {}
      }
    };
  }, [pdfUrl, selectedPage, hostWidth]);

  const validHighlights = (
    Array.isArray(highlights) ? highlights : []
  ).filter(
    (h) =>
      (!h.page_number ||
        Number(h.page_number) === selectedPage) &&
      Number.isFinite(Number(h.x0)) &&
      Number.isFinite(Number(h.y0)) &&
      Number.isFinite(Number(h.x1)) &&
      Number.isFinite(Number(h.y1)) &&
      Number(h.page_width) > 0 &&
      Number(h.page_height) > 0
  );

  return (
    <div
      ref={hostRef}
      className="lcms-pdf-highlight-viewer"
      style={{
        width: "100%",
        maxHeight: "72vh",
        overflow: "auto",
        padding: 12,
        boxSizing: "border-box",
        background: "#edf0f4",
      }}
    >
      {availablePages.length > 1 && (
        <div
          className="lcms-pdf-page-nav"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            flexWrap: "wrap",
            marginBottom: 10,
            position: "sticky",
            top: 0,
            zIndex: 4,
          }}
        >
          <span
            style={{
              fontSize: 10,
              color: "#64778e",
              marginRight: 4,
            }}
          >
            Citation pages:
          </span>

          {availablePages.map((page) => (
            <button
              type="button"
              key={page}
              onClick={() => setSelectedPage(page)}
              aria-pressed={selectedPage === page}
              style={{
                border: "1px solid #d6e2ef",
                borderRadius: 5,
                padding: "4px 8px",
                background:
                  selectedPage === page ? "#286db7" : "#fff",
                color:
                  selectedPage === page ? "#fff" : "#28639c",
                fontSize: 10,
                cursor: "pointer",
              }}
            >
              {page}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className="inline-warning">
          Loading PDF page…
        </div>
      )}

      {error && (
        <div className="inline-warning">{error}</div>
      )}

      <div
        className="lcms-pdf-page"
        style={{
          position: "relative",
          width: pageSize.width || "max-content",
          height: pageSize.height || "auto",
          margin: "0 auto",
          background: "#fff",
          boxShadow: "0 1px 7px rgba(0,0,0,.18)",
        }}
      >
        <canvas
          ref={canvasRef}
          style={{ display: "block" }}
        />

        {pageSize.width > 0 &&
          validHighlights.map((h, index) => {
            const left =
              (Number(h.x0) / Number(h.page_width)) *
              pageSize.width;

            const top =
              (Number(h.y0) / Number(h.page_height)) *
              pageSize.height;

            const width =
              ((Number(h.x1) - Number(h.x0)) /
                Number(h.page_width)) *
              pageSize.width;

            const height =
              ((Number(h.y1) - Number(h.y0)) /
                Number(h.page_height)) *
              pageSize.height;

            if (
              ![left, top, width, height].every(
                Number.isFinite
              ) ||
              width <= 0 ||
              height <= 0
            ) {
              return null;
            }

            return (
              <div
                key={
                  h.id ??
                  `${h._origin_citation_id || "h"}-${
                    h.segment_order ?? index
                  }-${index}`
                }
                title={h.matched_text || "Source highlight"}
                aria-label="Highlighted source text"
                style={{
                  position: "absolute",
                  left,
                  top,
                  width,
                  height,
                  background: "rgba(255, 214, 0, 0.38)",
                  borderBottom:
                    "2px solid rgba(205, 145, 0, 0.9)",
                  boxSizing: "border-box",
                  pointerEvents: "none",
                  zIndex: 2,
                }}
              />
            );
          })}
      </div>

      {!loading &&
        !error &&
        validHighlights.length === 0 && (
          <div
            className="inline-warning"
            style={{ marginTop: 10 }}
          >
            No saved PDF highlight coordinates are available
            for this citation page. The source page is displayed
            without an overlay.
          </div>
        )}
    </div>
  );
}