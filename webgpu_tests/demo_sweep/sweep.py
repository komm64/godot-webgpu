#!/usr/bin/env python3
"""WebGPU regression sweep over godot-demo-projects."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

DEFAULT_CHROME_WIN = "C:/Program Files/Google/Chrome/Application/chrome.exe"
SKIP_DIRS = {".godot", "addons", ".git"}
GODOT_TIMEOUT_SEC = 300

# Demos that cannot run as a web export on any renderer: C# projects (no web
# export in Godot 4) and OpenXR (native runtimes only). They are listed as
# skipped rather than failed.
UNSUPPORTED_PREFIXES = ("mono/", "xr/openxr_")

EXTRA_MIME_TYPES = {
    ".wasm": "application/wasm",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".pck": "application/octet-stream",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def default_chrome_path() -> str:
    if sys.platform == "win32":
        return DEFAULT_CHROME_WIN
    if sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return "google-chrome"


def win_abs_path(p: str | Path) -> str:
    """Return an absolute path with forward slashes (C:/... on Windows, never /c/...)."""
    s = str(p)
    if sys.platform == "win32":
        m = re.match(r"^/([a-zA-Z])/(.*)$", s)
        if m:
            s = f"{m.group(1).upper()}:/{m.group(2)}"
    return str(Path(s).resolve()).replace("\\", "/")


def discover_demos(demos_dir: Path, filter_substr: str | None) -> list[tuple[str, str, Path]]:
    """Find every project.godot under demos_dir, skipping .godot/ and addons/."""
    found: list[tuple[str, str, Path]] = []
    for root, dirnames, filenames in os.walk(demos_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if "project.godot" not in filenames:
            continue
        dir_path = Path(root)
        rel = os.path.relpath(dir_path, demos_dir).replace("\\", "/")
        if rel == ".":
            rel = demos_dir.name
        if filter_substr and filter_substr not in rel:
            continue
        name = rel.replace("/", "_")
        found.append((name, rel, dir_path))
    found.sort(key=lambda item: item[1])
    return found


# Demos that need Forward+ features the Mobile renderer lacks, so they fail the
# same way with upstream Godot on Mobile (Web always renders with Mobile).
FORWARD_PLUS_ONLY = {
    "compute/post_shader",  # Mobile's color buffer can't be a storage image.
}


def unsupported_reason(rel: str) -> str | None:
    if rel in FORWARD_PLUS_ONLY:
        return "needs Forward+ (fails on Mobile upstream too)"
    for prefix in UNSUPPORTED_PREFIXES:
        if rel.startswith(prefix):
            return f"unsupported on web ({prefix.rstrip('/_')})"
    return None


def patch_project_godot(project_file: Path) -> None:
    """Set WebGPU rendering keys under [rendering] without disturbing anything else."""
    text = project_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    target_keys = {
        "renderer/rendering_method.web": 'renderer/rendering_method.web="mobile"',
        "rendering_device/driver.web": 'rendering_device/driver.web="webgpu"',
    }

    rendering_start: int | None = None
    rendering_end = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if rendering_start is None:
                if stripped == "[rendering]":
                    rendering_start = idx
            else:
                rendering_end = idx
                break

    if rendering_start is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("[rendering]")
        lines.append("")
        lines.extend(target_keys.values())
    else:
        seen: set[str] = set()
        for idx in range(rendering_start + 1, rendering_end):
            for key, replacement in target_keys.items():
                if re.match(rf"^\s*{re.escape(key)}\s*=", lines[idx]):
                    lines[idx] = replacement
                    seen.add(key)
                    break
        missing = [val for key, val in target_keys.items() if key not in seen]
        if missing:
            insert_at = rendering_end
            while insert_at > rendering_start + 1 and lines[insert_at - 1].strip() == "":
                insert_at -= 1
            lines[insert_at:insert_at] = missing

    project_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_export_presets(presets_file: Path, template_abs: str, bake: bool = False) -> None:
    escaped = template_abs.replace('"', '\\"')
    content = (
        "[preset.0]\n\n"
        'name="Web"\n'
        'platform="Web"\n'
        "runnable=true\n"
        'export_path=""\n\n'
        "[preset.0.options]\n\n"
        f'custom_template/debug="{escaped}"\n'
        f'custom_template/release="{escaped}"\n'
        "variant/extensions_support=true\n"
        # Templates built with threads=no are named *.nothreads.*.
        f"variant/thread_support={'false' if '.nothreads.' in Path(template_abs).name else 'true'}\n"
        "vram_texture_compression/for_desktop=true\n"
        "html/export_icon=false\n"
    )
    if bake:
        content += "shader_baker/enabled=true\n"
    presets_file.write_text(content, encoding="utf-8")


def patch_index_html(index_html: Path) -> None:
    """Ensure GODOT_CONFIG in index.html contains \"renderingDriver\":\"webgpu\"."""
    html = index_html.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(const\s+GODOT_CONFIG\s*=\s*)(\{)", html)
    if not match:
        return
    obj_start = match.start(2)
    decoder = json.JSONDecoder()
    config, obj_end = decoder.raw_decode(html, obj_start)
    if not isinstance(config, dict):
        return
    config["renderingDriver"] = "webgpu"
    serialized = json.dumps(config, separators=(",", ":"))
    patched = html[:obj_start] + serialized + html[obj_end:]
    index_html.write_text(patched, encoding="utf-8")


def run_godot_step(cmd: list[str]) -> tuple[int, list[str]]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=GODOT_TIMEOUT_SEC,
            check=False,
        )
        return proc.returncode, proc.stdout.splitlines()
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        lines = raw.splitlines()
        lines.append(f"TIMEOUT after {GODOT_TIMEOUT_SEC}s: {' '.join(cmd)}")
        return 124, lines


