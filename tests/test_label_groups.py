from com.chrisvoncsefalvay.organiq.label_groups import group_key_for_label, group_segment_labels
from com.chrisvoncsefalvay.organiq.models import SegmentLabel


def test_total_segmentator_labels_are_grouped_by_anatomy():
    labels = (
        SegmentLabel(13, "lung_upper_lobe_left", 10),
        SegmentLabel(18, "vertebrae_L5", 20),
        SegmentLabel(58, "rib_left_1", 30),
        SegmentLabel(44, "heart_myocardium", 40),
        SegmentLabel(104, "urinary_bladder", 50),
        SegmentLabel(32760, "skin_shell", 60),
        SegmentLabel(999, "tumour_candidate", 60),
    )

    groups = group_segment_labels(labels)
    grouped = {group.key: [label.name for label in group.labels] for group in groups}

    assert grouped["body_shell"] == ["skin_shell"]
    assert grouped["lungs"] == ["lung_upper_lobe_left"]
    assert grouped["vertebrae"] == ["vertebrae_L5"]
    assert grouped["ribs"] == ["rib_left_1"]
    assert grouped["heart"] == ["heart_myocardium"]
    assert grouped["kidneys_and_urinary"] == ["urinary_bladder"]
    assert grouped["other"] == ["tumour_candidate"]


def test_group_key_handles_threshold_and_custom_names():
    assert group_key_for_label("lung") == "threshold_classes"
    assert group_key_for_label("soft tissue") == "threshold_classes"
    assert group_key_for_label("portal_vein_and_splenic_vein") == "blood_vessels"
    assert group_key_for_label("skin_shell") == "body_shell"
    assert group_key_for_label("left temporal lesion") == "other"

