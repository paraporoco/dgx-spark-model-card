# Findings

Things established while building this, with the evidence. Written down because each cost real time to discover and none of it is documented anywhere obvious.

Platform: ASUS Ascent GX10 (GB10 Grace Blackwell, 121.6 GiB unified, Ubuntu 24.04 arm64), DGX Dashboard 0.29.2-2, llama-swap v251, llama.cpp b10341.

---

## 1. The DGX Dashboard has no extension point

**[ESTABLISHED]** `dgx-dashboard` 0.29.2-2 ships two binaries and no assets:

```
$ dpkg -L dgx-dashboard | wc -l
41                    # ...and zero .js, .css or .html files
```

The frontend is a React Router 7 SPA (`isSpaMode: true`, 24 chunks) compiled **into** the 32 MB Go binary at `/opt/nvidia/dgx-dashboard-service/dashboard-service`. Routes are hardcoded Go handlers:

```
/api/login
/api/v1/{hostname, telemetry, jupyterlab, jupyterlab/stop,
         chrome/available, chrome/install, chrome/install/status,
         ota/status, update_reboot, updates/available, updates/list, logout}
```

The only mutable config the service reads is `ports.env` (24 bytes) and `jupyterlab_ports.yaml` — and the latter is a **username→port map for the one JupyterLab card**, not a card registry. Adding an entry provisions JupyterLab for another *user*; it does not add a card.

Privileged operations go over D-Bus to a root service (`com.nvidia.dgx.dashboard.admin1`) with PAM auth and a JWT. Login is `POST /api/login` with system credentials.

**Consequence:** anything in-dashboard must be injected at runtime, or you are patching NVIDIA's binary.

## 2. The card grid selectors are literal Tailwind, not hashed

**[ESTABLISHED for 0.29.2-2]** Rendered headless (`google-chrome --headless=new --dump-dom`; Chrome is already installed on the Spark) the real DOM is:

```html
<div class="pt-[120px] px-6 max-w-[1140px] mx-auto">
  <div class="flex flex-col gap-8">
    <div class="flex justify-between w-full">…header…</div>
    <div class="flex gap-4 flex-wrap">            ← the card grid
      <div … data-testid="skele-panel" …>         ← each card
```

Considerably more stable than a minified build usually is. `findGrid()` therefore tries, most specific first:

1. `[data-testid="skele-panel"]` → `parentElement`
2. `div.flex.gap-4.flex-wrap`
3. `[class*="max-w-"] .flex.flex-col.gap-8` → `lastElementChild`

## 3. The NVIDIA design system is available to injected code

**[ESTABLISHED]** The full Kaizen stylesheet is in the page: `nv-panel`, `nv-panel-header`, `nv-panel-footer`, `nv-button--kind-primary`, `nv-button--color-brand`, `nv-text--*`, `nv-tag--color-*`, `nv-status-indicator`, `nv-segmented-control-*` — all driven by CSS custom properties (`--background-color-surface-base`, `--text-color-feedback-success`, `--border-color-base`, …).

So an injected card can look native for free, and inherits the live theme. This card additionally inline-styles everything structural, so it degrades to plain-but-working if a class disappears.

**No `Content-Security-Policy` header is served**, which is what makes script injection possible at all.

## 4. NVFP4 is compiled natively for GB10 in stock llama.cpp

**[ESTABLISHED]** GB10 reports `compute_cap 12.1`. A locally built llama.cpp (b10341, `CMAKE_CUDA_ARCHITECTURES=121`) contains:

```
GGML_TYPE_NVFP4
tmpxft_…-6_mmq-instance-nvfp4.cudafe1.cpp      ← dedicated quantised matmul
_Z18quantize_mmq_nvfp4ILb0ELb0EE…              ← four template variants
```

and `cuobjdump -lelf libggml-cuda.so` reports **141 SASS entries, every one `sm_121a`** — the architecture-specific target that exposes Blackwell FP4 tensor-core instructions.

