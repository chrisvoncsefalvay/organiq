from __future__ import annotations

from collections.abc import Iterable
from typing import Callable

from .defaults import defaults_for_label
from .models import DistanceFieldMetadata, MeshArtifact, SegmentationResult, TissueDefaults


SDF_NARROW_BAND_MM = 12.0
SDF_PADDING_VOXELS = 1
VOXEL_FACE_FALLBACK_LIMIT = 20000
MESHING_METHOD_SDF = "sdf"
MESHING_METHOD_MARCHING_CUBES = "marching_cubes"
MESHING_METHODS = (MESHING_METHOD_SDF, MESHING_METHOD_MARCHING_CUBES)
ProgressCallback = Callable[[int, int, str], None]


class _FallbackMeshingError(RuntimeError):
    pass


def mesh_selected_labels(
    segmentation: SegmentationResult,
    selected_values: Iterable[int] | None = None,
    smooth: bool = True,
    method: str = MESHING_METHOD_SDF,
    progress: ProgressCallback | None = None,
) -> list[MeshArtifact]:
    if selected_values is None:
        selected = {label.value for label in segmentation.labels if label.selected}
    else:
        selected = {int(value) for value in selected_values}
    by_value = {label.value: label for label in segmentation.labels}
    ordered_values = [value for value in sorted(selected) if value != 0 and value in by_value]
    total = max(len(ordered_values), 1)
    _report_progress(progress, 0, total, "preparing selected labels")
    meshes: list[MeshArtifact] = []
    for index, value in enumerate(ordered_values, start=1):
        label = by_value[value]
        _report_progress(progress, index - 1, total, f"meshing {label.name}")
        auxiliary_mask = segmentation.auxiliary_label_volumes.get(value)
        if auxiliary_mask is None:
            mesh = mesh_label(
                segmentation.label_volume,
                value,
                label.name,
                segmentation.spacing_mm,
                smooth=smooth,
                method=method,
                source_voxels=label.voxel_count,
                mean_hounsfield=label.mean_hounsfield,
            )
        else:
            mesh = mesh_binary_mask(
                auxiliary_mask,
                value,
                label.name,
                segmentation.spacing_mm,
                smooth=smooth,
                method=method,
                source_voxels=label.voxel_count,
                mean_hounsfield=label.mean_hounsfield,
            )
        meshes.append(mesh)
        _report_progress(progress, index, total, f"meshed {index} of {total} labels")
    return meshes


def _report_progress(progress: ProgressCallback | None, completed: int, total: int, status: str) -> None:
    if progress is None:
        return
    progress(int(completed), max(int(total), 1), status)


def mesh_label(
    label_volume,
    label_value: int,
    label_name: str,
    spacing_mm: tuple[float, float, float],
    smooth: bool = True,
    method: str = MESHING_METHOD_SDF,
    source_voxels: int | None = None,
    mean_hounsfield: float | None = None,
) -> MeshArtifact:
    import numpy as np

    mask = np.asarray(label_volume == label_value, dtype=bool)
    return mesh_binary_mask(
        mask,
        label_value,
        label_name,
        spacing_mm,
        smooth=smooth,
        method=method,
        source_voxels=source_voxels,
        mean_hounsfield=mean_hounsfield,
    )


def mesh_binary_mask(
    label_mask,
    label_value: int,
    label_name: str,
    spacing_mm: tuple[float, float, float],
    smooth: bool = True,
    method: str = MESHING_METHOD_SDF,
    source_voxels: int | None = None,
    mean_hounsfield: float | None = None,
) -> MeshArtifact:
    import numpy as np

    mask = np.asarray(label_mask, dtype=bool)
    if mask.ndim != 3:
        raise ValueError(f"label mask must be 3D, got shape {mask.shape}")
    spacing = _normalise_spacing_mm(spacing_mm)

    defaults = defaults_for_label(label_name)
    extraction_method = normalise_meshing_method(method)
    try:
        if extraction_method == MESHING_METHOD_MARCHING_CUBES:
            return _mesh_with_marching_cubes(
                mask,
                label_value,
                label_name,
                spacing,
                source_voxels,
                mean_hounsfield,
                smooth,
                defaults,
            )
        return _mesh_with_sdf(mask, label_value, label_name, spacing, source_voxels, mean_hounsfield, smooth, defaults)
    except (ImportError, _FallbackMeshingError):
        return _mesh_with_voxel_faces(
            mask,
            label_value,
            label_name,
            spacing,
            source_voxels,
            extraction_method,
            mean_hounsfield,
        )


