#!/usr/bin/env python3
"""Compare each demo's WebGPU frame with the native Windows frame.

Runs on the output of sweep.py (its web/ and work/ folders). For each demo:
- Web: the page runs with --fixed-fps 60, requestAnimationFrame is held after
  --frame callbacks, and the page is screenshotted (frame_shot.mjs).
- Native: the imported project runs on the stock editor with the Mobile
  renderer, --fixed-fps 60 and Movie Maker, which writes every frame.
The web page starts its main loop a few animation frames late, so the native
frame closest to the web one within --search frames is used.

Usage:
  python native_compare.py --sweep-out <sweep --out dir> --godot <godot.exe> --out <dir>
      [--filter 3d/] [--frame 180] [--rendering-driver d3d12]

Needs Pillow and numpy. Writes results.json, summary.md and one
native | web | difference image per demo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import sweep  # noqa: E402

DIFF_THRESHOLD = 24  # Per-channel difference counted as a changed pixel.


def set_web_args(index_html: Path, args: list[str]) -> None:
    html = index_html.read_text(encoding="utf-8")
    html = re.sub(r'"args":\[[^\]]*\]', '"args":' + json.dumps(args), html, count=1)
    index_html.write_text(html, encoding="utf-8")


def web_frame(web_root: Path, name: str, frame: int, size: tuple[int, int], out_png: Path, port: int, cdp_port: int, profile: Path) -> bool:
    set_web_args(web_root / name / "index.html", ["--fixed-fps", "60"])
    server, _ = sweep.start_http_server(web_root, port)
    chrome = sweep.launch_chrome(args.chrome, cdp_port, profile)
    try:
        subprocess.run(
            ["node", str(Path(__file__).parent / "frame_shot.mjs"), str(cdp_port), f"http://127.0.0.1:{port}/{name}/index.html",
                str(frame), str(size[0]), str(size[1]), str(out_png)],
            capture_output=True, text=True, timeout=200)
    finally:
        sweep.stop_chrome(chrome)
        server.shutdown()
    return out_png.exists()


def native_frames(godot: str, project: Path, frames: int, movie_dir: Path, driver: str) -> list[Path]:
    movie_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [godot, "--path", str(project), "--rendering-method", "mobile", "--rendering-driver", driver, "--position", "0,0",
            "--audio-driver", "Dummy", "--fixed-fps", "60", "--write-movie", str(movie_dir / "f.png"), "--quit-after", str(frames)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    return sorted(movie_dir.glob("f*.png"))


def compare(native: Path, web: np.ndarray) -> tuple[float, float, np.ndarray]:
    a = np.asarray(Image.open(native).convert("RGB")).astype(np.int16)
    if a.shape != web.shape:
        return 1e9, 100.0, a
    d = np.abs(a - web)
    return float(d.mean()), float((d.max(axis=2) > DIFF_THRESHOLD).mean() * 100), a


def main() -> int:
    global args
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sweep-out", required=True, type=Path)
    p.add_argument("--godot", required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--filter", default=None)
    p.add_argument("--frame", type=int, default=180)
    p.add_argument("--search", type=int, default=12, help="Native frames before --frame to search (default: 12).")
    p.add_argument("--rendering-driver", default="d3d12")
    p.add_argument("--chrome", default="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    p.add_argument("--port", type=int, default=8870)
    p.add_argument("--cdp-port", type=int, default=9370)
    args = p.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    web_root = args.sweep_out / "web"
    results = []
    for name in sorted(d.name for d in web_root.iterdir() if (d / "index.html").exists()):
        if args.filter and not name.startswith(args.filter.replace("/", "_")):
            continue
        project = args.sweep_out / "work" / name
        if not (project / "project.godot").exists():
            continue
        print(f"[compare] {name}", flush=True)
        movie = out / "movie" / name
        frames = native_frames(args.godot, project, args.frame + 2, movie, args.rendering_driver)
        if not frames:
            results.append({"name": name, "status": "native_failed"})
            continue
        size = Image.open(frames[0]).size
        web_png = out / f"{name}.web.png"
        if not web_frame(web_root, name, args.frame, size, web_png, args.port, args.cdp_port, out / "chrome-profile"):
            results.append({"name": name, "status": "web_failed"})
            shutil.rmtree(movie, ignore_errors=True)
            continue
        web = np.asarray(Image.open(web_png).convert("RGB")).astype(np.int16)
        best = None
        for f in frames[max(0, args.frame - args.search):]:
            mean, changed, a = compare(f, web)
            if best is None or mean < best[0]:
                best = (mean, changed, f, a)
        mean, changed, f, a = best
        h, w = web.shape[:2]
        sheet = Image.new("RGB", (w * 3 // 2, h // 2))
        diff = np.clip(np.abs(a - web) * 4, 0, 255).astype(np.uint8) if a.shape == web.shape else np.zeros_like(web, dtype=np.uint8)
        for i, im in enumerate([a.astype(np.uint8), web.astype(np.uint8), diff]):
            sheet.paste(Image.fromarray(im).resize((w // 2, h // 2)), (i * w // 2, 0))
        sheet.save(out / f"{name}.png")
        results.append({"name": name, "status": "ok", "native_frame": int(f.stem[1:]), "mean_diff": round(mean, 2), "changed_pct": round(changed, 2)})
        shutil.rmtree(movie, ignore_errors=True)
        web_png.unlink()

    (out / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = ["| Demo | Mean diff (0-255) | Changed pixels | Image |", "| --- | ---: | ---: | --- |"]
    for r in sorted(results, key=lambda r: -r.get("changed_pct", 101)):
        if r["status"] != "ok":
            lines.append(f"| {r['name']} | {r['status']} | | |")
        else:
            lines.append(f"| {r['name']} | {r['mean_diff']} | {r['changed_pct']}% | [png]({r['name']}.png) |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.rmtree(out / "movie", ignore_errors=True)
    shutil.rmtree(out / "chrome-profile", ignore_errors=True)
    print(f"Wrote {out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
