#!/usr/bin/env python3
"""
dgx-model-card — sidecar control service for the NVIDIA DGX Dashboard.

Controls llama-swap-managed models over loopback HTTP. Requires no root,
no polkit rule, no sudoers entry, and touches nothing under /opt/nvidia/.

v2 adds:
  * model selection across every model llama-swap knows about
  * a HOLD switch that blocks card-initiated loads
  * a memory headroom guard that refuses a load that would not fit

Scope limit, stated plainly: HOLD and the guard govern loads this service
initiates. llama-swap loads on demand for ANY client, so a request from
Continue (or curl) still loads a model regardless. See README notes.

Stdlib only. Python 3.10+ (Ubuntu 24.04 noble ships 3.12).
"""

import http.server
import json
import os
import re
import socketserver
import threading
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- config

SWAP = os.environ.get("NC_SWAP_URL", "http://127.0.0.1:8100").rstrip("/")
DEFAULT_MODEL = os.environ.get("NC_MODEL", "")   # empty -> first model llama-swap reports
LISTEN_HOST = os.environ.get("NC_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("NC_PORT", "8110"))
WEBROOT = os.environ.get(
    "NC_WEBROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))
SWAP_CONFIG = os.environ.get("NC_SWAP_CONFIG", "/etc/llama-swap/config.yaml")
STATE_DIR = os.environ.get("STATE_DIRECTORY", os.environ.get("NC_STATE_DIR", "/tmp"))
STATE_PATH = os.path.join(STATE_DIR.split(":")[0], "state.json")

# Headroom kept free on top of the model's own bytes, in GiB. The GGUF is
# mmap'd, so resident size tracks file size closely; this margin is for the
# KV cache, CUDA context, and whatever else is running on the box.
DEFAULT_MARGIN_GIB = float(os.environ.get("NC_MARGIN_GIB", "10"))

# Event log. Answers "what happened while I was away" — the gate already sees
# every load, eviction and refusal; without this it discards all of it.
EVENTS_MAX = int(os.environ.get("NC_EVENTS_MAX", "2000"))
MIN_HOLDER_BYTES = int(float(os.environ.get("NC_MIN_HOLDER_GIB", "0.5")) * (1 << 30))

ALLOWED_ORIGINS = {
    "http://localhost:11000", "http://127.0.0.1:11000",
    "http://localhost:11005", "http://127.0.0.1:11005",
    "http://localhost:8110", "http://127.0.0.1:8110",
}

VERSION = "2.3.0"

GATE_PORT = int(os.environ.get("NC_GATE_PORT", "8111"))
GATE_ENABLED = os.environ.get("NC_GATE", "1") not in ("0", "false", "no")
SWAP_HOST = os.environ.get("NC_SWAP_HOST", "127.0.0.1")
SWAP_PORT = int(os.environ.get("NC_SWAP_PORT", "8100"))
_gate_stats = {"passed": 0, "refused": 0, "last_refusal": None,
               "last_refused_model": None, "last_refused_at": None,
               "last_model": None, "last_model_at": None,
               "last_model_loaded": False, "last_client": None}
STARTED_AT = time.time()

# ---------------------------------------------------------------- state

_lock = threading.Lock()
_pending = {"action": None, "model": None, "since": 0.0, "error": None}
_state = {"selected": DEFAULT_MODEL, "hold": False, "margin_gib": DEFAULT_MARGIN_GIB}


def load_state():
    try:
        with open(STATE_PATH) as fh:
            data = json.load(fh)
        with _lock:
            for k in ("selected", "hold", "margin_gib"):
                if k in data:
                    _state[k] = data[k]
    except Exception:
        pass


def save_state():
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with _lock:
            snapshot = dict(_state)
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=1)
        os.replace(tmp, STATE_PATH)
    except Exception as e:  # noqa: BLE001
        print("state save failed: %s" % e, flush=True)


def get_state():
    with _lock:
        return dict(_state)


def set_state(**kw):
    with _lock:
        _state.update(kw)
    save_state()


def _set_pending(action, model=None, error=None):
    with _lock:
        _pending.update({"action": action, "model": model,
                         "since": time.time(), "error": error})


def _get_pending():
    with _lock:
        return dict(_pending)


# ------------------------------------------------- llama-swap config parsing

_cfg_cache = {"mtime": 0.0, "paths": {}, "groups": {}, "exclusive": set()}


def parse_swap_config():
    """Read model GGUF paths and group membership from llama-swap's config.

    Deliberately a line scanner, not a YAML parser: stdlib has no YAML and
    this config's shape is known and stable. Any parse failure degrades to
    'size unknown', which makes the guard permissive rather than wrong.
    """
    try:
        st = os.stat(SWAP_CONFIG)
    except OSError:
        return _cfg_cache
    if st.st_mtime == _cfg_cache["mtime"]:
        return _cfg_cache

    paths, groups, exclusive = {}, {}, set()
    section = None          # 'models' | 'groups'
    cur_model = None
    cur_group = None
    in_members = False

    try:
        with open(SWAP_CONFIG) as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                indent = len(line) - len(line.lstrip())

                if indent == 0:
                    section = line.split(":")[0].strip()
                    cur_model = cur_group = None
                    in_members = False
                    continue

                if section == "models":
                    m = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
                    if m:
                        cur_model = m.group(1)
                        continue
                    if cur_model:
                        p = re.search(r"-m\s+(\S+\.gguf)", line)
                        if p:
                            paths[cur_model] = p.group(1)

                elif section == "groups":
                    m = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
                    if m:
                        cur_group = m.group(1)
                        groups.setdefault(cur_group, [])
                        in_members = False
                        continue
                    if cur_group:
                        if re.match(r"^\s+exclusive:\s*true\s*$", line):
                            exclusive.add(cur_group)
                        elif re.match(r"^\s+members:\s*$", line):
                            in_members = True
                        elif in_members:
                            mm = re.match(r"^\s+-\s+([A-Za-z0-9._-]+)\s*$", line)
                            if mm:
                                groups[cur_group].append(mm.group(1))
                            else:
                                in_members = False
    except Exception as e:  # noqa: BLE001
        print("swap config parse failed: %s" % e, flush=True)
        return _cfg_cache

    _cfg_cache.update({"mtime": st.st_mtime, "paths": paths,
                       "groups": groups, "exclusive": exclusive})
    return _cfg_cache


def model_size_bytes(model_id):
    path = parse_swap_config()["paths"].get(model_id)
    if not path:
        return None
    try:
        return os.stat(path).st_size
    except OSError:
        return None


def group_of(model_id):
    for g, members in parse_swap_config()["groups"].items():
        if model_id in members:
            return g
    return None


def is_exclusive(group):
    return group in parse_swap_config()["exclusive"]


# ---------------------------------------------------------------- llama-swap client

def swap_get(path, timeout=8):
    req = urllib.request.Request(SWAP + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def swap_json(path, timeout=8):
    status, body = swap_get(path, timeout=timeout)
    return status, json.loads(body.decode("utf-8", "replace"))


def engine_state():
    models, running = [], []
    try:
        _, m = swap_json("/v1/models", timeout=5)
        models = m.get("data", [])
    except Exception:
        return False, [], []
    try:
        _, r = swap_json("/running", timeout=5)
        running = r.get("running", [])
    except Exception:
        pass
    return True, models, running


# ---------------------------------------------------------------- memory guard

GIB = 1073741824


_holders_cache = {"at": 0.0, "rows": []}


def gpu_holders():
    """Processes holding GPU memory, largest first.

    On GB10 this is the ONLY view that sees it: a job holding 74 GiB of unified
    memory shows ~2.6 GiB of RSS and nothing in cgroup accounting. Without this
    the card can report "not enough memory" but never say what took it.
    """
    now = time.time()
    if now - _holders_cache["at"] < 5:
        return _holders_cache["rows"]

    rows = []
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6).stdout
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                pid, name, mib = int(parts[0]), parts[1], float(parts[2])
            except ValueError:
                continue
            b = int(mib * 1048576)
            if b < MIN_HOLDER_BYTES:
                continue
            rows.append({"pid": pid, "name": os.path.basename(name),
                         "bytes": b, "ours": False, "cmd": "", "elapsed": ""})
    except Exception:
        return _holders_cache["rows"]

    # Which of these are llama-swap's own upstreams? Anything else is memory the
    # guard cannot reclaim, and the card must say so rather than imply otherwise.
    for r in rows:
        try:
            with open("/proc/%d/cmdline" % r["pid"], "rb") as fh:
                cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            r["cmd"] = cmd[:160]
            r["ours"] = "llama-server" in cmd
            if not r["name"] or r["name"] in ("python", "python3"):
                for tok in cmd.split():
                    if tok.endswith(".py"):
                        r["name"] = os.path.basename(tok)
                        break
            with open("/proc/%d/stat" % r["pid"]) as fh:
                pass
        except Exception:
            pass

    rows.sort(key=lambda x: -x["bytes"])
    _holders_cache.update({"at": now, "rows": rows})
    return rows


def held_outside():
    """(bytes, largest_row) held by processes that are not llama-swap upstreams."""
    rows = [r for r in gpu_holders() if not r["ours"]]
    return sum(r["bytes"] for r in rows), (rows[0] if rows else None)


def mem_info():
    try:
        vals = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                vals[k] = int(v.split()[0]) * 1024  # bytes
        return {"total": vals["MemTotal"], "available": vals["MemAvailable"]}
    except Exception:
        return {"total": None, "available": None}


EVENTS_PATH = os.path.join(STATE_DIR.split(":")[0], "events.jsonl")
_events_lock = threading.Lock()


def log_event(kind, **fields):
    rec = {"ts": int(time.time()), "kind": kind}
    rec.update({k: v for k, v in fields.items() if v is not None})
    line = json.dumps(rec, separators=(",", ":"))
    try:
        with _events_lock:
            os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
            with open(EVENTS_PATH, "a") as fh:
                fh.write(line + "\n")
            # cheap rotation: only when the file is plausibly large
            if os.path.getsize(EVENTS_PATH) > EVENTS_MAX * 200:
                with open(EVENTS_PATH) as fh:
                    keep = fh.readlines()[-EVENTS_MAX:]
                tmp = EVENTS_PATH + ".tmp"
                with open(tmp, "w") as fh:
                    fh.writelines(keep)
                os.replace(tmp, EVENTS_PATH)
    except Exception as e:  # noqa: BLE001
        print("event log write failed: %s" % e, flush=True)
    print("EVENT %s %s" % (kind, line), flush=True)


def read_events(n=50):
    try:
        with open(EVENTS_PATH) as fh:
            lines = fh.readlines()[-n:]
        out = []
        for l in lines:
            try:
                out.append(json.loads(l))
            except Exception:
                pass
        return out
    except FileNotFoundError:
        return []
    except Exception:
        return []


def evaluate_load(model_id, running_ids):
    """Can this model be loaded right now? Returns (ok, reason, detail).

    Accounts for eviction: loading a member of an exclusive group unloads the
    other members first, so their bytes come back to us before the new model
    is mapped.
    """
    st = get_state()
    held = st["hold"]

    size = model_size_bytes(model_id)
    mem = mem_info()
    if size is None or mem["available"] is None:
        if held:
            return False, "hold", "loading is on hold"
        return True, "unknown", "size or memory unknown — guard not enforced"

    grp = group_of(model_id)
    reclaimed = 0
    if grp and is_exclusive(grp):
        members = set(parse_swap_config()["groups"].get(grp, []))
        for rid in running_ids:
            if rid != model_id and rid in members:
                reclaimed += model_size_bytes(rid) or 0

    margin = int(st["margin_gib"] * GIB)
    effective = mem["available"] + reclaimed
    needed = size + margin

    fits = effective >= needed
    short = ("needs %.1f GiB (model %.1f + %.1f margin), %.1f GiB would be free"
             % (needed / GIB, size / GIB, margin / GIB, effective / GIB))

    # Name what is holding the memory. A refusal without this is true and
    # useless: the next question is always "taken by what?".
    outside, biggest = held_outside()
    if not fits and biggest is not None:
        short += (". Largest holder: %s (pid %d) — %.1f GiB"
                  % (biggest["name"], biggest["pid"], biggest["bytes"] / GIB))

    # Hold and headroom are independent. Report both instead of letting the
    # first one hide the state of the machine.
    if held and not fits:
        return False, "hold+headroom", "loading is on hold. It would also not fit: " + short
    if held:
        return False, "hold", "loading is on hold"
    if not fits:
        return False, "headroom", short
    return True, "ok", "%.1f GiB free after load" % ((effective - size) / GIB)


# ---------------------------------------------------------------- workers

def _load_worker(model_id):
    t0 = time.time()
    log_event("load_start", model=model_id,
              size_gib=round((model_size_bytes(model_id) or 0) / GIB, 1))
    try:
        try:
            swap_get("/upstream/%s/health" % model_id, timeout=3600)
            _set_pending(None)
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                _set_pending(None)
                return
        except urllib.error.URLError:
            pass
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }).encode()
        req = urllib.request.Request(
            SWAP + "/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3600).read()
        _set_pending(None)
    except Exception as e:  # noqa: BLE001
        log_event("load_failed", model=model_id,
                  duration_s=int(time.time() - t0), error="%s: %s" % (type(e).__name__, e))
        _set_pending(None, error="%s: %s" % (type(e).__name__, e))


def _unload_worker():
    try:
        swap_get("/unload", timeout=120)
        log_event("unload_requested")
        _set_pending(None)
    except Exception as e:  # noqa: BLE001
        _set_pending(None, error="%s: %s" % (type(e).__name__, e))


def _watch_resident():
    """Diff llama-swap's resident set so loads, evictions and unloads are recorded
    even when another client caused them."""
    prev = None
    starts = {}
    while True:
        try:
            _, r = swap_json("/running", timeout=6)
            cur = {x.get("model"): (x.get("state") or "") for x in r.get("running", [])}
        except Exception:
            time.sleep(10)
            continue

        if prev is not None:
            for mid, stt in cur.items():
                if mid not in prev and stt != "ready":
                    starts[mid] = time.time()
                was = prev.get(mid)
                if stt == "ready" and was != "ready":
                    t0 = starts.pop(mid, None)
                    log_event("load_ready", model=mid,
                              duration_s=int(time.time() - t0) if t0 else None,
                              size_gib=round((model_size_bytes(mid) or 0) / GIB, 1))
            for mid in prev:
                if mid not in cur:
                    log_event("unloaded", model=mid,
                              replaced_by=(", ".join(k for k in cur if k not in prev) or None))
        prev = cur
        time.sleep(5)


# ---------------------------------------------------------------- status

def build_status():
    reachable, models, running = engine_state()
    pending = _get_pending()
    st = get_state()
    cfg = parse_swap_config()
    mem = mem_info()

    outside_bytes, _biggest = held_outside()
    running_by_id = {r.get("model"): r for r in running}
    running_ids = list(running_by_id)

    entries = []
    for m in models:
        mid = m.get("id")
        size = model_size_bytes(mid)
        grp = group_of(mid)
        run = running_by_id.get(mid)
        if run:
            state = run.get("state") or "unknown"
        elif pending["action"] == "start" and pending["model"] == mid:
            state = "starting"
        else:
            state = (m.get("status") or {}).get("value", "unknown")
        ok, reason, detail = evaluate_load(mid, running_ids)
        grp_members = set(parse_swap_config()["groups"].get(grp, [])) if grp else set()
        evicts = [r for r in running_ids
                  if r != mid and r in grp_members and grp and is_exclusive(grp)]
        reclaim = sum(model_size_bytes(r) or 0 for r in evicts)
        needed = (size + int(st["margin_gib"] * GIB)) if size else None
        effective = (mem["available"] + reclaim) if mem["available"] is not None else None
        entries.append({
            "needed_gib": round(needed / GIB, 1) if needed else None,
            "effective_gib": round(effective / GIB, 1) if effective is not None else None,
            "evicts": evicts,
            "id": mid,
            "name": m.get("name") or mid,
            "state": state,
            "group": grp,
            "exclusive": bool(grp and is_exclusive(grp)),
            "size_gib": round(size / GIB, 1) if size else None,
            "proxy": (run or {}).get("proxy"),
            "ttl": (run or {}).get("ttl"),
            "can_load": ok,
            "load_reason": reason,
            "load_detail": detail,
            "blockers": ([] if ok else reason.split("+")),
        })

    selected = st["selected"]
    if selected not in [e["id"] for e in entries] and entries:
        selected = DEFAULT_MODEL if any(e["id"] == DEFAULT_MODEL for e in entries) else entries[0]["id"]

    sel = next((e for e in entries if e["id"] == selected), None)
    if not reachable:
        sel_state = "engine-down"
    elif sel:
        sel_state = sel["state"]
    else:
        sel_state = "unknown"
    if pending["action"] == "stop":
        sel_state = "stopping"

    return {
        "version": VERSION,
        "engine": {"url": SWAP, "reachable": reachable},
        "selected": selected,
        "state": sel_state,
        "display_name": (sel or {}).get("name", selected),
        "proxy": (sel or {}).get("proxy"),
        "ttl": (sel or {}).get("ttl"),
        "models": entries,
        "resident": [
            {"id": r.get("model"), "name": r.get("name"), "state": r.get("state")}
            for r in running
        ],
        "hold": st["hold"],
        "margin_gib": st["margin_gib"],
        "config_seen": bool(cfg["paths"]),
        "pending": pending["action"],
        "pending_model": pending["model"],
        "last_error": pending["error"],
        "memory": {
            "total_gib": round(mem["total"] / GIB, 1) if mem["total"] else None,
            "available_gib": round(mem["available"] / GIB, 1) if mem["available"] else None,
            "held_outside_gib": round(outside_bytes / GIB, 1) if outside_bytes else 0,
            "holders": [
                {"pid": h["pid"], "name": h["name"], "gib": round(h["bytes"] / GIB, 1),
                 "ours": h["ours"], "cmd": h["cmd"]}
                for h in gpu_holders()
            ],
        },
        "gate": {
            "enabled": GATE_ENABLED,
            "port": GATE_PORT,
            "passed": _gate_stats["passed"],
            "refused": _gate_stats["refused"],
            "last_refusal": _gate_stats["last_refusal"],
            "last_refused_model": _gate_stats["last_refused_model"],
            "last_refused_at": _gate_stats["last_refused_at"],
            "last_model": _gate_stats["last_model"],
            "last_model_at": _gate_stats["last_model_at"],
            "last_model_loaded": _gate_stats["last_model_loaded"],
            "last_client": _gate_stats["last_client"],
        },
        "sidecar_uptime_s": int(time.time() - STARTED_AT),
        "ts": int(time.time()),
    }


# ---------------------------------------------------------------- http

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "dgx-model-card/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, indent=1))

    def _file(self, name, ctype):
        try:
            with open(os.path.join(WEBROOT, name), "rb") as fh:
                self._send(200, fh.read(), ctype)
        except FileNotFoundError:
            self._json(404, {"error": "not found", "path": name})

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if p == "/card.js":
            return self._file("card.js", "application/javascript; charset=utf-8")
        if p == "/api/status":
            return self._json(200, build_status())
        if p == "/api/logs":
            n = 200
            if "?" in self.path:
                from urllib.parse import parse_qs
                q = parse_qs(self.path.split("?", 1)[1])
                try:
                    n = max(1, min(2000, int(q.get("n", ["200"])[0])))
                except ValueError:
                    pass
            try:
                _, body = swap_get("/logs", timeout=10)
                lines = [l for l in body.decode("utf-8", "replace").splitlines()
                         if not NOISE.search(l)]
                return self._json(200, {"lines": lines[-n:]})
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": str(e), "lines": []})
        if p == "/api/events":
            n = 30
            if "?" in self.path:
                from urllib.parse import parse_qs
                q = parse_qs(self.path.split("?", 1)[1])
                try:
                    n = max(1, min(500, int(q.get("n", ["30"])[0])))
                except ValueError:
                    pass
            return self._json(200, {"events": read_events(n)})
        if p == "/healthz":
            return self._json(200, {"ok": True, "version": VERSION})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        body = self._body()

        if p == "/api/hold":
            enabled = bool(body.get("enabled"))
            set_state(hold=enabled)
            log_event("hold", enabled=enabled)
            return self._json(200, {"hold": enabled})

        if p == "/api/margin":
            try:
                g = float(body.get("gib"))
            except (TypeError, ValueError):
                return self._json(400, {"error": "gib must be a number"})
            set_state(margin_gib=max(0.0, min(64.0, g)))
            return self._json(200, {"margin_gib": get_state()["margin_gib"]})

        if p == "/api/select":
            mid = body.get("model")
            _, models, _ = engine_state()
            ids = [m.get("id") for m in models]
            if mid not in ids:
                return self._json(400, {"error": "unknown model", "known": ids})
            set_state(selected=mid)
            return self._json(200, {"selected": mid})

        if p == "/api/start":
            st = build_status()
            mid = body.get("model") or st["selected"]
            entry = next((e for e in st["models"] if e["id"] == mid), None)
            if entry is None:
                return self._json(400, {"error": "unknown model", "model": mid})
            if entry["state"] in ("ready", "starting"):
                return self._json(200, {"accepted": False,
                                        "reason": "already " + entry["state"],
                                        "model": mid})
            if not entry["can_load"]:
                return self._json(409, {"accepted": False,
                                        "reason": entry["load_reason"],
                                        "detail": entry["load_detail"],
                                        "model": mid})
            set_state(selected=mid)
            _set_pending("start", mid)
            threading.Thread(target=_load_worker, args=(mid,), daemon=True).start()
            return self._json(202, {"accepted": True, "action": "start", "model": mid})

        if p == "/api/stop":
            _set_pending("stop")
            threading.Thread(target=_unload_worker, daemon=True).start()
            return self._json(202, {"accepted": True, "action": "stop"})

        return self._json(404, {"error": "not found"})


NOISE = re.compile(r"(GET /(v1/models|running|logs|upstream/[^ ]+/health) HTTP).*Python-urllib")



# ---------------------------------------------------------------- enforcing gate

import http.client  # noqa: E402

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

# Paths that can cause llama-swap to start a model.
LOADING_PATHS = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings",
                 "/v1/rerank", "/v1/reranking")


