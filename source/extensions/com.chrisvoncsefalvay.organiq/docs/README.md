# Organiq

Sim Ready anatomy from DICOM volumes.

Organiq turns DICOM volume data into organised anatomy assets for Isaac Sim. It is built for simulation teams that need anatomical geometry in the scene, with enough structure, material authoring and physics metadata to keep the result useful after export. Generated USDs also retain physical volume information (density, Young's modulus and Poisson coefficient) as metadata, as well as radiodensity in Hounsfield units. This allows for downstream simulation of e.g. CBCT.

The extension brings the conversion work into a single Isaac panel: load a study, select the anatomy that matters, generate meshes, export USD and instantiate the result with tissue-aware materials and simulation settings.

Generated USDs keep organ systems, textures and physics materials together as an instanceable anatomy component. The result is easier to inspect, reuse and place into larger simulation scenes.
