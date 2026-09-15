"""
app/ui/theme_model.py
======================
Theme data model for the projector display.

Scoped to a core property set for this first pass — font, color,
alignment, solid/image background, and basic layout. Deferred to a
follow-up pass: shadow layers, stroke, gradients, image blur/overlay,
vertical/horizontal/subtitle-overlay layout modes, verse-number
superscript.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List


@dataclass
class Theme:
    name: str = "Untitled Theme"

    # Text
    font_family: str = "Georgia"
    font_weight: str = "normal"          # "normal" | "bold"
    font_size: int = 52                  # base/max size; display still auto-shrinks to fit
    text_color: str = "#F0EBE0"
    text_align: str = "center"           # "left" | "center" | "right"

    # Reference line
    ref_font_family: str = "Georgia"
    ref_font_size: int = 28
    ref_color: str = "#D4AF37"
    reference_position: str = "below"    # "above" | "below"

    # Background
    background_type: str = "solid"       # "solid" | "image"
    background_color: str = "#14213D"    # deep oxford blue, not flat black
    background_image_path: str = ""
    background_fit: str = "cover"        # "cover" | "contain" | "stretch"

    # Layout
    content_area_pct: int = 80           # % of window width used for text wrapping
    padding: int = 60                    # px, top/bottom margin
    element_spacing: int = 28            # px, between verse text and reference

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Theme":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def clone(self, **overrides) -> "Theme":
        data = self.to_dict()
        data.update(overrides)
        return Theme.from_dict(data)


def default_starter_themes() -> List[Theme]:
    return [
        Theme(
            name="Classic Gold",
            font_family="Georgia", font_weight="normal", font_size=52,
            text_color="#F0EBE0", text_align="center",
            ref_font_family="Georgia", ref_font_size=28, ref_color="#D4AF37",
            reference_position="below",
            background_type="solid", background_color="#14213D",
            content_area_pct=80, padding=60, element_spacing=28,
        ),
        Theme(
            name="Modern Minimal",
            font_family="Segoe UI", font_weight="normal", font_size=48,
            text_color="#FFFFFF", text_align="center",
            ref_font_family="Segoe UI", ref_font_size=22, ref_color="#8A8FA8",
            reference_position="below",
            background_type="solid", background_color="#1C2333",
            content_area_pct=70, padding=70, element_spacing=22,
        ),
        Theme(
            name="High Contrast",
            font_family="Arial", font_weight="bold", font_size=56,
            text_color="#FFFF00", text_align="center",
            ref_font_family="Arial", ref_font_size=26, ref_color="#FFFFFF",
            reference_position="above",
            background_type="solid", background_color="#000000",
            content_area_pct=85, padding=50, element_spacing=24,
        ),
    ]
