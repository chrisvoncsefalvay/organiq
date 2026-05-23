# Organiq

Organiq adds a left-docked Isaac workflow for loading CT DICOM studies, running MONAI segmentation, generating an outer skin shell, meshing selected anatomy, exporting USD and applying tissue-aware physics.

The window is available from `Utilities > Organiq`. The workflow frames are:

1. environment preflight
2. load DICOM folder
3. segment volume
4. select objects to mesh
5. mesh selected objects with tissue-aware organic smoothing
6. turn meshes into USD
7. instantiate with textured materials and physics

The supported segmentation backend is the MONAI `wholeBody_ct_segmentation` bundle. It is the only default segment panel choice because Organiq's runner expects `configs/inference.json`, the low-resolution and high-resolution whole-body checkpoints and TotalSegmentator-style label metadata.

MONAI inference runs as a cancellable, timeout-bounded subprocess. The progress panel reports elapsed time during long runs and stores only bounded stdout and stderr excerpts for failure diagnosis.

The label selector groups TotalSegmentator-style labels by anatomy, including the body shell, lungs, heart, vessels, vertebrae, ribs, skeleton, muscles and other labels. Each group can be selected or cleared as a unit.
