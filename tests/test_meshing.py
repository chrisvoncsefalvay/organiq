import numpy as np
import pytest

from com.chrisvoncsefalvay.organiq.meshing import (
    MESHING_METHOD_MARCHING_CUBES,
    MESHING_METHOD_SDF,
    _clean_triangle_mesh,
    _mesh_with_voxel_faces,
    mesh_binary_mask,
    mesh_label,
    mesh_selected_labels,
)
from com.chrisvoncsefalvay.organiq.models import SegmentLabel, SegmentationResult
from com.chrisvoncsefalvay.organiq.segmentation import SYNTHETIC_SKIN_LABEL_VALUE
from com.chrisvoncsefalvay.organiq.segmentation import segmentation_from_array


def test_mesh_label_builds_surface_geometry():
    labels = np.zeros((4, 4, 4), dtype=np.uint8)
    labels[1:3, 1:3, 1:3] = 1
    mesh = mesh_label(labels, 1, "bone", (1.0, 1.0, 1.0))
    assert mesh.label_name == "bone"
    assert mesh.meshing_method == MESHING_METHOD_SDF
    assert len(mesh.vertices_m) > 0
    assert len(mesh.faces) > 0
    assert len(mesh.vertex_normals) == len(mesh.vertices_m)


def test_mesh_selected_labels_respects_selection():
    labels = np.zeros((4, 4, 4), dtype=np.uint8)
    labels[1:3, 1:3, 1:3] = 1
    labels[0:2, 0:2, 0:2] = 2
    hu = np.full(labels.shape, -1000.0, dtype=np.float32)
    hu[labels == 1] = 700.0
    hu[labels == 2] = -800.0
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "bone", 2: "lung"}, source_volume=hu)
    meshes = mesh_selected_labels(segmentation, selected_values=(2,))
    assert [mesh.label_name for mesh in meshes] == ["lung"]
    assert meshes[0].mean_hounsfield == -800.0


def test_mesh_selected_labels_uses_auxiliary_skin_shell_mask():
    label_volume = np.zeros((5, 5, 5), dtype=np.uint8)
    body_mask = np.zeros_like(label_volume, dtype=bool)
    body_mask[1:4, 1:4, 1:4] = True
    segmentation = SegmentationResult(
        label_volume=label_volume,
        spacing_mm=(1.0, 1.0, 1.0),
        labels=(SegmentLabel(SYNTHETIC_SKIN_LABEL_VALUE, "skin_shell", 26),),
        source="test",
        auxiliary_label_volumes={SYNTHETIC_SKIN_LABEL_VALUE: body_mask},
    )

    meshes = mesh_selected_labels(segmentation)

    assert [mesh.label_name for mesh in meshes] == ["skin_shell"]
    assert meshes[0].source_voxels == 26


def test_organic_mesher_relaxes_vertices_off_voxel_planes():
    pytest.importorskip("skimage")
    zz, yy, xx = np.mgrid[0:12, 0:12, 0:12]
    mask = ((xx - 5.5) ** 2 + (yy - 5.5) ** 2 + (zz - 5.5) ** 2) < 18.0

    mesh = mesh_binary_mask(mask, 3, "liver", (1.0, 1.0, 1.0), smooth=True)

    coords_mm = np.asarray(mesh.vertices_m) * 1000.0
    fractional = np.abs(coords_mm - np.round(coords_mm))
    assert len(mesh.faces) > 0
    assert np.any((fractional > 1.0e-3) & (fractional < 0.49))
    assert mesh.distance_field is not None
    assert mesh.distance_field.narrow_band_mm == 12.0
    assert mesh.distance_field.min_distance_mm < 0.0
    assert mesh.distance_field.max_distance_mm > 0.0


def test_marching_cubes_mesher_is_available_without_sdf_metadata():
    pytest.importorskip("skimage")
    labels = np.zeros((5, 5, 5), dtype=np.uint8)
    labels[1:4, 1:4, 1:4] = 1

    mesh = mesh_label(labels, 1, "liver", (1.0, 1.0, 1.0), method=MESHING_METHOD_MARCHING_CUBES)

    assert mesh.meshing_method == MESHING_METHOD_MARCHING_CUBES
    assert len(mesh.vertices_m) > 0
    assert len(mesh.faces) > 0
    assert mesh.distance_field is None


def test_sdf_mesher_preserves_boundary_voxel_extent():
    pytest.importorskip("scipy")
    pytest.importorskip("skimage")
    mask = np.ones((1, 1, 1), dtype=bool)

    mesh = mesh_binary_mask(mask, 1, "bone", (1.0, 1.0, 1.0), smooth=False, method=MESHING_METHOD_SDF)

    vertices = np.asarray(mesh.vertices_m)
    np.testing.assert_allclose(vertices.min(axis=0), (-0.0005, -0.0005, -0.0005), atol=1.0e-7)
    np.testing.assert_allclose(vertices.max(axis=0), (0.0005, 0.0005, 0.0005), atol=1.0e-7)


def test_voxel_fallback_uses_centered_voxel_coordinates():
    mask = np.ones((1, 1, 1), dtype=bool)

    mesh = _mesh_with_voxel_faces(mask, 1, "bone", (1.0, 1.0, 1.0), None)

    vertices = np.asarray(mesh.vertices_m)
    np.testing.assert_allclose(vertices.min(axis=0), (-0.0005, -0.0005, -0.0005), atol=1.0e-12)
    np.testing.assert_allclose(vertices.max(axis=0), (0.0005, 0.0005, 0.0005), atol=1.0e-12)


def test_clean_triangle_mesh_removes_degenerate_and_duplicate_faces():
    vertices = np.asarray(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 1.0),
        )
    )
    faces = np.asarray(
        (
            (0, 1, 2),
            (0, 2, 1),
            (0, 0, 1),
            (0, 3, 4),
        )
    )

    clean_vertices, clean_faces = _clean_triangle_mesh(vertices, faces)

    assert clean_vertices.shape == (3, 3)
    assert clean_faces.tolist() == [[0, 1, 2]]


def test_clean_triangle_mesh_ignores_out_of_range_faces():
    vertices = np.asarray(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
    faces = np.asarray(((0, 1, 2), (0, 1, 99), (-1, 0, 2)))

    clean_vertices, clean_faces = _clean_triangle_mesh(vertices, faces)

    assert clean_vertices.shape == (3, 3)
    assert clean_faces.tolist() == [[0, 1, 2]]

