from __future__ import annotations

import asyncio
import gc
import os
from pathlib import Path

import carb
import omni.kit.app
import omni.ui as ui


DEMO_DICOM_ENV = "ORGANIQ_DEMO_DICOM_FOLDER"
DEMO_REPORT_ENV = "ORGANIQ_DEMO_REPORT_PATH"
EXTENSION_CLASS = "com.chrisvoncsefalvay.organiq.extension.OrganiqExtension"


def _find_organiq_extension():
    for item in gc.get_objects():
        item_type = type(item)
        class_name = f"{item_type.__module__}.{item_type.__name__}"
        if class_name == EXTENSION_CLASS:
            return item
    return None


async def open_demo_chest_ct() -> None:
    app = omni.kit.app.get_app()
    dicom_folder_value = os.environ.get(DEMO_DICOM_ENV)
    if not dicom_folder_value:
        carb.log_error(f"Set {DEMO_DICOM_ENV} to a CT DICOM folder before running the demo loader")
        return
    report_path_value = os.environ.get(DEMO_REPORT_ENV)
    dicom_folder = Path(dicom_folder_value)
    if not dicom_folder.exists():
        carb.log_error(f"Organiq demo DICOM folder does not exist: {dicom_folder}")
        return

    extension = None
    for _ in range(300):
        extension = _find_organiq_extension()
        if extension is not None and getattr(extension, "_window", None) is not None:
            break
        await app.next_update_async()
    if extension is None:
        carb.log_error("Organiq demo loader could not find the Organiq extension instance")
        return

    extension._window.visible = True
    viewport = ui.Workspace.get_window("Viewport")
    if viewport:
        extension._window.dock_in(viewport, ui.DockPosition.LEFT, extension._dock_ratio_for_fixed_width())

    model = None
    for _ in range(120):
        extension._request_build_ui()
        await app.next_update_async()
        model = extension._models.get("dicom_folder")
        if model is not None:
            break
    if model is None:
        carb.log_error("Organiq demo loader could not find the DICOM folder field")
        return
    model.set_value(str(dicom_folder))

    extension._set_status("loading demo chest CT")
    series = extension._workflow.scan(dicom_folder)
    if not series:
        extension._set_status("demo folder contains no CT series")
        extension._request_build_ui()
        return

    volume = extension._workflow.load_series(series[0].series_uid)
    extension._update_volume_preview_providers(volume)
    extension._mark_section_complete("load")
    extension._set_status(f"Loaded {volume.series.file_count} CT slices from {volume.series.patient_id}")
    extension._build_ui()
    await app.next_update_async()
    if report_path_value:
        report_path = Path(report_path_value)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            "\n".join(
                (
                    "status=ok",
                    f"folder={dicom_folder}",
                    f"patient={volume.series.patient_id}",
                    f"series={volume.series.series_uid}",
                    f"slices={volume.series.file_count}",
                    f"rows={volume.series.rows}",
                    f"columns={volume.series.columns}",
                    f"spacing_mm={volume.series.spacing_mm}",
                )
            ),
            encoding="utf-8",
        )
    carb.log_info(f"Organiq demo chest CT loaded from {dicom_folder}")


asyncio.ensure_future(open_demo_chest_ct())
