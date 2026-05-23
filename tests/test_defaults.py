from com.chrisvoncsefalvay.organiq.defaults import defaults_for_label, is_rigid_label, normalise_label_name


def test_label_normalisation():
    assert normalise_label_name("Left Kidney") == "left_kidney"


def test_bone_aliases_are_rigid():
    assert is_rigid_label("left femur")
    assert defaults_for_label("mandible").density_kg_m3 >= 1800.0


def test_soft_tissue_aliases_are_deformable():
    assert defaults_for_label("liver").simulation_mode == "deformable"
    assert defaults_for_label("renal cortex").name == "kidney"
    assert defaults_for_label("aorta").semantic_class == "fluid_like"


def test_skin_shell_defaults_are_static_surface_collision_shell():
    defaults = defaults_for_label("skin_shell")
    assert defaults.simulation_mode == "surface_shell"
    assert defaults.semantic_class == "skin"
    assert defaults.deformable_resolution == 0
    assert defaults.mesh_smoothing_mm >= 2.0

