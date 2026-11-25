from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

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
        "menu_disabled_color": QColor(152, 152, 157, 125),
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
        "menu_disabled_color": QColor(110, 110, 115, 135),
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


class AppleGlassMenu(QMenu):
    """macOS frosted-glass context menu matching AI docker's dark/light theme & elevation."""

    def __init__(
        self,
        title: str = "",
        *,
        theme: str = "dark",
        alpha: int = 232,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._theme = theme
        self._alpha = alpha
        self._shadow_pad = 8

        self.setWindowFlags(
            self.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self._last_layout_key: tuple[bool, bool] | None = None
        self._apply_menu_metrics(has_checkable=False, has_submenu=False)

    def _apply_menu_metrics(self, *, has_checkable: bool, has_submenu: bool) -> None:
        key = (has_checkable, has_submenu)
        if key == self._last_layout_key:
            return
        self._last_layout_key = key
        pad_left = 26 if has_checkable else 14
        pad_right = 26 if has_submenu else 14
        self.setStyleSheet(
            f"""
            QMenu {{
                background: transparent;
                border: none;
                padding: 13px 13px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
            }}
            QMenu::item {{
                background: transparent;
                color: transparent;
                padding: 6px {pad_right}px 6px {pad_left}px;
                margin: 1px 0px;
            }}
            QMenu::separator {{
                height: 9px;
                background: transparent;
            }}
            """
        )

    def _sync_metrics(self) -> None:
        visible = [a for a in self.actions() if a.isVisible()]
        self._apply_menu_metrics(
            has_checkable=any(a.isCheckable() for a in visible),
            has_submenu=any(a.menu() is not None for a in visible),
        )

    def sizeHint(self):
        self._sync_metrics()
        return super().sizeHint()

    def showEvent(self, event) -> None:
        self._sync_metrics()
        super().showEvent(event)

    def addMenu(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and isinstance(args[0], str) and not kwargs:
            sub = AppleGlassMenu(
                args[0], theme=self._theme, alpha=self._alpha, parent=self
            )
            super().addMenu(sub)
            return sub
        return super().addMenu(*args, **kwargs)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        pal = get_palette(self._theme)

        pad = float(self._shadow_pad)
        glass_rect = QRectF(
            pad, pad, self.width() - pad * 2.0, self.height() - pad * 2.0
        )
        paint_apple_glass(
            painter, glass_rect, radius=13.0, theme=self._theme, alpha=self._alpha
        )

        visible_actions = [a for a in self.actions() if a.isVisible()]
        has_checkable = any(a.isCheckable() for a in visible_actions)
        active = self.activeAction()
        painter.setFont(self.font())

        for act in visible_actions:
            rect = self.actionGeometry(act)
            if rect.isEmpty():
                continue

            if act.isSeparator():
                y = float(rect.center().y()) + 0.5
                painter.setPen(QPen(pal["divider"], 1.0))
                painter.drawLine(
                    QPointF(rect.left() + 5.0, y),
                    QPointF(rect.right() - 5.0, y),
                )
                continue

            enabled = act.isEnabled()
            selected = (act is active) and enabled
            destructive = bool(act.property("destructive"))

            item_rect = QRectF(rect).adjusted(0.0, 1.0, 0.0, -1.0)

            if selected:
                hl_color = QColor(pal["alert"] if destructive else pal["accent"])
                hl_color.setAlpha(235)
                hl_path = QPainterPath()
                hl_path.addRoundedRect(item_rect, 7.0, 7.0)
                painter.fillPath(hl_path, hl_color)

            if act.isCheckable() and act.isChecked():
                cx = item_rect.left() + 13.0
                cy = item_rect.center().y()
                chk = QPainterPath()
                chk.moveTo(cx - 3.6, cy + 0.3)
                chk.lineTo(cx - 1.0, cy + 3.0)
                chk.lineTo(cx + 4.1, cy - 2.9)
                chk_color = (
                    QColor("#ffffff")
                    if selected
                    else (pal["accent"] if enabled else pal["menu_disabled_color"])
                )
                painter.setPen(
                    QPen(chk_color, 1.75, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                )
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(chk)

            text_left = item_rect.left() + (24.0 if has_checkable else 11.0)
            has_sub = act.menu() is not None
            text_right = item_rect.right() - (22.0 if has_sub else 11.0)
            text_rect = QRectF(
                text_left,
                item_rect.top(),
                max(10.0, text_right - text_left),
                item_rect.height(),
            )
            if selected:
                text_color = QColor("#ffffff")
            elif not enabled:
                text_color = pal["menu_disabled_color"]
            elif destructive:
                text_color = pal["alert"]
            else:
                text_color = pal["text_primary"]
            painter.setPen(text_color)
            display_text = painter.fontMetrics().elidedText(
                act.text().replace("&&", "&"),
                Qt.ElideRight,
                int(text_rect.width()),
            )
            painter.drawText(
                text_rect,
                Qt.AlignVCenter | Qt.AlignLeft | Qt.TextSingleLine,
                display_text,
            )

            if has_sub:
                ax = item_rect.right() - 11.0
                ay = item_rect.center().y()
                chev = QPainterPath()
                chev.moveTo(ax - 1.8, ay - 3.6)
                chev.lineTo(ax + 1.9, ay)
                chev.lineTo(ax - 1.8, ay + 3.6)
                chev_color = (
                    QColor("#ffffff")
                    if selected
                    else (pal["text_header"] if enabled else pal["menu_disabled_color"])
                )
                painter.setPen(
                    QPen(chev_color, 1.45, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                )
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(chev)

        painter.end()
