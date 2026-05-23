from __future__ import annotations

import asyncio
import gc
import weakref
from pathlib import Path

import carb
import omni.ext
import omni.kit.app
import omni.kit.commands
import omni.ui as ui
import omni.usd
from isaacsim.gui.components.element_wrappers import ScrollingWindow
from isaacsim.gui.components.menu import make_menu_item_description
from omni.kit.menu.utils import add_menu_items, remove_menu_items
from omni.kit.widget.filebrowser import FileBrowserItem
from omni.kit.window.filepicker import FilePickerDialog

from .dependencies import dependency_status, install_missing_packages, missing_packages
from .jobs import ActionProgress
from .label_groups import SegmentLabelGroup, group_segment_labels
from .meshing import MESHING_METHOD_MARCHING_CUBES, MESHING_METHOD_SDF
from .paths import DEFAULT_MODEL_ROOT, DEFAULT_OUTPUT_ROOT
from .previews import build_volume_preview_images
from .segmentation import DEFAULT_MONAI_BUNDLE, MONAI_BUNDLE_PRESETS
from .usd_writer import preview_meshes_on_stage
from .viewport import frame_paths_next_update
from .workflow import OrganiqWorkflow

EXTENSION_NAME = "Organiq"
EXTENSION_VERSION = "0.1.0"
MONAI_BUNDLE_OPTIONS = MONAI_BUNDLE_PRESETS
MESHING_METHOD_OPTIONS = ("signed distance field", "marching cubes")
MESHING_METHOD_OPTION_VALUES = {
    "signed distance field": MESHING_METHOD_SDF,
    "marching cubes": MESHING_METHOD_MARCHING_CUBES,
}
WINDOW_HEIGHT = 760
DOCK_RATIO = 0.42
MAX_DOCK_RATIO = 0.72
PANEL_CONTENT_WIDTH = 560
FLEX_WIDTH = 1.0
ROW_LABEL_WIDTH = 70
BROWSE_BUTTON_WIDTH = 34
PANEL_MIN_WIDTH = 700
PANEL_CHROME_WIDTH = 180
PREVIEW_COLUMN_COUNT = 3
PREVIEW_TILE_WIDTH = 160
PREVIEW_IMAGE_SIZE = 144
PREVIEW_TILE_SPACING = 10
PREVIEW_STRIP_WIDTH = PREVIEW_COLUMN_COUNT * PREVIEW_TILE_WIDTH + (PREVIEW_COLUMN_COUNT - 1) * PREVIEW_TILE_SPACING
WINDOW_WIDTH = max(PANEL_MIN_WIDTH, PREVIEW_STRIP_WIDTH + PANEL_CHROME_WIDTH)
TREE_CHECKBOX_COLUMN_WIDTH = 30
TREE_LABEL_MIN_WIDTH = 180
TREE_LABEL_COLUMN_WIDTH = FLEX_WIDTH
TREE_BRANCH_WIDTH = 18
TREE_LABEL_TEXT_LIMIT = 48
MESH_RECORD_TEXT_LIMIT = 72
PATH_TEXT_LIMIT = 68
ACTION_BUTTON_HEIGHT = 24
CONTENT_BOOKMARK_LABEL = "Organiq outputs"
CONTENT_BROWSER_WINDOW_NAME = "Content"
CONTENT_REVEAL_BUTTON_WIDTH = 72
WORKFLOW_SECTIONS = ("load", "segment", "select", "mesh", "export", "instantiate")
FRAME_STYLE = {
    "CollapsableFrame": {
        "color": 0xFFE7E7E7,
        "font_size": 13,
        "margin_width": 2,
        "margin_height": 2,
        "padding": 4,
    }
}
COMPLETE_FRAME_STYLE = {
    "CollapsableFrame": {
        "color": 0xFF75D98B,
        "font_size": 13,
        "margin_width": 2,
        "margin_height": 2,
        "padding": 4,
    },
    "CollapsableFrame:hovered": {"color": 0xFF91E9A2},
}


def _ellipsize(text: object, limit: int) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _flex_width():
    return ui.Fraction(FLEX_WIDTH)


class _PercentProgressModel(ui.AbstractValueModel):
    def __init__(self, value: float = 0.0):
        super().__init__()
        self._value = max(0.0, min(1.0, float(value)))

    def set_value(self, value):
        clamped = max(0.0, min(1.0, float(value)))
        if clamped != self._value:
            self._value = clamped
            self._value_changed()

    def get_value_as_float(self):
        return self._value

    def get_value_as_string(self):
        return f"{round(self._value * 100)}%"


class _LabelTreeItem(ui.AbstractItem):
    def __init__(self, kind: str, title: str, group: SegmentLabelGroup | None = None, label=None):
        super().__init__()
        self.kind = kind
        self.title = title
        self.group = group
        self.label = label
        self.children: list[_LabelTreeItem] = []
        self.title_model = ui.SimpleStringModel(title)

    @classmethod
    def from_group(cls, group: SegmentLabelGroup) -> "_LabelTreeItem":
        item = cls("group", group.title, group=group)
        item.children = [cls("label", label.name, label=label) for label in group.labels]
        return item


