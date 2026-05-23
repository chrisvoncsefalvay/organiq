import zipfile

from tools import package_extension


def test_release_archive_import_root_is_extension_root(tmp_path):
    archive_path = tmp_path / "com.chrisvoncsefalvay.organiq-0.1.0.zip"

    package_extension._write_archive(archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert "config/extension.toml" in names
    assert "com/chrisvoncsefalvay/organiq/__init__.py" in names
    assert "com.chrisvoncsefalvay.organiq/config/extension.toml" not in names


def test_release_archive_name_is_kit_importable_package_id():
    assert package_extension.archive_name({"name": "com.chrisvoncsefalvay.organiq", "version": "0.1.0"}) == (
        "com.chrisvoncsefalvay.organiq-0.1.0.zip"
    )
