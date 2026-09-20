"""A local UI, served over loopback by the standard library.

No framework and no new dependencies: the page is one HTML string and the server
is `http.server`. It binds 127.0.0.1 by default, so nothing is reachable from
outside the machine; Docker passes --host 0.0.0.0 because the container needs to
accept the forwarded port, and the published port is what limits exposure there.

This is a development server. It is fine for a single person driving one browser
tab and is not intended to face a network.
"""

import base64
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import MAX_UPLOAD_BYTES, AnalysisService

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Floor plan to 2D layout</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --panel:#fff; --line:#dcdfe4;
          --ink:#1d2128; --muted:#6b7280; --accent:#2f6feb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14161a; --panel:#1c1f25; --line:#2c313a; --ink:#e7e9ee;
            --muted:#9aa2b1; --accent:#5b8cf5; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid var(--line);
           background:var(--panel); display:flex; gap:16px; align-items:center;
           flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; font-weight:600; }
  main { display:grid; grid-template-columns: 300px 1fr 320px; gap:16px;
         padding:16px; align-items:start; }
  @media (max-width: 1100px) { main { grid-template-columns:1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line);
           border-radius:10px; padding:14px; }
  .panel h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
              color:var(--muted); margin:0 0 10px; font-weight:600; }
  select, button, input[type=file] { font:inherit; color:inherit; }
  select, button { background:var(--bg); border:1px solid var(--line);
                   border-radius:7px; padding:7px 10px; cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button:disabled { opacity:.5; cursor:default; }
  label.row { display:block; margin-bottom:14px; }
  label.row .top { display:flex; justify-content:space-between; gap:8px;
                   font-size:12px; color:var(--muted); margin-bottom:4px; }
  label.row .top b { color:var(--ink); font-variant-numeric:tabular-nums; }
  input[type=range] { width:100%; accent-color:var(--accent); }
  #viewer { display:flex; align-items:center; justify-content:center;
            min-height:320px; }
  #viewer img { max-width:100%; height:auto; border-radius:8px; }
  table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
  th, td { text-align:right; padding:5px 4px; border-bottom:1px solid var(--line); }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--muted); font-weight:600; font-size:12px; }
  .swatch { display:inline-block; width:10px; height:10px; border-radius:2px;
            margin-right:6px; vertical-align:-1px; }
  .muted { color:var(--muted); }
  .status { font-size:13px; color:var(--muted); }
  .error { color:#c0392b; }
  .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
  .stat { display:flex; justify-content:space-between; padding:3px 0;
          font-variant-numeric:tabular-nums; }
  .stat span:first-child { color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Floor plan &rarr; 2D room layout</h1>
  <select id="sample"></select>
  <input type="file" id="upload" accept=".webp,.png,.jpg,.jpeg,.bmp,.tif,.tiff">
  <select id="view">
    <option value="rooms">rooms</option>
    <option value="walls">wall graph</option>
  </select>
  <span id="status" class="status"></span>
</header>

<main>
  <section class="panel">
    <h2>Parameters</h2>
    <div id="params"></div>
    <div class="actions">
      <button id="reset">Reset defaults</button>
    </div>
  </section>

  <section class="panel">
    <h2 id="viewTitle">Result</h2>
    <div id="viewer"><span class="muted">Loading&hellip;</span></div>
  </section>

  <section class="panel">
    <h2>Rooms</h2>
    <div id="summary"></div>
    <table id="rooms"><thead>
      <tr><th>id</th><th>area px</th><th>share</th><th>gon</th></tr>
    </thead><tbody></tbody></table>
    <div class="actions">
      <button id="saveJson">Save JSON</button>
      <button id="savePng" class="primary">Save PNG</button>
    </div>
  </section>
</main>

<script>
const PALETTE = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#946789","#8c564b",
  "#e377c2","#7f7f7f","#bcbd22","#17becf","#98df8a","#ffbb78","#ff9896",
  "#b0b0b0","#f7b6d2","#dbda89"];
let SPEC = [], current = null, timer = null, busy = false;

const $ = (id) => document.getElementById(id);
const label = (n) => n.replace(/_/g, " ");

function buildParams() {
  $("params").innerHTML = SPEC.map((p) => `
    <label class="row">
      <span class="top"><span>${label(p.name)}</span><b id="v_${p.name}"></b></span>
      <input type="range" id="p_${p.name}" min="${p.min}" max="${p.max}"
             step="${p.step}" value="${p.default}">
    </label>`).join("");
  bind();
}

function bind() {
  SPEC.forEach((p) => {
    const el = $("p_" + p.name);
    const show = () => { $("v_" + p.name).textContent =
      p.integer ? el.value : Number(el.value).toFixed(3); };
    show();
    el.oninput = () => { show(); schedule(); };
  });
}
buildParams.refresh = bind;

function params() {
  const out = {};
  SPEC.forEach((p) => { out[p.name] = $("p_" + p.name).value; });
  return out;
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(run, 350);        // debounce: each run is a full analysis
}

function query() {
  return new URLSearchParams(params());
}

async function run(file) {
  if (busy) { schedule(); return; }
  busy = true;
  const started = performance.now();
  $("status").textContent = "analysing\\u2026";
  $("status").className = "status";
  try {
    let res;
    if (file) {
      const q = query();
      q.set("name", file.name);
      res = await fetch("/api/analyze?" + q, { method: "POST", body: file });
    } else {
      const q = query();
      q.set("sample", $("sample").value);
      res = await fetch("/api/analyze?" + q, { method: "POST" });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    current = data;
    render(data);
    const ms = Math.round(performance.now() - started);
    $("status").textContent = `${data.report.room_count} regions \\u00b7 ${ms} ms`;
  } catch (err) {
    $("status").textContent = String(err.message || err);
    $("status").className = "status error";
  } finally {
    busy = false;
  }
}

function currentImage(data) {
  return $("view").value === "walls"
    ? [data.walls, "Wall graph - corners, junctions, ends"]
    : [data.annotated, "Rooms"];
}

function render(data) {
  const [b64, title] = currentImage(data);
  $("viewTitle").textContent = title;
  $("viewer").innerHTML =
    `<img alt="analysed plan" src="data:image/png;base64,${b64}">`;

  const r = data.report;
  $("summary").innerHTML = [
    ["regions", r.room_count],
    ["wall corners", r.wall_graph.corner_count],
    ["wall junctions", r.wall_graph.junction_count],
    ["free wall ends", r.wall_graph.endpoint_count],
    ["room area px", r.total_room_area_px.toLocaleString()],
    ["wall tops px", r.wall_area_px.toLocaleString()],
    ["footprint px", r.footprint_area_px.toLocaleString()],
    ["brightness mode", r.wall_brightness_mode],
    ["callouts removed px", r.annotation_removed_px],
  ].map(([k, v]) => `<div class="stat"><span>${k}</span><span>${v}</span></div>`).join("");

  $("rooms").tBodies[0].innerHTML = r.rooms.map((room, i) => `
    <tr>
      <td><span class="swatch" style="background:${PALETTE[i % PALETTE.length]}"></span>${room.id}${room.is_enclosed ? "" : " *"}</td>
      <td>${room.area_px.toLocaleString()}</td>
      <td>${(room.relative_area * 100).toFixed(1)}%</td>
      <td>${room.polygon_px.length}</td>
    </tr>`).join("") +
    (r.rooms.some((x) => !x.is_enclosed)
      ? `<tr><td colspan="4" class="muted">* open to exterior</td></tr>` : "");
}

function download(name, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

function stem() {
  return (current?.report.image || "plan").replace(/\\.[^.]+$/, "");
}

$("saveJson").onclick = () => {
  if (!current) return;
  download(stem() + "__rooms.json",
    new Blob([JSON.stringify(current.report, null, 2)], {type: "application/json"}));
};
$("savePng").onclick = () => {
  if (!current) return;
  const [b64] = currentImage(current);
  const suffix = {rooms: "__annotated", walls: "__walls"}[$("view").value];
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  download(stem() + suffix + ".png", new Blob([bytes], {type: "image/png"}));
};
$("reset").onclick = () => {
  SPEC.forEach((p) => { $("p_" + p.name).value = p.default; });
  buildParams();
  run();
};
$("sample").onchange = () => run();
// Both views arrive with every analysis, so switching never re-runs the pipeline.
$("view").onchange = () => { if (current) render(current); };
$("upload").onchange = (e) => { if (e.target.files[0]) run(e.target.files[0]); };

// The current view is expressed in the URL, so a configuration can be
// bookmarked or linked to: ?sample=...&view=walls&wall_delta=12
function applyUrlState() {
  const q = new URLSearchParams(location.search);
  const view = q.get("view");
  if (view && [...$("view").options].some((o) => o.value === view)) $("view").value = view;
  const sample = q.get("sample");
  if (sample && [...$("sample").options].some((o) => o.value === sample)) {
    $("sample").value = sample;
  }
  SPEC.forEach((p) => {
    const v = q.get(p.name);
    if (v !== null) $("p_" + p.name).value = v;
  });
  buildParams.refresh();
}

(async function init() {
  const meta = await (await fetch("/api/meta")).json();
  SPEC = meta.parameters;
  buildParams();
  $("sample").innerHTML = meta.samples
    .map((s) => `<option value="${s}">${s}</option>`).join("");
  applyUrlState();
  if (meta.samples.length || location.search) run();
  else $("viewer").innerHTML = '<span class="muted">No samples found - upload an image.</span>';
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "floorplan"
    service: AnalysisService = None      # injected by serve()

    # -- helpers ------------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def log_message(self, fmt, *args):
        pass                              # the CLI prints its own, quieter, log

    # -- routes -------------------------------------------------------------

    def do_GET(self):
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/meta":
            self._json(200, {"samples": self.service.samples(),
                             "parameters": self.service.parameter_spec()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            self._json(404, {"error": "not found"})
            return

        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        sample = query.pop("sample", None)
        name = query.pop("name", "")

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            self._json(413, {"error": "file too large"})
            return

        started = time.perf_counter()
        try:
            if length:
                report, annotated, walls = self.service.analyse_upload(
                    self.rfile.read(length), name, query)
            elif sample:
                report, annotated, walls = self.service.analyse_sample(sample, query)
            else:
                self._json(400, {"error": "no sample selected and no file uploaded"})
                return
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception as exc:                       # noqa: BLE001
            self._json(500, {"error": f"analysis failed: {exc}"})
            return

        payload = {
            "report": report,
            "annotated": base64.b64encode(annotated).decode("ascii"),
            "walls": base64.b64encode(walls).decode("ascii"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
        self._json(200, payload)


def serve(host: str = "127.0.0.1", port: int = 8000,
          samples_dir: Path = Path("data/input_images")) -> int:
    Handler.service = AnalysisService(samples_dir)
    httpd = ThreadingHTTPServer((host, port), Handler)

    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"floorplan UI on http://{shown}:{port}  (ctrl-c to stop)")
    if host == "0.0.0.0":
        print("  bound to all interfaces - intended for Docker port mapping")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
