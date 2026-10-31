/* Only Python owns audio. This adapter never calls any media play method. */
(() => {
  "use strict";
  const emotions = new Set(["neutral", "happy", "angry", "sad", "crying", "surprised"]);
  const phases = new Set(["idle", "listening", "thinking", "speaking"]);
  const playback = new Set(["idle", "prepared", "started", "ended", "cancelled", "error"]);
  const mouths = new Set(["rest", "closed", "parted", "small", "low", "mid", "body", "open", "high", "wide", "round", "tight", "flat", "grit"]);
  const terminal = new Set(["ended", "cancelled", "error"]);

  class FaceAdapter {
    constructor(frame, {poll = true} = {}) {
      this.frame = frame;
      this.status = document.getElementById("status");
      this.state = null;
      this.received = 0;
      this.lastSuccess = performance.now();
      this.session = null;
      this.seenSessions = new Set();
      this.retired = new Set();
      this.terminal = new Set();
      this.sequence = -1;
      this.closed = false;
      this.timer = null;
      this.controller = null;
      this.pollGeneration = 0;
      this.raf = null;
      this.frameWindow = null;
      this.entrance = null;
      this.entranceState = {status: "idle", target: "expression_layers"};
      this.resetFace();
      this.onFramePageHide = () => this.close();
      this.onLoad = () => {
        if (this.closed) return;
        this.cancelEntrance("frame_reloaded");
        this.resetFace();
        this.bindFrame();
        if (this.state) this.applyFace();
      };
      this.onVisibility = () => {
        // Poll before rendering after a suspended tab; never replay old cues.
        this.cancelEntrance("visibility_changed");
        this.suspend();
        this.pollGeneration++;
        clearTimeout(this.timer);
        this.controller?.abort();
        this.controller = null;
        if (poll && !document.hidden) this.poll();
      };
      this.onPageHide = () => this.close();
      frame.addEventListener("load", this.onLoad);
      document.addEventListener("visibilitychange", this.onVisibility);
      window.addEventListener("pagehide", this.onPageHide);
      this.bindFrame();
      this.observer = new MutationObserver(() => {
        if (!frame.isConnected) this.close();
      });
      this.observer.observe(document.documentElement, {childList: true, subtree: true});
      if (poll) this.poll();
      this.tick = this.tick.bind(this);
      this.raf = requestAnimationFrame(this.tick);
    }

    get face() {
      try { return this.frame.contentWindow?.BMOFace; } catch (_) { return null; }
    }

    presentExpression({reducedMotion = false, duration = 550} = {}) {
      // The approved background rectangle is a SIBLING of lookRoot. A temporary
      // external SVG group moves only that expression subtree: native gaze,
      // blinking, mouth, brows and tears retain every original transform/path.
      this.cancelEntrance("superseded");
      let doc, svg, root, version;
      try {
        doc = this.frame.contentDocument;
        svg = doc?.getElementById("face");
        root = doc?.getElementById("lookRoot");
        version = this.face?.getState().version;
      } catch (_) {}
      if (this.closed || !svg || !root || root.parentNode !== svg
          || svg.namespaceURI !== "http://www.w3.org/2000/svg"
          || version !== "13.2") {
        this.entranceState = {status: "artwork_unavailable", target: "expression_layers"};
        return Promise.resolve({...this.entranceState});
      }
      if (reducedMotion || doc.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
        this.entranceState = {status: "reduced_motion", target: "expression_layers"};
        return Promise.resolve({...this.entranceState});
      }
      let layer, offset;
      const originalNext = root.nextSibling;
      const restore = () => {
        if (layer && root.parentNode === layer) {
          svg.insertBefore(root, originalNext?.parentNode === svg ? originalNext : null);
        }
        layer?.remove();
      };
      try {
        layer = doc.createElementNS(svg.namespaceURI, "g");
        layer.setAttribute("data-bmo-expression-entrance", "v1");
        svg.insertBefore(layer, root);
        layer.appendChild(root);
        // R10 owns its viewBox/zoom. No canvas/iframe/body/main transform or
        // zoom is added; setup errors restore the original tree too.
        const box = svg.viewBox.baseVal, bounds = layer.getBBox();
        // A full viewBox also covers native blink/emotion shape expansion.
        offset = Math.max(box.height, box.y + box.height - bounds.y + 8);
        if (!Number.isFinite(offset) || offset <= 0) throw Error("Expression geometry unavailable");
      } catch (_) {
        restore();
        this.entranceState = {status: "animation_unavailable", target: "expression_layers"};
        return Promise.resolve({...this.entranceState});
      }
      const milliseconds = Number.isFinite(duration) ? Math.max(0, Math.min(550, duration)) : 550;
      this.entranceState = {status: "running", target: "expression_layers", duration_ms: milliseconds,
                            offset_units: offset};
      return new Promise(resolve => {
        let settled = false, animation;
        const finish = status => {
          if (settled) return;
          settled = true;
          // Restore the exact original parent/order, never the gaze transform.
          restore();
          animation?.cancel();
          this.entrance = null;
          this.entranceState = {...this.entranceState, status};
          resolve({...this.entranceState});
        };
        this.entrance = {layer, finish, animation: null};
        try {
          animation = layer.animate([{transform: `translateY(${offset}px)`}, {transform: "translateY(0px)"}],
            {duration: milliseconds, easing: "cubic-bezier(.2,.7,.2,1)", fill: "both"});
          this.entrance.animation = animation;
          animation.finished.then(() => finish("completed"), () => finish("cancelled"));
        } catch (_) {
          finish("animation_unavailable");
        }
      });
    }

    cancelEntrance(reason = "cancelled") {
      const entrance = this.entrance;
      if (!entrance) return;
      entrance.finish(reason);
      entrance.animation?.cancel();
    }

    bindFrame() {
      if (!this.face || this.frameWindow) return;
      this.frameWindow = this.frame.contentWindow;
      this.frameWindow.addEventListener("pagehide", this.onFramePageHide);
    }

    resetFace() {
      this.appliedEmotion = null;
      this.appliedActivity = null;
      this.speaking = false;
      this.mouth = null;
      this.lastMouth = 0;
    }

    identity(state) {
      return JSON.stringify([state.session_id, state.turn_id, state.utterance_id]);
    }

    applySnapshot(next) {
      if (this.closed || !next || !emotions.has(next.emotion) || !phases.has(next.phase)
          || !playback.has(next.playback) || !Number.isInteger(next.sequence) || next.sequence < 0
          || !Number.isFinite(next.position) || next.position < 0 || !Array.isArray(next.cues)
          || next.cues.length > 10000
          || ![next.session_id, next.turn_id, next.utterance_id].every(x => typeof x === "string")) return false;
      let last = -1;
      for (const cue of next.cues) {
        if (!cue || !Number.isFinite(cue.time) || cue.time < last || cue.time < 0 || cue.time > 180 || !mouths.has(cue.mouth)) return false;
        last = cue.time;
      }
      if (next.session_id !== this.session) {
        if (this.seenSessions.has(next.session_id)) return false;
        if (this.session !== null) this.seenSessions.add(this.session);
        this.session = next.session_id;
        this.sequence = -1;
        this.suspend();
      }
      if (next.sequence < this.sequence) return false;
      const key = this.identity(next);
      if (this.retired.has(key)
          || (["prepared", "started"].includes(next.playback) && this.terminal.has(key))) return false;
      if (this.state && this.identity(this.state) !== key && this.state.utterance_id) {
        this.retired.add(this.identity(this.state));
        this.suspend();
      }
      if (terminal.has(next.playback)) this.terminal.add(key);
      this.sequence = next.sequence;
      this.state = {...next, cues: next.playback === "started" ? next.cues.map(c => ({...c})) : []};
      this.received = performance.now();
      this.lastSuccess = this.received;
      if (this.status) this.status.textContent = "";
      this.applyFace();
      return true;
    }

    applyFace() {
      const face = this.face, state = this.state;
      if (!face || !state || this.closed) return;
      const active = state.playback === "started" && state.phase === "speaking" && !!state.utterance_id;
      const emotionChanged = state.emotion !== this.appliedEmotion;
      if (emotionChanged) {
        face.setEmotion(state.emotion);
        this.appliedEmotion = state.emotion;
        this.appliedActivity = "idle"; // setEmotion resets a non-idle activity.
      }
      if (active) {
        if (!this.speaking) {
          face.setSpeaking(true);
          this.speaking = true;
          this.appliedActivity = "idle";
          this.mouth = null;
        }
        // During speech a changed emotion gets setEmotion only, never activity.
        this.renderMouth(performance.now());
      } else {
        if (this.speaking || terminal.has(state.playback)) {
          face.stop();
          this.speaking = false;
          this.mouth = null;
        }
        const activity = state.phase === "speaking" ? "thinking" : state.phase;
        if (activity !== this.appliedActivity) {
          face.setActivity(activity);
          this.appliedActivity = activity;
        }
      }
    }

    renderMouth(now) {
      if (!this.speaking || !this.face || !this.state) return;
      const state = this.state;
      const position = state.position + Math.max(0, now - this.received) / 1000;
      let lo = 0, hi = state.cues.length - 1, index = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (state.cues[mid].time <= position + 1e-7) { index = mid; lo = mid + 1; }
        else hi = mid - 1;
      }
      const mouth = index < 0 ? "closed" : state.cues[index].mouth;
      // Refresh a held key before the original external-mouth timeout expires.
      if (mouth !== this.mouth || now - this.lastMouth >= 80) {
        this.face.setMouth(mouth, .25);
        this.mouth = mouth;
        this.lastMouth = now;
      }
    }

    suspend() {
      this.face?.stop();
      this.speaking = false;
      this.mouth = null;
    }

    async poll() {
      if (this.closed || this.controller || document.hidden) return;
      clearTimeout(this.timer);
      const generation = this.pollGeneration;
      const controller = new AbortController();
      this.controller = controller;
      const timeout = setTimeout(() => controller.abort(), 1000);
      try {
        const response = await fetch("/api/state", {cache: "no-store", signal: controller.signal});
        if (!response.ok) throw new Error("Face state unavailable");
        const snapshot = await response.json();
        if (!this.closed && generation === this.pollGeneration && !document.hidden) this.applySnapshot(snapshot);
      } catch (_) {
        if (!this.closed && generation === this.pollGeneration && performance.now() - this.lastSuccess > 1000) {
          this.suspend();
          if (this.status) this.status.textContent = "Reconnecting…";
        }
      } finally {
        clearTimeout(timeout);
        if (this.controller === controller) this.controller = null;
        if (!this.closed && generation === this.pollGeneration && !document.hidden) this.timer = setTimeout(() => this.poll(), 80);
      }
    }

    tick(now) {
      if (this.closed) return;
      if (!document.hidden && performance.now() - this.lastSuccess < 1100) this.renderMouth(now);
      else if (this.speaking) this.suspend();
      this.raf = requestAnimationFrame(this.tick);
    }

    close() {
      if (this.closed) return;
      this.cancelEntrance("closed");
      this.suspend();
      this.closed = true;
      this.pollGeneration++;
      this.state = null;
      clearTimeout(this.timer);
      cancelAnimationFrame(this.raf);
      this.controller?.abort();
      this.controller = null;
      this.frameWindow?.removeEventListener("pagehide", this.onFramePageHide);
      this.frameWindow = null;
      this.frame.removeEventListener("load", this.onLoad);
      document.removeEventListener("visibilitychange", this.onVisibility);
      window.removeEventListener("pagehide", this.onPageHide);
      this.observer.disconnect();
    }
  }
  window.BMOFaceAdapter = FaceAdapter;
  window.BMOIntegration = new FaceAdapter(document.getElementById("face"));
})();