def normalise_meshing_method(method: str) -> str:
    value = str(method or MESHING_METHOD_SDF).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "signed_distance_field": MESHING_METHOD_SDF,
        "distance_field": MESHING_METHOD_SDF,
        "sdf": MESHING_METHOD_SDF,
        "marching": MESHING_METHOD_MARCHING_CUBES,
        "marching_cube": MESHING_METHOD_MARCHING_CUBES,
        "marching_cubes": MESHING_METHOD_MARCHING_CUBES,
    }
    if value in aliases:
        return aliases[value]
    if value in MESHING_METHODS:
        return value
    raise ValueError(f"unknown meshing method: {method}")


def _normalise_spacing_mm(spacing_mm: tuple[float, float, float]) -> tuple[float, float, float]:
    if len(spacing_mm) != 3:
        raise ValueError(f"spacing_mm must contain three values, got {spacing_mm!r}")
    spacing = tuple(float(value) for value in spacing_mm)
    if any(value <= 0.0 for value in spacing):
        raise ValueError(f"spacing_mm values must be positive, got {spacing_mm!r}")
    return spacing


def _mesh_with_sdf(
    mask,
    label_value: int,
    label_name: str,
    spacing_mm: tuple[float, float, float],
    source_voxels: int | None,
    mean_hounsfield: float | None,
    smooth: bool,
    defaults: TissueDefaults,
) -> MeshArtifact:
    import numpy as np
    from skimage import measure

    mask = np.asarray(mask, dtype=bool)
    crop, offset_zyx, source_count = _active_region(mask, spacing_mm, SDF_NARROW_BAND_MM)
    prepared = _prepare_mask(crop, smooth, defaults)
    if not bool(np.any(prepared)):
        raise _FallbackMeshingError("mesh preprocessing removed all label voxels")

    padded = np.pad(prepared, SDF_PADDING_VOXELS, mode="constant", constant_values=False)
    sdf_mm = _signed_distance_field_mm(padded, spacing_mm, SDF_NARROW_BAND_MM)
    sdf_metadata = _distance_field_metadata(sdf_mm, spacing_mm, SDF_NARROW_BAND_MM)
    field = sdf_mm.astype(np.float32, copy=False)
    if smooth:
        field = _smooth_field(field, spacing_mm, defaults)
    if float(field.min()) > 0.0 or float(field.max()) < 0.0:
        raise _FallbackMeshingError("label has no extractable SDF zero level set")

    sz, sy, sx = (float(value) / 1000.0 for value in spacing_mm)
    vertices_zyx, faces = _extract_zero_level_set(field, (sz, sy, sx), measure)
    vertices = _vertices_zyx_to_xyz_m(vertices_zyx, spacing_mm, offset_zyx, SDF_PADDING_VOXELS)
    if smooth and defaults.mesh_smoothing_iterations > 0:
        vertices = _taubin_smooth(
            vertices,
            faces,
            defaults.mesh_smoothing_iterations,
            defaults.mesh_relax_lambda,
            defaults.mesh_relax_mu,
            spacing_mm,
            defaults.mesh_smoothing_mm,
        )
    vertices, faces = _clean_triangle_mesh(vertices, faces)
    vertices, faces = _curvature_adaptive_decimate(vertices, faces, defaults)
    normals = _compute_vertex_normals(vertices, faces)

    vertices_m = tuple((float(v[0]), float(v[1]), float(v[2])) for v in vertices)
    face_tuple = tuple(tuple(int(i) for i in face) for face in faces)
    return MeshArtifact(
        label_value=label_value,
        label_name=label_name,
        vertices_m=vertices_m,
        faces=face_tuple,
        source_voxels=int(source_voxels if source_voxels is not None else source_count),
        mean_hounsfield=mean_hounsfield,
        meshing_method=MESHING_METHOD_SDF,
        distance_field=sdf_metadata,
        vertex_normals=tuple((float(n[0]), float(n[1]), float(n[2])) for n in normals),
    )


