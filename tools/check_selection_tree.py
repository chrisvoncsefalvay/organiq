from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

import carb
import omni.kit.app
import omni.ui as ui


REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = REPO_ROOT / "source" / "extensions" / "com.chrisvoncsefalvay.organiq"
BUILD_ROOT = REPO_ROOT / "build"
sys.path.insert(0, str(EXT_ROOT))

from com.chrisvoncsefalvay.organiq.extension import _LabelSelectionTreeDelegate, _LabelSelectionTreeModel
from com.chrisvoncsefalvay.organiq.label_groups import group_segment_labels
from com.chrisvoncsefalvay.organiq.models import SegmentLabel
from com.chrisvoncsefalvay.organiq.workflow import OrganiqWorkflow


class _Harness:
    def __init__(self, labels: tuple[SegmentLabel, ...]):
        self._workflow = OrganiqWorkflow()
        self._workflow.selected_label_values = {label.value for label in labels}
        self.status = ""

    def _set_label_selected(self, label_value: int, selected: bool) -> None:
        self._workflow.set_label_selected(label_value, selected)
        self.status = f"selected {len(self._workflow.selected_label_values)} labels"

    def _set_group_selected(self, label_values: tuple[int, ...], selected: bool) -> None:
        self._workflow.set_labels_selected(label_values, selected)
        self.status = f"selected {len(self._workflow.selected_label_values)} labels"


async def _run() -> int:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = BUILD_ROOT / "organiq_selection_tree_check.json"

    labels = (
        SegmentLabel(13, "lung_upper_lobe_left", 10),
        SegmentLabel(14, "lung_lower_lobe_left", 11),
        SegmentLabel(18, "vertebrae_L5", 12),
        SegmentLabel(58, "rib_left_1", 13),
        SegmentLabel(44, "heart_myocardium", 14),
        SegmentLabel(104, "urinary_bladder", 15),
        SegmentLabel(32760, "skin_shell", 17),
        SegmentLabel(999, "custom_lesion", 16),
    )
    groups = group_segment_labels(labels)
    harness = _Harness(labels)
    model = _LabelSelectionTreeModel(groups)
    delegate = _LabelSelectionTreeDelegate(harness)
    window = ui.Window("Organiq selection tree check", width=390, height=420, visible=True)
    with window.frame:
        tree_view = ui.TreeView(
            model,
            delegate=delegate,
            root_visible=False,
            header_visible=False,
            columns_resizable=False,
            column_widths=[30, ui.Percent(100)],
            height=320,
            keep_alive=True,
        )

    app = omni.kit.app.get_app()
    for _ in range(6):
        await app.next_update_async()

    root_items = model.get_item_children(None)
    _require(len(root_items) >= 5, "tree did not expose grouped root items")
    _require(any(item.group.key == "body_shell" for item in root_items), "body shell group missing")
    _require(any(item.group.key == "lungs" for item in root_items), "lungs group missing")
    _require(any(item.group.key == "vertebrae" for item in root_items), "vertebrae group missing")
    _require(any(item.group.key == "ribs" for item in root_items), "ribs group missing")
    _require(any(item.group.key == "other" for item in root_items), "other group missing")

    lung_item = next(item for item in root_items if item.group.key == "lungs")
    lung_values = tuple(label.value for label in lung_item.group.labels)
    harness._set_group_selected(lung_values, False)
    tree_view.dirty_widgets()
    for _ in range(2):
        await app.next_update_async()
    _require(not any(value in harness._workflow.selected_label_values for value in lung_values), "lung clear failed")

    harness._set_group_selected(lung_values, True)
    tree_view.dirty_widgets()
    for _ in range(2):
        await app.next_update_async()
    _require(all(value in harness._workflow.selected_label_values for value in lung_values), "lung select failed")

    report = {
        "status": "ok",
        "groups": [item.group.key for item in root_items],
        "selected_count": len(harness._workflow.selected_label_values),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    carb.log_info(f"Organiq selection tree check passed: {report_path}")
    print("organiq_selection_tree_check=ok")
    print(f"report={report_path}")
    return 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _entry() -> None:
    exit_code = 1
    try:
        exit_code = await _run()
    except Exception as exc:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = BUILD_ROOT / "organiq_selection_tree_check.json"
        report_path.write_text(
            json.dumps({"status": "failed", "error": str(exc), "traceback": traceback.format_exc()}, indent=2),
            encoding="utf-8",
        )
        carb.log_error(f"Organiq selection tree check failed: {exc}")
        print(f"organiq_selection_tree_check=failed: {exc}")
        print(f"report={report_path}")
    finally:
        omni.kit.app.get_app().post_uncancellable_quit(exit_code)


asyncio.ensure_future(_entry())

