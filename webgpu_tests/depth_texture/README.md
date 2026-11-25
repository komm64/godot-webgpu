# Depth texture test

Checks the two paths where the Mobile renderer samples a depth buffer as a
float texture, which WebGPU does not allow directly (the driver binds a float
copy of the depth buffer instead):

- left half: a user shader with `hint_depth_texture`, drawing linear depth as
  bands one unit apart. A flat colour means the depth reads as zero.
- right half: depth of field (`CameraAttributesPractical` far blur). The far
  boxes should be blurred, the near ones sharp.

Run it like a demo:

```sh
python webgpu_tests/demo_sweep/sweep.py --demos webgpu_tests/depth_texture ...
```

and look at `shots/depth_texture.png`.