def _mesh_with_marching_cubes(
    mask,
    label_value: int,
    label_name: str,
    spacing_mm: tuple[float, float, float],
    source_voxels: int | None,
    mean_hounsfield: float | None,
    smooth: bool,
    defaults: TissueDefaults,
) -> MeshArtifact:
    import numpy as np
    from skimage import measure

    mask = np.asarray(mask, dtype=bool)
    crop, offset_zyx, source_count = _active_region(mask, spacing_mm, SDF_NARROW_BAND_MM)
    prepared = _prepare_mask(crop, smooth, defaults)
    if not bool(np.any(prepared)):
        raise _FallbackMeshingError("mesh preprocessing removed all label voxels")

    field = np.pad(prepared.astype(np.float32, copy=False), SDF_PADDING_VOXELS, mode="constant", constant_values=0.0)
    if float(field.min()) >= 0.5 or float(field.max()) < 0.5:
        raise _FallbackMeshingError("label has no extractable surface")

    sz, sy, sx = (float(value) / 1000.0 for value in spacing_mm)
    try:
        vertices_zyx, faces, _, _ = measure.marching_cubes(
            field,
            level=0.5,
            spacing=(sz, sy, sx),
            allow_degenerate=False,
        )
    except ValueError as exc:
        raise _FallbackMeshingError(str(exc)) from exc
    vertices = _vertices_zyx_to_xyz_m(vertices_zyx, spacing_mm, offset_zyx, SDF_PADDING_VOXELS)
    if smooth and defaults.mesh_smoothing_iterations > 0:
        vertices = _taubin_smooth(
            vertices,
            faces,
            defaults.mesh_smoothing_iterations,
            defaults.mesh_relax_lambda,
            defaults.mesh_relax_mu,
            spacing_mm,
            defaults.mesh_smoothing_mm,
        )
    vertices, faces = _clean_triangle_mesh(vertices, faces)
    vertices, faces = _curvature_adaptive_decimate(vertices, faces, defaults)
    normals = _compute_vertex_normals(vertices, faces)

    return MeshArtifact(
        label_value=label_value,
        label_name=label_name,
        vertices_m=tuple((float(v[0]), float(v[1]), float(v[2])) for v in vertices),
        faces=tuple(tuple(int(i) for i in face) for face in faces),
        source_voxels=int(source_voxels if source_voxels is not None else source_count),
        mean_hounsfield=mean_hounsfield,
        meshing_method=MESHING_METHOD_MARCHING_CUBES,
        vertex_normals=tuple((float(n[0]), float(n[1]), float(n[2])) for n in normals),
    )


def _prepare_mask(mask, smooth: bool, defaults: TissueDefaults):
    if not smooth:
        return mask
    try:
        from scipy import ndimage as ndi
    except Exception:
        return mask

    clean = mask
    if defaults.mesh_closing_iterations > 0:
        structure = ndi.generate_binary_structure(3, 2)
        clean = ndi.binary_closing(clean, structure=structure, iterations=int(defaults.mesh_closing_iterations))
    if defaults.semantic_class not in {"bone", "fluid_like"}:
        clean = ndi.binary_fill_holes(clean)
    if defaults.mesh_keep_largest_component:
        labels, count = ndi.label(clean)
        if count > 1:
            sizes = ndi.sum(clean, labels, index=range(1, count + 1))
            keep = int(sizes.argmax()) + 1
            clean = labels == keep
    return clean


def _active_region(mask, spacing_mm: tuple[float, float, float], margin_mm: float):
    import math
    import numpy as np

    mask = np.asarray(mask, dtype=bool)
    occupied = np.nonzero(mask)
    if occupied[0].size == 0:
        raise ValueError("label has no voxels")

    margins = tuple(max(1, int(math.ceil(float(margin_mm) / spacing))) for spacing in spacing_mm)
    starts = tuple(max(0, int(axis.min()) - margin) for axis, margin in zip(occupied, margins))
    stops = tuple(
        min(int(mask.shape[index]), int(axis.max()) + margin + 1)
        for index, (axis, margin) in enumerate(zip(occupied, margins))
    )
    slices = tuple(slice(start, stop) for start, stop in zip(starts, stops))
    return np.ascontiguousarray(mask[slices]), starts, int(occupied[0].size)


