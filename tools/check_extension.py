from __future__ import annotations

import sys
import tempfile
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
sys.path.insert(0, str(EXT_ROOT))


def main() -> int:
    manifest_path = EXT_ROOT / "config" / "extension.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    module_name = manifest["python"]["module"][0]["name"]
    assert module_name == "com.chrisvoncsefalvay.organiq"

    from com.chrisvoncsefalvay.organiq.defaults import defaults_for_label
    from com.chrisvoncsefalvay.organiq.meshing import mesh_selected_labels
    from com.chrisvoncsefalvay.organiq.segmentation import segmentation_from_array

    import numpy as np

    zz, yy, xx = np.mgrid[0:18, 0:18, 0:18]
    labels = np.zeros((18, 18, 18), dtype=np.uint8)
    labels[((xx - 6.0) ** 2 + (yy - 8.0) ** 2 + (zz - 8.0) ** 2) < 24.0] = 1
    labels[(((xx - 12.0) / 3.0) ** 2 + (((yy - 9.0) / 5.0) ** 2) + (((zz - 9.0) / 4.0) ** 2)) < 1.0] = 5
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "spleen", 5: "liver"}, source="monai_bundle")
    meshes = mesh_selected_labels(segmentation, selected_values=(1, 5))
    assert len(meshes) == 2
    assert all(mesh.distance_field is not None for mesh in meshes)
    assert all(len(mesh.vertex_normals) == len(mesh.vertices_m) for mesh in meshes)
    assert defaults_for_label("femur").simulation_mode == "rigid"
    assert defaults_for_label("liver").simulation_mode == "deformable"

    try:
        from com.chrisvoncsefalvay.organiq.usd_writer import export_meshes_to_usd

        out = Path(tempfile.gettempdir()) / "organiq_check.usd"
        result = export_meshes_to_usd(meshes, out)
        assert result.path.exists()
        print(f"usd_export={result.path}")
    except ModuleNotFoundError as exc:
        if exc.name != "pxr":
            raise
        print("usd_export=skipped_no_pxr")

    print("manifest=ok")
    print(f"meshes={len(meshes)}")
    print("check=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

