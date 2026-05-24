# Omniverse practices

Organiq follows Kit extension conventions, authors ordinary USD and keeps medical imaging dependencies optional until the workflow needs them. 

Or at least it tries its very best. 

The exported scenes are simulation-first assets with complete material, physics, lighting and camera context.

## Materials

Organiq uses MDL source-asset materials, not the deprecated `mdlMaterial` schema. Biological materials are authored as `OmniPBR.mdl` with explicit `diffuse_color_constant`, `diffuse_texture`, `normalmap_texture`, `bump_factor`, `reflection_roughness_constant`, `metallic_constant`, `enable_opacity` and `opacity_constant` inputs. Opaque bone stays mineral and rough. Soft tissue uses fractional opacity, organ-specific albedo and generated normal maps so layered anatomy reads well under RTX.

Every material prim carries `organiq:semanticClass` and `organiq:opacity`. The visual binding is separate from the physics binding, so a mesh can keep a beautiful biological shader while PhysX receives a rigid or deformable material under the `physics` binding purpose.

Texture sets are deterministic per tissue and USD export. Organiq writes albedo and normal PNGs next to the USD under `<scene>_textures`, then binds them through OmniPBR asset inputs using relative, slash-normalised asset paths. Bone receives porous mineral detail, organs receive mottled vascular colour, muscle receives directional fibres, lung receives fine cellular detail and the CT-derived skin shell receives subtle pores with low opacity. Meshes keep a universal `UsdPreviewSurface` binding for reliable viewport import and also receive a `full` material-purpose binding to the textured OmniPBR material.

## Meshing

The selected label mask is not exported as a voxel cast. Organiq pads the mask, applies tissue-aware morphology, samples the surface with marching cubes, relaxes the result with Taubin smoothing, removes duplicate triangles and drops degenerate faces before USD authoring. Bone labels keep a sharper profile. Soft organs and lung are authored as surface deformables. The CT-derived skin shell uses stronger smoothing and exports as a surface collision envelope.

Each segmentation label records its mean Hounsfield value from the source CT volume. The value is propagated to the mesh artifact and authored as `organiq:meanHounsfield` on the exported USD mesh prim, so reconstruction and simulation tools can tune materials from measured density instead of label names alone.

## Scene

The exported USD is a complete inspectable scene. It has `/World` as the default prim, metres per unit set to 1, kilograms per unit set to 1, a `physicsScene`, a neutral context table, a dome light, a rectangular key light, a rim light and a camera. `/World` is authored as an assembly. The anatomy sits under `/World/organiq` as a component with extents hints, stable asset metadata and component-local `Looks` and `PhysicsMaterials` scopes. The component can be referenced as an instanceable prim without losing its materials or physics bindings.

The lighting is intentionally simple: cool dome fill, large soft key and a rim that separates translucent tissue from the table. The stage remains simulation-first, with visuals authored as normal USD rather than viewport-only state.

## Physics

Bone-like labels receive `UsdPhysics.RigidBodyAPI`, `UsdPhysics.CollisionAPI`, `UsdPhysics.MeshCollisionAPI`, `UsdPhysics.MassAPI` and a `UsdPhysics.MaterialAPI` physics material. Density is authored on the rigid body so OpenUSD mass calculation uses the tissue density rather than the default water-like density.

Soft tissue receives current OmniPhysics surface deformable authoring. In a running Isaac Sim session Organiq calls `omni.physx.scripts.deformableUtils.set_physics_surface_deformable_body` on the visible triangular organ mesh, then records surface rest-shape points, triangle indices, deformation pose data and surface deformable material attributes. Organiq does not call the auto-volume tet cooking path for segmented organ surfaces. The outer skin shell receives `UsdPhysics.CollisionAPI` plus `UsdPhysics.MeshCollisionAPI` as a static collision surface.

## Extension

The extension is a single Kit extension folder with `config/extension.toml` and a direct-under-root Python package at `com/chrisvoncsefalvay/organiq`. Heavy medical dependencies stay optional. The UI exposes preflight and install actions, while the extension itself can still load in Isaac before MONAI, pydicom or scikit-image are installed.

The UI follows Isaac patterns: an `omni.ext.IExt` lifecycle, a `ScrollingWindow`, a Utilities menu item and `ui.CollapsableFrame` workflow sections. Long-running DICOM, MONAI and meshing actions run through a cancellable action context so the Kit UI remains responsive and operators can stop a long job cleanly.

## References

- [Omniverse current MDL schema requirement](https://docs.omniverse.nvidia.com/kit/docs/asset-requirements/latest/capabilities/materials/requirements/material-mdl-schema.html)
- [OmniPBR opacity parameters](https://docs.omniverse.nvidia.com/materials-and-rendering/latest/templates/OmniPBR.html)
- [PhysX soft body material parameters](https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/SoftBodies.html)
- [Omniverse Kit extensions in depth](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/107.2.0/guide/extensions_advanced.html)
- [OpenUSD physics mass and density semantics](https://openusd.org/docs/api/usd_physics_page_front.html)
- [MONAI wholeBody CT segmentation bundle](https://huggingface.co/MONAI/wholeBody_ct_segmentation/blob/main/docs/README.md)