def _vertices_zyx_to_xyz_m(vertices_zyx, spacing_mm: tuple[float, float, float], offset_zyx, padding_voxels: int):
    import numpy as np

    spacing_m = np.asarray(spacing_mm, dtype=np.float64) / 1000.0
    offset_zyx = np.asarray(offset_zyx, dtype=np.float64)
    origin_shift_zyx_m = (offset_zyx - float(padding_voxels)) * spacing_m
    vertices_zyx = np.asarray(vertices_zyx, dtype=np.float64)
    return np.column_stack(
        (
            vertices_zyx[:, 2] + origin_shift_zyx_m[2],
            vertices_zyx[:, 1] + origin_shift_zyx_m[1],
            vertices_zyx[:, 0] + origin_shift_zyx_m[0],
        )
    )


def _smooth_field(field, spacing_mm: tuple[float, float, float], defaults: TissueDefaults):
    if defaults.mesh_smoothing_mm <= 0.0:
        return field
    try:
        from scipy import ndimage as ndi
    except Exception:
        return field

    sigma = tuple(min(float(defaults.mesh_smoothing_mm) / float(spacing), 3.0) for spacing in spacing_mm)
    if max(sigma) <= 0.0:
        return field
    return ndi.gaussian_filter(field, sigma=sigma, mode="nearest", truncate=3.0)


def _signed_distance_field_mm(mask, spacing_mm: tuple[float, float, float], narrow_band_mm: float):
    import numpy as np
    from scipy import ndimage as ndi

    mask = np.asarray(mask, dtype=bool)
    sampling = tuple(float(value) for value in spacing_mm)
    outside_mm = ndi.distance_transform_edt(~mask, sampling=sampling)
    inside_mm = ndi.distance_transform_edt(mask, sampling=sampling)
    sdf_mm = outside_mm - inside_mm
    return np.clip(sdf_mm, -float(narrow_band_mm), float(narrow_band_mm)).astype(np.float32)


def _distance_field_metadata(
    sdf_mm,
    spacing_mm: tuple[float, float, float],
    narrow_band_mm: float,
) -> DistanceFieldMetadata:
    import numpy as np

    field = np.asarray(sdf_mm, dtype=np.float32)
    return DistanceFieldMetadata(
        shape=tuple(int(value) for value in field.shape),
        spacing_mm=tuple(float(value) for value in spacing_mm),
        narrow_band_mm=float(narrow_band_mm),
        min_distance_mm=float(np.min(field)),
        max_distance_mm=float(np.max(field)),
    )


def _extract_zero_level_set(field, spacing_m: tuple[float, float, float], skimage_measure):
    vertices, faces = _extract_zero_level_set_with_flying_edges(field, spacing_m)
    if vertices is not None and faces is not None:
        return vertices, faces
    try:
        vertices, faces, _, _ = skimage_measure.marching_cubes(
            field,
            level=0.0,
            spacing=spacing_m,
            allow_degenerate=False,
        )
    except ValueError as exc:
        raise _FallbackMeshingError(str(exc)) from exc
    return vertices, faces


def _extract_zero_level_set_with_flying_edges(field, spacing_m: tuple[float, float, float]):
    try:
        import numpy as np
        import vtk
        from vtk.util import numpy_support
    except Exception:
        return None, None

    data = np.asarray(field, dtype=np.float32)
    image = vtk.vtkImageData()
    image.SetDimensions(int(data.shape[2]), int(data.shape[1]), int(data.shape[0]))
    image.SetSpacing(float(spacing_m[2]), float(spacing_m[1]), float(spacing_m[0]))
    scalars = numpy_support.numpy_to_vtk(data.ravel(order="C"), deep=True, array_type=vtk.VTK_FLOAT)
    scalars.SetName("sdf_mm")
    image.GetPointData().SetScalars(scalars)

    flying_edges = vtk.vtkFlyingEdges3D()
    flying_edges.SetInputData(image)
    flying_edges.SetValue(0, 0.0)
    flying_edges.ComputeNormalsOff()
    flying_edges.Update()
    poly = flying_edges.GetOutput()
    points = poly.GetPoints()
    polys = poly.GetPolys()
    if points is None or polys is None or points.GetNumberOfPoints() == 0 or polys.GetNumberOfCells() == 0:
        return None, None

    vertices_xyz = numpy_support.vtk_to_numpy(points.GetData()).astype(np.float64, copy=False)
    faces = _triangles_from_vtk_polys(numpy_support.vtk_to_numpy(polys.GetData()))
    if faces.size == 0:
        return None, None
    vertices_zyx = np.column_stack((vertices_xyz[:, 2], vertices_xyz[:, 1], vertices_xyz[:, 0]))
    return vertices_zyx, faces


