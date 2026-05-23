from types import SimpleNamespace

import numpy as np

from com.chrisvoncsefalvay.organiq.dicom import _nifti_affine, _slice_sort_key, _spacing_from_header
from com.chrisvoncsefalvay.organiq.models import Volume


def test_slice_sort_key_uses_image_orientation_normal():
    ds_far = SimpleNamespace(
        ImageOrientationPatient=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ImagePositionPatient=(0.0, 10.0, 0.0),
        InstanceNumber=2,
    )
    ds_near = SimpleNamespace(
        ImageOrientationPatient=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        ImagePositionPatient=(0.0, 5.0, 0.0),
        InstanceNumber=1,
    )

    assert _slice_sort_key(ds_far) < _slice_sort_key(ds_near)
    assert _slice_sort_key(ds_far) != float(ds_far.ImagePositionPatient[2])


def test_spacing_uses_projected_slice_positions():
    ds = SimpleNamespace(PixelSpacing=(0.75, 0.5), SliceThickness=1.0)

    assert _spacing_from_header(ds, [-10.0, -5.0, 0.0]) == (5.0, 0.75, 0.5)


def test_nifti_affine_uses_volume_direction_and_origin():
    volume = Volume(
        data=np.zeros((2, 3, 4), dtype=np.float32),
        spacing_mm=(2.0, 3.0, 4.0),
        origin_mm=(10.0, 20.0, 30.0),
        direction=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    )

    affine = _nifti_affine(volume, np)

    np.testing.assert_allclose(affine[:3, 0], (4.0, 0.0, 0.0))
    np.testing.assert_allclose(affine[:3, 1], (0.0, 0.0, 3.0))
    np.testing.assert_allclose(affine[:3, 2], (0.0, -2.0, 0.0))
    np.testing.assert_allclose(affine[:3, 3], (10.0, 20.0, 30.0))