**[ASSUMED]** The payoff should be in **prefill, not decode**. Batch-1 decode is memory-bandwidth bound; FP4 tensor cores help where work is compute-bound, which is prompt processing. Expect first-token latency to improve more than tokens/sec.

**[CONTESTED]** llama.cpp discussion #22042 argues `block_nvfp4` does not fully represent the tensor without its F32 scale, that dequantisation is split between GGML and the caller, and that reordering `GGML_OP_MUL` against non-linearities yields *functionally incorrect* results. Dated May 2026 with no merged PRs at the time; the August build has merged kernels, so something landed since. **Test output quality, not only speed.**

## 5. GGUFs from the Ollama blob store can carry tool-less chat templates

**[ESTABLISHED]** Serving Ollama's blobs directly under llama.cpp with `--jinja`:

| Model | `tools` in embedded template | Tool call emitted |
|---|---|---|
| Nemotron-3-Super-120B | yes (10 770 chars) | yes |
| Devstral-24B | **no** (1 590 chars) | no — even with `tool_choice: "required"` |
| Mistral-7B | no | no |

Verified against the upstream `llama-server` directly, bypassing any proxy — so it is neither the router's fault nor llama.cpp's. Ollama supplies its own template at request time; that capability does **not** survive being served from the raw GGUF.

If a model tool-called under Ollama and stops under llama.cpp, this is why. Fix is `--chat-template-file`, not an engine change.

## 6. Ollama's blob store is a fragile backing store

**[ESTABLISHED]** A common migration is to symlink `/opt/models/*.gguf` at `/usr/share/ollama/.ollama/models/blobs/sha256-…`. Every blob starts with magic `47475546` = `GGUF`, so this is genuinely zero-copy.

But once `ollama.service` is disabled, nothing manages that store. A cleanup, a package purge, or a reinstall that garbage-collects unreferenced blobs takes out every model at once. Copy them out, or accept the dependency knowingly.

## 7. NVIDIA Sync has a first-class, user-owned extension point

**[ESTABLISHED]** NVIDIA Sync is an Electron shell over an SSH port-forwarder (`nvsync-<arch> connect <alias>`), forwarding the Spark's loopback dashboard `:11000` and per-user JupyterLab `:1100x` to the client's loopback.

Its app list lives **on the Spark**, at `~/.config/NVIDIA/Sync/config/custom.json`, mode 0600, owned by the user and **part of no NVIDIA package**:

```json
[{"id":"…","port":"12000","name":"Open WebUI","scriptContent":"",
  "autoOpen":true,"url":"http://localhost:12000/","interactive":false,"shown":true}]
```

Edited through `nvsync config read|write <alias>`. Optional per-port lifecycle scripts live at `~/.config/NVIDIA/Sync/bin/scripts/port-<port>.bash` and run for the lifetime of the tunnel.

Two practical notes:

- `autoOpen` is evaluated **at connect time**. A config written mid-session needs a reconnect.
- The CLI's `open` / `status` subcommands attach via `session/<alias>.socket`, which the **GUI's** connection does not create — so `nvsync open` fails with `daemon not running: socket doesn't exist` while the GUI is connected. Reconnect in the app instead.

## 8. Sizing the memory guard on unified memory

**[ESTABLISHED]** Measured on this box:

| | |
|---|---|
| Cold load, 4.1 GiB model | 8–14 s |
| Cold load, 65.1 GiB model | **7 min 24 s** (≈ 8.8 GiB/min) |
| Unloading a pinned 65 GiB service | 38 → 109 GiB available |

The GGUF is mmap'd, so resident size tracks file size closely and `stat()` is a good proxy for what a load will cost. The margin exists for KV cache, CUDA context, and whatever else shares the pool — which on a unified-memory box is *everything*.

Real instance during development: a fine-tuning job held 88 of 121 GiB with the GPU at 96 % and 3 GiB of swap in use. Nothing was loaded in llama-swap. Neither the 65 GiB nor the 48 GiB model could load. That is the case this guard exists for, and the card said so before anything was pressed.