def export_demo(
    name: str,
    src_dir: Path,
    godot_abs: str,
    template_abs: str,
    out_dir: Path,
    bake: bool = False,
) -> dict:
    work_dir = out_dir / "work" / name
    web_dir = out_dir / "web" / name
    index_html = web_dir / "index.html"

    if work_dir.exists():
        shutil.rmtree(work_dir)
    if web_dir.exists():
        shutil.rmtree(web_dir)

    shutil.copytree(src_dir, work_dir, ignore=shutil.ignore_patterns(".godot", ".git"))
    patch_project_godot(work_dir / "project.godot")
    write_export_presets(work_dir / "export_presets.cfg", template_abs, bake=bake)
    web_dir.mkdir(parents=True, exist_ok=True)

    work_abs = win_abs_path(work_dir)
    index_abs = win_abs_path(index_html)

    all_logs: list[str] = []
    rc, import_logs = run_godot_step([godot_abs, "--headless", "--path", work_abs, "--import"])
    all_logs.extend(import_logs)

    if rc == 0:
        if bake:
            export_cmd = [
                godot_abs,
                "--rendering-method",
                "mobile",
                "--path",
                work_abs,
                "--export-release",
                "Web",
                index_abs,
            ]
        else:
            export_cmd = [
                godot_abs,
                "--headless",
                "--path",
                work_abs,
                "--export-release",
                "Web",
                index_abs,
            ]
        rc, export_logs = run_godot_step(export_cmd)
        all_logs.extend(export_logs)

    if rc == 0 and not index_html.is_file():
        rc = 1
        all_logs.append(f"Export returned 0 but {index_html} was not created.")

    if rc == 0:
        patch_index_html(index_html)
        return {"name": name, "export_ok": True, "export_rc": 0}

    tail = all_logs[-20:]
    summary_err = f"export failed (rc={rc})"
    if tail:
        summary_err = f"{summary_err}: {tail[-1][:240]}"
    return {
        "name": name,
        "export_ok": False,
        "export_rc": rc,
        "export_log": tail,
        "first_frame_ms": None,
        "frames": 0,
        "gpu_errors": 0,
        "js_errors": 0,
        "first_errors": [summary_err],
        "shot": None,
    }


