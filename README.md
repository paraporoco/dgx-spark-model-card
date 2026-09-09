# DGX Spark model card

A **Local models** card for the NVIDIA DGX Dashboard: pick a model, see what is loaded, and refuse loads that will not fit in memory — without modifying a single NVIDIA file.

Built and running on an ASUS Ascent GX10 (GB10 Grace Blackwell, 128 GB unified memory, Ubuntu 24.04 arm64) serving models through [llama-swap](https://github.com/mostlygeek/llama-swap) and llama.cpp.

```
┌──────────────────────────────────────────────────────────┐
│ Local models                                  [ Serving ] │
│ llama-swap · 7 available · one large model at a time      │
├──────────────────────────────────────────────────────────┤
│ LOADED NOW                                                │
│  ● Nemotron-3-Super-120B Q4_K                             │
│    65.1 GiB · large · unloads after 30 min idle           │
│  Last asked for by a client: nemotron-3-super-120b        │
│  · 4 min ago · already loaded                             │
├──────────────────────────────────────────────────────────┤
│ MEMORY                                                    │
│  ███████████████████░░░░░░  88.3 used · 33.3 free · 121.6 │
│                             Keep free:  [−] 10 GiB [+]    │
├──────────────────────────────────────────────────────────┤
│ LOAD A MODEL                                              │
│  [ Qwen3-Coder-Next 80B — 48.2 GiB — won't fit        ▾ ] │
│  Not enough memory: needs 58.2 GiB free (48.2 model +     │
│  10 reserve), 33.3 GiB available.                         │
├──────────────────────────────────────────────────────────┤
│ AUTOMATIC LOADING              [ Allowed | Blocked ]      │
│  A request from your editor may load a model. Loads that  │
│  do not fit in memory are refused either way.             │
├──────────────────────────────────────────────────────────┤
│ Show log                            [Unload all] [Start]  │
└──────────────────────────────────────────────────────────┘
```

---

## Why

The DGX Dashboard ships two cards: GPU telemetry and JupyterLab. If you serve local models on the box, the thing you actually want to see and control is not there.

More importantly, on a **unified-memory** machine the models and everything else compete for the same pool. Load a 65 GiB model while a fine-tuning job is running and you do not get a slow machine, you get an OOM. This card exists to make that impossible by accident.

## What it does

- **Model selector** across every model llama-swap knows about, each annotated with size and whether it currently fits.
- **Loaded now** as a distinct section — status is never the same widget as intent.
- **Memory bar** with the selected model's requirement marked on the same scale, and an adjustable reserve.
- **Plain-language plan**: one sentence saying exactly what Start will do, including which model it will evict.
- **Load gate** — an optional reverse proxy in front of llama-swap that refuses loads which are blocked or which will not fit, for *every* client, not just this card.
- **Last asked for by a client** — read from the gate's own traffic, so you can see what your editor is about to load.
- **Memory attribution** — names the processes holding GPU memory, and marks how much of it is held *outside* llama-swap where the guard cannot reclaim it.
- **Event log** — loads with real durations, evictions, refusals with reason and requester, hold changes; recorded even when another client caused them.

## The compatibility rule

**Nothing under `/opt/nvidia/` is touched.** No file patched, no package modified, no asset replaced.

The dashboard frontend is a React SPA compiled into a 32 MB Go binary; there is no plugin API and no asset directory. So the card is injected at runtime by a userscript, and all its logic is served by a sidecar you control. After install, `dpkg -V dgx-dashboard` is still clean and both NVIDIA binaries still hash-match.

A dashboard upgrade can move the card or drop it. It cannot break the dashboard, because the dashboard never knows the card is there.

## Requirements

- NVIDIA DGX Dashboard (developed against **0.29.2-2**)
- [llama-swap](https://github.com/mostlygeek/llama-swap) on loopback (developed against **v251**)
- Python 3.10+ — **standard library only**, no pip, no venv
- A userscript manager (Violentmonkey / Tampermonkey) in whichever browser you open the dashboard with

## Install

```bash
git clone https://github.com/paraporoco/dgx-spark-model-card
cd dgx-spark-model-card
sudo ./install.sh          # --user <you> --port 8110 --gate-port 8111 --margin 10
```

Then install `userscript/dgx-model-card.user.js` in your userscript manager and open the dashboard.

The sidecar binds `127.0.0.1` only. To reach it from another machine, forward both ports:

```bash
ssh -L 11000:127.0.0.1:11000 -L 8110:127.0.0.1:8110 you@spark
```

**On a DGX Spark reached through NVIDIA Sync**, do it properly instead — Sync's app list is a user-owned config on the Spark, editable through its own CLI:

```bash
nvsync config read  <alias>          # JSON array of {id, port, name, url, autoOpen, shown}
nvsync config write <alias>          # add an entry for port 8110, then reconnect
```

Reconnecting makes Sync tunnel 8110 and show the card as a tile of its own — which also makes the injected card work, since both need the same tunnel.

## The load gate

By default the sidecar also listens on `:8111` and proxies to llama-swap. Point your reverse proxy at `:8111` instead of llama-swap directly and every client is governed:

```caddy
:11436 {
    @unauthorized not header Authorization "Bearer YOUR_TOKEN_HERE"
    respond @unauthorized 401
    reverse_proxy 127.0.0.1:8111     # the gate, not llama-swap
}
```

Rules, in order:

1. A request naming a model that is **already loaded** always passes. Serving a resident model allocates no new weights, so a warm session keeps working even while loading is Blocked.
2. A request that would **start** a model is checked against Blocked and against free memory.
3. Anything it cannot determine — unparseable body, no `model` field, llama-swap unreachable, internal error — **passes**. The gate exists to stop loads it is confident about, not to become a new way for inference to break.

A refusal is `503` with an OpenAI-shaped body so your editor shows something readable:

```json
{"error": {"message": "loading 'nemotron-3-super-120b' was refused -- needs 75.1 GiB free
                       (65.1 model + 10.0 margin), 32.9 GiB would be free",
           "type": "model_load_refused", "code": "headroom"}}
```

The request body is buffered — the gate has to read `model` — but **the response is not**. Upstream is read with `read1()` and relayed chunked. Measured against a direct connection to llama-swap:

| | through the gate | direct |
|---|---|---|
| TTFB | 0.0056 s | 0.0147 s |
| total | 7.34 s | 7.45 s |
| SSE frames | 153 | 153 |

First SSE frame at 0.07 s, last at 8.20 s across 170 frames — genuinely incremental, not buffered and dumped.

Set `NC_GATE=0` to run the card without the gate.

## The memory guard

Sizes come from `stat()` on the GGUF paths parsed out of llama-swap's config; group membership and `exclusive: true` come from the same parse. **A parse failure degrades to "size unknown", which makes the guard permissive rather than wrong.**

Loading a member of an exclusive group evicts the others first, so their bytes are credited back:

```
effective_available = MemAvailable + Σ(size of resident same-exclusive-group members)
required            = size(target) + margin
```

Verified with a 4.1 GiB model resident and 94.7 GiB free:

```
nemotron-3-super-120b  exclusive=True   → "33.6 GiB free after load"   # 94.7 + 4.1 − 65.1
nomic-embed-v2-moe     exclusive=False  → "93.8 GiB free after load"   # 94.7 − 0.9, no credit
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NC_HOST` / `NC_PORT` | `127.0.0.1` / `8110` | card + API |
| `NC_GATE` / `NC_GATE_PORT` | `1` / `8111` | enforcing proxy |
| `NC_SWAP_URL` | `http://127.0.0.1:8100` | llama-swap |
| `NC_SWAP_CONFIG` | `/etc/llama-swap/config.yaml` | where sizes and groups are read from |
| `NC_MODEL` | — | initial selection; the card's own choice persists over it |
| `NC_MARGIN_GIB` | `10` | memory kept free on top of the model |
| `NC_WEBROOT` | `<install>/web` | static files |

State (`selected`, `hold`, `margin_gib`) persists in `$STATE_DIRECTORY/state.json`.

## API

| | |
|---|---|
| `GET /api/status` | models with sizes, groups, per-model fit verdict, resident list, memory, hold, gate counters |
| `POST /api/select` | `{model}` |
| `POST /api/start` | `{model?}` — `202`, or `409` with the reason |
| `POST /api/stop` | unload everything |
| `POST /api/hold` | `{enabled}` |
| `POST /api/margin` | `{gib}` |
| `GET /api/logs?n=` | llama-swap log tail, own polling filtered out |
| `GET /api/events?n=` | event log: loads, unloads, refusals, hold changes |
| `GET /healthz` | liveness |

CORS is allow-listed to the dashboard origins; a foreign origin gets no `Access-Control-Allow-Origin` header at all.

## Security posture

- Binds loopback only. Both ports.
- **No privilege.** Start and stop are HTTP calls to llama-swap. No polkit rule, no sudoers entry, no `systemctl`, no root.
- systemd unit is hardened: `ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges`, `MemoryDenyWriteExecute`, empty `CapabilityBoundingSet`, `RestrictAddressFamilies=AF_INET AF_INET6`.
- No credentials in this repo. If you put the gate behind a bearer proxy, the token lives in your proxy config, not here.

## Known limits

- **Placement depends on the dashboard's DOM.** Developed against 0.29.2-2, where the card grid is `div.flex.gap-4.flex-wrap` and cards carry `data-testid="skele-panel"`. `findGrid()` tries three selectors, most specific first. A dashboard update can still move the card; it cannot break the dashboard. `packaging/99-dgx-model-card-apt-notice` prints a reminder to re-check when the package version changes.
- **The gate shares a process with the card.** A crash there takes the inference path with it. Mitigated by `Restart=on-failure`, fail-open on every decision error, and a one-line bypass — point your proxy back at llama-swap.
- **It reports what clients have asked for, not what they are configured to ask for.** The sidecar does not read your editor's config, by design.
- **The guard governs llama-swap, not the machine.** Anything else on the box — a training job, LM Studio, another runtime — can take memory the card can see but cannot prevent or reclaim. That memory is reported as *held outside llama-swap* precisely so the limit is visible rather than assumed away.
- The load-time estimate assumes ~8.8 GiB/min, measured on this hardware with a cold page cache. Yours will differ.

## Naming

The service was originally `nemotron-card`, when it controlled exactly one model. It grew into a general model selector, and everything was renamed to `dgx-model-card` in 2.2.1.

If you are upgrading from the old name: ports, the API and behaviour are unchanged — paths, the unit name and the console handle move. Stop and disable the old unit, run `install.sh`, then copy your old `state.json` into the new `StateDirectory` and restart so your selection, reserve and hold setting carry across. The console handle is now `window.__dgxModelCard`.

## Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with or endorsed by NVIDIA. "DGX" and "DGX Spark" are NVIDIA trademarks, used here only to say what this runs on.
