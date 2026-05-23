import numpy as np

from com.chrisvoncsefalvay.organiq.segmentation import segmentation_from_array
from com.chrisvoncsefalvay.organiq.workflow import OrganiqWorkflow


def test_workflow_meshes_selected_monai_labels():
    workflow = OrganiqWorkflow()
    labels = np.zeros((8, 8, 8), dtype=np.uint8)
    labels[2:5, 2:5, 2:5] = 1
    labels[5:7, 1:4, 1:4] = 5
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "spleen", 5: "liver"}, source="monai_bundle")
    workflow.state.segmentation = segmentation
    workflow.selected_label_values = {5}

    meshes = workflow.mesh_selected()

    assert [mesh.label_name for mesh in meshes] == ["liver"]
    assert workflow.state.status.startswith("Meshed")


def test_workflow_mesh_progress_reports_real_label_counts():
    workflow = OrganiqWorkflow()
    labels = np.zeros((8, 8, 8), dtype=np.uint8)
    labels[1:3, 1:3, 1:3] = 1
    labels[4:6, 4:6, 4:6] = 5
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "spleen", 5: "liver"})
    workflow.state.segmentation = segmentation
    workflow.selected_label_values = {1, 5}
    calls = []

    workflow.mesh_selected(progress=lambda completed, total, status: calls.append((completed, total, status)))

    assert calls[0][:2] == (0, 2)
    assert calls[-1][:2] == (2, 2)


def test_workflow_batch_label_selection():
    workflow = OrganiqWorkflow()
    labels = np.zeros((4, 4, 4), dtype=np.uint8)
    labels[0:2, 0:2, 0:2] = 1
    labels[2:4, 2:4, 2:4] = 5
    segmentation = segmentation_from_array(labels, (1.0, 1.0, 1.0), {1: "spleen", 5: "liver"}, source="monai_bundle")
    workflow.state.segmentation = segmentation
    values = {label.name: label.value for label in segmentation.labels}

    workflow.select_no_labels()
    assert workflow.selected_label_values == set()
    assert workflow.mesh_selected() == []

    workflow.set_labels_selected((values["spleen"], values["liver"]), True)
    assert workflow.selected_label_values == {values["spleen"], values["liver"]}

    workflow.set_labels_selected((values["liver"],), False)
    assert workflow.selected_label_values == {values["spleen"]}

    workflow.select_all_labels()
    assert workflow.selected_label_values == {label.value for label in segmentation.labels}


def test_workflow_exposes_monai_only_segmentation_path():
    workflow = OrganiqWorkflow()

    assert hasattr(workflow, "segment_monai")
    assert not hasattr(workflow, "segment_threshold")
    assert not hasattr(workflow, "segment_custom")

