# Godot WebGPU

Godot Engine with a WebGPU rendering driver for web exports, tracking
upstream **stable** releases.

| Branch | Based on |
|---|---|
| `4.7` | Godot 4.7.2-stable |

This is a continuation of [dwalter/godotwebgpu](https://github.com/dwalter/godotwebgpu),
which built the WebGPU driver on Godot 4.6.2 and has not been updated since
May 2026. The goal here is narrow: keep that driver working on current
stable Godot releases, with as small a diff from upstream as possible.
No unrelated engine changes are included.

## Credits

- **[David Walter](https://github.com/dwalter)** created the WebGPU
  `RenderingDeviceDriver` (`drivers/webgpu/`), the SPIR-V → WGSL pipeline
  built on Tint, and the web platform integration. His 165 commits are
  merged unchanged, with their original hashes and authorship.
- **[Shane Gadsby](https://github.com/Shane-Gadsby)** adapted the driver to
  the Godot 4.7 `RenderingDeviceDriver` interface
  ([Shane-Gadsby/godotwebgpu](https://github.com/Shane-Gadsby/godotwebgpu)).
  That commit is cherry-picked with its authorship kept.
- Godot Engine is developed by the
  [Godot Engine contributors](https://github.com/godotengine/godot).

On top of that, this fork adds fixes found while shipping web apps on
WebGPU, such as eager texture destruction to avoid VRAM growth,
written-range buffer flushes, storage texture shadow syncing,
threaded-rendering fixes and Windows build support.

## Maintenance

This fork is maintained for the maintainer's own projects, which ship on
it. Issues and pull requests are welcome, but they are handled on a
best-effort basis. New stable Godot releases are picked up when those
projects move to them.

## Building the web export template

Requirements:
- Emscripten 4.0.10 or newer
- `glslangValidator`
- A host C++ compiler, used to build `bin/tint_convert_cli` (the build-time
  SPIR-V → WGSL precompiler)

```sh
scons platform=web target=template_release webgpu=yes dlink_enabled=yes threads=no opengl3=no
```

The build compiles `drivers/webgpu/tint_cli` first, then precompiles the
engine shaders to WGSL. On Windows it uses WSL's `bash`, or Git Bash with
LLVM `clang++`.

The `komm64 WebGPU Web Build` GitHub Actions workflow builds the template
on demand (Actions → Run workflow), and for `v*-webgpu*` tags.

## Using it

Export from a stock Godot 4.7 editor with the custom web template built
above. Use the Mobile renderer (`rendering/renderer/rendering_method.web =
"mobile"`). The page needs `renderingDriver: "webgpu"` in `GODOT_CONFIG`.
This fork's export plugin sets it when you export with an editor built from
this branch. With a stock editor, add it to the exported `index.html`.

Browsers without WebGPU support cannot run these exports.

## License

MIT, like Godot. See [LICENSE.txt](LICENSE.txt) and
[COPYRIGHT.txt](COPYRIGHT.txt) for the third-party components, including
Tint (BSD-3-Clause) and SPIRV-Tools (Apache-2.0).

The upstream Godot README is at
[godotengine/godot](https://github.com/godotengine/godot#readme).
