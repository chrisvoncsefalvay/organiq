from com.chrisvoncsefalvay.organiq.previews import _fit_size


def test_fit_size_preserves_physical_height_from_slice_spacing():
    assert _fit_size(width=20, height=10, size=100, row_spacing_mm=5.0, col_spacing_mm=1.0) == (100, 40)


def test_fit_size_preserves_physical_width_from_column_spacing():
    assert _fit_size(width=20, height=10, size=100, row_spacing_mm=1.0, col_spacing_mm=5.0) == (10, 100)
