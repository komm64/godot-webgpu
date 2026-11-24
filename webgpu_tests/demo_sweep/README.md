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
  [--seconds 15] \
  [--jobs 1] \
  [--compare <old-results.json>]
```

Key options:
- `--filter <substring>`: Run only demos whose relative path contains `<substring>`.
- `--jobs <N>`: Export up to `N` demos in parallel (default `1`).
- `--seconds <N>`: Run time per demo in headless Chrome before capturing metrics and screenshot (default `15`).
- `--chrome <path>`: Chrome executable (Windows default: `C:/Program Files/Google/Chrome/Application/chrome.exe`).

## Outputs

Written under `--out`:
- `work/<name>/`: Temporary patched copy of each demo (`project.godot` + `export_presets.cfg`).
- `web/<name>/index.html`: Exported WebGPU build with `"renderingDriver":"webgpu"` patched into `GODOT_CONFIG`.
- `shots/<name>.png`: Screenshot captured after `--seconds`.
- `results.json`: Per-demo JSON records (`name`, `export_ok`, `export_rc`, `first_frame_ms`, `frames`, `gpu_errors`, `js_errors`, `first_errors`, `shot`, plus `export_log` on export failure).
- `summary.md`: Markdown summary table sorted with failures (and regressions) first.

## Compare Mode

Pass `--compare <old-results.json>` to check for regressions against a previous sweep. `summary.md` gains `Compare` and `Regressions` columns flagging:
- `export ok->fail`
- `gpu_errors 0->N`
- `js_errors` increase
- `frames` dropping to `0` when previously `> 0`

Exits with code `1` if any demo regressed, or `0` otherwise.
