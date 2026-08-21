"""
app/ui/theme_store.py
=======================
Load/save themes as JSON files under themes/. Handles the built-in
starter set, custom theme CRUD, and import/export.
"""

import os
import json
import re
from typing import List, Optional

from app.ui.theme_model import Theme, default_starter_themes

THEMES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "themes")
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "theme"


def _theme_path(name: str) -> str:
    return os.path.join(THEMES_DIR, f"{_slugify(name)}.json")


def name_in_use(name: str, exclude_name: Optional[str] = None) -> bool:
    """True if `name` would collide with an existing theme's FILE, by
    slug -- not just by exact display-name string. Two distinct display
    names that differ only in punctuation/case (e.g. "Gold!" and
    "Gold?") both slugify to the same "gold.json" and would silently
    clobber each other's saved file otherwise; a caller comparing new
    names against list_themes()'s exact strings would miss that.
    `exclude_name` lets a rename check against every OTHER theme's slug
    without flagging the theme's own current one."""
    os.makedirs(THEMES_DIR, exist_ok=True)
    target_slug = _slugify(name)
    exclude_slug = _slugify(exclude_name) if exclude_name else None

    for fname in os.listdir(THEMES_DIR):
        if not fname.endswith(".json"):
            continue
        slug = fname[:-5]
        if slug == exclude_slug:
            continue
        if slug == target_slug:
            return True

    return False


def ensure_starter_themes():
    os.makedirs(THEMES_DIR, exist_ok=True)
    if any(f.endswith(".json") for f in os.listdir(THEMES_DIR)):
        return
    for theme in default_starter_themes():
        save_theme(theme)


def list_themes() -> List[str]:
    os.makedirs(THEMES_DIR, exist_ok=True)
    ensure_starter_themes()
    names = []
    for fname in sorted(os.listdir(THEMES_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(THEMES_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            names.append(data.get("name", fname[:-5]))
        except Exception:
            continue
    return names


def load_theme(name: str) -> Optional[Theme]:
    path = _theme_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Theme.from_dict(data)
    except Exception:
        return None


def save_theme(theme: Theme):
    os.makedirs(THEMES_DIR, exist_ok=True)
    with open(_theme_path(theme.name), "w", encoding="utf-8") as f:
        json.dump(theme.to_dict(), f, indent=2)


def delete_theme(name: str):
    path = _theme_path(name)
    if os.path.exists(path):
        os.remove(path)


def rename_theme(old_name: str, new_name: str) -> Optional[Theme]:
    theme = load_theme(old_name)
    if not theme:
        return None
    delete_theme(old_name)
    theme.name = new_name
    save_theme(theme)
    return theme


def duplicate_theme(name: str, new_name: Optional[str] = None) -> Optional[Theme]:
    theme = load_theme(name)
    if not theme:
        return None
    new_name = new_name or f"{name} Copy"
    dup = theme.clone(name=new_name)
    save_theme(dup)
    return dup


def export_theme(name: str, dest_path: str) -> bool:
    theme = load_theme(name)
    if not theme:
        return False
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(theme.to_dict(), f, indent=2)
    return True


def import_theme(src_path: str) -> Optional[Theme]:
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        theme = Theme.from_dict(data)
        save_theme(theme)
        return theme
    except Exception:
        return None
