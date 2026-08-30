# Luman Tools

Blender add-on that builds interior trim for **GTA V MLO interiors**: baseboards
(skirting / cornice), door casings and window casings — mitered, unwrapped and
already wearing a Sollumz `normal_spec.sps` material.

Select the walls, press one button, get an export-ready prop.

![Luman Tools in the viewport](docs/preview.png)

## Tools

**View3D → Sidebar (N) → Luman Tools → Interior**

| | Select | Result |
|---|---|---|
| **Baseboard** | wall faces, or wall/floor edges | run along the selection, as Skirting or Cornice |
| **Door Frame** | the opening's outline on both wall faces (6 edges) | casing swept outward, top corners mitered 45° |
| **Window Frame** | the outline on one face (4 edges) | casing on this side; **Mirror** adds the far half, wall thickness read off the mesh |

## Why it exports clean

- **Correct miters by construction** — inside, outside, any angle. No booleans.
- **Continuous geometry** — cross-sections share vertices, so no doubles and no shading seam at a bend.
- **UVs built, not unwrapped** — arc length in metres, so the grain never restarts or rotates at a corner and texel density is identical everywhere.
- **Game-mesh hygiene** — bottom and back faces off by default, End Gap to clear a door casing, Wall Inset / Floor Sink to kill hairline gaps, contact shading baked into `Color 1`.
- **Live** — the object keeps its source, selection and settings; drag Height/Depth and it rebuilds in place. The source mesh is never touched.

## Profiles

Inner and outer edge shaped independently: `Square` · `Chamfer` · `Bullnose` ·
`Cove` · `Ogee` · `Stepped` · `Custom` (own point list, in fractions of
Depth/Height so it survives the size sliders).

## Sollumz

Builds `normal_spec.sps` from diffuse / `_n` / `_s` maps, writing `UVMap 0` and
`Color 1` as the shader expects. Sollumz is detected at runtime and never
modified. Without it everything still works — the material falls back to a
Principled BSDF wired the same way.

## Install

Download as ZIP → Blender **Edit → Preferences → Add-ons → Install from Disk** →
enable **Luman Tools**. Requires Blender 4.2+. Sollumz optional but recommended.

## License

GPL-3.0-or-later. The bundled `.dds` wood maps are GTA V textures included as a
working default and belong to Rockstar Games — point **Texture Folder** at your
own maps for anything you distribute.