def _triangles_from_vtk_polys(connectivity):
    import numpy as np

    connectivity = np.asarray(connectivity, dtype=np.int64)
    if connectivity.size == 0:
        return np.empty((0, 3), dtype=np.int64)
    if connectivity.size % 4 == 0:
        cells = connectivity.reshape((-1, 4))
        if np.all(cells[:, 0] == 3):
            return np.ascontiguousarray(cells[:, 1:4], dtype=np.int64)

    faces = []
    index = 0
    while index < len(connectivity):
        count = int(connectivity[index])
        if count == 3:
            faces.append(tuple(int(value) for value in connectivity[index + 1 : index + 4]))
        index += count + 1
    return np.asarray(faces, dtype=np.int64)


def _taubin_smooth(
    vertices,
    faces,
    iterations: int,
    relax_lambda: float,
    relax_mu: float,
    spacing_mm: tuple[float, float, float] | None = None,
    displacement_limit_mm: float = 1.0,
):
    import numpy as np

    if len(vertices) == 0 or len(faces) == 0:
        return vertices
    if len(vertices) > 400000:
        return vertices
    iteration_count = int(iterations)
    if len(vertices) > 150000:
        iteration_count = min(iteration_count, 2)
    if iteration_count <= 0:
        return vertices

    edges, neighbour_counts = _directed_unique_edges(faces, len(vertices))
    if edges.size == 0:
        return vertices

    original = vertices.astype(np.float64, copy=True)
    current = original.copy()
    for _ in range(iteration_count):
        current = _laplacian_step(current, edges, neighbour_counts, relax_lambda)
        current = _laplacian_step(current, edges, neighbour_counts, relax_mu)
        current = _limit_vertex_displacement(original, current, displacement_limit_mm, spacing_mm)
    return current


def _directed_unique_edges(faces, vertex_count: int):
    import numpy as np

    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return np.empty((0, 2), dtype=np.int64), np.zeros(vertex_count, dtype=np.int64)

    directed = np.concatenate(
        (
            faces[:, (0, 1)],
            faces[:, (1, 2)],
            faces[:, (2, 0)],
            faces[:, (1, 0)],
            faces[:, (2, 1)],
            faces[:, (0, 2)],
        ),
        axis=0,
    )
    directed = np.unique(directed, axis=0)
    counts = np.bincount(directed[:, 0], minlength=vertex_count).astype(np.int64, copy=False)
    return directed, counts


def _limit_vertex_displacement(
    original,
    current,
    displacement_limit_mm: float,
    spacing_mm: tuple[float, float, float] | None,
):
    import numpy as np

    if len(original) == 0:
        return current
    spacing_floor_mm = min(float(value) for value in spacing_mm) if spacing_mm else 1.0
    limit_m = max(float(displacement_limit_mm), spacing_floor_mm * 0.5) / 1000.0
    delta = current - original
    distance = np.linalg.norm(delta, axis=1)
    over_limit = distance > limit_m
    if np.any(over_limit):
        scale = limit_m / np.maximum(distance[over_limit], 1.0e-12)
        current[over_limit] = original[over_limit] + delta[over_limit] * scale[:, None]
    return current


