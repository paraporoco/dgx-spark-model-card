/* dgx-model-card v2.2 — "Local models" card for the NVIDIA DGX Dashboard.
 *
 * Touches no NVIDIA file. Mounts one node into the card grid; removed with
 * window.__dgxModelCard.destroy().
 *
 * Layout is deliberately three labelled blocks — LOADED NOW / MEMORY /
 * LOAD A MODEL — so status and intent are never the same widget.
 */
(function () {
  "use strict";

  if (window.__dgxModelCard && window.__dgxModelCard.destroy) {
    window.__dgxModelCard.destroy();
  }

  var API = (function () {
    var s = document.currentScript && document.currentScript.src;
    if (s) { try { return new URL(s).origin; } catch (e) {} }
    return "http://127.0.0.1:8110";
  })();

  var MOUNT_ID = "dgx-model-card-root";
  var POLL_IDLE = 6000, POLL_BUSY = 2500;
  var timer = null, observer = null, logsOpen = false, busy = false;
  var lastStatus = null, notice = null;

  // Observed cold-load rate on this box: 65.1 GiB in 7 min 24 s.
  var GIB_PER_MIN = 8.8;

  var OK = "var(--text-color-feedback-success, #76b900)";
  var WARN = "var(--text-color-feedback-warning, #f5b800)";
  var ERR = "var(--text-color-feedback-error, #f5484c)";
  var MUTED = "var(--text-color-secondary, #8f8f8f)";
  var LINE = "var(--border-color-base, #3a3a3a)";
  var RAISED = "var(--background-color-surface-raised, #1a1a1a)";

  var LBL = "nv-text nv-text--body-regular-sm";
  var MONO = "nv-text nv-text--mono-sm";

  function api(path, opts) {
    return fetch(API + path, Object.assign({ mode: "cors", cache: "no-store" }, opts || {}))
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); });
  }
  function post(path, payload) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
                       body: JSON.stringify(payload || {}) });
  }

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") n.setAttribute("style", attrs[k]);
      else if (k === "class") n.className = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }

  function heading(text) {
    return el("div", {
      class: "nv-text nv-text--label-bold-xs",
      style: "letter-spacing:.09em;text-transform:uppercase;color:" + MUTED + ";"
    }, [text]);
  }

  function findGrid() {
    var p = document.querySelector('[data-testid="skele-panel"]');
    if (p && p.parentElement) return p.parentElement;
    var g = document.querySelector("div.flex.gap-4.flex-wrap");
    if (g) return g;
    var col = document.querySelector('[class*="max-w-"] .flex.flex-col.gap-8');
    if (col && col.lastElementChild) return col.lastElementChild;
    return null;
  }

  function ago(ts) {
    if (!ts) return null;
    var s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return s + " s ago";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    return Math.floor(s / 3600) + " h ago";
  }

  function eta(gib) {
    if (!gib || gib < 20) return null;
    return "≈ " + Math.max(1, Math.round(gib / GIB_PER_MIN)) + " min if not cached";
  }

  function dot(color, size) {
    return el("span", {
      class: "nv-status-indicator",
      style: "--size:" + (size || 10) + "px;--color:" + color + ";background-color:" + color +
             ";width:" + (size || 10) + "px;height:" + (size || 10) + "px;border-radius:999px;" +
             "display:inline-block;flex:0 0 auto;"
    });
  }

  // ---------------------------------------------------------------- sections

  function loadedNow(st) {
    var res = st.resident || [];
    var body;

    if (!res.length) {
      body = el("div", { style: "display:flex;align-items:center;gap:10px;" }, [
        dot(MUTED),
        el("span", { class: "nv-text nv-text--body-semibold-md", style: "color:" + MUTED + ";" },
           ["Nothing loaded"])
      ]);
    } else {
      body = el("div", { style: "display:flex;flex-direction:column;gap:10px;" },
        res.map(function (r) {
          var m = (st.models || []).filter(function (x) { return x.id === r.id; })[0] || {};
          var starting = r.state === "starting";
          return el("div", { style: "display:flex;align-items:flex-start;gap:10px;" }, [
            el("div", { style: "padding-top:5px;" }, [dot(starting ? WARN : OK)]),
            el("div", { style: "display:flex;flex-direction:column;gap:2px;min-width:0;" }, [
              el("span", { class: "nv-text nv-text--body-semibold-md" },
                 [(m.name || r.id) + (starting ? "  — loading…" : "")]),
              el("span", { class: LBL, style: "color:" + MUTED + ";" }, [
                (m.size_gib ? m.size_gib + " GiB" : "") +
                (m.group ? " · " + m.group : "") +
                (m.ttl ? " · unloads after " + (m.ttl / 60) + " min idle" : "") +
                (m.proxy ? " · " + m.proxy : "")
              ])
            ])
          ]);
        }));
    }

    var g = st.gate || {};
    var lastLine = g.last_model
      ? el("div", {
          style: "display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;padding-top:4px;" +
                 "border-top:1px dashed " + LINE + ";margin-top:2px;"
        }, [
          el("span", { class: LBL, style: "color:" + MUTED + ";" }, ["Last asked for by a client:"]),
          el("span", { class: MONO }, [g.last_model]),
          el("span", { class: LBL, style: "color:" + MUTED + ";" },
             ["· " + (ago(g.last_model_at) || "") + " · " +
              (g.last_model_loaded ? "already loaded" : "triggered a load")])
        ])
      : el("div", {
          style: "padding-top:4px;border-top:1px dashed " + LINE + ";margin-top:2px;"
        }, [
          el("span", { class: LBL, style: "color:" + MUTED + ";" },
             ["No client request seen yet. Whatever Continue asks for next is what loads automatically."])
        ]);

    return el("div", { style: "display:flex;flex-direction:column;gap:10px;" },
              [heading("Loaded now"), body, lastLine]);
  }

  function memoryBlock(st, sel) {
    var mem = st.memory || {};
    var total = mem.total_gib || 0, avail = mem.available_gib || 0;
    var used = Math.max(0, total - avail);
    var usedPct = total ? (used / total) * 100 : 0;

    // Where the selected model's requirement lands on the same bar.
    var needPct = (total && sel && sel.needed_gib)
      ? Math.min(100, ((total - sel.needed_gib) / total) * 100) : null;

    var bar = el("div", {
      style: "position:relative;height:10px;border-radius:999px;overflow:hidden;background:" + RAISED +
             ";border:1px solid " + LINE + ";"
    }, [
      el("div", {
        style: "position:absolute;left:0;top:0;bottom:0;width:" + usedPct.toFixed(1) + "%;" +
               "background:" + (usedPct > 85 ? WARN : OK) + ";opacity:.85;"
      }),
      needPct !== null ? el("div", {
        title: "memory needed for " + (sel.name || ""),
        style: "position:absolute;top:-3px;bottom:-3px;left:" + needPct.toFixed(1) + "%;width:2px;" +
               "background:" + (sel.can_load ? OK : ERR) + ";"
      }) : null
    ]);

    var stepper = el("div", { style: "display:flex;align-items:center;gap:6px;" }, [
      el("button", {
        class: "nv-button nv-button--kind-tertiary nv-button--color-neutral nv-button--size-tiny",
        type: "button", title: "lower the reserve",
        onclick: function () { setMargin(Math.max(0, st.margin_gib - 2)); }
      }, ["−"]),
      el("span", { class: MONO, style: "min-width:52px;text-align:center;" },
         [st.margin_gib + " GiB"]),
      el("button", {
        class: "nv-button nv-button--kind-tertiary nv-button--color-neutral nv-button--size-tiny",
        type: "button", title: "raise the reserve",
        onclick: function () { setMargin(Math.min(64, st.margin_gib + 2)); }
      }, ["+"])
    ]);

    return el("div", { style: "display:flex;flex-direction:column;gap:8px;" }, [
      heading("Memory"),
      bar,
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;" }, [
        el("span", { class: MONO }, [used.toFixed(1) + " used · " + avail.toFixed(1) + " free · " + total + " GiB total"]),
        el("div", { style: "display:flex;align-items:center;gap:8px;" }, [
          el("span", { class: LBL, style: "color:" + MUTED + ";" }, ["Keep free:"]),
          stepper
        ])
      ])
    ]);
  }

  function loadBlock(st, sel) {
    var options = (st.models || []).map(function (m) {
      var fit = m.can_load ? "fits" : (m.load_reason === "hold" ? "blocked" : "won't fit");
      return el("option", {
        value: m.id,
        selected: m.id === st.selected ? "selected" : null
      }, [m.name + " — " + (m.size_gib || "?") + " GiB — " + fit]);
    });

    var select = el("select", {
      class: "nv-text nv-text--body-regular-sm",
      style: "width:100%;padding:8px 10px;border-radius:6px;background:" + RAISED +
             ";color:var(--text-color-primary,#e6e6e6);border:1px solid " + LINE + ";",
      onchange: function (ev) {
        busy = true; notice = null; refresh();
        post("/api/select", { model: ev.target.value })
          .catch(function () {}).then(function () { busy = false; tick(); });
      }
    }, options);

    // One plain sentence describing exactly what Start does.
    var plan, planColor = MUTED;
    if (!sel) {
      plan = "Select a model.";
    } else if (sel.state === "ready") {
      plan = sel.name + " is already loaded and serving.";
      planColor = OK;
    } else if (sel.state === "starting") {
      plan = sel.name + " is loading now.";
      planColor = WARN;
    } else if (!sel.can_load && sel.load_reason === "hold") {
      plan = "Automatic loading is blocked, so Start is disabled. Allow it below to load " + sel.name + ".";
      planColor = WARN;
    } else if (!sel.can_load) {
      plan = "Not enough memory: " + sel.name + " needs " + sel.needed_gib +
             " GiB free (" + sel.size_gib + " model + " + st.margin_gib + " reserve), " +
             sel.effective_gib + " GiB available.";
      planColor = ERR;
    } else {
      var e = eta(sel.size_gib);
      plan = "Start will " +
             (sel.evicts && sel.evicts.length ? "unload " + sel.evicts.join(", ") + " and load " : "load ") +
             sel.name + " (" + sel.size_gib + " GiB" + (e ? ", " + e : "") + ").";
      planColor = OK;
    }

    return el("div", { style: "display:flex;flex-direction:column;gap:8px;" }, [
      heading("Load a model"),
      select,
      el("div", { class: LBL, style: "color:" + planColor + ";word-break:break-word;" }, [plan])
    ]);
  }

  function autoLoadBlock(st) {
    var held = !!st.hold;

    function segment(label, active, onClick, color) {
      return el("button", {
        class: "nv-segmented-control-item" + (active ? " nv-segmented-control-item--selected" : ""),
        type: "button",
        onclick: onClick,
        style: "padding:5px 14px;border-radius:5px;cursor:pointer;font-size:12px;" +
               "border:1px solid " + (active ? (color || LINE) : "transparent") + ";" +
               "background:" + (active ? RAISED : "transparent") + ";" +
               "color:" + (active ? (color || "inherit") : MUTED) + ";"
      }, [label]);
    }

    var seg = el("div", {
      class: "nv-segmented-control-root",
      style: "display:inline-flex;gap:2px;padding:2px;border-radius:7px;border:1px solid " + LINE + ";"
    }, [
      segment("Allowed", !held, function () { setHold(false); }, OK),
      segment("Blocked", held, function () { setHold(true); }, WARN)
    ]);

    var g = st.gate || {};
    var explain = held
      ? "No model can load — not from this card, not from Continue. Anything already loaded keeps serving."
      : "A request from Continue may load a model. Loads that do not fit in memory are refused either way.";

    return el("div", { style: "display:flex;flex-direction:column;gap:8px;" }, [
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:12px;" }, [
        heading("Automatic loading"), seg
      ]),
      el("div", { class: LBL, style: "color:" + (held ? WARN : MUTED) + ";" }, [explain]),
      g.enabled === false
        ? el("div", { class: LBL, style: "color:" + ERR + ";" },
             ["Gate is off — this setting is advisory only."])
        : (g.refused ? el("div", { class: LBL, style: "color:" + MUTED + ";" },
             [g.refused + " request" + (g.refused === 1 ? "" : "s") + " refused so far" +
              (g.last_refused_model ? " (last: " + g.last_refused_model + ")" : "")]) : null)
    ]);
  }

  // ---------------------------------------------------------------- render

  function render(st) {
    var sel = (st.models || []).filter(function (m) { return m.id === st.selected; })[0] || null;
    var res = st.resident || [];
    var anyStarting = res.some(function (r) { return r.state === "starting"; });

    var tag, tagColor;
    if (st.state === "engine-down") { tag = "Engine down"; tagColor = "red"; }
    else if (anyStarting) { tag = "Loading"; tagColor = "yellow"; }
    else if (res.length) { tag = "Serving"; tagColor = "green"; }
    else { tag = "Idle"; tagColor = "gray"; }

    var startBtn = el("button", {
      class: "nv-button nv-button--kind-primary nv-button--color-brand nv-button--size-small",
      type: "button",
      onclick: function () { act("/api/start", { model: st.selected }); }
    }, ["Start"]);
    var stopBtn = el("button", {
      class: "nv-button nv-button--kind-secondary nv-button--color-neutral nv-button--size-small",
      type: "button",
      onclick: function () { act("/api/stop"); }
    }, ["Unload all"]);

    if (busy || anyStarting || st.state === "engine-down" ||
        !sel || sel.can_load === false || sel.state === "ready") startBtn.disabled = true;
    if (busy || !res.length) stopBtn.disabled = true;

    return el("div", {
      id: MOUNT_ID,
      class: "nv-panel nv-panel--elevation-low flex-1 min-w-full md:min-w-[520px]",
      style: "display:flex;flex-direction:column;gap:20px;padding:24px;border-radius:8px;" +
             "flex:1 1 0%;min-width:min(100%,520px);"
    }, [
      el("div", { class: "nv-panel-header",
                  style: "display:flex;align-items:center;justify-content:space-between;gap:16px;" }, [
        el("div", { style: "display:flex;flex-direction:column;gap:2px;" }, [
          el("span", { class: "nv-panel-header-heading nv-text nv-text--title-sm" }, ["Local models"]),
          el("span", { class: LBL, style: "color:" + MUTED + ";" },
             ["llama-swap · " + (st.models || []).length + " available · one large model at a time"])
        ]),
        el("div", { style: "display:flex;align-items:center;gap:8px;" }, [
          st.hold ? el("span", { class: "nv-tag nv-tag--color-yellow nv-tag--kind-outline" }, ["Loading blocked"]) : null,
          el("span", { class: "nv-tag nv-tag--color-" + tagColor }, [tag])
        ])
      ]),

      el("div", { style: "display:flex;flex-direction:column;gap:20px;flex:1 1 auto;" }, [
        loadedNow(st),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        memoryBlock(st, sel),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        loadBlock(st, sel),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        autoLoadBlock(st),

        notice ? el("div", { class: LBL,
          style: "padding:8px 12px;border-radius:6px;background:rgba(245,184,0,.08);color:" + WARN + ";" },
          [notice]) : null,
        st.last_error ? el("div", { class: LBL, style: "color:" + ERR + ";word-break:break-word;" },
          [String(st.last_error)]) : null,

        logsOpen ? el("pre", {
          id: "dgx-model-card-logs",
          style: "margin:0;max-height:180px;overflow:auto;font-size:11px;line-height:1.45;background:" +
                 RAISED + ";padding:10px;border-radius:6px;white-space:pre-wrap;word-break:break-all;opacity:.85;"
        }, ["loading logs…"]) : null
      ]),

      el("div", { class: "nv-panel-footer",
                  style: "display:flex;justify-content:space-between;align-items:center;gap:8px;" }, [
        el("button", {
          class: "nv-button nv-button--kind-tertiary nv-button--color-neutral nv-button--size-small",
          type: "button",
          onclick: function () { logsOpen = !logsOpen; refresh(); if (logsOpen) loadLogs(); }
        }, [logsOpen ? "Hide log" : "Show log"]),
        el("div", { style: "display:flex;gap:8px;" }, [stopBtn, startBtn])
      ])
    ]);
  }

  // ---------------------------------------------------------------- actions

  function setHold(v) {
    busy = true; notice = null; refresh();
    post("/api/hold", { enabled: v }).catch(function () {})
      .then(function () { busy = false; tick(); });
  }
  function setMargin(g) {
    busy = true; refresh();
    post("/api/margin", { gib: g }).catch(function () {})
      .then(function () { busy = false; tick(); });
  }
  function act(path, payload) {
    busy = true; notice = null; refresh();
    post(path, payload).then(function (r) {
      if (r.status === 409) {
        notice = r.body.reason === "hold"
          ? "Refused: automatic loading is blocked. Set it to Allowed first."
          : "Refused: " + (r.body.detail || r.body.reason);
      } else if (r.body && r.body.accepted === false && r.body.reason) {
        notice = r.body.reason;
      }
    }).catch(function () { notice = "Sidecar unreachable at " + API; })
      .then(function () { busy = false; schedule(POLL_BUSY); tick(); });
  }

  function mount(node) {
    var grid = findGrid();
    if (!grid) return false;
    var cur = document.getElementById(MOUNT_ID);
    if (cur && cur.parentElement === grid) {
      if (document.activeElement && cur.contains(document.activeElement) &&
          document.activeElement.tagName === "SELECT") return true;
      grid.replaceChild(node, cur);
    } else {
      if (cur && cur.parentElement) cur.parentElement.removeChild(cur);
      grid.appendChild(node);
    }
    return true;
  }

  function loadLogs() {
    api("/api/logs?n=120").then(function (r) {
      var p = document.getElementById("dgx-model-card-logs");
      if (p) { p.textContent = (r.body.lines || []).join("\n") || "(empty)"; p.scrollTop = p.scrollHeight; }
    }).catch(function () {});
  }

  function refresh() { if (lastStatus) mount(render(lastStatus)); }

  function tick() {
    api("/api/status").then(function (r) {
      lastStatus = r.body;
      mount(render(lastStatus));
      if (logsOpen) loadLogs();
      var b = (lastStatus.resident || []).some(function (x) { return x.state === "starting"; }) ||
              lastStatus.pending;
      schedule(b ? POLL_BUSY : POLL_IDLE);
    }).catch(function (e) {
      lastStatus = { state: "engine-down", selected: null, models: [], resident: [],
                     hold: false, margin_gib: 0, memory: {}, gate: {},
                     last_error: "sidecar unreachable at " + API + " (" + e + ")" };
      mount(render(lastStatus));
      schedule(POLL_IDLE);
    });
  }

  function schedule(ms) { if (timer) clearTimeout(timer); timer = setTimeout(tick, ms); }

  function watch() {
    observer = new MutationObserver(function () {
      if (!document.getElementById(MOUNT_ID) && lastStatus && findGrid()) mount(render(lastStatus));
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function waitForGrid(n) {
    if (findGrid()) { tick(); watch(); return; }
    if (n > 120) return;
    setTimeout(function () { waitForGrid(n + 1); }, 500);
  }

  window.__dgxModelCard = {
    api: API, refresh: tick,
    destroy: function () {
      if (timer) clearTimeout(timer);
      if (observer) observer.disconnect();
      var n = document.getElementById(MOUNT_ID);
      if (n && n.parentElement) n.parentElement.removeChild(n);
      delete window.__dgxModelCard;
    }
  };

  waitForGrid(0);
})();
