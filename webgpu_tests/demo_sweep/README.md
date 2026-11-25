# WebGPU Demo Sweep

Self-contained regression sweep tool for exporting `godot-demo-projects` to WebGPU and verifying them in headless Chrome via CDP. Uses only Python 3 stdlib and Node.js 22+ built-ins (`WebSocket`, `fetch`).

## Usage

```bash
python webgpu_tests/demo_sweep/sweep.py \
  --godot <godot-editor-exe> \
  --template <webgpu-template.zip> \
  --demos <godot-demo-projects-dir> \
  --out <output-dir> \
  [--filter <substring>] \
  [--chrome <chrome-exe>] \
  [--port 8860] \
  [--cdp-port 9360] \
  [--seconds 15]   [--max-seconds 60] \
  [--jobs 1] \
  [--compare <old-results.json>]
```

Key options:
- `--filter <substring>`: Run only demos whose relative path contains `<substring>`.
- `--jobs <N>`: Export up to `N` demos in parallel (default `1`).
- `--seconds <N>`: Run time per demo after its first frame, before capturing metrics and screenshot (default `15`).
- `--max-seconds <N>`: Longest wait for a demo that is still loading or compiling shaders and has not drawn a frame (default `60`).
- `--chrome <path>`: Chrome executable (Windows default: `C:/Program Files/Google/Chrome/Application/chrome.exe`).

## Outputs

Written under `--out`:
- `work/<name>/`: Temporary patched copy of each demo (`project.godot` + `export_presets.cfg`).
- `web/<name>/index.html`: Exported WebGPU build with `"renderingDriver":"webgpu"` patched into `GODOT_CONFIG`.
- `shots/<name>.png`: Screenshot captured after `--seconds`.
- `results.json`: Per-demo JSON records (`name`, `export_ok`, `export_rc`, `first_frame_ms`, `frames`, `gpu_errors`, `js_errors`, `env_errors`, `warnings`, `first_errors`, `shot`, plus `export_log` on export failure).
- `summary.md`: Markdown summary table sorted with failures (and regressions) first.

## Error counts

- `gpu_errors`: WebGPU uncaptured errors (validation, out of memory).
- `js_errors`: console errors and exceptions, such as Godot `ERROR:` lines.
- `env_errors`: errors from the headless page, not the engine: 404s, and
  pointer lock, media or MIDI permission errors. Not counted as failures.
- `warnings`: Godot `WARNING:` lines. Not counted as failures.

Demos that cannot run on the web on any renderer (`mono/` C# projects and
`xr/openxr_*`) are listed as skipped and not exported.

## Compare Mode

Pass `--compare <old-results.json>` to check for regressions against a previous sweep. `summary.md` gains `Compare` and `Regressions` columns flagging:
- `export ok->fail`
- `gpu_errors 0->N`
- `js_errors` increase
- `frames` dropping to `0` when previously `> 0`

Exits with code `1` if any demo regressed, or `0` otherwise.
