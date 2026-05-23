import tomllib
from pathlib import Path


def test_extension_manifest_points_at_direct_package():
    manifest = tomllib.loads(
        Path("source/extensions/com.chrisvoncsefalvay.organiq/config/extension.toml").read_text(encoding="utf-8")
    )
    assert manifest["package"]["name"] == "com.chrisvoncsefalvay.organiq"
    assert not manifest["package"]["name"].startswith("omni")
    assert manifest["package"]["repository"] == "https://github.com/chrisvoncsefalvay/organiq"
    assert {"omniverse", "kit", "extension"}.issubset(set(manifest["package"]["keywords"]))
    assert manifest["package"]["readme"] == "docs/README.md"
    assert manifest["package"]["changelog"] == "docs/CHANGELOG.md"
    assert manifest["package"]["icon"] == "data/icon.png"
    assert manifest["package"]["preview_image"] == "data/preview.png"
    assert set(manifest["package"]["target"]["platform"]) == {"linux-x86_64", "windows-x86_64"}
    assert manifest["package"]["target"]["kit"] == ["107.3.3"]
    assert manifest["python"]["module"][0]["name"] == "com.chrisvoncsefalvay.organiq"
    assert "omni.physx" in manifest["dependencies"]
    assert "omni.kit.window.filepicker" in manifest["dependencies"]
    assert "omni.kit.viewport.utility" in manifest["dependencies"]
    assert "isaacsim.gui.components" in manifest["dependencies"]

