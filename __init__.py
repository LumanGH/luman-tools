"""Luman Tools - add-on entry point.

Blender only recognises a FOLDER as an add-on if it is a Python package, i.e.
if it contains this file. Without it the folder is skipped silently, which is
exactly what "it does not show up in Preferences > Add-ons" looks like.

Everything real lives in the sibling modules; this file only says "here is an
add-on, here is its bl_info, here is how to switch it on and off".
"""

bl_info = {
    "name": "Luman Tools",
    "blender": (4, 2, 0),
    "category": "Object",
    "author": "Luman",
    "version": (3, 3, 0),
    "location": "View3D > Sidebar (N) > Luman Tools",
    "description": (
        "Interior trim: baseboards/cornices, door and window casings, with mitered "
        "corners and a ready unwrap. Prop distribution, renaming, texture auto-linking "
        "and placement along curves live under Experimental"
    ),
}

from . import luman_tools


def register():
    luman_tools.register()


def unregister():
    luman_tools.unregister()
