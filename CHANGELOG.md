# Changelog

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
