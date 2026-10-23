/* BMO v13.2 presentation-only viewport. No art, audio, or state-machine edits. */
(() => {
  "use strict";
  const mounted = new WeakMap();
  const CENTER = Object.freeze({x: 500, y: 350});
  // Conservative envelope for all approved artwork, tears and combined gaze.
  // Includes a stroke allowance. No per-emotion resizing or position changes.
  const ENVELOPE = Object.freeze({left: 148, top: 156, right: 852, bottom: 540});
  const STYLE_ID = "bmo-viewport-v1-style";
  const CSS = `body.kiosk #stage,#stage:fullscreen{
    width:100%;height:100%;max-height:none;max-width:none;
  }`;

  function attach(doc, {scale = 1.5} = {}) {
    if (!doc || !doc.defaultView || typeof doc.getElementById !== "function")
      throw new TypeError("A same-origin face Document is required.");
    if (!Number.isFinite(scale) || scale < 0.75 || scale > 1.5)
      throw new RangeError("Viewport scale must be from 0.75 through 1.5.");
    const existing = mounted.get(doc);
    if (existing) {
      if (existing.requestedScale !== scale)
        throw new Error("Dispose the previous viewport before changing its scale.");
      return existing;
    }
    const win = doc.defaultView;
    const svg = doc.getElementById("face");
    const stage = doc.getElementById("stage");
    const face = win.BMOFace;
    if (!svg || !stage || !doc.getElementById("lookRoot") || !face ||
        face.getState().version !== "13.2" ||
        svg.getAttribute("viewBox") !== "0 0 1000 700")
      throw new Error("The approved v13.2 face viewport is required.");
    if (doc.getElementById(STYLE_ID))
      throw new Error("A viewport style already exists without an owner.");
    const oldViewBox = svg.getAttribute("viewBox");
    const oldAspect = svg.getAttribute("preserveAspectRatio");
    const style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    doc.head.appendChild(style);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    let closed = false;
    let state = null;
    const halfWidth = Math.max(CENTER.x - ENVELOPE.left, ENVELOPE.right - CENTER.x);
    const halfHeight = Math.max(CENTER.y - ENVELOPE.top, ENVELOPE.bottom - CENTER.y);
    function refresh() {
      if (closed) return;
      const rect = svg.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const unit = Math.min(rect.width / 1000, rect.height / 700);
      const padding = Math.max(4, Math.min(rect.width, rect.height) * 0.025);
      // Cap zoom only to fit the entire animated envelope on narrow previews.
      const fitX = Math.max(0.01, rect.width / 2 - padding) / (halfWidth * unit);
      const fitY = Math.max(0.01, rect.height / 2 - padding) / (halfHeight * unit);
      const effectiveScale = Math.min(scale, fitX, fitY);
      const width = 1000 / effectiveScale, height = 700 / effectiveScale;
      const viewBox = [CENTER.x - width / 2, CENTER.y - height / 2, width, height]
        .map(n => Number(n.toFixed(9))).join(" ");
      if (svg.getAttribute("viewBox") !== viewBox) svg.setAttribute("viewBox", viewBox);
      state = Object.freeze({
        layoutVersion: "7inch-uniform-v1", artworkVersion: "13.2",
        requestedScale: scale, effectiveScale,
        width: rect.width, height: rect.height, padding,
        unitPixels: unit * effectiveScale, viewBox
      });
    }
    const observer = typeof win.ResizeObserver === "function"
      ? new win.ResizeObserver(refresh) : null;
    observer?.observe(svg);
    win.addEventListener("resize", refresh);
    doc.addEventListener("fullscreenchange", refresh);
    const api = Object.freeze({
      requestedScale: scale,
      getState: () => state ? {...state} : null,
      refresh,
      dispose() {
        if (closed) return;
        closed = true;
        observer?.disconnect();
        win.removeEventListener("resize", refresh);
        doc.removeEventListener("fullscreenchange", refresh);
        win.removeEventListener("pagehide", api.dispose);
        svg.setAttribute("viewBox", oldViewBox);
        if (oldAspect === null) svg.removeAttribute("preserveAspectRatio");
        else svg.setAttribute("preserveAspectRatio", oldAspect);
        style.remove();
        mounted.delete(doc);
      }
    });
    mounted.set(doc, api);
    win.addEventListener("pagehide", api.dispose);
    refresh();
    return api;
  }
  window.BMOViewportV1 = Object.freeze({attach, envelope: ENVELOPE});
})();
