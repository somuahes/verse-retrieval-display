"""
app/ui/style_kit.py
=====================
Shared look for the app's secondary windows (Browser, History, and any
future ones) — the same dark/gold palette as the Operator Panel's own
dark theme, kept in one place so new windows don't hand-roll their own
QSS. Deliberately fixed to dark (not wired to the Operator Panel's
dark/light/system toggle) — same precedent as the existing Theme
Designer window, and it keeps this module a plain, self-contained
constants file with no dependency back on main_ui.py.
"""

PALETTE = {
    "bg":        "#0D0F14",
    "panel":     "#13161E",
    "card":      "#1A1E29",
    "border":    "#252A38",
    "border_hi": "#353D54",
    "text_p":    "#F0EBE0",
    "text_m":    "#8A8FA8",
    "text_d":    "#4A5068",
    "gold":      "#C9A84C",
    "gold_l":    "#E8C97A",
    "gold_d":    "#7A6330",
    "green":     "#3DD68C",
    "green_d":   "#1A4D36",
    "red":       "#E85555",
    "red_d":     "#4D1E1E",
}

RS = "6px"
R = "10px"


def c(key: str) -> str:
    return PALETTE[key]


def window_qss() -> str:
    return f"""
    QMainWindow, QWidget {{
        background: {c('bg')};
        color: {c('text_p')};
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
    }}
    QScrollBar:vertical {{ background: {c('bg')}; width: 6px; border-radius: 3px; }}
    QScrollBar::handle:vertical {{ background: {c('border_hi')}; border-radius: 3px; min-height: 20px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 0; }}
    QToolTip {{
        background: {c('card')}; color: {c('text_p')};
        border: 1px solid {c('border_hi')}; padding: 4px;
    }}
    QListWidget, QTableWidget {{
        background: {c('bg')}; border: 1px solid {c('border')};
        border-radius: {RS}; color: {c('text_p')};
        gridline-color: {c('border')};
    }}
    QListWidget::item, QTableWidget::item {{ padding: 6px; }}
    QListWidget::item:selected, QTableWidget::item:selected {{
        background: {c('gold_d')}; color: {c('gold_l')};
    }}
    QHeaderView::section {{
        background: {c('panel')}; color: {c('text_m')};
        border: none; border-bottom: 1px solid {c('border')};
        padding: 6px; font-weight: 700; font-size: 11px;
    }}
    """


def btn_qss(kind: str = "ghost") -> str:
    bg, fg, border, hover, pressed = {
        "primary": (c('gold'),   "#1A1200",  c('gold'),   c('gold_l'), c('gold_d')),
        "danger":  (c('red_d'),  c('red'),   c('red_d'),  c('red'),    c('red_d')),
        "nav":     (c('card'),   c('text_p'), c('border'), c('border_hi'), c('border')),
        "ghost":   ("transparent", c('text_m'), c('border'), c('card'), c('border')),
        "active":  (c('gold_d'), c('gold_l'), c('gold_d'), c('gold_d'), c('gold_d')),
    }.get(kind, ("transparent", c('text_m'), c('border'), c('card'), c('border')))

    return (
        f"QPushButton {{"
        f"  background: {bg}; color: {fg}; border: 1px solid {border};"
        f"  border-radius: {RS}; padding: 7px 14px; font-weight: 600;"
        f"}}"
        f"QPushButton:hover {{ background: {hover}; color: {c('text_p')}; border-color: {c('border_hi')}; }}"
        f"QPushButton:pressed {{ background: {pressed}; }}"
        f"QPushButton:checked {{ background: {c('gold_d')}; color: {c('gold_l')}; border-color: {c('gold_d')}; }}"
        f"QPushButton:disabled {{ color: {c('text_d')}; border-color: {c('border')}; }}"
    )


def combo_qss() -> str:
    return (
        f"QComboBox {{"
        f"  background: {c('bg')}; border: 1px solid {c('border')};"
        f"  border-radius: {RS}; color: {c('text_p')}; padding: 7px 10px;"
        f"}}"
        f"QComboBox:hover {{ border-color: {c('border_hi')}; }}"
        f"QComboBox::drop-down {{ border: none; width: 22px; }}"
        f"QComboBox QAbstractItemView {{"
        f"  background: {c('panel')}; border: 1px solid {c('border_hi')};"
        f"  color: {c('text_p')}; selection-background-color: {c('gold_d')};"
        f"  selection-color: {c('gold_l')};"
        f"}}"
    )


def card_qss(gold_border: bool = False) -> str:
    border = c('gold_d') if gold_border else c('border')
    return (
        f"QFrame {{"
        f"  background: {c('card')}; border: 1px solid {border};"
        f"  border-radius: {R};"
        f"}}"
    )


def eyebrow_qss() -> str:
    """Small uppercase, letter-spaced section-label style — used for card
    headers across the app's secondary windows."""
    return (
        f"color: {c('text_d')}; font-size: 10.5px; font-weight: 700;"
        f"letter-spacing: 1.5px; background: transparent; border: none;"
    )


def app_icon_pixmap():
    """A small generated app icon (gold star-of-scripture on ink navy) —
    avoids shipping a binary asset for one glyph. Used as the window
    icon for the Operator Panel and every secondary window so the app
    has a consistent taskbar identity."""
    from PyQt5.QtGui import QPixmap, QPainter, QColor, QFont
    from PyQt5.QtCore import Qt

    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(c('bg')))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 14, 14)
    painter.setPen(QColor(c('gold')))
    font = QFont("Georgia", 34, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pm.rect(), Qt.AlignCenter, "✦")
    painter.end()
    return pm


def app_icon():
    """QIcon version of app_icon_pixmap() — pass straight to
    setWindowIcon()."""
    from PyQt5.QtGui import QIcon
    return QIcon(app_icon_pixmap())