def _clean_triangle_mesh(vertices, faces):
    import numpy as np

    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if vertices.size == 0 or faces.size == 0:
        raise ValueError("mesh has no geometry")
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError("mesh vertices must have shape (N, 3)")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("mesh faces must have shape (N, 3)")

    finite_vertices = np.isfinite(vertices).all(axis=1)
    valid_faces = np.all(faces >= 0, axis=1)
    valid_faces &= np.all(faces < len(vertices), axis=1)
    if np.any(valid_faces):
        valid_indices = np.flatnonzero(valid_faces)
        valid_faces[valid_indices] &= np.all(finite_vertices[faces[valid_indices]], axis=1)
    valid_faces &= faces[:, 0] != faces[:, 1]
    valid_faces &= faces[:, 1] != faces[:, 2]
    valid_faces &= faces[:, 0] != faces[:, 2]
    faces = faces[valid_faces]
    if len(faces) == 0:
        raise ValueError("mesh has no valid faces")

    edge_ab = vertices[faces[:, 1]] - vertices[faces[:, 0]]
    edge_ac = vertices[faces[:, 2]] - vertices[faces[:, 0]]
    area = np.linalg.norm(np.cross(edge_ab, edge_ac), axis=1) * 0.5
    extent = np.ptp(vertices, axis=0)
    extent_scale = max(float(np.linalg.norm(extent)), 1.0e-6)
    min_area = max((extent_scale * 1.0e-7) ** 2, 1.0e-14)
    faces = faces[area > min_area]
    if len(faces) == 0:
        raise ValueError("mesh has no non-degenerate faces")

    _, unique_indices = np.unique(np.sort(faces, axis=1), axis=0, return_index=True)
    faces = faces[np.sort(unique_indices)]

    used_vertices, inverse = np.unique(faces.reshape(-1), return_inverse=True)
    vertices = vertices[used_vertices]
    faces = inverse.reshape((-1, 3)).astype(np.int64, copy=False)
    return vertices, faces


def _curvature_adaptive_decimate(vertices, faces, defaults: TissueDefaults):
    if len(faces) <= 180000 or defaults.semantic_class in {"bone", "surface_shell"}:
        return vertices, faces
    decimated = _decimate_with_vtk(vertices, faces, target_reduction=0.35)
    if decimated is None:
        return vertices, faces
    return decimated


def _decimate_with_vtk(vertices, faces, target_reduction: float):
    try:
        import numpy as np
        import vtk
        from vtk.util import numpy_support
    except Exception:
        return None

    points = vtk.vtkPoints()
    points.SetData(numpy_support.numpy_to_vtk(np.asarray(vertices, dtype=np.float64), deep=True))

    faces = np.asarray(faces, dtype=np.int64)
    cells = np.empty((len(faces), 4), dtype=np.int64)
    cells[:, 0] = 3
    cells[:, 1:4] = faces
    cell_array = vtk.vtkCellArray()
    cell_array.SetCells(
        len(faces),
        numpy_support.numpy_to_vtkIdTypeArray(cells.reshape(-1), deep=True),
    )

    poly = vtk.vtkPolyData()
    poly.SetPoints(points)
    poly.SetPolys(cell_array)

    decimate = vtk.vtkQuadricDecimation()
    decimate.SetInputData(poly)
    decimate.SetTargetReduction(float(target_reduction))
    decimate.AttributeErrorMetricOn()
    decimate.VolumePreservationOn()
    decimate.Update()

    out = decimate.GetOutput()
    out_points = out.GetPoints()
    out_polys = out.GetPolys()
    if out_points is None or out_polys is None:
        return None

    out_vertices = numpy_support.vtk_to_numpy(out_points.GetData()).astype(np.float64, copy=False)
    out_faces = _triangles_from_vtk_polys(numpy_support.vtk_to_numpy(out_polys.GetData()))
    if out_faces.size == 0:
        return None
    return _clean_triangle_mesh(out_vertices, out_faces)


def _compute_vertex_normals(vertices, faces):
    import numpy as np

    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    normals = np.zeros_like(vertices, dtype=np.float64)
    if len(vertices) == 0 or len(faces) == 0:
        return normals

    tri_vertices = vertices[faces]
    face_normals = np.cross(tri_vertices[:, 1] - tri_vertices[:, 0], tri_vertices[:, 2] - tri_vertices[:, 0])
    lengths = np.linalg.norm(face_normals, axis=1)
    valid = lengths > 1.0e-12
    face_normals[~valid] = 0.0
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)

    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1.0e-12
    normals[valid] /= lengths[valid][:, None]
    normals[~valid] = (0.0, 0.0, 1.0)
    return normals


