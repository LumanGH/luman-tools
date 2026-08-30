# Luman Tools

A Blender add-on for GTA V modding. All the tools sit in one sidebar tab.

## Interior

Baseboards, door frames and window frames. Pick the walls, press one button, and
you get a separate mesh with clean corners, finished UVs and a material on it.

![Interior](docs/preview.png)

**View3D → Sidebar (N) → Luman Tools → Interior**

### Baseboard

Select wall faces, or the edges where wall meets floor, and you get a baseboard
along them — at the floor, or up at the ceiling.

![Baseboard settings](docs/baseboard.png)

### Door Frame

Select the 6 edges around a doorway, on both sides of the wall, and you get a
frame around the opening with the corners cut at 45°.

![Door Frame settings](docs/door-frame.png)

### Window Frame

Select the 4 edges around a window, on one side of the wall, and you get a frame
on that side. **Mirror** adds the one on the other side.

![Window Frame settings](docs/window-frame.png)

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