def start_http_server(
    web_dir: Path, port: int
) -> tuple[http.server.ThreadingHTTPServer, threading.Thread]:
    serve_dir = str(web_dir.resolve())

    class SweepHandler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            **EXTRA_MIME_TYPES,
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=serve_dir, **kwargs)

        def end_headers(self) -> None:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, format: str, *args) -> None:
            return

    class ReusableHTTPServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(("127.0.0.1", port), SweepHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def launch_chrome(chrome_exe: str, cdp_port: int, profile_dir: Path) -> subprocess.Popen:
    profile_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome_exe,
        "--headless=new",
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={win_abs_path(profile_dir)}",
        "--enable-unsafe-webgpu",
        "--enable-features=Vulkan",
        "--use-angle=d3d11",
        "--no-first-run",
        # A fresh Edge profile installs the signed-in user's synced extensions,
        # whose onboarding tabs push the demo tab to the background, where its
        # timers run once a second.
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome exited early with code {proc.returncode}")
        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", cdp_port, timeout=1.0)
            conn.request("GET", "/json/version")
            resp = conn.getresponse()
            resp.read()
            if resp.status == 200:
                return proc
        except (OSError, http.client.HTTPException):
            time.sleep(0.2)
        finally:
            if conn is not None:
                conn.close()

    stop_chrome(proc)
    raise RuntimeError(f"Chrome CDP did not become ready on port {cdp_port} within 20s")


def stop_chrome(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def run_check(
    check_mjs: Path,
    cdp_port: int,
    url: str,
    seconds: float,
    max_seconds: float,
    shot_path: Path,
    out_dir: Path,
) -> dict:
    cmd = [
        "node",
        str(check_mjs),
        "--cdp-port",
        str(cdp_port),
        "--url",
        url,
        "--seconds",
        str(seconds),
        "--max-seconds",
        str(max_seconds),
        "--shot",
        str(shot_path),
    ]
    timeout_sec = int(max(seconds, max_seconds)) + 90
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "first_frame_ms": None,
            "frames": 0,
            "gpu_errors": 0,
            "js_errors": 1,
            "first_errors": [f"check.mjs timed out after {timeout_sec}s"],
            "shot": None,
            "perf": None,
            "create": [],
        }

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        err = (proc.stderr.strip() or f"check.mjs exited with rc={proc.returncode}")[:300]
        return {
            "first_frame_ms": None,
            "frames": 0,
            "gpu_errors": 0,
            "js_errors": 1,
            "first_errors": [err],
            "shot": None,
            "perf": None,
            "create": [],
        }

    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {
            "first_frame_ms": None,
            "frames": 0,
            "gpu_errors": 0,
            "js_errors": 1,
            "first_errors": [f"Invalid JSON from check.mjs: {exc}"],
            "shot": None,
            "perf": None,
            "create": [],
        }

    shot_rel: str | None = None
    if data.get("shot") and Path(data["shot"]).is_file():
        shot_rel = os.path.relpath(data["shot"], out_dir).replace("\\", "/")

    return {
        "first_frame_ms": data.get("first_frame_ms"),
        "frames": int(data.get("frames") or 0),
        "gpu_errors": int(data.get("gpu_errors") or 0),
        "js_errors": int(data.get("js_errors") or 0),
        "env_errors": int(data.get("env_errors") or 0),
        "warnings": int(data.get("warnings") or 0),
        "first_errors": list(data.get("first_errors") or [])[:5],
        "shot": shot_rel,
        "perf": data.get("perf"),
        "create": list(data.get("create") or []),
    }


def compare_results(
    results: list[dict], old_path: Path
) -> tuple[dict[str, dict], bool]:
    old_list = json.loads(old_path.read_text(encoding="utf-8"))
    old_map = {item["name"]: item for item in old_list if isinstance(item, dict) and "name" in item}

    diffs: dict[str, dict] = {}
    any_regressed = False

    for cur in results:
        name = cur["name"]
        old = old_map.get(name)
        if cur.get("skipped"):
            diffs[name] = {"status": "skipped", "reasons": []}
            continue
        if old is None:
            diffs[name] = {"status": "new", "reasons": []}
            continue

        reasons: list[str] = []
        if old.get("export_ok") and not cur.get("export_ok"):
            reasons.append("export ok->fail")
        if int(old.get("gpu_errors") or 0) == 0 and int(cur.get("gpu_errors") or 0) > 0:
            reasons.append(f"gpu_errors 0->{cur['gpu_errors']}")
        if int(cur.get("js_errors") or 0) > int(old.get("js_errors") or 0):
            reasons.append(f"js_errors {old.get('js_errors', 0)}->{cur['js_errors']}")
        if int(old.get("frames") or 0) > 0 and int(cur.get("frames") or 0) == 0:
            reasons.append(f"frames {old['frames']}->0")

        if reasons:
            any_regressed = True
            diffs[name] = {"status": "regressed", "reasons": reasons}
        else:
            diffs[name] = {"status": "ok", "reasons": []}

    return diffs, any_regressed


def is_failure(item: dict) -> bool:
    if item.get("skipped"):
        return False
    return (
        not item.get("export_ok")
        or int(item.get("gpu_errors") or 0) > 0
        or int(item.get("js_errors") or 0) > 0
        or int(item.get("frames") or 0) == 0
    )


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def first_create_at_ms(item: dict) -> str:
    create = item.get("create")
    if not isinstance(create, list) or not create:
        return "-"
    first = create[0]
    if not isinstance(first, str):
        return "-"
    match = re.search(r"\bat=(\d+(?:\.\d+)?)", first)
    return match.group(1) if match else "-"


def write_summary_md(out_dir: Path, results: list[dict], diffs: dict[str, dict] | None) -> None:
    def sort_key(item: dict) -> tuple:
        regressed = bool(diffs and diffs.get(item["name"], {}).get("status") == "regressed")
        return (
            0 if regressed else 1,
            0 if is_failure(item) else 1,
            0 if not item.get("export_ok") else 1,
            -int(item.get("gpu_errors") or 0),
            -int(item.get("js_errors") or 0),
            item["name"],
        )

    ordered = sorted(results, key=sort_key)
    total = len(results)
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = sum(1 for r in results if is_failure(r))
    passed = total - failed - skipped
    export_failed = sum(1 for r in results if not r.get("export_ok") and not r.get("skipped"))

    lines = [
        "# WebGPU Demo Sweep Summary",
        "",
        f"- Total: **{total}** | Passed: **{passed}** | Failed: **{failed}** | Export failed: **{export_failed}** | Skipped: **{skipped}**",
    ]
    if diffs is not None:
        regressed_count = sum(1 for d in diffs.values() if d["status"] == "regressed")
        lines.append(f"- Regressions vs baseline: **{regressed_count}**")
    lines.append("")

    if diffs is not None:
        lines.append(
            "| Demo | Compare | Regressions | Export | Frames | First Frame (ms) | First Create (ms) | GPU Errors | JS Errors | Shot | First Errors |"
        )
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    else:
        lines.append(
            "| Demo | Export | Frames | First Frame (ms) | First Create (ms) | GPU Errors | JS Errors | Shot | First Errors |"
        )
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")

    for item in ordered:
        if item.get("skipped"):
            exp = "skipped"
        else:
            exp = "ok" if item.get("export_ok") else f"fail ({item.get('export_rc', '?')})"
        ff = "-" if item.get("first_frame_ms") is None else str(item["first_frame_ms"])
        fc = first_create_at_ms(item)
        shot = f"[png]({item['shot']})" if item.get("shot") else "-"
        errs = "; ".join(md_escape(e) for e in (item.get("first_errors") or [])[:5]) or "-"
        if item.get("skipped"):
            errs = item["skipped"]
        if diffs is not None:
            diff = diffs.get(item["name"], {"status": "new", "reasons": []})
            status = diff["status"].upper()
            reasons = md_escape(", ".join(diff["reasons"])) if diff["reasons"] else "-"
            lines.append(
                f"| {md_escape(item['name'])} | {status} | {reasons} | {exp} | {item['frames']} | {ff} | {fc} | {item['gpu_errors']} | {item['js_errors']} | {shot} | {errs} |"
            )
        else:
            lines.append(
                f"| {md_escape(item['name'])} | {exp} | {item['frames']} | {ff} | {fc} | {item['gpu_errors']} | {item['js_errors']} | {shot} | {errs} |"
            )

    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export godot-demo-projects for WebGPU and run a headless Chrome regression sweep."
    )
    parser.add_argument("--godot", required=True, help="Path to stable Godot editor executable.")
    parser.add_argument("--template", required=True, help="Path to WebGPU web export template zip.")
    parser.add_argument("--demos", required=True, help="Path to godot-demo-projects directory.")
    parser.add_argument("--out", required=True, help="Output directory for work, web, shots, and reports.")
    parser.add_argument("--filter", default=None, help="Optional substring filter on demo relative path.")
    parser.add_argument(
        "--chrome",
        default=default_chrome_path(),
        help=f"Path to Chrome executable (default: {default_chrome_path()}).",
    )
    parser.add_argument("--port", type=int, default=8860, help="Local HTTP server port (default: 8860).")
    parser.add_argument("--cdp-port", type=int, default=9360, help="Chrome DevTools port (default: 9360).")
    parser.add_argument(
        "--seconds", type=float, default=15, help="Seconds to run each demo after its first frame (default: 15)."
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=120,
        help="Longest wait for a demo that has not drawn a frame yet (default: 120).",
    )
    parser.add_argument("--jobs", type=int, default=1, help="Parallel export jobs (default: 1).")
    parser.add_argument(
        "--bake",
        action="store_true",
        help=(
            "Enable shader_baker/enabled=true and export with --rendering-method mobile (no --headless). "
            "Requires an editor built from the same commit as the template (this fork's editor, not stock Godot)."
        ),
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Visits per exported demo in the same browser profile to measure warm caches (default: 1).",
    )
    parser.add_argument("--compare", default=None, help="Previous results.json for regression comparison.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    godot_abs = win_abs_path(args.godot)
    template_abs = win_abs_path(args.template)
    demos_dir = Path(args.demos).resolve()
    out_dir = Path(args.out).resolve()
    web_root = out_dir / "web"
    shots_dir = out_dir / "shots"
    profile_dir = out_dir / "chrome-profile"
    check_mjs = Path(__file__).resolve().parent / "check.mjs"

    if not demos_dir.is_dir():
        print(f"ERROR: --demos directory not found: {demos_dir}", file=sys.stderr)
        return 2
    if not Path(godot_abs).is_file():
        print(f"ERROR: --godot executable not found: {godot_abs}", file=sys.stderr)
        return 2
    if not Path(template_abs).is_file():
        print(f"ERROR: --template file not found: {template_abs}", file=sys.stderr)
        return 2
    if args.compare and not Path(args.compare).is_file():
        print(f"ERROR: --compare file not found: {args.compare}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    web_root.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    demos = discover_demos(demos_dir, args.filter)
    print(f"Discovered {len(demos)} demo(s) under {demos_dir}")
    skipped = {name: unsupported_reason(rel) for name, rel, _ in demos if unsupported_reason(rel)}
    demos_to_export = [d for d in demos if d[0] not in skipped]

    export_results: dict[str, dict] = {}
    jobs = max(1, args.jobs)
    repeat = max(1, args.repeat)
    if jobs == 1:
        for name, rel, src_dir in demos_to_export:
            print(f"[export] {rel} -> {name}", flush=True)
            export_results[name] = export_demo(
                name, src_dir, godot_abs, template_abs, out_dir, bake=args.bake
            )
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {
                pool.submit(
                    export_demo, name, src_dir, godot_abs, template_abs, out_dir, args.bake
                ): (name, rel)
                for name, rel, src_dir in demos_to_export
            }
            for fut in as_completed(futs):
                name, rel = futs[fut]
                res = fut.result()
                export_results[name] = res
                status = "ok" if res["export_ok"] else f"fail (rc={res['export_rc']})"
                print(f"[export] {rel}: {status}", flush=True)

    exported_names = [name for name, _, _ in demos_to_export if export_results[name]["export_ok"]]
    server: http.server.ThreadingHTTPServer | None = None
    chrome_proc: subprocess.Popen | None = None
    results: list[dict] = []

    try:
        if exported_names:
            server, _ = start_http_server(web_root, args.port)
            chrome_proc = launch_chrome(args.chrome, args.cdp_port, profile_dir)

        for name, _, _ in demos:
            if name in skipped:
                results.append({"name": name, "skipped": skipped[name], "export_ok": False, "export_rc": None,
                                "first_frame_ms": None, "frames": 0, "gpu_errors": 0, "js_errors": 0,
                                "first_errors": [], "shot": None})
                continue
            exp = export_results[name]
            if not exp["export_ok"]:
                results.append(exp)
                continue
            url = f"http://127.0.0.1:{args.port}/{name}/index.html"
            for visit in range(repeat):
                entry_name = name if repeat == 1 else f"{name}#{visit}"
                shot_path = shots_dir / (f"{name}.png" if repeat == 1 else f"{name}_{visit}.png")
                print(f"[run] {entry_name} ({args.seconds}s)", flush=True)
                check = run_check(check_mjs, args.cdp_port, url, args.seconds, args.max_seconds, shot_path, out_dir)
                results.append(
                    {
                        "name": entry_name,
                        "export_ok": True,
                        "export_rc": 0,
                        "first_frame_ms": check["first_frame_ms"],
                        "frames": check["frames"],
                        "gpu_errors": check["gpu_errors"],
                        "js_errors": check["js_errors"],
                        "env_errors": check.get("env_errors", 0),
                        "warnings": check.get("warnings", 0),
                        "first_errors": check["first_errors"],
                        "shot": check["shot"],
                        "perf": check.get("perf"),
                        "create": check.get("create", []),
                    }
                )
    except KeyboardInterrupt:
        print("\nInterrupted; cleaning up Chrome and HTTP server...", file=sys.stderr)
        return 130
    finally:
        stop_chrome(chrome_proc)
        if server is not None:
            server.shutdown()
            server.server_close()

    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    diffs: dict[str, dict] | None = None
    any_regressed = False
    if args.compare:
        diffs, any_regressed = compare_results(results, Path(args.compare))

    write_summary_md(out_dir, results, diffs)
    print(f"Wrote {results_path} and {out_dir / 'summary.md'}")

    if args.compare and any_regressed:
        print("Regressions detected against baseline.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
