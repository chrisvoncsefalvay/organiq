# physics defaults

Organiq authors a metre and kilogram USD stage. Rigid mass is written with `UsdPhysics.MassAPI` density. Deformable tissue materials are written with the current OmniPhysics deformable material attributes.

| class | mode | density kg/m3 | Young's modulus Pa | Poisson ratio | visual treatment |
| --- | --- | ---: | ---: | ---: | --- |
| bone | rigid | 1850 | 15000000000 | 0.30 | opaque mineral OmniPBR, high roughness, porous normal map |
| cancellous bone | rigid | 1200 | 1000000000 | 0.25 | opaque porous bone OmniPBR |
| lung | deformable | 350 | 5000 | 0.47 | translucent pink, high roughness, fine cellular normal map |
| liver | deformable | 1060 | 12000 | 0.48 | deep red translucent organ, mottled albedo and soft surface relief |
| kidney | deformable | 1050 | 10000 | 0.48 | dark red translucent organ |
| spleen | deformable | 1060 | 8000 | 0.48 | violet red translucent organ |
| heart | deformable | 1060 | 50000 | 0.47 | saturated red muscle tissue |
| muscle | deformable | 1070 | 60000 | 0.46 | red fibrous tissue |
| fat | deformable | 920 | 4000 | 0.48 | yellow adipose tissue |
| skin | deformable | 1100 | 100000 | 0.48 | warm translucent tissue |
| skin shell | surface shell | 1090 | 100000 | 0.48 | CT-derived outer body shell, translucent skin material and static mesh collision |
| blood and vessels | deformable | 1060 | 1000 | 0.49 | dark glossy fluid-like tissue |
| brain | deformable | 1040 | 2500 | 0.49 | muted pink soft tissue |
| soft tissue | deformable | 1050 | 10000 | 0.48 | generic translucent tissue |

The density basis follows ICRU 44/46 tissue tables and ICRP 110-style organ density tables. Reference values used directly include adipose 0.95 g/cm3, brain 1.04, kidney 1.05, liver 1.06, spleen 1.06, heart 1.06, blood 1.06, skin 1.09, spongiosa 1.18 and cortical bone 1.92. Organiq keeps lung near the inflated-tissue prior and maps cortical/trabecular bone labels to rigid bodies.

PhysX deformable material priors keep Poisson ratios below 0.5 and write Young's modulus per tissue class. This matches the PhysX FEM convention where Young's modulus controls stiffness and Poisson ratio controls volume preservation. The exported prims also carry deformable hexahedral resolution, damping, density, smoothing and semantic metadata so the authored USD remains inspectable outside the extension. The CT-derived `skin_shell` is exported as a surface collision envelope rather than a volume deformable because it is a thin exterior shell, not a filled anatomical volume.

Organiq generates the outer skin shell from the CT body envelope using a -550 HU body threshold, largest-component clean-up, hole filling and a 2 mm shell voxel count. The mesh is generated from the filled body envelope so Isaac receives a continuous exterior surface rather than a fragmented label mask.

Sources:

- A very long summer spent doing forensic reconstruction
- [ICRU tissue table reproduced in Radiation Oncology](https://link.springer.com/article/10.1186/s13014-018-0971-8/tables/1)
- [QRM ICRU tissue-equivalent material table](https://www.qrm.de/en/products/icru-tissues/)
- [BioNumbers entry for ICRP Publication 110 organ density and mass table](https://bionumbers.hms.harvard.edu/bionumber.aspx?id=110245)
- [Medical ultrasound review with soft-tissue Young's modulus table](https://pmc.ncbi.nlm.nih.gov/articles/PMC3177611/)
- [Scientific Reports skin elasticity review](https://www.nature.com/articles/s41598-017-15830-7)
- [OpenUSD physics schema mass and density semantics](https://openusd.org/docs/api/usd_physics_page_front.html)
- [PhysX soft body material parameters](https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/SoftBodies.html)
