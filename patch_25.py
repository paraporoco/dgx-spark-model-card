#!/usr/bin/env python3
"""2.4.0 -> 2.5.0: keep-warm (#3), wider layout, two-column body."""
import sys
P, C = sys.argv[1], sys.argv[2]

s = open(P, encoding="utf-8").read()


def sub(old, new):
    global s
    assert old in s, "py anchor missing: " + old[:70]
    s = s.replace(old, new, 1)


sub('VERSION = "2.4.0"', 'VERSION = "2.5.0"')

sub('''_state = {"selected": DEFAULT_MODEL, "hold": False, "margin_gib": DEFAULT_MARGIN_GIB,
          "tools": {}}''',
    '''# warm: at most ONE model. llama-swap's large group is exclusive, so keeping two
# large models warm would just make them evict each other in a loop.
_state = {"selected": DEFAULT_MODEL, "hold": False, "margin_gib": DEFAULT_MARGIN_GIB,
          "tools": {}, "warm": None}''')

sub('''            for k in ("selected", "hold", "margin_gib", "tools"):''',
    '''            for k in ("selected", "hold", "margin_gib", "tools", "warm"):''')

# ---------------------------------------------------------------- keep-warm loop
sub('''def _watch_resident():''',
    '''def _keep_warm():
    """Refresh a model's TTL before it expires, and bring it back if it is gone.

    Two rules it must not break:
      * Hold means no NEW loads. Pinging a model that is already resident
        allocates nothing, so that continues; reloading an evicted one does not.
      * The headroom guard applies to a reload exactly as it does to any other
        load. Keep-warm must never be the thing that OOMs the box.
    """
    last_ping = {}
    while True:
        time.sleep(20)
        try:
            target = get_state().get("warm")
            if not target:
                continue
            try:
                _, r = swap_json("/running", timeout=6)
                running = {x.get("model"): x for x in r.get("running", [])}
            except Exception:
                continue

            entry = running.get(target)
            if entry and (entry.get("state") == "ready"):
                ttl = entry.get("ttl") or 0
                if ttl <= 0:
                    continue                      # no expiry to defend against
                due = max(30, ttl - 120)
                if time.time() - last_ping.get(target, 0) < due:
                    continue
                try:
                    swap_get("/upstream/%s/health" % target, timeout=30)
                    last_ping[target] = time.time()
                    log_event("keepwarm_ping", model=target, ttl=ttl)
                except Exception as e:  # noqa: BLE001
                    log_event("keepwarm_ping_failed", model=target, error=str(e)[:80])
                continue

            if entry:
                continue                          # mid-load; leave it alone

            # Not resident. Reload only if the same rules that govern any other
            # load allow it.
            ok, reason, detail = evaluate_load(target, list(running))
            if not ok:
                if time.time() - last_ping.get("_blocked:" + target, 0) > 600:
                    last_ping["_blocked:" + target] = time.time()
                    log_event("keepwarm_blocked", model=target, reason=reason,
                              detail=detail or None)
                continue
            log_event("keepwarm_reload", model=target)
            _set_pending("start", target)
            threading.Thread(target=_load_worker, args=(target,), daemon=True).start()
            last_ping[target] = time.time()
        except Exception as e:  # noqa: BLE001
            print("keep-warm loop error: %s" % e, flush=True)


def _watch_resident():''')

sub('''    threading.Thread(target=_watch_resident, daemon=True).start()''',
    '''    threading.Thread(target=_watch_resident, daemon=True).start()
    threading.Thread(target=_keep_warm, daemon=True).start()''')

# ---------------------------------------------------------------- status + endpoint
sub('''        "hold": st["hold"],''',
    '''        "hold": st["hold"],
        "warm": st.get("warm"),''')

sub('''        if p == "/api/margin":''',
    '''        if p == "/api/warm":
            mid = body.get("model")
            enabled = bool(body.get("enabled", True))
            if not enabled:
                set_state(warm=None)
                log_event("keepwarm", enabled=False)
                return self._json(200, {"warm": None})
            _, models, _ = engine_state()
            if mid not in [m.get("id") for m in models]:
                return self._json(400, {"error": "unknown model", "model": mid})
            set_state(warm=mid)
            log_event("keepwarm", enabled=True, model=mid)
            return self._json(200, {"warm": mid})

        if p == "/api/margin":''')

open(P, "w", encoding="utf-8").write(s)
print("model_card.py -> 2.5.0")

# ---------------------------------------------------------------- card.js
s = open(C, encoding="utf-8").read()


def csub(old, new):
    global s
    assert old in s, "js anchor missing: " + old[:70]
    s = s.replace(old, new, 1)


