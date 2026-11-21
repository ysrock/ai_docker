from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

THEMES: dict[str, dict[str, Any]] = {
    "dark": {
        "glass_top": (44, 47, 58),
        "glass_mid": (30, 32, 41),
        "glass_bot": (22, 24, 31),
        "rim_top": QColor(255, 255, 255, 66),
        "rim_mid": QColor(255, 255, 255, 24),
        "rim_bot": QColor(255, 255, 255, 14),
        "sheen": QColor(255, 255, 255, 46),
        "shadow_rgb": (0, 0, 0),
        "shadow_alphas": (14, 22, 28),
        "grip": QColor(235, 235, 245, 76),
        "running_dot": QColor(245, 245, 247, 230),
        "running_dot_glow": QColor(255, 255, 255, 65),
        "accent": QColor("#0a84ff"),
        "alert": QColor("#ff453a"),
        "badge_cutout": QColor(28, 30, 39, 250),
        "text_primary": QColor("#f5f5f7"),
        "text_row": QColor("#d1d5db"),
        "text_alert": QColor("#fff1f2"),
        "text_header": QColor("#98989d"),
        "text_header_hover": QColor("#e5e5ea"),
        "divider": QColor(255, 255, 255, 18),
        "row_divider": QColor(255, 255, 255, 11),
        "row_hover": QColor(255, 255, 255, 20),
        "row_active": QColor(10, 132, 255, 36),
        "row_active_hover": QColor(10, 132, 255, 50),
        "row_alert": QColor(255, 69, 58, 30),
        "row_alert_hover": QColor(255, 69, 58, 46),
        "row_press": QColor(10, 132, 255, 64),
        "tick_idle": QColor(152, 152, 157, 92),
        "tick_hover": QColor(209, 213, 219, 150),
        "icon_hover_bg": QColor(255, 255, 255, 22),
        "icon_press_bg": QColor(10, 132, 255, 48),
        "menu_bg": "#1e2028",
        "menu_fg": "#f5f5f7",
        "menu_border": "rgba(255, 255, 255, 38)",
        "menu_hover": "rgba(10, 132, 255, 185)",
        "menu_sep": "rgba(255, 255, 255, 24)",
        "menu_disabled": "rgba(152, 152, 157, 115)",
        "tip_bg": "#1b1d24",
        "tip_fg": "#f5f5f7",
        "tip_border": "rgba(255, 255, 255, 46)",
    },
    "light": {
        "glass_top": (253, 254, 255),
        "glass_mid": (245, 246, 250),
        "glass_bot": (236, 239, 246),
        "rim_top": QColor(15, 23, 42, 32),
        "rim_mid": QColor(15, 23, 42, 36),
        "rim_bot": QColor(15, 23, 42, 44),
        "sheen": QColor(255, 255, 255, 215),
        "shadow_rgb": (15, 23, 42),
        "shadow_alphas": (10, 16, 22),
        "grip": QColor(60, 60, 67, 88),
        "running_dot": QColor(29, 29, 31, 210),
        "running_dot_glow": QColor(29, 29, 31, 35),
        "accent": QColor("#007aff"),
        "alert": QColor("#ff3b30"),
        "badge_cutout": QColor(246, 247, 251, 252),
        "text_primary": QColor("#1d1d1f"),
        "text_row": QColor("#3a3a3c"),
        "text_alert": QColor("#b91c1c"),
        "text_header": QColor("#6e6e73"),
        "text_header_hover": QColor("#1d1d1f"),
        "divider": QColor(15, 23, 42, 20),
        "row_divider": QColor(15, 23, 42, 12),
        "row_hover": QColor(15, 23, 42, 14),
        "row_active": QColor(0, 122, 255, 24),
        "row_active_hover": QColor(0, 122, 255, 36),
        "row_alert": QColor(255, 59, 48, 22),
        "row_alert_hover": QColor(255, 59, 48, 34),
        "row_press": QColor(0, 122, 255, 48),
        "tick_idle": QColor(110, 110, 115, 95),
        "tick_hover": QColor(60, 60, 67, 150),
        "icon_hover_bg": QColor(15, 23, 42, 14),
        "icon_press_bg": QColor(0, 122, 255, 32),
        "menu_bg": "#f7f8fb",
        "menu_fg": "#1d1d1f",
        "menu_border": "rgba(15, 23, 42, 34)",
        "menu_hover": "#007aff",
        "menu_sep": "rgba(15, 23, 42, 20)",
        "menu_disabled": "rgba(110, 110, 115, 130)",
        "tip_bg": "#fbfbfd",
        "tip_fg": "#1d1d1f",
        "tip_border": "rgba(15, 23, 42, 36)",
    },
}


def get_palette(theme: str) -> dict[str, Any]:
    return THEMES["light"] if theme == "light" else THEMES["dark"]


def paint_apple_glass(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    theme: str = "dark",
    alpha: int = 232,
    drop_highlight: bool = False,
) -> None:
    """Paint macOS-inspired frosted glass surface with smooth elevation shadow & specular edge."""
    pal = get_palette(theme)
    sr, sg, sb = pal["shadow_rgb"]
    a1, a2, a3 = pal["shadow_alphas"]
    for dx, dy, expand, sa in (
        (0.0, 5.5, 3.2, a1),
        (0.0, 3.2, 1.6, a2),
        (0.0, 1.4, 0.5, a3),
    ):
        srect = rect.adjusted(-expand + dx, -expand * 0.35 + dy, expand + dx, expand + dy)
        spath = QPainterPath()
        spath.addRoundedRect(srect, radius + expand * 0.55, radius + expand * 0.55)
        painter.fillPath(spath, QColor(sr, sg, sb, sa))

    t_r, t_g, t_b = pal["glass_top"]
    m_r, m_g, m_b = pal["glass_mid"]
    b_r, b_g, b_b = pal["glass_bot"]
    body_grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
    body_grad.setColorAt(0.0, QColor(t_r, t_g, t_b, alpha))
    body_grad.setColorAt(0.52, QColor(m_r, m_g, m_b, min(250, alpha + 4)))
    body_grad.setColorAt(1.0, QColor(b_r, b_g, b_b, min(252, alpha + 8)))

    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, body_grad)

    sheen_rect = QRectF(
        rect.left() + radius * 0.65, rect.top() + 1.0, rect.width() - radius * 1.3, 1.2
    )
    if sheen_rect.width() > 10:
        sh = pal["sheen"]
        sheen_grad = QLinearGradient(sheen_rect.left(), 0, sheen_rect.right(), 0)
        sheen_grad.setColorAt(0.0, QColor(sh.red(), sh.green(), sh.blue(), 0))
        sheen_grad.setColorAt(0.5, sh)
        sheen_grad.setColorAt(1.0, QColor(sh.red(), sh.green(), sh.blue(), 0))
        painter.fillRect(sheen_rect, sheen_grad)

    if drop_highlight:
        painter.setPen(QPen(pal["accent"], 2.0))
    else:
        rim_grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        rim_grad.setColorAt(0.0, pal["rim_top"])
        rim_grad.setColorAt(0.4, pal["rim_mid"])
        rim_grad.setColorAt(1.0, pal["rim_bot"])
        painter.setPen(QPen(rim_grad, 1.0))
    painter.drawPath(path)