def resident_ids():
    try:
        _, r = swap_json("/running", timeout=4)
        return [x.get("model") for x in r.get("running", [])]
    except Exception:
        return None      # unknown -> caller fails open


def gate_decision(path, body_bytes, client=None):
    """(allow, model_id, reason, detail).

    Fails OPEN on anything it cannot determine: an unparseable body, an
    unreachable llama-swap, an unknown model. The gate exists to stop loads
    we are confident about, not to become a new way for inference to break.
    """
    model = None
    p = path.split("?", 1)[0]

    if p.startswith("/upstream/"):
        parts = p.split("/", 3)
        if len(parts) > 2 and parts[2]:
            model = parts[2]
    elif p in LOADING_PATHS:
        if not body_bytes:
            return True, None, "no-body", ""
        try:
            model = json.loads(body_bytes.decode("utf-8", "replace")).get("model")
        except Exception:
            return True, None, "unparsed", ""

    if not model:
        return True, None, "no-model", ""

    _gate_stats["last_model"] = model
    _gate_stats["last_model_at"] = int(time.time())
    _gate_stats["last_client"] = client

    running = resident_ids()
    if running is None:
        return True, model, "engine-unknown", ""
    _gate_stats["last_model_loaded"] = model in running
    if model in running:
        # Already loaded. Serving it allocates no new weights, so hold and the
        # headroom guard do not apply -- a warm session keeps working.
        return True, model, "resident", ""

    ok, reason, detail = evaluate_load(model, running)
    return ok, model, reason, detail