def _laplacian_step(vertices, edges, neighbour_counts, factor: float):
    import numpy as np

    updated = vertices.copy()
    active = neighbour_counts > 0
    if not bool(np.any(active)):
        return updated
    accum = np.zeros_like(vertices, dtype=np.float64)
    np.add.at(accum, edges[:, 0], vertices[edges[:, 1]])
    mean = accum[active] / neighbour_counts[active, None]
    updated[active] += float(factor) * (mean - vertices[active])
    return updated


def _mesh_with_voxel_faces(
    mask,
    label_value: int,
    label_name: str,
    spacing_mm: tuple[float, float, float],
    source_voxels: int | None,
    method: str = MESHING_METHOD_SDF,
    mean_hounsfield: float | None = None,
) -> MeshArtifact:
    import numpy as np

    mask = np.asarray(mask, dtype=bool)
    coords = np.argwhere(mask)
    if coords.size == 0:
        raise ValueError("label has no voxels")
    if coords.shape[0] > VOXEL_FACE_FALLBACK_LIMIT:
        raise RuntimeError("scikit-image is required for large masks")

    spacing_xyz_m = np.asarray((spacing_mm[2], spacing_mm[1], spacing_mm[0]), dtype=np.float64) / 1000.0
    vertex_blocks = []
    shape = mask.shape

    face_defs = (
        ((-1, 0, 0), ((0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1))),
        ((1, 0, 0), ((1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0))),
        ((0, -1, 0), ((0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0))),
        ((0, 1, 0), ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))),
        ((0, 0, -1), ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))),
        ((0, 0, 1), ((0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1))),
    )

    for (dz, dy, dx), corners in face_defs:
        neighbours = coords + np.asarray((dz, dy, dx), dtype=np.int64)
        inside_bounds = (
            (neighbours[:, 0] >= 0)
            & (neighbours[:, 0] < shape[0])
            & (neighbours[:, 1] >= 0)
            & (neighbours[:, 1] < shape[1])
            & (neighbours[:, 2] >= 0)
            & (neighbours[:, 2] < shape[2])
        )
        occupied = np.zeros(len(coords), dtype=bool)
        if np.any(inside_bounds):
            bounded = neighbours[inside_bounds]
            occupied[inside_bounds] = mask[bounded[:, 0], bounded[:, 1], bounded[:, 2]]
        exposed = coords[~occupied]
        if exposed.size == 0:
            continue

        corners_zyx = np.asarray(corners, dtype=np.int64)
        face_vertices_zyx = exposed[:, None, :] + corners_zyx[None, :, :]
        face_vertices_xyz = face_vertices_zyx[:, :, (2, 1, 0)]
        vertex_blocks.append(face_vertices_xyz.reshape((-1, 3)))

    if not vertex_blocks:
        raise ValueError("mesh has no exposed voxel faces")

    all_face_vertices = np.concatenate(vertex_blocks, axis=0)
    unique_vertices_int, inverse = np.unique(all_face_vertices, axis=0, return_inverse=True)
    quads = inverse.reshape((-1, 4))
    clean_vertices = (unique_vertices_int.astype(np.float64) - 0.5) * spacing_xyz_m
    clean_faces = np.empty((len(quads) * 2, 3), dtype=np.int64)
    clean_faces[0::2] = quads[:, (0, 1, 2)]
    clean_faces[1::2] = quads[:, (0, 2, 3)]
    clean_vertices, clean_faces = _clean_triangle_mesh(clean_vertices, clean_faces)
    normals = _compute_vertex_normals(clean_vertices, clean_faces)
    return MeshArtifact(
        label_value=label_value,
        label_name=label_name,
        vertices_m=tuple((float(v[0]), float(v[1]), float(v[2])) for v in clean_vertices),
        faces=tuple(tuple(int(i) for i in face) for face in clean_faces),
        source_voxels=int(source_voxels if source_voxels is not None else coords.shape[0]),
        mean_hounsfield=mean_hounsfield,
        meshing_method=f"{method}_voxel_fallback",
        vertex_normals=tuple((float(n[0]), float(n[1]), float(n[2])) for n in normals),
    )