class _LabelSelectionTreeModel(ui.AbstractItemModel):
    def __init__(self, groups: tuple[SegmentLabelGroup, ...]):
        super().__init__()
        self._children = [_LabelTreeItem.from_group(group) for group in groups]

    def get_item_children(self, item=None):
        if item is None:
            return self._children
        return item.children

    def get_item_value_model_count(self, item=None):
        return 2

    def get_item_value_model(self, item=None, column_id: int = 0):
        if item is None:
            return None
        return item.title_model


class _LabelSelectionTreeDelegate(ui.AbstractItemDelegate):
    def __init__(self, extension: "OrganiqExtension"):
        super().__init__()
        self._extension = weakref.proxy(extension)

    def build_branch(self, model, item, column_id, level, expanded):
        if item is None or not item.children:
            ui.Spacer(width=TREE_BRANCH_WIDTH)
            return
        ui.Label(
            "v" if expanded else ">",
            width=TREE_BRANCH_WIDTH,
            alignment=ui.Alignment.CENTER,
            style={"color": 0xFFB8B8B8},
            tooltip="Expand or collapse",
        )

    def build_widget(self, model, item, column_id, level, expanded):
        if item.kind == "group":
            self._build_group_widget(item, column_id)
        else:
            self._build_label_widget(item, column_id)

    def _build_group_widget(self, item: _LabelTreeItem, column_id: int) -> None:
        values = tuple(label.value for label in item.group.labels)
        selected = sum(1 for value in values if value in self._extension._workflow.selected_label_values)
        total = len(values)
        if column_id == 0:
            checkbox_model = ui.SimpleBoolModel(total > 0 and selected == total)
            checkbox_model.add_value_changed_fn(
                lambda model, label_values=values: self._extension._set_group_selected(
                    label_values, bool(model.get_value_as_bool())
                )
            )
            ui.CheckBox(model=checkbox_model, width=22)
        elif column_id == 1:
            text = f"{item.group.title} ({selected}/{total})"
            ui.Label(
                _ellipsize(text, TREE_LABEL_TEXT_LIMIT),
                width=_flex_width(),
                style={"color": 0xFFE7E7E7},
                tooltip=text,
            )

    def _build_label_widget(self, item: _LabelTreeItem, column_id: int) -> None:
        label = item.label
        if column_id == 0:
            checkbox_model = ui.SimpleBoolModel(label.value in self._extension._workflow.selected_label_values)
            checkbox_model.add_value_changed_fn(
                lambda model, value=label.value: self._extension._set_label_selected(
                    value, bool(model.get_value_as_bool())
                )
            )
            ui.CheckBox(model=checkbox_model, width=22)
        elif column_id == 1:
            text = f"{label.value}  {label.name}"
            ui.Label(_ellipsize(text, TREE_LABEL_TEXT_LIMIT), width=_flex_width(), tooltip=text)


