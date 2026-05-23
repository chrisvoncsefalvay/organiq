from __future__ import annotations

from dataclasses import dataclass

from .models import Volume


@dataclass(frozen=True)
class VolumePreviewImage:
    name: str
    width: int
    height: int
    rgba: bytes


def build_volume_preview_images(volume: Volume, size: int = 128) -> tuple[VolumePreviewImage, ...]:
    import numpy as np

    data = np.asarray(volume.data, dtype=np.float32)
    if data.ndim != 3 or min(data.shape) <= 0:
        return ()

    slice_spacing, row_spacing, col_spacing = (float(value) for value in volume.spacing_mm)
    axial = data[data.shape[0] // 2, :, :]
    coronal = data[:, data.shape[1] // 2, :]
    sagittal = data[:, :, data.shape[2] // 2]
    return (
        _preview_from_slice("axial", axial, size, row_spacing, col_spacing),
        _preview_from_slice("coronal", coronal, size, slice_spacing, col_spacing),
        _preview_from_slice("sagittal", sagittal, size, slice_spacing, row_spacing),
    )


def _preview_from_slice(
    name: str,
    slice_data,
    size: int,
    row_spacing_mm: float = 1.0,
    col_spacing_mm: float = 1.0,
) -> VolumePreviewImage:
    import numpy as np

    image = np.asarray(slice_data, dtype=np.float32)
    image = np.flipud(image)
    image = _window_ct_slice(image)
    image = _resize_nearest(image, size, row_spacing_mm, col_spacing_mm)
    alpha = np.full(image.shape, 255, dtype=np.uint8)
    rgba = np.stack((image, image, image, alpha), axis=-1)
    return VolumePreviewImage(name=name, width=int(rgba.shape[1]), height=int(rgba.shape[0]), rgba=rgba.tobytes())


def _window_ct_slice(image):
    import numpy as np

    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return np.zeros(image.shape, dtype=np.uint8)
    low = max(float(np.percentile(finite, 1.0)), -1000.0)
    high = min(float(np.percentile(finite, 99.0)), 1000.0)
    if high <= low:
        high = low + 1.0
    clipped = np.clip(image, low, high)
    return np.asarray((clipped - low) * (255.0 / (high - low)), dtype=np.uint8)


def _resize_nearest(image, size: int, row_spacing_mm: float = 1.0, col_spacing_mm: float = 1.0):
    import numpy as np

    if image.size == 0:
        return np.zeros((size, size), dtype=np.uint8)
    height, width = image.shape
    target_height, target_width = _fit_size(width, height, size, row_spacing_mm, col_spacing_mm)
    y_idx = np.linspace(0, height - 1, target_height).astype(np.int64)
    x_idx = np.linspace(0, width - 1, target_width).astype(np.int64)
    resized = image[y_idx][:, x_idx]
    canvas = np.zeros((size, size), dtype=np.uint8)
    y0 = (size - target_height) // 2
    x0 = (size - target_width) // 2
    canvas[y0 : y0 + target_height, x0 : x0 + target_width] = resized
    return canvas


def _fit_size(
    width: int,
    height: int,
    size: int,
    row_spacing_mm: float = 1.0,
    col_spacing_mm: float = 1.0,
) -> tuple[int, int]:
    physical_width = float(width) * max(float(col_spacing_mm), 1.0e-6)
    physical_height = float(height) * max(float(row_spacing_mm), 1.0e-6)
    scale = float(size) / max(physical_width, physical_height, 1.0e-6)
    return max(1, int(round(physical_height * scale))), max(1, int(round(physical_width * scale)))
