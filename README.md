# Luman Tools

A Blender add-on for GTA V modding. All the tools sit in one sidebar tab.

## Interior

Baseboards, door frames and window frames. Pick the walls, press one button, and
you get a separate mesh with clean corners, finished UVs and a material on it.

![Interior](docs/preview.png)

**View3D → Sidebar (N) → Luman Tools → Interior**

| Baseboard | Door Frame | Window Frame |
|---|---|---|
| <img src="docs/baseboard.png" width="240"> | <img src="docs/door-frame.png" width="240"> | <img src="docs/window-frame.png" width="240"> |
| Select wall faces, or the edges where wall meets floor — a baseboard runs along them, at the floor or up at the ceiling. | Select the 6 edges around a doorway, on both sides of the wall — a frame goes round the opening, corners cut at 45°. | Select the 4 edges around a window, on one side of the wall — **Mirror** adds the frame on the other side too. |

Both edges can be square, chamfer, bullnose, cove, ogee, stepped, or a profile
you type in yourself. Drag a slider and the mesh rebuilds right away — the wall
itself is never touched.

## Install

[Download the latest release](https://github.com/LumanGH/luman-tools/releases/latest)

1. Download the ZIP from the release — do not unpack it.
2. Blender → **Edit → Preferences → Add-ons → Install from Disk** → pick the ZIP.
3. Enable **Luman Tools**.
4. Press **N** in the 3D viewport → **Luman Tools** tab.

Requires Blender 4.2 or newer.
