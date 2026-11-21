from __future__ import annotations

import atexit
import ctypes
import math
import os
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import (
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .config import CONFIG_PATH, load_config, save_config
from .dialogs import AddAgentDialog, DiscoverAgentsDialog
from .discovery import build_agent_from_file
from .icons import load_agent_icon
from .launcher import (
    focus_hwnd,
    get_agent_window_map,
    get_foreground_hwnd,
    is_autostart_enabled,
    launch_agent,
    set_autostart,
)
from .notifications import (
    AgentNotificationSummary,
    NotificationTracker,
    WindowNotificationState,
)
from .taskbar import TaskbarHider

user32 = ctypes.windll.user32
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterShellHookWindow.argtypes = [wintypes.HWND]
user32.RegisterShellHookWindow.restype = wintypes.BOOL
user32.DeregisterShellHookWindow.argtypes = [wintypes.HWND]
user32.DeregisterShellHookWindow.restype = wintypes.BOOL

HSHELL_WINDOWACTIVATED = 4
HSHELL_RUDEAPPACTIVATED = 0x8004
HSHELL_FLASH = 0x8006

TYPE_LABELS = {
    "exe": "桌面应用",
    "msix": "桌面应用",
    "cli": "终端 CLI",
    "web": "网页应用",
}

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

    # Inner top glass sheen
    sheen_rect = QRectF(rect.left() + radius * 0.65, rect.top() + 1.0, rect.width() - radius * 1.3, 1.2)
    if sheen_rect.width() > 10:
        sh = pal["sheen"]
        sheen_grad = QLinearGradient(sheen_rect.left(), 0, sheen_rect.right(), 0)
        sheen_grad.setColorAt(0.0, QColor(sh.red(), sh.green(), sh.blue(), 0))
        sheen_grad.setColorAt(0.5, sh)
        sheen_grad.setColorAt(1.0, QColor(sh.red(), sh.green(), sh.blue(), 0))
        painter.fillRect(sheen_rect, sheen_grad)

    # Rim border (glows Apple Blue when dragging a .exe/.lnk file over the dock)
    if drop_highlight:
        painter.setPen(QPen(pal["accent"], 2.0))
    else:
        rim_grad = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        rim_grad.setColorAt(0.0, pal["rim_top"])
        rim_grad.setColorAt(0.4, pal["rim_mid"])
        rim_grad.setColorAt(1.0, pal["rim_bot"])
        painter.setPen(QPen(rim_grad, 1.0))
    painter.drawPath(path)


class DragGrip(QWidget):
    """Subtle Apple-minimal drag handle at the left edge of Row 1."""

    def __init__(self, height: int = 46, theme: str = "dark", parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("按住拖动悬浮窗位置")
        self.setFixedSize(16, height)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(get_palette(self._theme)["grip"])
        painter.setPen(Qt.NoPen)

        r = 1.35
        xs = [5.5, 10.5]
        mid_y = self.height() / 2.0
        ys = [mid_y - 7.5, mid_y - 2.5, mid_y + 2.5, mid_y + 7.5]
        for x in xs:
            for y in ys:
                painter.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
        painter.end()


class AgentIconButton(QWidget):
    """Top-row icon button with smooth 60fps macOS Dock wave magnification & classic 4px dot."""

    clicked = Signal(dict)
    right_clicked = Signal(dict, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        icon: QIcon,
        button_size: int = 46,
        icon_size: int = 32,
        theme: str = "dark",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.agent = agent
        self._icon = icon
        self._button_size = button_size
        self._icon_size = icon_size
        self._theme = theme
        self._hovered = False
        self._pressed = False
        self._window_count = 0
        self._show_running_dot = True
        self._notif_count = 0
        self._has_alert = False
        self._notif_text = ""
        self._press_global: QPoint | None = None
        self._dragging_parent = False

        # Smooth magnification animation state
        self._scale = 1.0
        self._target_scale = 1.0

        # Extra vertical headroom so magnified icons float smoothly without clipping
        self.setFixedSize(button_size, button_size + 6)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._refresh_tooltip()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_target_scale(self, scale: float) -> None:
        self._target_scale = scale

    def step_animation(self) -> bool:
        """Advance magnification lerp by one frame; returns True if still animating."""
        diff = self._target_scale - self._scale
        if abs(diff) < 0.003:
            if self._scale != self._target_scale:
                self._scale = self._target_scale
                self.update()
            return False
        self._scale += diff * 0.28
        self.update()
        return True

    def update_state(
        self,
        window_count: int,
        show_dot: bool,
        notif_count: int,
        has_alert: bool,
        notif_text: str,
    ) -> None:
        changed = (
            self._window_count != window_count
            or self._show_running_dot != show_dot
            or self._notif_count != notif_count
            or self._has_alert != has_alert
            or self._notif_text != notif_text
        )
        if changed:
            self._window_count = window_count
            self._show_running_dot = show_dot
            self._notif_count = notif_count
            self._has_alert = has_alert
            self._notif_text = notif_text
            self._refresh_tooltip()
            self.update()

    def _refresh_tooltip(self) -> None:
        name = self.agent.get("name", "Agent")
        type_label = TYPE_LABELS.get(self.agent.get("type", "exe"), "应用")
        if self._window_count > 1:
            status = f"运行中 ({self._window_count} 个窗口)"
        elif self._window_count == 1:
            status = "运行中"
        else:
            status = type_label
        lines = [f"{name}  ·  {status}"]
        if self._has_alert:
            if self._notif_text:
                lines.append(f"通知：{self._notif_text[:90]}")
            elif self._notif_count > 0:
                lines.append(f"有 {self._notif_count} 条未读通知")
            else:
                lines.append("有新通知 / 等待查看")
        self.setToolTip("\n".join(lines))

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._press_global = event.globalPosition().toPoint()
            self._dragging_parent = False
            self.update()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.agent, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Feed cursor position to parent TopDockCapsule for smooth wave magnification
        parent_bar = self.parentWidget()
        if isinstance(parent_bar, TopDockCapsule):
            parent_bar.update_wave_from_global(event.globalPosition().toPoint())

        if self._pressed and self._press_global is not None and (event.buttons() & Qt.LeftButton):
            cur = event.globalPosition().toPoint()
            if not self._dragging_parent and (cur - self._press_global).manhattanLength() > 6:
                self._dragging_parent = True
                self._pressed = False
                self.update()
                win = self.window()
                if isinstance(win, AgentDockWindow):
                    win.begin_external_drag(cur)
            if self._dragging_parent:
                win = self.window()
                if isinstance(win, AgentDockWindow):
                    win.update_external_drag(cur)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            was_dragging = self._dragging_parent
            self._dragging_parent = False
            self._pressed = False
            self._press_global = None
            self.update()
            if was_dragging:
                win = self.window()
                if isinstance(win, AgentDockWindow):
                    win.end_external_drag()
            elif self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.agent)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pal = get_palette(self._theme)

        if self._pressed or self._hovered:
            bg_rect = QRectF(2.0, 5.0, self.width() - 4.0, self.height() - 10.0)
            path = QPainterPath()
            path.addRoundedRect(bg_rect, 11.0, 11.0)
            painter.fillPath(path, pal["icon_press_bg"] if self._pressed else pal["icon_hover_bg"])

        eff_scale = self._scale * (0.93 if self._pressed else 1.0)
        draw_size = min(float(self._button_size - 4), self._icon_size * eff_scale)
        lift = (eff_scale - 1.0) * 14.0  # smooth Apple Dock upward float

        dpr = self.devicePixelRatioF()
        pm = self._icon.pixmap(QSize(int(math.ceil(draw_size * dpr)), int(math.ceil(draw_size * dpr))))
        if not pm.isNull():
            pm.setDevicePixelRatio(dpr)
            x = (self.width() - draw_size) / 2.0
            base_y = (self.height() - draw_size) / 2.0 - 1.0
            y = max(2.0, base_y - lift)
            painter.drawPixmap(QRectF(x, y, draw_size, draw_size), pm, QRectF(pm.rect()))

        # Classic Apple macOS 4px circular running dot at bottom
        if self._window_count > 0 and self._show_running_dot:
            painter.setPen(Qt.NoPen)
            dot_d = 4.0
            dx = (self.width() - dot_d) / 2.0
            dy = self.height() - 5.2
            painter.setBrush(pal["running_dot_glow"])
            painter.drawEllipse(QRectF(dx - 1.2, dy - 1.2, dot_d + 2.4, dot_d + 2.4))
            painter.setBrush(pal["running_dot"])
            painter.drawEllipse(QRectF(dx, dy, dot_d, dot_d))

        # Crisp Apple red notification badge in top-right corner
        if self._has_alert:
            badge_text = ""
            if self._notif_count > 0:
                badge_text = "9+" if self._notif_count > 9 else str(self._notif_count)
            bh = 15.0 if badge_text else 9.5
            bw = 19.5 if len(badge_text) > 1 else bh
            bx = self.width() - bw - 1.0
            by = max(1.0, 3.0 - lift * 0.5)
            badge_rect = QRectF(bx, by, bw, bh)

            painter.setBrush(pal["alert"])
            painter.setPen(QPen(pal["badge_cutout"], 1.6))
            painter.drawRoundedRect(badge_rect, bh / 2.0, bh / 2.0)

            if badge_text:
                font = QFont("Segoe UI", 7, QFont.Bold)
                painter.setFont(font)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(badge_rect, Qt.AlignCenter, badge_text)

        painter.end()


class TopDockCapsule(QWidget):
    """Row 1: evenly-spaced Apple glass Dock bar with 60fps wave magnification."""

    def __init__(self, theme: str = "dark", alpha: int = 232, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self._alpha = alpha
        self._shadow_pad = 7
        self._drop_highlight = False
        self._icon_buttons: list[AgentIconButton] = []

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.row_layout = QHBoxLayout(self)
        self.row_layout.setContentsMargins(
            self._shadow_pad + 9,
            self._shadow_pad + 3,
            self._shadow_pad + 11,
            self._shadow_pad + 3,
        )
        self.row_layout.setSpacing(6)
        self.row_layout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        # 60fps animation timer (auto-stops when icons settle to 0% idle CPU)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick_wave)

    def set_theme_and_alpha(self, theme: str, alpha: int) -> None:
        self._theme = theme
        self._alpha = alpha
        self.update()

    def set_drop_highlight(self, enabled: bool) -> None:
        if self._drop_highlight != enabled:
            self._drop_highlight = enabled
            self.update()

    def set_icon_buttons(self, buttons: list[AgentIconButton]) -> None:
        self._icon_buttons = buttons

    def update_wave_from_global(self, global_pos: QPoint) -> None:
        local_x = self.mapFromGlobal(global_pos).x()
        radius = 96.0
        for btn in self._icon_buttons:
            center_x = btn.x() + btn.width() / 2.0
            dist = abs(local_x - center_x)
            if dist < radius:
                bell = 0.5 * (1.0 + math.cos(math.pi * dist / radius))
                btn.set_target_scale(1.0 + 0.26 * bell)
            else:
                btn.set_target_scale(1.0)
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def reset_wave(self) -> None:
        for btn in self._icon_buttons:
            btn.set_target_scale(1.0)
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def _tick_wave(self) -> None:
        still_moving = False
        for btn in self._icon_buttons:
            if btn.step_animation():
                still_moving = True
        if not still_moving:
            self._anim_timer.stop()

    def mouseMoveEvent(self, event) -> None:
        self.update_wave_from_global(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.reset_wave()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pad = self._shadow_pad
        rect = QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        paint_apple_glass(
            painter,
            rect,
            radius=18.0,
            theme=self._theme,
            alpha=self._alpha,
            drop_highlight=self._drop_highlight,
        )
        painter.end()


class ProjectWindowRow(QWidget):
    """Clean borderless project window row inside an Apple glass workspace card."""

    clicked = Signal(int, str)
    right_clicked = Signal(dict, int, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        win_info: dict[str, Any],
        is_active: bool,
        notif: WindowNotificationState | None,
        width: int = 128,
        show_top_divider: bool = False,
        theme: str = "dark",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.agent = agent
        self.hwnd = int(win_info["hwnd"])
        self.project = str(win_info.get("project") or agent.get("name", "窗口"))
        self.full_title = str(win_info.get("title") or self.project)
        self.is_active = is_active
        self.notif = notif or WindowNotificationState()
        self._show_top_divider = show_top_divider
        self._theme = theme
        self._hovered = False
        self._pressed = False

        self.setFixedSize(width, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

        tip_lines = [self.project]
        if self.full_title != self.project:
            tip_lines.append(self.full_title)
        if self.notif.has_alert:
            if self.notif.latest_text:
                tip_lines.append(f"通知：{self.notif.latest_text[:100]}")
            elif self.notif.count > 0:
                tip_lines.append(f"未读通知：{self.notif.count}")
            else:
                tip_lines.append("有新通知 / 窗口闪烁提醒")
        self.setToolTip("\n".join(tip_lines))

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.agent, self.hwnd, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit(self.hwnd, self.agent.get("id", ""))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = get_palette(self._theme)

        if self._show_top_divider and not self._hovered and not self.is_active and not self.notif.has_alert:
            painter.fillRect(QRectF(6, 0, self.width() - 12, 1.0), pal["row_divider"])

        has_alert = self.notif.has_alert
        row_rect = QRectF(1.0, 2.0, self.width() - 2.0, self.height() - 4.0)

        if self._pressed:
            bg = pal["row_press"]
        elif has_alert:
            bg = pal["row_alert_hover"] if self._hovered else pal["row_alert"]
        elif self.is_active:
            bg = pal["row_active_hover"] if self._hovered else pal["row_active"]
        elif self._hovered:
            bg = pal["row_hover"]
        else:
            bg = QColor(0, 0, 0, 0)

        if bg.alpha() > 0:
            rpath = QPainterPath()
            rpath.addRoundedRect(row_rect, 8.0, 8.0)
            painter.fillPath(rpath, bg)

        # Apple-style left vertical accent pill
        painter.setPen(Qt.NoPen)
        if has_alert:
            bar_color = pal["alert"]
            bar_h = 13.0
        elif self.is_active:
            bar_color = pal["accent"]
            bar_h = 13.0
        else:
            bar_color = pal["tick_hover"] if self._hovered else pal["tick_idle"]
            bar_h = 8.0
        painter.setBrush(bar_color)
        painter.drawRoundedRect(
            QRectF(5.5, (self.height() - bar_h) / 2.0, 2.6, bar_h), 1.3, 1.3
        )

        # Right notification count pill
        right_reserved = 6
        if has_alert:
            badge_text = ""
            if self.notif.count > 0:
                badge_text = "9+" if self.notif.count > 9 else str(self.notif.count)
            bh = 15.0 if badge_text else 7.5
            bw = 19.0 if len(badge_text) > 1 else bh
            bx = self.width() - bw - 5.0
            by = (self.height() - bh) / 2.0
            brect = QRectF(bx, by, bw, bh)

            painter.setBrush(pal["alert"])
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(brect, bh / 2.0, bh / 2.0)
            if badge_text:
                bfont = QFont("Segoe UI", 7, QFont.Bold)
                painter.setFont(bfont)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(brect, Qt.AlignCenter, badge_text)
            right_reserved = int(bw) + 10

        # Project name text
        text_left = 13
        text_width = max(16, self.width() - text_left - right_reserved)
        font = QFont("Microsoft YaHei UI", 8)
        font.setPointSizeF(8.5)
        font.setWeight(QFont.DemiBold if (self.is_active or has_alert) else QFont.Normal)
        painter.setFont(font)
        fm = QFontMetrics(font)
        elided = fm.elidedText(self.project, Qt.ElideRight, text_width)

        if has_alert:
            text_color = pal["text_alert"]
        elif self.is_active or self._hovered:
            text_color = pal["text_primary"]
        else:
            text_color = pal["text_row"]
        painter.setPen(text_color)
        painter.drawText(
            QRectF(text_left, 0, text_width, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            elided,
        )
        painter.end()


class WorkspaceCardHeader(QWidget):
    """Minimal macOS header strip of an AgentWorkspaceCard: 13px mini icon + muted Agent name."""

    clicked = Signal(dict)
    right_clicked = Signal(dict, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        icon: QIcon,
        window_count: int,
        width: int = 128,
        theme: str = "dark",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.agent = agent
        self._icon = icon
        self._window_count = window_count
        self._theme = theme
        self._hovered = False
        self.setFixedSize(width, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{agent.get('name', '')} · 点击切换窗口")

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.agent)
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.agent, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pal = get_palette(self._theme)

        dpr = self.devicePixelRatioF()
        ico_sz = 13
        pm = self._icon.pixmap(QSize(int(ico_sz * dpr), int(ico_sz * dpr)))
        if not pm.isNull():
            pm.setDevicePixelRatio(dpr)
            painter.drawPixmap(4, int((self.height() - ico_sz) / 2), ico_sz, ico_sz, pm)

        right_pad = 18 if self._window_count > 1 else 6
        font = QFont("Segoe UI", 7)
        font.setPointSizeF(7.8)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        name = self.agent.get("name", "Agent")
        elided = fm.elidedText(name, Qt.ElideRight, max(12, self.width() - 22 - right_pad))
        painter.setPen(pal["text_header_hover"] if self._hovered else pal["text_header"])
        painter.drawText(
            QRectF(21, 0, self.width() - 21 - right_pad, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            elided,
        )

        if self._window_count > 1:
            cnt_font = QFont("Segoe UI", 7, QFont.DemiBold)
            painter.setFont(cnt_font)
            painter.setPen(pal["text_header"])
            painter.drawText(
                QRectF(self.width() - 18, 0, 14, self.height()),
                Qt.AlignVCenter | Qt.AlignRight,
                str(self._window_count),
            )

        painter.fillRect(QRectF(4, self.height() - 1.0, self.width() - 8, 1.0), pal["divider"])
        painter.end()


class AgentWorkspaceCard(QWidget):
    """Apple frosted glass card grouping all open project windows for one running Agent."""

    header_clicked = Signal(dict)
    header_right_clicked = Signal(dict, QPoint)
    window_clicked = Signal(int, str)
    window_right_clicked = Signal(dict, int, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        icon: QIcon,
        windows: list[dict[str, Any]],
        foreground_hwnd: int,
        notif_summary: AgentNotificationSummary,
        card_width: int = 138,
        theme: str = "dark",
        alpha: int = 232,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._alpha = alpha
        self._shadow_pad = 6
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(card_width + self._shadow_pad * 2)

        box = QVBoxLayout(self)
        box.setContentsMargins(
            self._shadow_pad + 5,
            self._shadow_pad + 4,
            self._shadow_pad + 5,
            self._shadow_pad + 5,
        )
        box.setSpacing(2)

        inner_w = card_width - 10
        header = WorkspaceCardHeader(
            agent=agent,
            icon=icon,
            window_count=len(windows),
            width=inner_w,
            theme=theme,
            parent=self,
        )
        header.clicked.connect(self.header_clicked.emit)
        header.right_clicked.connect(self.header_right_clicked.emit)
        box.addWidget(header)

        for idx, w in enumerate(windows):
            h = int(w["hwnd"])
            row = ProjectWindowRow(
                agent=agent,
                win_info=w,
                is_active=(h == foreground_hwnd),
                notif=notif_summary.by_hwnd.get(h),
                width=inner_w,
                show_top_divider=(idx > 0),
                theme=theme,
                parent=self,
            )
            row.clicked.connect(self.window_clicked.emit)
            row.right_clicked.connect(self.window_right_clicked.emit)
            box.addWidget(row)

        self.adjustSize()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pad = self._shadow_pad
        rect = QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        paint_apple_glass(painter, rect, radius=14.0, theme=self._theme, alpha=self._alpha)
        painter.end()


class AgentDockWindow(QWidget):
    """Apple macOS-inspired two-tier floating glass dock with drag-drop, auto-discovery & dual theme."""

    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._drag_offset: QPoint | None = None
        self._buttons: dict[str, AgentIconButton] = {}
        self._icons: dict[str, QIcon] = {}
        self._agent_windows: dict[str, list[dict[str, Any]]] = {}
        self._last_shelf_sig: tuple[Any, ...] | None = None
        self._taskbar_hider = TaskbarHider()
        self._notif_tracker = NotificationTracker()
        self._shellhook_msg: int = 0
        self._shellhook_registered: bool = False

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup_resources)
        atexit.register(self._cleanup_resources)

        self.setWindowTitle("AI docker")
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAcceptDrops(True)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(2)
        self._root_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        theme = self._theme()
        self._top_bar = TopDockCapsule(theme=theme, alpha=self._capsule_alpha(), parent=self)
        self._grip = DragGrip(
            height=int(self.config["ui"].get("button_size", 46)),
            theme=theme,
            parent=self._top_bar,
        )
        self._root_layout.addWidget(self._top_bar, 0, Qt.AlignLeft)

        self._shelf = QWidget(self)
        self._shelf.setAttribute(Qt.WA_TranslucentBackground, True)
        self._shelf_layout = QHBoxLayout(self._shelf)
        self._shelf_layout.setContentsMargins(1, 0, 1, 0)
        self._shelf_layout.setSpacing(0)
        self._shelf_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._shelf.hide()
        self._root_layout.addWidget(self._shelf, 0, Qt.AlignLeft)

        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=False)
        self._restore_position()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1200)
        self._status_timer.timeout.connect(self._refresh_state)
        self._status_timer.start()
        QTimer.singleShot(200, self._refresh_state)

    @property
    def _columns(self) -> list[AgentIconButton]:
        return list(self._buttons.values())

    def _theme(self) -> str:
        t = str(self.config["ui"].get("theme", "dark")).lower()
        return "light" if t == "light" else "dark"

    def _capsule_alpha(self) -> int:
        opacity = float(self.config["ui"].get("opacity", 0.92))
        return max(170, min(248, int(opacity * 255)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._shellhook_registered:
            try:
                self._shellhook_msg = int(user32.RegisterWindowMessageW("SHELLHOOK"))
                self._shellhook_registered = bool(
                    user32.RegisterShellHookWindow(wintypes.HWND(int(self.winId())))
                )
            except Exception:
                self._shellhook_registered = False

    def nativeEvent(self, eventType, message):
        if self._shellhook_registered and self._shellhook_msg and eventType in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == self._shellhook_msg:
                    code = int(msg.wParam)
                    target_hwnd = int(msg.lParam)
                    if code == HSHELL_FLASH and target_hwnd:
                        self._notif_tracker.on_shell_flash(target_hwnd, get_foreground_hwnd())
                        QTimer.singleShot(50, self._refresh_state)
                    elif code in (HSHELL_WINDOWACTIVATED, HSHELL_RUDEAPPACTIVATED) and target_hwnd:
                        self._notif_tracker.clear_window(target_hwnd)
                        QTimer.singleShot(80, self._refresh_state)
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    # --- Drag & Drop .exe / .lnk directly onto dock to add Agent ---
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local and local.lower().endswith((".exe", ".lnk")):
                    self._top_bar.set_drop_highlight(True)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._top_bar.set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._top_bar.set_drop_highlight(False)
        added = 0
        existing_paths = {
            os.path.normcase(os.path.normpath(a.get("path", "")))
            for a in self.config.get("agents", [])
            if a.get("path")
        }
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local or not local.lower().endswith((".exe", ".lnk")):
                continue
            new_agent = build_agent_from_file(local, self.config.get("agents", []))
            if not new_agent:
                continue
            norm = os.path.normcase(os.path.normpath(new_agent["path"]))
            if norm in existing_paths:
                # Re-enable if it already existed but was hidden
                for a in self.config["agents"]:
                    if a.get("path") and os.path.normcase(os.path.normpath(a["path"])) == norm:
                        a["enabled"] = True
                        added += 1
                        break
                continue
            existing_paths.add(norm)
            self.config["agents"].append(new_agent)
            added += 1

        if added > 0:
            save_config(self.config)
            top_left = self.pos()
            self._rebuild_dock(force_refresh_icons=False)
            self.move(top_left)
            self._refresh_state()
            event.acceptProposedAction()

    def _apply_styles(self) -> None:
        theme = self._theme()
        pal = get_palette(theme)
        self._top_bar.set_theme_and_alpha(theme, self._capsule_alpha())
        self._grip.set_theme(theme)
        for btn in self._buttons.values():
            btn.set_theme(theme)

        self.setStyleSheet(
            f"""
            QToolTip {{
                background-color: {pal['tip_bg']};
                color: {pal['tip_fg']};
                border: 1px solid {pal['tip_border']};
                border-radius: 8px;
                padding: 5px 10px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
            }}
            QMenu {{
                background-color: {pal['menu_bg']};
                color: {pal['menu_fg']};
                border: 1px solid {pal['menu_border']};
                border-radius: 10px;
                padding: 5px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 6px 20px 6px 12px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {pal['menu_hover']};
                color: #ffffff;
            }}
            QMenu::item:disabled {{
                color: {pal['menu_disabled']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {pal['menu_sep']};
                margin: 4px 8px;
            }}
            """
        )

    def _rebuild_dock(self, force_refresh_icons: bool = False) -> None:
        ui = self.config["ui"]
        theme = self._theme()
        btn_size = int(ui.get("button_size", 46))
        icon_size = int(ui.get("icon_size", 32))

        while self._top_bar.row_layout.count():
            item = self._top_bar.row_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._grip:
                w.deleteLater()
        self._buttons.clear()
        self._icons.clear()

        self._grip.set_theme(theme)
        self._grip.setFixedSize(16, btn_size)
        self._top_bar.row_layout.addWidget(self._grip)

        btn_list: list[AgentIconButton] = []
        for agent in self.config.get("agents", []):
            if agent.get("enabled", True) is False:
                continue
            aid = agent.get("id", "")
            icon = load_agent_icon(agent, force_refresh=force_refresh_icons, theme=theme)
            self._icons[aid] = icon
            btn = AgentIconButton(
                agent=agent,
                icon=icon,
                button_size=btn_size,
                icon_size=icon_size,
                theme=theme,
                parent=self._top_bar,
            )
            btn.clicked.connect(self._on_agent_clicked)
            btn.right_clicked.connect(self._on_agent_right_clicked)
            self._top_bar.row_layout.addWidget(btn)
            self._buttons[aid] = btn
            btn_list.append(btn)

        self._top_bar.set_icon_buttons(btn_list)
        self._last_shelf_sig = None
        self._top_bar.adjustSize()
        self.adjustSize()
        self.update()

    def _rebuild_shelf(
        self,
        agents: list[dict[str, Any]],
        agent_windows: dict[str, list[dict[str, Any]]],
        foreground_hwnd: int,
        notif_map: dict[str, AgentNotificationSummary],
        show_list: bool,
    ) -> bool:
        theme = self._theme()
        sig_cards = []
        active_agents: list[dict[str, Any]] = []
        if show_list:
            for ag in agents:
                if ag.get("enabled", True) is False:
                    continue
                aid = ag.get("id", "")
                wins = agent_windows.get(aid, [])
                if not wins:
                    continue
                active_agents.append(ag)
                summary = notif_map.get(aid, AgentNotificationSummary())
                win_sigs = tuple(
                    (
                        int(w["hwnd"]),
                        w.get("project", ""),
                        w.get("title", ""),
                        int(w["hwnd"]) == foreground_hwnd,
                        summary.by_hwnd[int(w["hwnd"])].count if int(w["hwnd"]) in summary.by_hwnd else 0,
                        summary.by_hwnd[int(w["hwnd"])].flashing if int(w["hwnd"]) in summary.by_hwnd else False,
                        summary.by_hwnd[int(w["hwnd"])].latest_text if int(w["hwnd"]) in summary.by_hwnd else "",
                    )
                    for w in wins
                )
                sig_cards.append((aid, win_sigs))

        new_sig = (theme, bool(show_list), tuple(sig_cards))
        if new_sig == self._last_shelf_sig:
            return False
        self._last_shelf_sig = new_sig

        while self._shelf_layout.count():
            item = self._shelf_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if active_agents:
            card_width = int(self.config["ui"].get("window_chip_width", 138))
            alpha = self._capsule_alpha()
            for ag in active_agents:
                aid = ag.get("id", "")
                wins = agent_windows.get(aid, [])
                icon = self._icons.get(aid) or load_agent_icon(ag, force_refresh=False, theme=theme)
                summary = notif_map.get(aid, AgentNotificationSummary())
                card = AgentWorkspaceCard(
                    agent=ag,
                    icon=icon,
                    windows=wins,
                    foreground_hwnd=foreground_hwnd,
                    notif_summary=summary,
                    card_width=card_width,
                    theme=theme,
                    alpha=alpha,
                    parent=self._shelf,
                )
                card.header_clicked.connect(self._on_agent_clicked)
                card.header_right_clicked.connect(self._on_agent_right_clicked)
                card.window_clicked.connect(self._on_window_chip_clicked)
                card.window_right_clicked.connect(self._on_window_chip_right_clicked)
                self._shelf_layout.addWidget(card, 0, Qt.AlignTop)
            self._shelf.adjustSize()
            self._shelf.show()
        else:
            self._shelf.hide()

        return True

    def _restore_position(self) -> None:
        saved = self.config["ui"].get("pos")
        if isinstance(saved, list) and len(saved) == 2:
            pt = QPoint(int(saved[0]), int(saved[1]))
            for screen in QGuiApplication.screens():
                if screen.availableGeometry().contains(pt + QPoint(24, 24)):
                    self.move(pt)
                    return

        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.left() + (geo.width() - self.width()) // 2
            y = geo.top() + 14
            self.move(x, y)

    def _save_position(self) -> None:
        pos = self.pos()
        self.config["ui"]["pos"] = [pos.x(), pos.y()]
        save_config(self.config)

    def _refresh_state(self) -> None:
        ui = self.config["ui"]
        show_dot = bool(ui.get("show_running_dot", True))
        show_list = bool(ui.get("show_window_list", True))
        hide_enabled = bool(ui.get("hide_from_taskbar", True))
        agents = self.config.get("agents", [])

        self._agent_windows = get_agent_window_map(agents)
        fg_hwnd = get_foreground_hwnd()
        notif_map = self._notif_tracker.poll(agents, self._agent_windows, fg_hwnd)

        for aid, btn in self._buttons.items():
            wins = self._agent_windows.get(aid, [])
            summary = notif_map.get(aid, AgentNotificationSummary())
            btn.update_state(
                window_count=len(wins),
                show_dot=show_dot,
                notif_count=summary.total_count,
                has_alert=summary.has_alert,
                notif_text=summary.latest_text,
            )

        shelf_changed = self._rebuild_shelf(
            agents=agents,
            agent_windows=self._agent_windows,
            foreground_hwnd=fg_hwnd,
            notif_map=notif_map,
            show_list=show_list,
        )
        if shelf_changed:
            top_left = self.pos()
            self.adjustSize()
            self.move(top_left)
            self.update()

        if hide_enabled:
            target_hwnds: set[int] = set()
            for agent in agents:
                if agent.get("enabled", True) is False:
                    continue
                if agent.get("hide_from_taskbar", True) is False:
                    continue
                aid = agent.get("id", "")
                for w in self._agent_windows.get(aid, []):
                    target_hwnds.add(w["hwnd"])
            self._taskbar_hider.sync_hidden_windows(target_hwnds)
        else:
            if self._taskbar_hider.hidden_count > 0:
                self._taskbar_hider.restore_all()

    def _on_agent_clicked(self, agent: dict[str, Any]) -> None:
        aid = agent.get("id", "")
        wins = self._agent_windows.get(aid, [])
        if len(wins) <= 1:
            self._notif_tracker.clear_agent(aid, [w["hwnd"] for w in wins])

        focus_existing = bool(self.config["ui"].get("focus_existing", True))
        ok, msg = launch_agent(agent, focus_existing=focus_existing)
        if not ok:
            QMessageBox.warning(self, "AI docker 启动失败", f"{agent.get('name', '')}: {msg}")
        else:
            QTimer.singleShot(350, self._refresh_state)

    def _on_window_chip_clicked(self, hwnd: int, agent_id: str) -> None:
        wins = self._agent_windows.get(agent_id, [])
        self._notif_tracker.clear_window(hwnd, agent_id=agent_id if len(wins) <= 1 else None)
        focus_hwnd(hwnd)
        QTimer.singleShot(180, self._refresh_state)

    def _on_agent_right_clicked(self, agent: dict[str, Any], global_pos: QPoint) -> None:
        self._show_context_menu(global_pos, agent=agent, target_hwnd=None)

    def _on_window_chip_right_clicked(
        self, agent: dict[str, Any], hwnd: int, global_pos: QPoint
    ) -> None:
        self._show_context_menu(global_pos, agent=agent, target_hwnd=hwnd)

    def _show_context_menu(
        self,
        global_pos: QPoint,
        agent: dict[str, Any] | None = None,
        target_hwnd: int | None = None,
    ) -> None:
        menu = QMenu(self)

        if agent is not None:
            aid = agent.get("id", "")
            open_wins = self._agent_windows.get(aid, [])
            if target_hwnd is not None:
                act_focus = menu.addAction("切换到该窗口")
                act_focus.triggered.connect(lambda: self._on_window_chip_clicked(target_hwnd, aid))
                act_clear_win = menu.addAction("清除该窗口通知角标")
                act_clear_win.triggered.connect(
                    lambda: (self._notif_tracker.clear_window(target_hwnd, aid), self._refresh_state())
                )
                menu.addSeparator()
            elif open_wins:
                for w in open_wins[:8]:
                    proj = w.get("project") or w.get("title") or agent.get("name", "窗口")
                    hwnd = w["hwnd"]
                    act = menu.addAction(f"切换到：{proj}")
                    act.triggered.connect(
                        lambda checked=False, h=hwnd, a=aid: self._on_window_chip_clicked(h, a)
                    )
                menu.addSeparator()

            new_inst = menu.addAction(f"新开实例：{agent.get('name', '')}")
            new_inst.triggered.connect(lambda: launch_agent(agent, focus_existing=False))

            if target_hwnd is None:
                visible_count = sum(
                    1 for a in self.config.get("agents", []) if a.get("enabled", True) is not False
                )
                hide_icon_act = menu.addAction(f"隐藏该图标：{agent.get('name', '')}")
                if visible_count <= 1:
                    hide_icon_act.setEnabled(False)
                    hide_icon_act.setText("隐藏该图标（至少保留 1 个）")
                else:
                    hide_icon_act.triggered.connect(
                        lambda checked=False, a=aid: self._set_agent_visible(a, False)
                    )
            menu.addSeparator()

        # Agent discovery & manual add
        discover_act = menu.addAction("扫描本机新安装的 Agent...")
        discover_act.triggered.connect(self._open_discover_dialog)

        add_act = menu.addAction("添加新 Agent...（或直接拖入 .exe/.lnk）")
        add_act.triggered.connect(self._open_add_dialog)

        all_agents = self.config.get("agents", [])
        hidden_count = sum(1 for a in all_agents if a.get("enabled", True) is False)
        vis_title = (
            f"显示 / 隐藏图标（已隐藏 {hidden_count} 个）"
            if hidden_count > 0
            else "显示 / 隐藏图标"
        )
        vis_menu = menu.addMenu(vis_title)
        visible_total = len(all_agents) - hidden_count
        for item_agent in all_agents:
            item_id = item_agent.get("id", "")
            item_name = item_agent.get("name", item_id)
            is_shown = item_agent.get("enabled", True) is not False
            act = vis_menu.addAction(item_name)
            act.setCheckable(True)
            act.setChecked(is_shown)
            if is_shown and visible_total <= 1:
                act.setEnabled(False)
            else:
                act.triggered.connect(
                    lambda checked=False, a=item_id: self._set_agent_visible(a, bool(checked))
                )
        if hidden_count > 0:
            vis_menu.addSeparator()
            show_all_act = vis_menu.addAction("全部显示")
            show_all_act.triggered.connect(self._show_all_agents)

        menu.addSeparator()

        is_dark = self._theme() == "dark"
        theme_act = menu.addAction("切换外观：macOS 浅色毛玻璃" if is_dark else "切换外观：macOS 深色磨砂")
        theme_act.triggered.connect(self._toggle_theme)

        show_list_action = menu.addAction("下方展开已打开项目窗口")
        show_list_action.setCheckable(True)
        show_list_action.setChecked(bool(self.config["ui"].get("show_window_list", True)))
        show_list_action.triggered.connect(self._toggle_show_window_list)

        hide_action = menu.addAction("自动隐藏已收纳 Agent 的任务栏图标")
        hide_action.setCheckable(True)
        hide_action.setChecked(bool(self.config["ui"].get("hide_from_taskbar", True)))
        hide_action.triggered.connect(self._toggle_hide_from_taskbar)

        focus_action = menu.addAction("点击图标时优先唤醒已开窗口")
        focus_action.setCheckable(True)
        focus_action.setChecked(bool(self.config["ui"].get("focus_existing", True)))
        focus_action.triggered.connect(self._toggle_focus_existing)

        clear_all_action = menu.addAction("清除全部通知角标")
        clear_all_action.triggered.connect(self._clear_all_notifications)

        menu.addSeparator()

        reload_action = menu.addAction("重新加载配置与图标")
        reload_action.triggered.connect(self._reload_all)

        edit_action = menu.addAction("打开配置文件 (agents.json)")
        edit_action.triggered.connect(lambda: os.startfile(str(CONFIG_PATH)))

        menu.addSeparator()

        autostart_action = menu.addAction("开机自动启动")
        autostart_action.setCheckable(True)
        autostart_action.setChecked(is_autostart_enabled())
        autostart_action.triggered.connect(self._toggle_autostart)

        quit_action = menu.addAction("退出 AI docker (还原任务栏图标)")
        quit_action.triggered.connect(self._quit_app)

        menu.exec(global_pos)

    def _open_discover_dialog(self) -> None:
        dlg = DiscoverAgentsDialog(self.config.get("agents", []), theme=self._theme(), parent=self)
        if dlg.exec() and dlg.selected_agents:
            self.config["agents"].extend(dlg.selected_agents)
            save_config(self.config)
            top_left = self.pos()
            self._rebuild_dock(force_refresh_icons=False)
            self.move(top_left)
            self._refresh_state()

    def _open_add_dialog(self) -> None:
        dlg = AddAgentDialog(self.config.get("agents", []), theme=self._theme(), parent=self)
        if dlg.exec() and dlg.created_agent:
            self.config["agents"].append(dlg.created_agent)
            save_config(self.config)
            top_left = self.pos()
            self._rebuild_dock(force_refresh_icons=False)
            self.move(top_left)
            self._refresh_state()

    def _toggle_theme(self) -> None:
        self.config["ui"]["theme"] = "light" if self._theme() == "dark" else "dark"
        save_config(self.config)
        top_left = self.pos()
        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=False)
        self.move(top_left)
        self._refresh_state()

    def _set_agent_visible(self, agent_id: str, visible: bool) -> None:
        agents = self.config.get("agents", [])
        if not visible:
            visible_count = sum(1 for a in agents if a.get("enabled", True) is not False)
            if visible_count <= 1:
                return
        changed = False
        for a in agents:
            if a.get("id") == agent_id:
                if (a.get("enabled", True) is not False) != visible:
                    a["enabled"] = visible
                    changed = True
                break
        if not changed:
            return
        save_config(self.config)
        top_left = self.pos()
        self._rebuild_dock(force_refresh_icons=False)
        self.move(top_left)
        self._refresh_state()

    def _show_all_agents(self) -> None:
        changed = False
        for a in self.config.get("agents", []):
            if a.get("enabled", True) is False:
                a["enabled"] = True
                changed = True
        if not changed:
            return
        save_config(self.config)
        top_left = self.pos()
        self._rebuild_dock(force_refresh_icons=False)
        self.move(top_left)
        self._refresh_state()

    def _toggle_show_window_list(self, checked: bool) -> None:
        self.config["ui"]["show_window_list"] = bool(checked)
        save_config(self.config)
        self._refresh_state()

    def _toggle_hide_from_taskbar(self, checked: bool) -> None:
        self.config["ui"]["hide_from_taskbar"] = bool(checked)
        save_config(self.config)
        if not checked:
            self._taskbar_hider.restore_all()
        else:
            self._refresh_state()

    def _toggle_focus_existing(self, checked: bool) -> None:
        self.config["ui"]["focus_existing"] = bool(checked)
        save_config(self.config)

    def _clear_all_notifications(self) -> None:
        for agent in self.config.get("agents", []):
            aid = agent.get("id", "")
            wins = self._agent_windows.get(aid, [])
            self._notif_tracker.clear_agent(aid, [w["hwnd"] for w in wins])
        self._refresh_state()

    def _reload_all(self) -> None:
        self.config = load_config()
        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=True)
        self._refresh_state()

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            set_autostart(bool(checked))
        except Exception as exc:
            QMessageBox.warning(self, "开机自启设置失败", str(exc))

    def _cleanup_resources(self) -> None:
        if self._shellhook_registered:
            try:
                user32.DeregisterShellHookWindow(wintypes.HWND(int(self.winId())))
            except Exception:
                pass
            self._shellhook_registered = False
        self._taskbar_hider.close()

    def _quit_app(self) -> None:
        self._cleanup_resources()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._cleanup_resources()
        super().closeEvent(event)

    # --- Window dragging ---
    def begin_external_drag(self, global_pos: QPoint) -> None:
        self._drag_offset = global_pos - self.frameGeometry().topLeft()

    def update_external_drag(self, global_pos: QPoint) -> None:
        if self._drag_offset is not None:
            self.move(global_pos - self._drag_offset)

    def end_external_drag(self) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._show_context_menu(QCursor.pos())