class GateHandler(http.server.BaseHTTPRequestHandler):
    server_version = "dgx-model-card-gate/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                      # the proxied path stays quiet; refusals log below

    # -- helpers ---------------------------------------------------
    def _read_body(self):
        n = self.headers.get("Content-Length")
        if n:
            try:
                return self.rfile.read(int(n))
            except Exception:
                return b""
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            buf = b""
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()
                    break
                buf += self.rfile.read(size)
                self.rfile.readline()
            return buf
        return b""

    def _refuse(self, model, reason, detail):
        _gate_stats["refused"] += 1
        _gate_stats["last_refusal"] = detail or reason
        _gate_stats["last_refused_model"] = model
        _gate_stats["last_refused_at"] = int(time.time())
        log_event("refused", model=model, reason=reason, detail=detail or None,
                  client=(self.headers.get("User-Agent") or "")[:60] or None)
        msg = ("dgx-model-card: loading '%s' was refused -- %s. "
               "Set automatic loading to Allowed on the Local models card, "
               "or free memory, then retry."
               % (model, detail or reason))
        print("GATE REFUSED %s (%s): %s" % (model, reason, detail), flush=True)
        payload = json.dumps({
            "error": {"message": msg, "type": "model_load_refused",
                      "code": reason, "param": model}
        }).encode()
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Retry-After", "60")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except Exception:
            pass

    def _proxy(self, body):
        conn = http.client.HTTPConnection(SWAP_HOST, SWAP_PORT, timeout=3600)
        headers = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                continue
            headers[k] = v
        if body:
            headers["Content-Length"] = str(len(body))
        try:
            conn.request(self.command, self.path, body=body or None, headers=headers)
            up = conn.getresponse()
        except Exception as e:  # noqa: BLE001
            try:
                payload = json.dumps({"error": {
                    "message": "dgx-model-card gate: upstream llama-swap unreachable (%s)" % e,
                    "type": "upstream_unavailable"}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception:
                pass
            conn.close()
            return

        _gate_stats["passed"] += 1
        clen = up.getheader("Content-Length")
        try:
            self.send_response(up.status)
            for k, v in up.getheaders():
                if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            if clen is not None:
                self.send_header("Content-Length", clen)
                self.end_headers()
                remaining = int(clen)
                while remaining > 0:
                    chunk = up.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            else:
                # Unknown length: streaming (SSE). read1() returns as soon as
                # anything arrives, so tokens are relayed as they are produced
                # instead of waiting for a full buffer.
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = up.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # client went away mid-stream
        except Exception as e:  # noqa: BLE001
            print("gate relay error: %s" % e, flush=True)
        finally:
            conn.close()

    # -- verbs -----------------------------------------------------
    def _handle(self):
        body = self._read_body()
        try:
            allow, model, reason, detail = gate_decision(
                self.path, body, (self.headers.get("User-Agent") or "")[:60])
        except Exception as e:  # noqa: BLE001
            print("gate decision error (failing open): %s" % e, flush=True)
            allow, model, reason, detail = True, None, "error", ""
        if not allow:
            return self._refuse(model, reason, detail)
        self._proxy(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    load_state()
    parse_swap_config()
    srv = Server((LISTEN_HOST, LISTEN_PORT), Handler)
    st = get_state()
    threading.Thread(target=_watch_resident, daemon=True).start()
    log_event("service_start", version=VERSION, port=LISTEN_PORT)
    if GATE_ENABLED:
        gate_srv = Server((LISTEN_HOST, GATE_PORT), GateHandler)
        threading.Thread(target=gate_srv.serve_forever, daemon=True).start()
        print("gate listening on %s:%d -> %s:%d"
              % (LISTEN_HOST, GATE_PORT, SWAP_HOST, SWAP_PORT), flush=True)
    print("dgx-model-card %s on %s:%d  selected=%s  hold=%s  margin=%.1f GiB  swap=%s"
          % (VERSION, LISTEN_HOST, LISTEN_PORT, st["selected"], st["hold"],
             st["margin_gib"], SWAP), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