csub('/* dgx-model-card v2.4 —', '/* dgx-model-card v2.5 —')

# --- widen the dashboard shell, and let the card body reflow to two columns ---
csub('''  var MOUNT_ID = "dgx-model-card-root";''',
    '''  var MOUNT_ID = "dgx-model-card-root";
  var STYLE_ID = "dgx-model-card-style";

  // Set to false to leave the dashboard's own 1140px shell alone.
  var WIDEN_DASHBOARD = true;

  // One stylesheet, injected once, removed by destroy(). It widens NVIDIA's
  // container at runtime -- no NVIDIA file is modified, and turning the script
  // off restores the stock layout exactly.
  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var css = "";
    if (WIDEN_DASHBOARD) {
      css += '.max-w-\\\\[1140px\\\\]{max-width:min(96vw,1800px)!important;}';
    }
    // The card's two middle blocks sit side by side when there is room and
    // stack when there is not. No media query, no measuring, no layout code:
    // flex-wrap with a sane basis does the whole job.
    css += '#' + MOUNT_ID + ' .dmc-split{display:flex;flex-wrap:wrap;gap:20px 28px;}';
    css += '#' + MOUNT_ID + ' .dmc-split > *{flex:1 1 320px;min-width:0;}';
    var el2 = document.createElement("style");
    el2.id = STYLE_ID;
    el2.textContent = css;
    (document.head || document.documentElement).appendChild(el2);
  }''')

csub('''  function render(st) {''',
    '''  function render(st) {
    injectStyle();''')

# put Memory and Load a model into the reflowing row
csub('''        loadedNow(st),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        memoryBlock(st, sel),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        loadBlock(st, sel),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        autoLoadBlock(st),''',
    '''        loadedNow(st),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        el("div", { class: "dmc-split" }, [memoryBlock(st, sel), loadBlock(st, sel)]),
        el("div", { style: "height:1px;background:" + LINE + ";" }),
        autoLoadBlock(st),''')

# let the card use the extra width when it has it
csub('''      style: "display:flex;flex-direction:column;gap:20px;padding:24px;border-radius:8px;" +
             "flex:1 1 0%;min-width:min(100%,520px);"''',
    '''      style: "display:flex;flex-direction:column;gap:20px;padding:24px;border-radius:8px;" +
             "flex:1 1 560px;min-width:min(100%,520px);"''')

# --- keep-warm control, next to the model it applies to ---------------------
csub('''    return el("div", { style: "display:flex;flex-direction:column;gap:8px;" }, [
      heading("Load a model"),
      select,
      el("div", { class: LBL, style: "color:" + planColor + ";word-break:break-word;" }, [plan])
    ]);''',
    '''    // Keep-warm defends against the idle TTL: the cost of a cold load is paid
    // on a schedule instead of on the next prompt.
    var warmOn = st.warm === st.selected;
    var warmBox = el("label", {
      style: "display:flex;align-items:center;gap:8px;cursor:pointer;",
      title: "Refreshes this model's TTL before it expires, and reloads it if it " +
             "gets evicted. Never overrides Blocked or the memory reserve."
    }, [
      el("input", {
        type: "checkbox",
        checked: warmOn ? "checked" : null,
        style: "accent-color:#76b900;width:15px;height:15px;cursor:pointer;",
        onchange: function (ev) {
          var on = ev.target.checked;
          busy = true; refresh();
          post("/api/warm", { model: st.selected, enabled: on })
            .catch(function () {}).then(function () { busy = false; tick(); });
        }
      }),
      el("span", { class: LBL }, ["Keep warm"]),
      el("span", { class: LBL, style: DIM },
         [st.warm && st.warm !== st.selected ? "(currently: " + st.warm + ")" : ""])
    ]);

    return el("div", { style: "display:flex;flex-direction:column;gap:8px;" }, [
      heading("Load a model"),
      select,
      el("div", { class: LBL, style: "color:" + planColor + ";word-break:break-word;" }, [plan]),
      warmBox
    ]);''')

# destroy() must take the stylesheet with it
csub('''      var n = document.getElementById(MOUNT_ID);
      if (n && n.parentElement) n.parentElement.removeChild(n);
      delete window.__dgxModelCard;''',
    '''      var n = document.getElementById(MOUNT_ID);
      if (n && n.parentElement) n.parentElement.removeChild(n);
      var stl = document.getElementById(STYLE_ID);
      if (stl && stl.parentElement) stl.parentElement.removeChild(stl);
      delete window.__dgxModelCard;''')

open(C, "w", encoding="utf-8").write(s)
print("card.js -> 2.5")
