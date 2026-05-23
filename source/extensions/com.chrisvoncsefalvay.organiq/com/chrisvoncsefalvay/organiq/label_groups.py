from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .defaults import normalise_label_name
from .models import SegmentLabel


@dataclass(frozen=True)
class SegmentLabelGroup:
    key: str
    title: str
    labels: tuple[SegmentLabel, ...]


_GROUP_RULES: tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...] = (
    ("body_shell", "body shell", (re.compile(r"skin_shell"), re.compile(r"outer_skin"), re.compile(r"body_surface"))),
    ("lungs", "lungs", (re.compile(r"^lung_"),)),
    ("heart", "heart", (re.compile(r"^heart_"), re.compile(r"myocard"))),
    (
        "blood_vessels",
        "blood vessels",
        (
            re.compile(r"aorta"),
            re.compile(r"artery"),
            re.compile(r"vena"),
            re.compile(r"vein"),
            re.compile(r"vessel"),
        ),
    ),
    (
        "kidneys_and_urinary",
        "kidneys and urinary",
        (re.compile(r"kidney"), re.compile(r"renal"), re.compile(r"adrenal"), re.compile(r"urinary_bladder")),
    ),
    (
        "gastrointestinal",
        "gastrointestinal",
        (
            re.compile(r"esophagus"),
            re.compile(r"stomach"),
            re.compile(r"bowel"),
            re.compile(r"duodenum"),
            re.compile(r"colon"),
            re.compile(r"gallbladder"),
            re.compile(r"pancreas"),
        ),
    ),
    ("solid_organs", "solid organs", (re.compile(r"spleen"), re.compile(r"liver"))),
    ("airway", "airway", (re.compile(r"trachea"), re.compile(r"pulmonary_artery"))),
    ("vertebrae", "vertebrae", (re.compile(r"^vertebrae_"), re.compile(r"vertebra"))),
    ("ribs", "ribs", (re.compile(r"^rib_"),)),
    (
        "appendicular_skeleton",
        "appendicular skeleton",
        (re.compile(r"humerus"), re.compile(r"scapula"), re.compile(r"clavicula"), re.compile(r"femur")),
    ),
    ("pelvis", "pelvis", (re.compile(r"hip"), re.compile(r"sacrum"))),
    (
        "muscles",
        "muscles",
        (re.compile(r"gluteus"), re.compile(r"autochthon"), re.compile(r"iliopsoas")),
    ),
    ("head", "head", (re.compile(r"brain"), re.compile(r"face"))),
    ("threshold_classes", "threshold classes", (re.compile(r"^bone$"), re.compile(r"^soft_tissue$"), re.compile(r"^lung$"))),
)


def group_segment_labels(labels: Iterable[SegmentLabel]) -> tuple[SegmentLabelGroup, ...]:
    buckets: dict[str, list[SegmentLabel]] = {key: [] for key, _, _ in _GROUP_RULES}
    buckets["other"] = []

    for label in labels:
        key = group_key_for_label(label.name)
        buckets.setdefault(key, []).append(label)

    groups: list[SegmentLabelGroup] = []
    for key, title, _ in _GROUP_RULES:
        grouped_labels = tuple(sorted(buckets.get(key, ()), key=lambda item: item.value))
        if grouped_labels:
            groups.append(SegmentLabelGroup(key=key, title=title, labels=grouped_labels))

    other_labels = tuple(sorted(buckets.get("other", ()), key=lambda item: item.value))
    if other_labels:
        groups.append(SegmentLabelGroup(key="other", title="other labels", labels=other_labels))
    return tuple(groups)


def group_key_for_label(label_name: str) -> str:
    key = normalise_label_name(label_name)
    for group_key, _, patterns in _GROUP_RULES:
        if any(pattern.search(key) for pattern in patterns):
            return group_key
    return "other"
