// Left half of the screen: linear depth read from DEPTH_TEXTURE, drawn as
// bands one unit apart. A flat colour means the depth texture reads as zero.
shader_type spatial;
render_mode unshaded, depth_test_disabled, depth_draw_never, cull_disabled, shadows_disabled;

uniform sampler2D depth_texture : hint_depth_texture, filter_nearest;

void vertex() {
	POSITION = vec4(VERTEX.xy, 1.0, 1.0);
}

void fragment() {
	float depth = texture(depth_texture, SCREEN_UV).r;
	vec4 view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, depth, 1.0);
	float linear_depth = -view.z / view.w;
	ALBEDO = vec3(fract(linear_depth), fract(linear_depth * 0.25), 0.2);
	ALPHA = 1.0;
}
