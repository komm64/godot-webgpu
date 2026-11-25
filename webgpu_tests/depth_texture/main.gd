extends Node3D
# Exercises the two ways the Mobile renderer samples a depth buffer as a float
# texture: a user shader with hint_depth_texture (left half) and the depth of
# field blur (far objects on the right half are blurred).


func _ready() -> void:
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_COLOR
	env.environment.background_color = Color(0.4, 0.5, 0.7)
	add_child(env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 30, 0)
	add_child(sun)

	var camera := Camera3D.new()
	camera.position = Vector3(0, 1.5, 4)
	camera.far = 50.0
	var attributes := CameraAttributesPractical.new()
	attributes.dof_blur_far_enabled = true
	attributes.dof_blur_far_distance = 8.0
	attributes.dof_blur_far_transition = 4.0
	attributes.dof_blur_amount = 0.2
	camera.attributes = attributes
	add_child(camera)

	var floor_mesh := MeshInstance3D.new()
	floor_mesh.mesh = PlaneMesh.new()
	(floor_mesh.mesh as PlaneMesh).size = Vector2(40, 40)
	add_child(floor_mesh)

	# Two rows of boxes from near to far, one per half of the screen, so the
	# depth bands (left) and the blur (right) both cover near and far.
	for i in 8:
		for side in [-1.0, 1.0]:
			var box := MeshInstance3D.new()
			box.mesh = BoxMesh.new()
			var material := StandardMaterial3D.new()
			material.albedo_color = Color.from_hsv(i / 8.0, 0.7, 0.9)
			box.material_override = material
			box.position = Vector3(side * 1.6, 0.5, 1.0 - i * 3.0)
			add_child(box)

	# Left half of the screen: x from -1 to 0 in clip space.
	var quad := MeshInstance3D.new()
	var quad_mesh := QuadMesh.new()
	quad_mesh.size = Vector2(1, 2)
	quad_mesh.center_offset = Vector3(-0.5, 0, 0)
	quad.mesh = quad_mesh
	var depth_material := ShaderMaterial.new()
	depth_material.shader = load("res://depth_view.gdshader")
	quad.material_override = depth_material
	quad.extra_cull_margin = 16384.0
	camera.add_child(quad)
