# Changelog

## 2.9.0 — 2026-09-09

- **A failed load can say why.** llama-swap v251 discards the upstream's
  stderr — `/logs` carries proxy lines only, and `/logs/upstream`,
  `/logs/upstream/<model>`, `/logs/proxy` and `/api/logs/upstream` are all 404
  — so every start failure reads `upstream command exited prematurely` and
  nothing else. Out of memory, a missing file and an unsupported projector are
  indistinguishable, and the message sends you to the config when the machine
  was the problem.
- `packaging/llama-server-logged` tees llama-server's stderr per model. Point
  the `${server}` macro at it; nothing else changes, and there is no runtime
  cost.
- `load_failed` events and the card's error line now carry the last upstream
  error lines. `GET /api/upstream-log?model=<id>` returns the tail of the most
  recent start.
- Without the wrapper the tail is empty and nothing behaves differently.

## 2.5.0 — 2026-09-09
- **Keep warm.** A per-model toggle that refreshes the model's TTL before it expires, and reloads it if it has been evicted — so the cost of a cold load is paid on a schedule instead of on your next prompt. At most one model at a time, because llama-swap's `large` group is exclusive and two warm large models would evict each other in a loop.
  - It obeys the same rules as everything else: **Blocked** stops a reload but not a ping (pinging a resident model allocates nothing), and the memory reserve applies to a reload exactly as to any other load. Verified live — blocked with `reason=hold` while loading was Blocked, then `keepwarm_reload` → ready once allowed.
- **Wider dashboard.** The stock shell is capped at 1140 px; the card now widens it to `min(96vw, 1800px)` through a single injected stylesheet, removed again by `destroy()`. No NVIDIA file is touched, and turning the userscript off restores the stock layout exactly. Set `WIDEN_DASHBOARD = false` at the top of `card.js` to leave it alone.
  - Targeted with an attribute selector — `[class~="max-w-[1140px]"]` — rather than a class selector, because the bracketed Tailwind class needs CSS identifier escaping that is easy to get wrong through two layers of quoting. Measured at 1920 px: container 1140 → 1800 px, beating Tailwind's own rule.
- **Two-column body when there is room.** Memory and *Load a model* sit side by side on a wide screen and stack on a narrow one, using `flex-wrap` with a `320px` basis — no media query, no measuring, no layout code.
- `tools/check-identifiers.js`: resolves bare upper-case identifiers. `node --check` passes a file that references an undefined constant, and when that throws inside a promise chain it surfaces as something unrelated.


## 2.4.0 — 2026-09-09
- **Real load progress.** llama-swap reports nothing between launching an upstream and its health check passing, so progress is read from the upstream `llama-server` process itself — GPU allocation where available, resident memory as the early fallback — against the GGUF's own size. Shown as a bar with `loaded / total GiB` and an ETA derived from the *observed* rate on this machine, not a baked-in constant. Verified live: 48.8 % at t+5 s, 99.9 % at t+10 s, ready at t+15 s.
- **Tool-capability badge.** On first load of a model its chat template is fetched from `/props` and tested for `tools` / `tool_calls`, then cached in state. Models are badged `tools ✓` / `tools ✗` in the dropdown and beside the loaded model. This makes visible a failure that is otherwise silent: a GGUF can carry a template with no tool block and answer in prose instead of calling tools, even with `tool_choice="required"`.
  - The badge is a **template check**, not a behavioural one, and the tooltip says so. A template can declare tools while the model still emits them malformed.


## 2.3.1 — 2026-09-09
- **Name the client, don't just record it.** 2.3.0 captured the requesting `User-Agent` and never displayed it. The card now reads *"Last asked for by a client: qwen3-coder-next-80b · Continue (OpenAI JS SDK) · 41 s ago · triggered a load"*, with the raw User-Agent kept in the tooltip.
- Labels derived from the User-Agents this stack actually produces — Continue.dev identifies as `OpenAI/JS`, since its OpenAI provider uses the JS SDK. Unknown agents degrade to their first path segment rather than being dropped.
- The event log names the client too, and gained `load_requested` so a load can be attributed to whoever caused it, not only observed after the fact.


## 2.3.0 — 2026-09-09
- **Memory attribution.** The card names what is holding the memory, via `nvidia-smi --query-compute-apps` — on unified memory that is the only view that sees it. A job holding 74 GiB shows ~2.6 GiB of RSS and nothing in cgroup accounting. Holders are listed under the memory bar, tagged `model` or `other`, and the largest non-model holder is named in the refusal itself.
- **Held outside llama-swap** is stated explicitly. The guard cannot reclaim memory taken by something it does not manage, and the card now says so instead of leaving it to be inferred.
- **Hold and headroom no longer mask each other.** They are independent conditions; both are evaluated and both reported (`blockers: ["hold","headroom"]`). Previously Hold short-circuited the check, hiding whether the model would also have failed to fit.
- **Event log.** `$STATE_DIRECTORY/events.jsonl` records load start/ready with real durations, unloads, evictions, refusals with reason and requesting client, and hold changes. A background watcher diffs llama-swap's resident set, so loads caused by *other* clients are recorded too. Exposed at `GET /api/events?n=` and shown as "Recent activity" in the log drawer.
- Gate records the requesting `User-Agent`, so the card can say who asked, not just what was asked for.


## 2.2.1 — 2026-09-09
- Renamed every user-visible identifier from the historical `nemotron-card` to `dgx-model-card`: `Server:` headers, the refusal message, the startup line, the injected mount and log element ids, and the console handle **`window.__nemotronCard` -> `window.__dgxModelCard`**.
- Refusal text now names the actual control: *"Set automatic loading to Allowed on the Local models card"*.
- `NC_MODEL` now defaults to empty (first model llama-swap reports) instead of a model specific to the author's box.
- No behavioural change. Reference deployment migrated with `install.sh`, state carried across.

## 2.2.0 — 2026-09-07
- Rebuilt the card as three labelled blocks: **Loaded now / Memory / Load a model**, so status and intent are never the same widget.
- Model selector annotates every option with size and whether it fits.
- One plain sentence states what Start will do, including which model it evicts.
- Memory bar with the selected model's requirement marked on the same scale; reserve is an adjustable stepper.
- Reports the last model a client asked for, read from the gate's own traffic.
- `Hold` became an `Allowed | Blocked` control with its real scope stated.

## 2.1.0 — 2026-09-05
- Added the enforcing gate: a reverse proxy in front of llama-swap that refuses blocked or oversized loads for every client, fails open on anything undetermined, and streams responses without buffering (measured frame-for-frame identical to a direct connection).

## 2.0.0 — 2026-09-05
- Generalised from one model to every model llama-swap knows about.
- Added the memory headroom guard with exclusive-group eviction accounting.
- Added the Hold switch and persistent state.

## 1.0.0 — 2026-09-05
- Single-model start/stop card, injected into the DGX Dashboard, no NVIDIA files modified.