class OrganiqExtension(omni.ext.IExt):
    def on_startup(self, ext_id: str):
        self._ext_id = ext_id
        self._workflow = OrganiqWorkflow()
        self._action_task: asyncio.Task | None = None
        self._action_progress: ActionProgress | None = None
        self._dock_task: asyncio.Task | None = None
        self._ui_build_task: asyncio.Task | None = None
        self._auto_show_task: asyncio.Task | None = None
        self._content_browser_task: asyncio.Task | None = None
        self._models: dict[str, object] = {}
        self._status = "ready"
        self._progress_model = _PercentProgressModel(0.0)
        self._progress_status = "idle"
        self._active_section: str | None = None
        self._section_complete = {
            "preflight": False,
            "load": False,
            "segment": False,
            "select": False,
            "mesh": False,
            "export": False,
            "instantiate": False,
        }
        self._section_collapsed = {
            "preflight": True,
            "load": False,
            "segment": False,
            "select": False,
            "mesh": False,
            "export": False,
            "instantiate": False,
        }
        self._section_messages: dict[str, str] = {}
        self._volume_preview_providers: list[tuple[str, object]] = []
        self._path_dialog: FilePickerDialog | None = None
        self._path_dialog_selected_path = ""
        self._label_tree_model: _LabelSelectionTreeModel | None = None
        self._label_tree_delegate: _LabelSelectionTreeDelegate | None = None
        self._label_tree_view: ui.TreeView | None = None
        self._selection_summary_label: ui.Label | None = None

        self._window = ScrollingWindow(
            title=EXTENSION_NAME,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            visible=False,
            dockPreference=ui.DockPreference.LEFT_BOTTOM,
        )
        self._window.set_visibility_changed_fn(self._on_window)
        self._menu_items = [
            make_menu_item_description(ext_id, EXTENSION_NAME, lambda a=weakref.proxy(self): a._menu_callback())
        ]
        add_menu_items(self._menu_items, "Utilities")
        self._auto_show_task = asyncio.ensure_future(self._show_window_on_startup())
        self._schedule_content_browser_reveal(DEFAULT_OUTPUT_ROOT, select_file=False, show_content=False, navigate=False)
        carb.log_info("Organiq extension started.")

    def on_shutdown(self):
        if self._action_progress is not None:
            self._action_progress.cancel()
        if self._action_task and not self._action_task.done():
            self._action_task.cancel()
        if self._dock_task and not self._dock_task.done():
            self._dock_task.cancel()
        if self._ui_build_task and not self._ui_build_task.done():
            self._ui_build_task.cancel()
        if self._auto_show_task and not self._auto_show_task.done():
            self._auto_show_task.cancel()
        if self._content_browser_task and not self._content_browser_task.done():
            self._content_browser_task.cancel()
        self._destroy_path_dialog()
        remove_menu_items(self._menu_items, "Utilities")
        self._label_tree_model = None
        self._label_tree_delegate = None
        self._label_tree_view = None
        self._selection_summary_label = None
        self._volume_preview_providers = []
        self._models = {}
        self._window = None
        gc.collect()

    def _on_window(self, visible):
        if visible:
            self._build_ui()

    def _menu_callback(self):
        self._window.visible = not self._window.visible

    async def _show_window_on_startup(self):
        app = omni.kit.app.get_app()
        for _ in range(8):
            await app.next_update_async()
        if self._window is None:
            return
        self._window.visible = True
        self._request_build_ui()
        carb.log_info("Organiq window shown")

    def _build_ui(self):
        self._apply_window_width()
        with self._window.frame:
            with ui.VStack(spacing=8, height=0, width=_flex_width()):
                self._build_header()
                self._build_preflight_frame()
                self._build_load_frame()
                self._build_volume_preview_strip()
                self._build_segment_frame()
                self._build_select_frame()
                self._build_mesh_frame()
                self._build_export_frame()
                self._build_instantiate_frame()

        self._dock_task = asyncio.ensure_future(self._dock_window())

    def _request_build_ui(self):
        if self._ui_build_task and not self._ui_build_task.done():
            return
        self._ui_build_task = asyncio.ensure_future(self._build_ui_next_update())

    async def _build_ui_next_update(self):
        await omni.kit.app.get_app().next_update_async()
        if self._window is None:
            return
        self._build_ui()

    async def _dock_window(self):
        await omni.kit.app.get_app().next_update_async()
        viewport = ui.Workspace.get_window("Viewport")
        window = ui.Workspace.get_window(EXTENSION_NAME)
        if viewport and window:
            window.dock_in(viewport, ui.DockPosition.LEFT, self._dock_ratio_for_fixed_width())
            self._apply_window_width()

    def _dock_ratio_for_fixed_width(self) -> float:
        try:
            width_getter = getattr(ui.Workspace, "get_main_window_width", None)
            main_width = float(width_getter() if width_getter is not None else ui.get_main_window_width())
        except Exception:
            return DOCK_RATIO
        if main_width <= 0:
            return DOCK_RATIO
        return min(MAX_DOCK_RATIO, max(DOCK_RATIO, WINDOW_WIDTH / main_width))

    def _apply_window_width(self) -> None:
        for window in (self._window, ui.Workspace.get_window(EXTENSION_NAME)):
            if window is None:
                continue
            try:
                window.width = WINDOW_WIDTH
            except Exception:
                pass

    def _build_header(self):
        with ui.VStack(spacing=2, height=0):
            with ui.HStack(height=24, spacing=6):
                ui.Label("Organiq", style={"font_size": 20})
                ui.Spacer(width=4)
                ui.Label(f"v{EXTENSION_VERSION}", width=46, alignment=ui.Alignment.RIGHT_CENTER, style={"font_size": 11, "color": 0xFFB8B8B8})
            ui.Label("Sim Ready anatomy from DICOM volumes", height=18, style={"font_size": 12, "color": 0xFFB8B8B8})

    def _build_preflight_frame(self):
        with self._section_frame("preflight", "0. Environment preflight"):
            with ui.VStack(spacing=6, height=0):
                for status in dependency_status():
                    text = "present" if status.available else "missing"
                    colour = 0xFF87D37C if status.available else 0xFFEB8A77
                    with ui.HStack(height=22):
                        ui.Label(status.dependency.import_name, width=84)
                        ui.Label(text, width=52, style={"color": colour})
                        ui.Label(
                            status.dependency.required_for,
                            width=_flex_width(),
                            word_wrap=True,
                        )
                self._section_button_or_progress("preflight", "install packages", self._install_missing_packages)

    def _build_load_frame(self):
        with self._section_frame("load", "1. Load DICOM folder"):
            with ui.VStack(spacing=6, height=0):
                self._models["dicom_folder"] = self._path_row(
                    "folder",
                    "dicom_folder",
                    self._path_value("dicom_folder"),
                    title="Select DICOM folder",
                    mode="folder",
                )
                self._section_button_or_progress("load", "scan", self._scan_folder)
                if self._workflow.available_series:
                    for series in self._workflow.available_series:
                        with ui.HStack(height=36):
                            summary = (
                                f"{series.series_description or series.series_uid} | "
                                f"{series.file_count} slices | {series.rows or 0} x {series.columns or 0}"
                            )
                            ui.Label(_ellipsize(summary, 76), width=_flex_width(), word_wrap=True, tooltip=summary)
                            ui.Button("load", width=72, clicked_fn=lambda uid=series.series_uid: self._load_series(uid))
                else:
                    ui.Label("no CT series scanned", style={"color": 0xFF909090})

    def _build_volume_preview_strip(self):
        if not self._volume_preview_providers:
            return
        with ui.HStack(height=118, width=PREVIEW_STRIP_WIDTH, spacing=PREVIEW_TILE_SPACING):
            for title, provider in self._volume_preview_providers:
                with ui.VStack(width=PREVIEW_TILE_WIDTH, spacing=4):
                    ui.ImageWithProvider(provider, width=PREVIEW_IMAGE_SIZE, height=PREVIEW_IMAGE_SIZE)
                    ui.Label(
                        title,
                        width=PREVIEW_IMAGE_SIZE,
                        alignment=ui.Alignment.CENTER,
                        style={"color": 0xFFB8B8B8},
                    )

    def _build_segment_frame(self):
        with self._section_frame("segment", "2. Segment volume"):
            with ui.VStack(spacing=6, height=0):
                self._models["bundle_name"] = self._combo_row("bundle", MONAI_BUNDLE_OPTIONS, DEFAULT_MONAI_BUNDLE)
                self._models["bundle_dir"] = self._path_row(
                    "cache",
                    "bundle_dir",
                    self._path_value("bundle_dir", str(DEFAULT_MODEL_ROOT)),
                    title="Select model cache folder",
                    mode="folder",
                )
                self._section_button_or_progress("segment", "segment", self._run_monai)

    def _build_select_frame(self):
        with self._section_frame("select", "3. Select objects to mesh"):
            with ui.VStack(spacing=5, height=0):
                segmentation = self._workflow.state.segmentation
                if segmentation is None or not segmentation.labels:
                    ui.Label("no labels segmented", style={"color": 0xFF909090})
                    return
                with ui.HStack(height=28, spacing=6):
                    self._selection_summary_label = ui.Label(self._selection_summary_text(), width=_flex_width())
                    ui.Button("select all", width=76, clicked_fn=self._select_all_labels)
                    ui.Button("select none", width=84, clicked_fn=self._select_no_labels)

                self._label_tree_model = _LabelSelectionTreeModel(group_segment_labels(segmentation.labels))
                self._label_tree_delegate = _LabelSelectionTreeDelegate(self)
                self._label_tree_view = ui.TreeView(
                    self._label_tree_model,
                    delegate=self._label_tree_delegate,
                    root_visible=False,
                    header_visible=False,
                    columns_resizable=False,
                    column_widths=[TREE_CHECKBOX_COLUMN_WIDTH, _flex_width()],
                    min_column_widths=[TREE_CHECKBOX_COLUMN_WIDTH, TREE_LABEL_MIN_WIDTH],
                    fixed_width_columns=[True, False],
                    resizeable_on_columns_resized=False,
                    expand_on_branch_click=True,
                    width=_flex_width(),
                    height=320,
                    keep_alive=True,
                )
                self._expand_label_tree()

    def _build_mesh_frame(self):
        with self._section_frame("mesh", "4. Mesh selected objects"):
            with ui.VStack(spacing=6, height=0):
                self._models["mesh_method"] = self._combo_row(
                    "mesher",
                    MESHING_METHOD_OPTIONS,
                    "signed distance field",
                )
                self._section_button_or_progress("mesh", "mesh", self._mesh_selected)
                if self._workflow.state.meshes:
                    for mesh in self._workflow.state.meshes:
                        method = mesh.meshing_method.replace("_", " ")
                        ui.Label(
                            _ellipsize(
                                f"{mesh.label_name}: {len(mesh.vertices_m)} vertices, "
                                f"{len(mesh.faces)} faces | {method}",
                                MESH_RECORD_TEXT_LIMIT,
                            ),
                            width=_flex_width(),
                            tooltip=f"{mesh.label_name}: {len(mesh.vertices_m)} vertices, "
                            f"{len(mesh.faces)} faces | {method}",
                        )

    def _build_export_frame(self):
        with self._section_frame("export", "5. Turn meshes into USD"):
            with ui.VStack(spacing=6, height=0):
                default_output = str(DEFAULT_OUTPUT_ROOT / "organiq_scene.usd")
                self._models["usd_output"] = self._path_row(
                    "USD",
                    "usd_output",
                    self._path_value("usd_output", default_output),
                    title="Select USD output",
                    mode="save",
                    extensions=(".usd", ".usda", ".usdc"),
                    default_extension=".usd",
                )
                self._section_button_or_progress("export", "export", self._export_usd)
                if self._workflow.state.usd_path is not None:
                    usd_path = str(self._workflow.state.usd_path)
                    with ui.HStack(height=24, spacing=6):
                        ui.Label(_ellipsize(usd_path, PATH_TEXT_LIMIT), width=_flex_width(), tooltip=usd_path)
                        ui.Button(
                            "reveal",
                            width=CONTENT_REVEAL_BUTTON_WIDTH,
                            clicked_fn=lambda path=usd_path: self._reveal_usd_in_content(path),
                        )

    def _build_instantiate_frame(self):
        with self._section_frame("instantiate", "6. Instantiate with physics"):
            with ui.VStack(spacing=6, height=0):
                self._models["instance_path"] = self._string_row("prim", "/World/Organiq_instance")
                self._section_button_or_progress("instantiate", "instantiate", self._instantiate)
                ui.Label(
                    "Bone labels become rigid bodies. Soft tissues become deformable surfaces. The outer skin shell is a collision surface.",
                    word_wrap=True,
                )

    def _string_row(self, label: str, default: str):
        model = ui.SimpleStringModel(default)
        with ui.HStack(height=28, spacing=6):
            ui.Label(label, width=ROW_LABEL_WIDTH, style={"color": 0xFFC8C8C8})
            ui.StringField(model=model, width=_flex_width())
        return model

    def _combo_row(self, label: str, options: tuple[str, ...], default: str):
        index = options.index(default) if default in options else 0
        with ui.HStack(height=28, spacing=6):
            ui.Label(label, width=ROW_LABEL_WIDTH, style={"color": 0xFFC8C8C8})
            model = ui.ComboBox(index, *options, name="ComboBox", width=_flex_width(), alignment=ui.Alignment.LEFT_CENTER).model
        return model

    def _section_frame(self, key: str, title: str):
        return ui.CollapsableFrame(
            title,
            collapsed=self._section_collapsed[key],
            height=0,
            style=COMPLETE_FRAME_STYLE if self._section_complete.get(key, False) else FRAME_STYLE,
        )

    def _section_button_or_progress(self, key: str, label: str, clicked_fn) -> None:
        if self._active_section == key:
            with ui.VStack(height=64, spacing=4):
                ui.Label(self._progress_status, style={"color": 0xFFB8B8B8})
                ui.ProgressBar(
                    model=self._progress_model,
                    height=16,
                    style={"ProgressBar": {"secondary_color": 0xFFFFFFFF, "font_size": 12}},
                )
                ui.Button("cancel", height=ACTION_BUTTON_HEIGHT, clicked_fn=self._cancel_active_action)
            return
        ui.Button(label, height=ACTION_BUTTON_HEIGHT, clicked_fn=clicked_fn)

    def _path_row(
        self,
        label: str,
        key: str,
        default: str,
        title: str,
        mode: str,
        extensions: tuple[str, ...] = (),
        default_extension: str = "",
    ):
        model = ui.SimpleStringModel(default)
        with ui.HStack(height=28, spacing=6):
            ui.Label(label, width=ROW_LABEL_WIDTH, style={"color": 0xFFC8C8C8})
            ui.StringField(model=model, width=_flex_width())
            ui.Button(
                "...",
                width=BROWSE_BUTTON_WIDTH,
                clicked_fn=lambda: self._show_path_dialog(key, title, mode, extensions, default_extension),
            )
        return model

    def _set_label_selected(self, label_value: int, selected: bool) -> None:
        self._workflow.set_label_selected(label_value, selected)
        self._set_selection_status()
        self._refresh_selection_tree()

    def _set_group_selected(self, label_values: tuple[int, ...], selected: bool) -> None:
        self._workflow.set_labels_selected(label_values, selected)
        self._set_selection_status()
        self._refresh_selection_tree()

    def _select_all_labels(self) -> None:
        self._workflow.select_all_labels()
        self._set_selection_status()
        self._refresh_selection_tree()

    def _select_no_labels(self) -> None:
        self._workflow.select_no_labels()
        self._set_selection_status()
        self._refresh_selection_tree()

    def _set_selection_status(self) -> None:
        segmentation = self._workflow.state.segmentation
        if segmentation is None:
            self._set_status("no labels segmented")
            return
        selected = len(self._workflow.selected_label_values)
        self._set_status(f"selected {selected} of {len(segmentation.labels)} labels")

    def _selection_summary_text(self) -> str:
        segmentation = self._workflow.state.segmentation
        if segmentation is None:
            return "0 labels selected"
        return f"{len(self._workflow.selected_label_values)} of {len(segmentation.labels)} labels selected"

    def _refresh_selection_tree(self) -> None:
        if self._selection_summary_label is not None:
            try:
                self._selection_summary_label.text = self._selection_summary_text()
            except Exception:
                pass
        if self._label_tree_view is not None:
            self._label_tree_view.dirty_widgets()

    def _expand_label_tree(self) -> None:
        if self._label_tree_view is None or self._label_tree_model is None:
            return
        for item in self._label_tree_model.get_item_children(None):
            try:
                self._label_tree_view.set_expanded(item, True, False)
            except Exception:
                pass

    def _path_value(self, key: str, default: str = "") -> str:
        model = self._models.get(key)
        if model is None:
            return default
        return model.get_value_as_string()

    def _combo_value(self, key: str, options: tuple[str, ...], default: str) -> str:
        model = self._models.get(key)
        if model is None:
            return default
        try:
            index = int(model.get_item_value_model().get_value_as_int())
        except Exception:
            return default
        if 0 <= index < len(options):
            return options[index]
        return default

    def _show_path_dialog(
        self,
        key: str,
        title: str,
        mode: str,
        extensions: tuple[str, ...] = (),
        default_extension: str = "",
    ):
        current_path = self._path_value(key).strip()
        current_directory, current_filename = self._split_picker_path(current_path, mode)
        self._path_dialog_selected_path = current_directory
        self._destroy_path_dialog()

        filter_options = self._file_filter_options(mode, extensions)
        apply_label = "select folder" if mode == "folder" else "select"
        dialog_args = {
            "allow_multi_selection": False,
            "apply_button_label": apply_label,
            "click_apply_handler": lambda filename, dirname: self._apply_path_dialog(
                key, mode, filename, dirname, default_extension
            ),
            "click_cancel_handler": lambda filename, dirname: self._hide_path_dialog(),
            "item_filter_fn": lambda item: self._filter_picker_item(item, mode, extensions),
            "selection_changed_fn": lambda items: self._on_path_dialog_selection_changed(items),
            "current_filename": current_filename,
            "current_directory": current_directory,
        }
        if filter_options:
            dialog_args["item_filter_options"] = filter_options
        self._path_dialog = FilePickerDialog(title, **dialog_args)
        if mode == "folder":
            self._path_dialog.set_filebar_label_name("folder: ")
        self._path_dialog.refresh_current_directory()
        self._path_dialog.show(current_path or current_directory)

    def _apply_path_dialog(self, key: str, mode: str, filename: str, dirname: str, default_extension: str):
        if mode == "folder":
            selected_path = self._path_dialog_selected_path or dirname
            if filename and selected_path == dirname:
                selected_path = self._combine_picker_path(dirname, filename)
        else:
            selected_path = self._combine_picker_path(dirname, filename)
            if mode == "save" and default_extension:
                selected_path = self._ensure_path_extension(selected_path, default_extension)

        selected_path = selected_path.strip()
        if selected_path:
            model = self._models.get(key)
            if model is not None:
                model.set_value(selected_path)
            self._set_status(f"selected {selected_path}")
        self._hide_path_dialog()

    def _hide_path_dialog(self):
        if self._path_dialog is not None:
            self._path_dialog.hide()

    def _destroy_path_dialog(self):
        if self._path_dialog is not None:
            self._path_dialog.destroy()
            self._path_dialog = None

    def _on_path_dialog_selection_changed(self, items: list[FileBrowserItem] | None = None):
        if not items:
            return
        self._path_dialog_selected_path = items[-1].path

    def _filter_picker_item(self, item: FileBrowserItem | None, mode: str, extensions: tuple[str, ...]) -> bool:
        if item is None or item.is_folder:
            return True
        if mode == "folder":
            return False
        if not extensions:
            return True
        if self._path_dialog is not None and self._path_dialog.current_filter_option == 1:
            return True
        return Path(item.path).suffix.lower() in extensions

    def _file_filter_options(self, mode: str, extensions: tuple[str, ...]) -> list[str]:
        if mode == "folder" or not extensions:
            return []
        return [", ".join(f"*{extension}" for extension in extensions), "all files (*)"]

    def _split_picker_path(self, path: str, mode: str) -> tuple[str, str]:
        if not path:
            return "", ""
        normalised = path.replace("\\", "/")
        if mode == "folder" or normalised.endswith("/"):
            return path, ""
        separator = "\\" if "\\" in path and "/" not in path else "/"
        directory, _, filename = path.rpartition(separator)
        return directory, filename

    def _combine_picker_path(self, dirname: str, filename: str) -> str:
        dirname = dirname.strip()
        filename = filename.strip()
        if not filename:
            return dirname
        if not dirname:
            return filename
        separator = "\\" if "\\" in dirname and "/" not in dirname else "/"
        if dirname.endswith(("/", "\\")):
            return f"{dirname}{filename}"
        return f"{dirname}{separator}{filename}"

    def _ensure_path_extension(self, path: str, extension: str) -> str:
        if not path or Path(path).suffix:
            return path
        return f"{path}{extension}"

    def _install_missing_packages(self):
        packages = missing_packages()
        if not packages:
            self._set_status("all optional packages are present")
            self._mark_section_complete("preflight")
            self._request_build_ui()
            return
        self._run_background(
            "installing packages",
            self._install_missing_packages_sync,
            section="preflight",
            complete_section="preflight",
        )

    def _install_missing_packages_sync(self, progress):
        packages = missing_packages()
        total = max(len(packages), 1)
        progress(0, total, "installing packages")
        report = install_missing_packages()
        if report is None:
            progress(total, total, "all optional packages are present")
            return "all optional packages are present"
        carb.log_info("Organiq install command: " + " ".join(report.command))
        if report.stdout:
            carb.log_info("Organiq install stdout: " + report.stdout[-4000:])
        if report.stderr:
            carb.log_warn("Organiq install stderr: " + report.stderr[-4000:])
        if report.failed or report.return_code != 0:
            failed = ", ".join(report.failed) if report.failed else f"pip exited {report.return_code}"
            raise RuntimeError(f"package install failed: {failed}")
        installed = ", ".join(report.installed) if report.installed else "packages"
        progress(total, total, f"installed {len(report.installed)} packages")
        return f"installed {installed}"

    def _scan_folder(self):
        folder = self._path_value("dicom_folder").strip()
        self._run_background(
            "scanning DICOM folder",
            lambda progress: self._workflow.scan(folder, progress=progress),
            section="load",
        )

    def _load_series(self, series_uid: str):
        self._run_background(
            "loading CT series",
            lambda progress: self._workflow.load_series(series_uid, progress=progress),
            on_complete=self._on_volume_loaded,
            section="load",
            complete_section="load",
        )

    def _run_monai(self):
        bundle = self._combo_value("bundle_name", MONAI_BUNDLE_OPTIONS, DEFAULT_MONAI_BUNDLE)
        bundle_dir = self._path_value("bundle_dir", str(DEFAULT_MODEL_ROOT)).strip() or str(DEFAULT_MODEL_ROOT)
        self._run_background(
            "running segmentation",
            lambda progress: self._workflow.segment_monai(bundle, bundle_dir, False, progress=progress),
            section="segment",
            complete_section="segment",
        )

    def _mesh_selected(self):
        self._mark_section_complete("select")
        method_label = self._combo_value("mesh_method", MESHING_METHOD_OPTIONS, "signed distance field")
        method = MESHING_METHOD_OPTION_VALUES.get(method_label, MESHING_METHOD_SDF)
        self._run_background(
            "meshing selected labels",
            lambda progress: self._workflow.mesh_selected(smooth=True, method=method, progress=progress),
            on_complete=self._preview_meshes,
            section="mesh",
            complete_section="mesh",
        )

    def _export_usd(self):
        output_path = self._path_value("usd_output", str(DEFAULT_OUTPUT_ROOT / "organiq_scene.usd")).strip()
        self._run_background(
            "exporting USD",
            lambda progress: self._workflow.export_usd(output_path, progress=progress),
            on_complete=self._on_usd_exported,
            section="export",
            complete_section="export",
        )

    def _instantiate(self):
        try:
            stage = omni.usd.get_context().get_stage()
            if stage is None:
                raise RuntimeError("No USD stage is open")
            prim_path = self._path_value("instance_path", "/World/Organiq_instance").strip() or "/World/Organiq_instance"
            path = self._workflow.instantiate(stage, prim_path=prim_path)
            camera_path = f"{path}/view_camera"
            root_prim = stage.GetPrimAtPath(path)
            if root_prim and root_prim.GetAttribute("organiq:viewCamera"):
                camera_path = root_prim.GetAttribute("organiq:viewCamera").Get() or camera_path
            self._schedule_frame_paths([path], expand_to_meshes=True, camera_path=camera_path, update_count=18)
            if self._workflow.state.usd_path is not None:
                self._schedule_content_browser_reveal(self._workflow.state.usd_path, select_file=True)
            self._set_status(f"instanced {path}")
            self._mark_section_complete("instantiate")
            self._request_build_ui()
        except Exception as exc:
            self._set_status(str(exc))
            carb.log_error(f"Organiq instantiate failed: {exc}")

    def _on_volume_loaded(self, volume):
        self._update_volume_preview_providers(volume)
        if volume.series is not None:
            return f"Loaded {volume.series.file_count} CT slices"
        return "Loaded CT series"

    def _on_usd_exported(self, result):
        usd_path = getattr(result, "path", None) or self._workflow.state.usd_path
        if usd_path is not None:
            self._schedule_content_browser_reveal(usd_path, select_file=True)
            return f"Exported USD {Path(usd_path).name}"
        return "Exported USD"

    def _update_volume_preview_providers(self, volume) -> None:
        providers = []
        for preview in build_volume_preview_images(volume):
            provider = ui.ByteImageProvider()
            provider.set_bytes_data(list(preview.rgba), [preview.width, preview.height])
            providers.append((preview.name, provider))
        self._volume_preview_providers = providers

    def _preview_meshes(self, meshes):
        if not meshes:
            return "Meshed 0 labels"
        stage = omni.usd.get_context().get_stage()
        if stage is None:
            return f"Meshed {len(meshes)} labels"
        preview = preview_meshes_on_stage(stage, meshes)
        self._schedule_frame_paths(preview.mesh_paths or (preview.root_path,), camera_path=preview.camera_path)
        return f"Previewed {len(meshes)} meshes on stage"

    def _schedule_frame_paths(
        self,
        paths,
        expand_to_meshes: bool = False,
        camera_path: str | None = None,
        update_count: int = 2,
    ):
        path_tuple = tuple(str(path) for path in paths if path)
        if not path_tuple:
            return
        asyncio.ensure_future(frame_paths_next_update(path_tuple, expand_to_meshes, camera_path, update_count))

    def _reveal_usd_in_content(self, usd_path: str | Path) -> None:
        self._schedule_content_browser_reveal(usd_path, select_file=True)

    def _schedule_content_browser_reveal(
        self,
        path: str | Path,
        select_file: bool = False,
        show_content: bool = True,
        navigate: bool = True,
    ) -> None:
        if self._content_browser_task and not self._content_browser_task.done():
            self._content_browser_task.cancel()
        self._content_browser_task = asyncio.ensure_future(
            self._show_in_content_browser_async(path, select_file=select_file, show_content=show_content, navigate=navigate)
        )

    async def _show_in_content_browser_async(
        self,
        path: str | Path,
        select_file: bool = False,
        show_content: bool = True,
        navigate: bool = True,
    ) -> bool:
        app = omni.kit.app.get_app()
        try:
            from omni.kit.window.content_browser import get_content_window
        except Exception as exc:
            carb.log_warn(f"Organiq could not access the Content browser: {exc}")
            return False

        target = Path(path).expanduser()
        try:
            target = target.resolve()
        except Exception:
            target = Path(path)
        target_folder = target if target.is_dir() else target.parent
        if str(target_folder):
            target_folder.mkdir(parents=True, exist_ok=True)

        content_browser = get_content_window()
        if show_content:
            try:
                ui.Workspace.show_window(CONTENT_BROWSER_WINDOW_NAME, True)
            except Exception:
                try:
                    if content_browser is not None and content_browser.window is None:
                        content_browser.show_window(None, True)
                except Exception:
                    pass

        for _ in range(120):
            content_browser = get_content_window()
            if content_browser is not None and content_browser.api is not None:
                break
            await app.next_update_async()
        if content_browser is None or content_browser.api is None:
            carb.log_warn("Organiq could not initialise the Content browser")
            return False

        output_root = Path(DEFAULT_OUTPUT_ROOT).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            view = content_browser.api.view
            if not view.is_bookmark(None, path=str(output_root)):
                for bookmark in list(view.all_collection_items("bookmarks")):
                    if bookmark.name == CONTENT_BOOKMARK_LABEL:
                        view.delete_bookmark(bookmark)
                        break
                content_browser.toggle_bookmark_from_path(
                    CONTENT_BOOKMARK_LABEL,
                    str(output_root),
                    True,
                    is_folder=True,
                )
        except Exception as exc:
            carb.log_warn(f"Organiq could not bookmark output USDs: {exc}")

        if navigate:
            folder_path = str(target_folder)
            try:
                await content_browser.navigate_to_async(folder_path)
            except Exception as exc:
                carb.log_warn(f"Organiq could not navigate the Content browser to {folder_path}: {exc}")
                return False
            try:
                content_browser.refresh_current_directory()
            except Exception:
                pass
            if select_file and target.is_file():
                try:
                    await content_browser.select_items_async(folder_path, filenames=[target.name])
                except Exception as exc:
                    carb.log_warn(f"Organiq could not select {target.name} in the Content browser: {exc}")
        return True

    def _run_background(self, status: str, fn, on_complete=None, section: str | None = None, complete_section: str | None = None):
        if self._action_task and not self._action_task.done():
            self._set_status("busy")
            self._request_build_ui()
            return
        progress = ActionProgress(status)
        self._action_progress = progress
        self._active_section = section
        if section is not None:
            self._open_section(section)
        self._set_status(status)
        self._set_progress(0.0, status)
        self._request_build_ui()
        self._action_task = asyncio.ensure_future(
            self._run_background_async(fn, progress, on_complete, complete_section)
        )

    async def _run_background_async(self, fn, progress: ActionProgress, on_complete=None, complete_section: str | None = None):
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(None, lambda: fn(progress))
        try:
            while not future.done():
                value, status = progress.snapshot()
                self._set_progress(value, status)
                await omni.kit.app.get_app().next_update_async()
            result = await future
            value, status = progress.snapshot()
            self._set_progress(value, status)
            self._set_progress(1.0, "complete")
            if complete_section is not None:
                self._mark_section_complete(complete_section)
            complete_status = on_complete(result) if on_complete is not None else None
            if isinstance(complete_status, str) and complete_status:
                self._set_status(complete_status)
            elif isinstance(result, str) and result:
                self._set_status(result)
            else:
                self._set_status("ready")
        except Exception as exc:
            self._set_progress(1.0, "failed")
            self._set_status(str(exc))
            carb.log_error(f"Organiq action failed: {exc}")
        self._active_section = None
        if self._action_progress is progress:
            self._action_progress = None
        self._request_build_ui()

    def _cancel_active_action(self):
        if self._action_progress is None:
            self._set_status("no active action")
            return
        self._action_progress.cancel()
        self._set_progress(*self._action_progress.snapshot())
        self._set_status("cancelling active action")
        self._request_build_ui()

    def _set_status(self, status: str):
        self._status = status
        carb.log_info(f"Organiq: {status}")

    def _set_progress(self, value: float, text: str | None = None):
        clamped = max(0.0, min(1.0, float(value)))
        self._progress_model.set_value(clamped)
        if text is not None:
            self._progress_status = text

    def _mark_section_complete(self, key: str) -> None:
        if key in self._section_complete:
            self._section_complete[key] = True
        self._open_next_incomplete_section()

    def _open_section(self, key: str) -> None:
        for section in self._section_collapsed:
            self._section_collapsed[section] = True
        if key in self._section_collapsed:
            self._section_collapsed[key] = False

    def _open_next_incomplete_section(self) -> None:
        for section in self._section_collapsed:
            self._section_collapsed[section] = True
        for section in WORKFLOW_SECTIONS:
            if not self._section_complete.get(section, False):
                self._section_collapsed[section] = False
                return
