# Luman Tools

My Blender toolkit — the tools I build for my own production work, gathered into
one add-on and one sidebar tab.

**Interior** is the first tool in the set: interior trim for **GTA V MLO
interiors** — baseboards (skirting / cornice), door casings and window casings,
mitered, unwrapped and textured. Select the walls, press one button, get an
export-ready prop.

![Luman Tools in the viewport](docs/preview.png)

## Interior

**View3D → Sidebar (N) → Luman Tools → Interior**

| | Select | Result |
|---|---|---|
| **Baseboard** | wall faces, or wall/floor edges | run along the selection, as Skirting or Cornice |
| **Door Frame** | the opening's outline on both wall faces (6 edges) | casing swept outward, top corners mitered 45° |
| **Window Frame** | the outline on one face (4 edges) | casing on this side; **Mirror** adds the far half, wall thickness read off the mesh |

More tools will be added to the set.

## Install

[Download the latest release](https://github.com/LumanGH/luman-tools/releases/latest)

1. Download the ZIP from the release — do not unpack it.
2. Blender → **Edit → Preferences → Add-ons → Install from Disk** → pick the ZIP.
3. Enable **Luman Tools**.
4. Press **N** in the 3D viewport → **Luman Tools** tab.

Requires Blender 4.2 or newer.
