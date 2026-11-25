from __future__ import annotations

import atexit
import ctypes
import math
import os
import time
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
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .config import CONFIG_PATH, PROJECT_ROOT, ConfigLoadError, load_config, save_config
from .dialogs import (
    AddAgentDialog,
    DiscoverAgentsDialog,
    UpdateDialog,
    normalize_web_url,
    read_internet_shortcut_url,
)
from .discovery import build_agent_from_file
from .icons import WebIconLoader, load_agent_icon
from .launcher import (
    close_hwnd,
    focus_hwnd,
    get_agent_window_map,
    get_foreground_hwnd,
    get_last_focused_hwnd,
    is_autostart_enabled,
    launch_agent,
    set_autostart,
)
from .ipc import (
    INSTALL_PROPERTY as _INSTALL_PROPERTY,
    IPC_CMD_CLEAR_NOTIFICATIONS,
    IPC_CMD_COLLAPSE,
    IPC_CMD_EXPAND,
    IPC_CMD_RELOAD,
    IPC_CONTROL_MESSAGE_NAME,
)
from .notifications import (
    AgentNotificationSummary,
    NotificationTracker,
    WindowNotificationState,
)
from .taskbar import TaskbarHider
from .theme import AppleGlassMenu, get_palette, paint_apple_glass
from .updater import UpdateChecker, UpdateCheckResult, launch_updater_script

user32 = ctypes.windll.user32
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterShellHookWindow.argtypes = [wintypes.HWND]
user32.RegisterShellHookWindow.restype = wintypes.BOOL
user32.DeregisterShellHookWindow.argtypes = [wintypes.HWND]
user32.DeregisterShellHookWindow.restype = wintypes.BOOL
user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
user32.SetPropW.restype = wintypes.BOOL
user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.RemovePropW.restype = wintypes.HANDLE

HSHELL_WINDOWACTIVATED = 4
HSHELL_RUDEAPPACTIVATED = 0x8004
HSHELL_FLASH = 0x8006

TYPE_LABELS = {
    "exe": "桌面应用",
    "msix": "桌面应用",
    "cli": "终端 CLI",
    "web": "网页应用",
}


class DragGrip(QWidget):
    """Dedicated Apple-minimal drag handle at the edge of the dock capsule to move the whole dock."""

    double_clicked = Signal()

    def __init__(
        self,
        height: int = 46,
        theme: str = "dark",
        orientation: str = "horizontal",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        self._size = height
        self._hovered = False
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("左键拖动悬浮窗位置 · 双击收缩为悬浮图标 · 右键打开全局菜单")
        self._apply_size()

    def _apply_size(self) -> None:
        if self._orientation == "vertical":
            self.setFixedSize(self._size, 16)
        else:
            self.setFixedSize(16, self._size)

    def set_orientation(self, orientation: str, size: int) -> None:
        self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        self._size = size
        self._apply_size()
        self.update()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = get_palette(self._theme)
        is_vert = self._orientation == "vertical"

        if self._hovered:
            bg = (
                QRectF(6.0, 1.0, self.width() - 12.0, self.height() - 2.0)
                if is_vert
                else QRectF(1.0, 6.0, self.width() - 2.0, self.height() - 12.0)
            )
            path = QPainterPath()
            path.addRoundedRect(bg, 6.0, 6.0)
            painter.fillPath(path, pal["icon_hover_bg"])

        dot_color = pal["text_header_hover"] if self._hovered else pal["grip"]
        painter.setBrush(dot_color)
        painter.setPen(Qt.NoPen)

        r = 1.35
        if is_vert:
            mid_x = self.width() / 2.0
            xs = [mid_x - 7.5, mid_x - 2.5, mid_x + 2.5, mid_x + 7.5]
            ys = [5.5, 10.5]
        else:
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
    double_clicked = Signal(dict)
    right_clicked = Signal(dict, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        icon: QIcon,
        button_size: int = 46,
        icon_size: int = 32,
        theme: str = "dark",
        orientation: str = "horizontal",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.agent = agent
        self._icon = icon
        self._button_size = button_size
        self._icon_size = icon_size
        self._theme = theme
        self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        self._hovered = False
        self._pressed = False
        self._window_count = 0
        self._show_running_dot = True
        self._notif_count = 0
        self._has_alert = False
        self._notif_text = ""
        self._released_to_taskbar = False
        self._press_global: QPoint | None = None
        self._reordering = False
        self._suppress_next_release = False

        # Short single-click debounce so double-click opens a new instance without double-firing
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(lambda: self.clicked.emit(self.agent))

        # Smooth magnification animation state
        self._scale = 1.0
        self._target_scale = 1.0

        # Extra headroom on the perpendicular axis so magnified icons float cleanly without clipping
        if self._orientation == "vertical":
            self.setFixedSize(button_size + 6, button_size)
        else:
            self.setFixedSize(button_size, button_size + 6)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self._refresh_tooltip()

    def _find_capsule(self) -> TopDockCapsule | None:
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, TopDockCapsule):
                return w
            w = w.parentWidget()
        return None

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_target_scale(self, scale: float) -> None:
        self._target_scale = scale

    def set_icon(self, icon: QIcon) -> None:
        self._icon = icon
        self.update()

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
        released_to_taskbar: bool = False,
    ) -> None:
        changed = (
            self._window_count != window_count
            or self._show_running_dot != show_dot
            or self._notif_count != notif_count
            or self._has_alert != has_alert
            or self._notif_text != notif_text
            or self._released_to_taskbar != released_to_taskbar
        )
        if changed:
            self._window_count = window_count
            self._show_running_dot = show_dot
            self._notif_count = notif_count
            self._has_alert = has_alert
            self._notif_text = notif_text
            self._released_to_taskbar = released_to_taskbar
            self._refresh_tooltip()
            self.update()

    def _refresh_tooltip(self) -> None:
        name = self.agent.get("name", "Agent")
        type_label = TYPE_LABELS.get(self.agent.get("type", "exe"), "应用")
        if self._window_count > 1:
            status = f"运行中 ({self._window_count} 个窗口 · 双击新开)"
        elif self._window_count == 1:
            status = "运行中 · 双击新开"
        else:
            status = type_label
        if self._released_to_taskbar:
            status += " · 已释放到任务栏"
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
            self._reordering = False
            self.update()
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.right_clicked.emit(self.agent, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        capsule = self._find_capsule()
        cur_global = event.globalPosition().toPoint()

        if self._press_global is not None and (event.buttons() & Qt.LeftButton):
            if not self._reordering and (cur_global - self._press_global).manhattanLength() > 8:
                self._click_timer.stop()
                self._reordering = True
                self._pressed = False
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                if capsule is not None:
                    capsule.begin_reorder(self)
            if self._reordering:
                if capsule is not None:
                    capsule.update_reorder(cur_global)
                event.accept()
                return
            event.accept()
            return

        # Normal hover: feed cursor position to parent TopDockCapsule for smooth wave magnification
        if capsule is not None:
            capsule.update_wave_from_global(cur_global)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_next_release = True
            self._pressed = False
            self._reordering = False
            self._press_global = None
            self.setCursor(Qt.PointingHandCursor)
            self.update()
            self.double_clicked.emit(self.agent)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._suppress_next_release:
                self._suppress_next_release = False
                self._pressed = False
                self._press_global = None
                self.update()
                event.accept()
                return
            was_reordering = self._reordering
            self._reordering = False
            self._pressed = False
            self._press_global = None
            self.setCursor(Qt.PointingHandCursor)
            self.update()
            if was_reordering:
                capsule = self._find_capsule()
                if capsule is not None:
                    capsule.finish_reorder()
            elif self.rect().contains(event.position().toPoint()):
                self._click_timer.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pal = get_palette(self._theme)
        is_vert = self._orientation == "vertical"
        bg_rect = (
            QRectF(5.0, 2.0, self.width() - 10.0, self.height() - 4.0)
            if is_vert
            else QRectF(2.0, 5.0, self.width() - 4.0, self.height() - 10.0)
        )

        if self._reordering:
            path = QPainterPath()
            path.addRoundedRect(bg_rect, 11.0, 11.0)
            painter.fillPath(path, pal["icon_press_bg"])
            pen = QPen(pal["accent"], 1.2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            painter.setOpacity(0.5)
        elif self._pressed or self._hovered:
            path = QPainterPath()
            path.addRoundedRect(bg_rect, 11.0, 11.0)
            painter.fillPath(path, pal["icon_press_bg"] if self._pressed else pal["icon_hover_bg"])

        eff_scale = self._scale * (0.93 if self._pressed else 1.0)
        draw_size = min(float(self._button_size - 4), self._icon_size * eff_scale)
        lift = 0.0 if is_vert else (eff_scale - 1.0) * 14.0  # keep vertical slot icons vertically centered

        dpr = self.devicePixelRatioF()
        pm = self._icon.pixmap(QSize(int(math.ceil(draw_size * dpr)), int(math.ceil(draw_size * dpr))))
        if not pm.isNull():
            pm.setDevicePixelRatio(dpr)
            x = (self.width() - draw_size) / 2.0
            base_y = (self.height() - draw_size) / 2.0 - (1.5 if is_vert else 1.0)
            y = max(1.0, base_y) if is_vert else max(2.0, base_y - lift)
            painter.drawPixmap(QRectF(x, y, draw_size, draw_size), pm, QRectF(pm.rect()))

        # Classic Apple macOS 4px circular running dot at bottom (hollow ring when released to taskbar)
        if self._window_count > 0 and self._show_running_dot:
            dot_d = 4.0
            dx = (self.width() - dot_d) / 2.0
            dy = self.height() - (4.5 if is_vert else 5.2)
            if self._released_to_taskbar:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(pal["running_dot"], 1.25))
                painter.drawEllipse(QRectF(dx - 0.3, dy - 0.3, dot_d + 0.6, dot_d + 0.6))
            else:
                painter.setPen(Qt.NoPen)
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
            bx = self.width() - bw - (2.0 if is_vert else 1.0)
            by = 2.0 if is_vert else max(1.0, 3.0 - lift * 0.5)
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
    """Main Apple glass Dock capsule (horizontal or vertical) with 60fps wave magnification & drag reorder."""

    reorder_requested = Signal(str, int)

    def __init__(
        self,
        theme: str = "dark",
        alpha: int = 232,
        orientation: str = "horizontal",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._alpha = alpha
        self._orientation = "vertical" if orientation == "vertical" else "horizontal"
        self._shadow_pad = 7
        self._drop_highlight = False
        self._icon_buttons: list[AgentIconButton] = []
        self._slot_widgets: dict[str, QWidget] = {}
        self._reordering_btn: AgentIconButton | None = None
        self._insert_slot: int | None = None

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.row_layout = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._apply_orientation_layout()

        # 60fps animation timer (auto-stops when icons settle to 0% idle CPU)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick_wave)

    def _apply_orientation_layout(self) -> None:
        pad = self._shadow_pad
        if self._orientation == "vertical":
            self.row_layout.setDirection(QBoxLayout.TopToBottom)
            self.row_layout.setContentsMargins(pad + 3, pad + 9, pad + 3, pad + 11)
            self.row_layout.setSpacing(4)
            self.row_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        else:
            self.row_layout.setDirection(QBoxLayout.LeftToRight)
            self.row_layout.setContentsMargins(pad + 9, pad + 3, pad + 11, pad + 3)
            self.row_layout.setSpacing(6)
            self.row_layout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

    def set_orientation(self, orientation: str) -> None:
        normalized = "vertical" if orientation == "vertical" else "horizontal"
        if self._orientation != normalized:
            self._orientation = normalized
            self._apply_orientation_layout()
            self.update()

    def first_icon_top_offset(self, grip_height: int) -> int:
        """Top Y coordinate of the first icon slot in vertical mode for zero-offset shelf alignment."""
        return self._shadow_pad + 9 + grip_height + 4

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
        self._reordering_btn = None
        self._insert_slot = None

    def set_slot_widgets(self, slots: dict[str, QWidget]) -> None:
        self._slot_widgets = slots

    def set_slot_heights(self, heights: dict[str, int], default_height: int) -> None:
        """Keep each vertical icon slot aligned to the height of its horizontal window strip."""
        for aid, slot in self._slot_widgets.items():
            target_h = max(default_height, heights.get(aid, default_height))
            if slot.height() != target_h or slot.minimumHeight() != target_h:
                slot.setFixedHeight(target_h)
        self.row_layout.invalidate()
        self.adjustSize()

    def begin_reorder(self, btn: AgentIconButton) -> None:
        self._reordering_btn = btn
        self.reset_wave()
        try:
            self._insert_slot = self._icon_buttons.index(btn)
        except ValueError:
            self._insert_slot = None
        self.update()

    def update_reorder(self, global_pos: QPoint) -> None:
        if self._reordering_btn is None or not self._icon_buttons:
            return
        is_vert = self._orientation == "vertical"
        local_pt = self.mapFromGlobal(global_pos)
        local_coord = local_pt.y() if is_vert else local_pt.x()
        slot = len(self._icon_buttons)
        for idx, btn in enumerate(self._icon_buttons):
            center = btn.mapTo(self, btn.rect().center())
            btn_center = center.y() if is_vert else center.x()
            if local_coord < btn_center:
                slot = idx
                break
        if slot != self._insert_slot:
            self._insert_slot = slot
            self.update()

    def finish_reorder(self) -> None:
        btn = self._reordering_btn
        slot = self._insert_slot
        self._reordering_btn = None
        self._insert_slot = None
        self.update()

        if btn is None or slot is None or btn not in self._icon_buttons:
            return
        src_idx = self._icon_buttons.index(btn)
        target_idx = slot if slot <= src_idx else slot - 1
        target_idx = max(0, min(len(self._icon_buttons) - 1, target_idx))
        if target_idx != src_idx:
            aid = btn.agent.get("id", "")
            if aid:
                self.reorder_requested.emit(aid, target_idx)

    def update_wave_from_global(self, global_pos: QPoint) -> None:
        if self._reordering_btn is not None:
            return
        is_vert = self._orientation == "vertical"
        local_pt = self.mapFromGlobal(global_pos)
        local_coord = local_pt.y() if is_vert else local_pt.x()
        radius = 96.0
        for btn in self._icon_buttons:
            center = btn.mapTo(self, btn.rect().center())
            btn_center = center.y() if is_vert else center.x()
            dist = abs(local_coord - btn_center)
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
        if self._reordering_btn is not None:
            self.update_reorder(event.globalPosition().toPoint())
        else:
            self.update_wave_from_global(event.globalPosition().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._reordering_btn is None:
            self.reset_wave()
        super().leaveEvent(event)

    def _indicator_pos_for_slot(self, slot: int) -> float | None:
        if not self._icon_buttons:
            return None
        is_vert = self._orientation == "vertical"
        if slot <= 0:
            first_tl = self._icon_buttons[0].mapTo(self, QPoint(0, 0))
            return float(first_tl.y() if is_vert else first_tl.x()) - 3.0
        if slot >= len(self._icon_buttons):
            last = self._icon_buttons[-1]
            last_tl = last.mapTo(self, QPoint(0, 0))
            end_coord = (last_tl.y() + last.height()) if is_vert else (last_tl.x() + last.width())
            return float(end_coord) + 3.0
        left_btn = self._icon_buttons[slot - 1]
        right_btn = self._icon_buttons[slot]
        left_tl = left_btn.mapTo(self, QPoint(0, 0))
        right_tl = right_btn.mapTo(self, QPoint(0, 0))
        left_end = (left_tl.y() + left_btn.height()) if is_vert else (left_tl.x() + left_btn.width())
        right_start = right_tl.y() if is_vert else right_tl.x()
        return (left_end + right_start) / 2.0

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

        # Draw glowing Apple Blue insertion bar while dragging an icon to reorder
        if self._reordering_btn is not None and self._insert_slot is not None:
            pos = self._indicator_pos_for_slot(self._insert_slot)
            if pos is not None:
                pal = get_palette(self._theme)
                accent = pal["accent"]
                painter.setPen(Qt.NoPen)
                if self._orientation == "vertical":
                    bar_w = max(24.0, rect.width() - 16.0)
                    bx = rect.left() + (rect.width() - bar_w) / 2.0
                    painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 85))
                    painter.drawRoundedRect(QRectF(bx - 1.0, pos - 3.0, bar_w + 2.0, 6.0), 3.0, 3.0)
                    painter.setBrush(accent)
                    painter.drawRoundedRect(QRectF(bx, pos - 1.4, bar_w, 2.8), 1.4, 1.4)
                else:
                    bar_h = max(24.0, rect.height() - 16.0)
                    by = rect.top() + (rect.height() - bar_h) / 2.0
                    painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 85))
                    painter.drawRoundedRect(QRectF(pos - 3.0, by - 1.0, 6.0, bar_h + 2.0), 3.0, 3.0)
                    painter.setBrush(accent)
                    painter.drawRoundedRect(QRectF(pos - 1.4, by, 2.8, bar_h), 1.4, 1.4)

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
        show_left_divider: bool = False,
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
        self._show_left_divider = show_left_divider
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
        if self._show_left_divider and not self._hovered and not self.is_active and not self.notif.has_alert:
            painter.fillRect(QRectF(0, 5, 1.0, self.height() - 10), pal["row_divider"])

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


class AgentHorizontalStrip(QWidget):
    """Horizontal wrapping frosted-glass strip of project windows aligned next to a vertical icon slot."""

    window_clicked = Signal(int, str)
    window_right_clicked = Signal(dict, int, QPoint)

    def __init__(
        self,
        agent: dict[str, Any],
        windows: list[dict[str, Any]],
        foreground_hwnd: int,
        notif_summary: AgentNotificationSummary,
        chip_width: int = 106,
        max_cols: int = 4,
        theme: str = "dark",
        alpha: int = 232,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._alpha = alpha
        self._shadow_pad = 5
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        grid = QGridLayout(self)
        grid.setContentsMargins(
            self._shadow_pad + 5,
            self._shadow_pad + 3,
            self._shadow_pad + 5,
            self._shadow_pad + 3,
        )
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)

        cols = max(1, int(max_cols))
        for idx, w in enumerate(windows):
            row_idx = idx // cols
            col_idx = idx % cols
            h = int(w["hwnd"])
            row_widget = ProjectWindowRow(
                agent=agent,
                win_info=w,
                is_active=(h == foreground_hwnd),
                notif=notif_summary.by_hwnd.get(h),
                width=chip_width,
                show_top_divider=(row_idx > 0),
                show_left_divider=(col_idx > 0),
                theme=theme,
                parent=self,
            )
            row_widget.clicked.connect(self.window_clicked.emit)
            row_widget.right_clicked.connect(self.window_right_clicked.emit)
            grid.addWidget(row_widget, row_idx, col_idx)

        self.adjustSize()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pad = self._shadow_pad
        rect = QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2)
        paint_apple_glass(painter, rect, radius=12.0, theme=self._theme, alpha=self._alpha)
        painter.end()


class CollapsedDockOrb(QWidget):
    """Compact macOS frosted-glass floating orb when AI docker auto-collapses during idle."""

    hovered = Signal()

    def __init__(
        self,
        icon: QIcon,
        theme: str = "dark",
        alpha: int = 232,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._icon = icon
        self._theme = theme
        self._alpha = alpha
        self._shadow_pad = 7
        self._orb_size = 52
        self._running_count = 0
        self._notif_count = 0
        self._has_alert = False
        self._alert_agent_id = ""
        self._alert_agent_name = ""
        self._alert_agent_count = 0
        self._alert_text = ""
        self._drop_highlight = False

        total = self._orb_size + self._shadow_pad * 2
        self.setFixedSize(total, total)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh_tooltip()

    def set_theme_and_alpha(self, theme: str, alpha: int) -> None:
        if self._theme != theme or self._alpha != alpha:
            self._theme = theme
            self._alpha = alpha
            self.update()

    def set_drop_highlight(self, active: bool) -> None:
        if self._drop_highlight != active:
            self._drop_highlight = active
            self.update()

    def update_state(
        self,
        icon: QIcon,
        running_count: int,
        notif_count: int,
        has_alert: bool,
        alert_agent_id: str = "",
        alert_agent_name: str = "",
        alert_agent_count: int = 0,
        alert_text: str = "",
    ) -> None:
        self._icon = icon
        self._running_count = running_count
        self._notif_count = notif_count
        self._has_alert = has_alert
        self._alert_agent_id = alert_agent_id
        self._alert_agent_name = alert_agent_name
        self._alert_agent_count = alert_agent_count
        self._alert_text = alert_text
        self._refresh_tooltip()
        self.update()

    def _refresh_tooltip(self) -> None:
        if self._has_alert:
            head = (
                f"{self._alert_agent_name} 有新通知"
                if self._alert_agent_name
                else "AI docker 有新通知"
            )
            if self._alert_agent_count > 1:
                head += f"（共 {self._alert_agent_count} 个 Agent）"
            lines = [head]
            if self._alert_text:
                lines.append(self._alert_text[:90])
            elif self._notif_count > 0:
                lines.append(f"共 {self._notif_count} 条未读通知")
            lines.append("鼠标移入展开悬浮窗")
            self.setToolTip("\n".join(lines))
        else:
            status = (
                f"运行中 {self._running_count} 个窗口"
                if self._running_count > 0
                else "待机中"
            )
            self.setToolTip(f"AI docker  ·  {status}\n鼠标移入展开悬浮窗 · 右键打开设置")

    def enterEvent(self, event) -> None:
        self.hovered.emit()
        super().enterEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pal = get_palette(self._theme)

        pad = float(self._shadow_pad)
        rect = QRectF(pad, pad, self.width() - pad * 2.0, self.height() - pad * 2.0)
        paint_apple_glass(painter, rect, radius=16.0, theme=self._theme, alpha=self._alpha)

        if self._drop_highlight:
            path = QPainterPath()
            path.addRoundedRect(rect, 16.0, 16.0)
            painter.setPen(QPen(pal["accent"], 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        elif self._has_alert:
            alert_ring = QColor(pal["alert"])
            alert_ring.setAlpha(185)
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 15.5, 15.5)
            painter.setPen(QPen(alert_ring, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        # Centered crisp HiDPI icon (34x34)
        draw_size = 34.0
        dpr = self.devicePixelRatioF()
        pm = self._icon.pixmap(
            QSize(int(math.ceil(draw_size * dpr)), int(math.ceil(draw_size * dpr)))
        )
        if not pm.isNull():
            pm.setDevicePixelRatio(dpr)
            ix = rect.left() + (rect.width() - draw_size) / 2.0
            iy = rect.top() + (rect.height() - draw_size) / 2.0 - (1.5 if self._running_count > 0 else 0.0)
            painter.drawPixmap(QRectF(ix, iy, draw_size, draw_size), pm, QRectF(pm.rect()))

        # Subtle running indicator dot at bottom of glass squircle
        if self._running_count > 0:
            dot_d = 4.0
            dx = rect.left() + (rect.width() - dot_d) / 2.0
            dy = rect.bottom() - 6.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(pal["running_dot_glow"])
            painter.drawEllipse(QRectF(dx - 1.2, dy - 1.2, dot_d + 2.4, dot_d + 2.4))
            painter.setBrush(pal["running_dot"])
            painter.drawEllipse(QRectF(dx, dy, dot_d, dot_d))

        # Crisp Apple red notification badge in top-right corner
        if self._has_alert:
            badge_text = ""
            if self._notif_count > 0:
                badge_text = "9+" if self._notif_count > 9 else str(self._notif_count)
            bh = 16.0 if badge_text else 10.0
            bw = 21.0 if len(badge_text) > 1 else bh
            bx = self.width() - bw - 2.0
            by = 2.0
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


class AgentDockWindow(QWidget):
    """Apple macOS-inspired two-tier floating glass dock with drag-drop, auto-discovery & dual theme."""

    def __init__(self) -> None:
        super().__init__()
        self.config = load_config()
        self._closing = False
        self._drag_offset: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._buttons: dict[str, AgentIconButton] = {}
        self._icons: dict[str, QIcon] = {}
        self._web_icons = WebIconLoader(self)
        self._web_icons.ready.connect(self._on_web_icon_ready)
        self._updater = UpdateChecker(self)
        self._updater.finished.connect(self._on_update_check_finished)
        self._available_update: UpdateCheckResult | None = None
        self._agent_windows: dict[str, list[dict[str, Any]]] = {}
        self._last_notif_map: dict[str, AgentNotificationSummary] = {}
        self._last_shelf_sig: tuple[Any, ...] | None = None
        self._taskbar_hider = TaskbarHider()
        self._notif_tracker = NotificationTracker()
        self._shellhook_msg: int = 0
        self._shellhook_registered: bool = False
        self._ipc_msg: int = 0
        self._cached_hwnd: int = 0

        self._collapsed: bool = False
        self._collapsed_anchor: QPoint | None = None
        self._vertical_mirrored: bool = False
        self._expand_cooldown_until: float = 0.0
        self._menu_open: bool = False
        self._dialog_open: bool = False
        self._default_app_icon: QIcon = self._load_default_app_icon()

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

        self._root_layout = QBoxLayout(QBoxLayout.TopToBottom, self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(2)
        self._root_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        theme = self._theme()
        self._orb = CollapsedDockOrb(
            icon=self._default_app_icon,
            theme=theme,
            alpha=self._capsule_alpha(),
            parent=self,
        )
        self._orb.hovered.connect(self._on_orb_hovered)
        self._orb.hide()
        self._root_layout.addWidget(self._orb, 0, Qt.AlignTop | Qt.AlignLeft)

        self._top_bar = TopDockCapsule(
            theme=theme,
            alpha=self._capsule_alpha(),
            orientation=self._orientation(),
            parent=self,
        )
        self._top_bar.reorder_requested.connect(self._move_agent_to_visible_index)
        self._grip = DragGrip(
            height=int(self.config["ui"].get("button_size", 46)),
            theme=theme,
            orientation=self._orientation(),
            parent=self._top_bar,
        )
        self._grip.double_clicked.connect(self._on_grip_double_clicked)
        self._root_layout.addWidget(self._top_bar, 0, Qt.AlignTop | Qt.AlignLeft)

        self._shelf = QWidget(self)
        self._shelf.setAttribute(Qt.WA_TranslucentBackground, True)
        self._shelf_layout = QBoxLayout(QBoxLayout.LeftToRight, self._shelf)
        self._shelf_layout.setContentsMargins(1, 0, 1, 0)
        self._shelf_layout.setSpacing(0)
        self._shelf_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._shelf.hide()
        self._root_layout.addWidget(self._shelf, 0, Qt.AlignTop | Qt.AlignLeft)

        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=False)
        self._restore_position()
        self._collapsed_anchor = self.pos()
        self._apply_layout_orientation(self._vertical_mirror_right(self._collapsed_anchor))
        self.adjustSize()
        self.move(self._expanded_pos_for_anchor(self._collapsed_anchor))

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._try_auto_collapse)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1200)
        self._status_timer.timeout.connect(self._refresh_state)
        self._status_timer.start()
        QTimer.singleShot(200, self._refresh_state)
        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.setInterval(6000)
        self._startup_update_timer.timeout.connect(
            lambda: self._updater.check_async(silent=True)
        )
        self._startup_update_timer.start()
        if self._auto_collapse_enabled():
            self._schedule_auto_collapse()

    @staticmethod
    def _load_default_app_icon() -> QIcon:
        for rel in ("assets/ai_docker.png", "assets/ai_docker.ico"):
            candidate = PROJECT_ROOT / rel
            if candidate.exists():
                icon = QIcon(str(candidate))
                if not icon.isNull():
                    return icon
        return QIcon()

    def _auto_collapse_enabled(self) -> bool:
        return bool(self.config["ui"].get("auto_collapse", True))

    def _auto_collapse_seconds(self) -> int:
        try:
            sec = int(self.config["ui"].get("auto_collapse_seconds", 8))
        except (TypeError, ValueError, OverflowError):
            sec = 8
        return max(2, min(300, sec))

    def _is_interaction_busy(self) -> bool:
        return bool(
            self.underMouse()
            or self._menu_open
            or self._dialog_open
            or (self._drag_offset is not None)
            or (self._top_bar._reordering_btn is not None)
        )

    def _schedule_auto_collapse(self) -> None:
        if not self._auto_collapse_enabled() or self._collapsed or self._is_interaction_busy():
            return
        self._collapse_timer.start(self._auto_collapse_seconds() * 1000)

    def _cancel_auto_collapse(self) -> None:
        self._collapse_timer.stop()

    def _try_auto_collapse(self) -> None:
        if not self._auto_collapse_enabled() or self._collapsed or self._is_interaction_busy():
            return
        self._collapse_dock()

    def _collapse_dock(self, force: bool = False) -> None:
        if self._collapsed:
            return
        if not force and self._is_interaction_busy():
            return
        self._cancel_auto_collapse()
        if self._collapsed_anchor is None:
            self._collapsed_anchor = self.pos()
        if force:
            # Prevent menu-close enterEvent from immediately bouncing a manual collapse open
            self._expand_cooldown_until = time.monotonic() + 0.45
        else:
            self._expand_cooldown_until = 0.0
        self._top_bar.hide()
        self._shelf.hide()
        self._orb.show()
        self._collapsed = True
        self.adjustSize()
        self.move(self._collapsed_anchor)
        self.update()

    def _on_orb_hovered(self) -> None:
        if self._collapsed and time.monotonic() >= self._expand_cooldown_until:
            self._expand_dock()

    def _on_grip_double_clicked(self) -> None:
        self._drag_offset = None
        self._drag_origin = None
        if not self._collapsed:
            self._collapsed_anchor = self._anchor_for_current_pos()
            self._collapse_dock(force=True)

    def _expand_dock(self) -> None:
        self._cancel_auto_collapse()
        self._expand_cooldown_until = 0.0
        if not self._collapsed:
            return
        self._collapsed_anchor = self.pos()
        layout_changed = self._apply_layout_orientation(
            self._vertical_mirror_right(self._collapsed_anchor)
        )
        if layout_changed:
            self._last_shelf_sig = None
            self._rebuild_shelf(
                agents=self.config.get("agents", []),
                agent_windows=self._agent_windows,
                foreground_hwnd=get_foreground_hwnd(),
                notif_map=self._last_notif_map,
                show_list=bool(self.config["ui"].get("show_window_list", True)),
            )
        self._orb.hide()
        self._top_bar.show()
        show_list = bool(self.config["ui"].get("show_window_list", True))
        if show_list and self._shelf_layout.count() > 0:
            self._shelf.show()
        else:
            self._shelf.hide()
        self._collapsed = False
        self.adjustSize()

        self.move(self._expanded_pos_for_anchor(self._collapsed_anchor))
        self.update()

    def _reposition_after_rebuild(self, anchor: QPoint) -> None:
        if self._collapsed:
            self.move(anchor)
        else:
            self._collapsed_anchor = anchor
            self.move(self._expanded_pos_for_anchor(anchor))

    def _persist_config(
        self, *, ui: dict[str, Any] | None = None, agents: list[dict[str, Any]] | None = None
    ) -> bool:
        candidate = dict(self.config)
        if ui is not None:
            candidate["ui"] = {**self.config["ui"], **ui}
        if agents is not None:
            candidate["agents"] = agents
        try:
            save_config(candidate)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "AI docker 配置保存失败",
                f"无法写入 agents.json（可能正被其他程序占用）：\n{exc}",
            )
            return False
        self.config = candidate
        if agents is not None:
            for agent in agents:
                button = self._buttons.get(agent.get("id", ""))
                if button is not None:
                    button.agent = agent
            self._last_shelf_sig = None
        return True

    def _clamp_expanded_position(self, position: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(position + QPoint(24, 24)) or QGuiApplication.primaryScreen()
        if screen is None:
            return position
        geo = screen.availableGeometry()
        max_x = geo.right() - self.width() + 1
        max_y = geo.bottom() - self.height() + 1
        return QPoint(
            max(geo.left(), min(position.x(), max_x)),
            max(geo.top(), min(position.y(), max_y)),
        )

    def enterEvent(self, event) -> None:
        self._cancel_auto_collapse()
        if self._collapsed and time.monotonic() >= self._expand_cooldown_until:
            self._expand_dock()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._expand_cooldown_until = 0.0
        self._schedule_auto_collapse()
        super().leaveEvent(event)

    @property
    def _columns(self) -> list[AgentIconButton]:
        return list(self._buttons.values())

    def _theme(self) -> str:
        t = str(self.config["ui"].get("theme", "dark")).lower()
        return "light" if t == "light" else "dark"

    def _orientation(self) -> str:
        val = str(self.config["ui"].get("orientation", "horizontal")).strip().lower()
        return "vertical" if val == "vertical" else "horizontal"

    def _is_vertical(self) -> bool:
        return self._orientation() == "vertical"

    def _vertical_mirror_right(self, anchor: QPoint | None = None) -> bool:
        if not self._is_vertical():
            return False
        pt = anchor if anchor is not None else (self._collapsed_anchor or self.pos())
        screen = QGuiApplication.screenAt(pt + QPoint(24, 24)) or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        geo = screen.availableGeometry()
        return (pt.x() + self._orb.width() / 2.0) > geo.center().x()

    def _apply_layout_orientation(self, mirror_right: bool | None = None) -> bool:
        is_vert = self._is_vertical()
        target_mirrored = (
            bool(mirror_right)
            if (is_vert and mirror_right is not None)
            else (self._vertical_mirror_right() if is_vert else False)
        )
        mirrored_changed = target_mirrored != self._vertical_mirrored
        self._vertical_mirrored = target_mirrored

        if is_vert:
            self._root_layout.setDirection(QBoxLayout.LeftToRight)
            self._root_layout.setSpacing(2)
            desired_order = (
                [self._orb, self._shelf, self._top_bar]
                if self._vertical_mirrored
                else [self._orb, self._top_bar, self._shelf]
            )
            self._shelf_layout.setDirection(QBoxLayout.TopToBottom)
            top_offset = self._top_bar.first_icon_top_offset(16)
            self._shelf_layout.setContentsMargins(0, top_offset, 0, 0)
            self._shelf_layout.setSpacing(4)
            self._shelf_layout.setAlignment(
                Qt.AlignTop | (Qt.AlignRight if self._vertical_mirrored else Qt.AlignLeft)
            )
        else:
            self._root_layout.setDirection(QBoxLayout.TopToBottom)
            self._root_layout.setSpacing(2)
            desired_order = [self._orb, self._top_bar, self._shelf]
            self._shelf_layout.setDirection(QBoxLayout.LeftToRight)
            self._shelf_layout.setContentsMargins(1, 0, 1, 0)
            self._shelf_layout.setSpacing(0)
            self._shelf_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        current_order = [
            self._root_layout.itemAt(i).widget()
            for i in range(self._root_layout.count())
        ]
        if current_order != desired_order:
            for w in desired_order:
                self._root_layout.removeWidget(w)
            for w in desired_order:
                self._root_layout.addWidget(w, 0, Qt.AlignTop | Qt.AlignLeft)
            self._root_layout.invalidate()
        return mirrored_changed

    def _expanded_pos_for_anchor(self, anchor: QPoint) -> QPoint:
        if self._is_vertical() and self._vertical_mirrored:
            raw = QPoint(anchor.x() + self._orb.width() - self.width(), anchor.y())
            return self._clamp_expanded_position(raw)
        return self._clamp_expanded_position(anchor)

    def _anchor_for_current_pos(self) -> QPoint:
        if self._collapsed or not (self._is_vertical() and self._vertical_mirrored):
            return self.pos()
        return QPoint(self.x() + self.width() - self._orb.width(), self.y())

    def _vertical_max_cols(self, chip_width: int) -> int:
        pt = self._collapsed_anchor or self.pos()
        screen = QGuiApplication.screenAt(pt + QPoint(24, 24)) or QGuiApplication.primaryScreen()
        if screen is None:
            return 4
        geo = screen.availableGeometry()
        bar_w = self._top_bar.width() or (int(self.config["ui"].get("button_size", 46)) + 26)
        avail_w = geo.width() - bar_w - 36
        cols = (avail_w - 20) // max(1, chip_width + 2)
        return max(1, min(6, int(cols)))

    def _capsule_alpha(self) -> int:
        opacity = float(self.config["ui"].get("opacity", 0.92))
        return max(170, min(248, int(opacity * 255)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            self._cached_hwnd = int(self.winId())
        except Exception:
            self._cached_hwnd = 0
        if not self._shellhook_registered and self._cached_hwnd:
            try:
                self._shellhook_msg = int(user32.RegisterWindowMessageW("SHELLHOOK"))
                self._shellhook_registered = bool(
                    user32.RegisterShellHookWindow(wintypes.HWND(self._cached_hwnd))
                )
            except Exception:
                self._shellhook_registered = False
        if self._cached_hwnd:
            user32.SetPropW(self._cached_hwnd, _INSTALL_PROPERTY, 1)
            if not self._ipc_msg:
                try:
                    self._ipc_msg = int(user32.RegisterWindowMessageW(IPC_CONTROL_MESSAGE_NAME))
                except Exception:
                    self._ipc_msg = 0

    def nativeEvent(self, eventType, message):
        if eventType in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if self._ipc_msg and msg.message == self._ipc_msg:
                    cmd = int(msg.wParam)
                    if cmd == IPC_CMD_RELOAD:
                        QTimer.singleShot(0, lambda: self._reload_all(force_refresh_icons=False, silent=True))
                    elif cmd == IPC_CMD_EXPAND:
                        QTimer.singleShot(0, self._expand_dock)
                    elif cmd == IPC_CMD_COLLAPSE:
                        QTimer.singleShot(0, lambda: self._collapse_dock(force=True))
                    elif cmd == IPC_CMD_CLEAR_NOTIFICATIONS:
                        QTimer.singleShot(0, self._clear_all_notifications)
                    return True, 0
                if self._shellhook_registered and self._shellhook_msg and msg.message == self._shellhook_msg:
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

    # --- Drag & Drop .exe / .lnk / .url / web URLs directly onto dock to add Agent ---
    def _extract_drop_payload(self, mime_data) -> tuple[list[str], str | None]:
        exe_files: list[str] = []
        web_url: str | None = None
        if mime_data.hasUrls():
            for url in mime_data.urls():
                if url.isLocalFile():
                    local = url.toLocalFile()
                    low = local.lower()
                    if low.endswith((".exe", ".lnk")):
                        exe_files.append(local)
                    elif low.endswith(".url") and web_url is None:
                        web_url = read_internet_shortcut_url(local)
                elif url.scheme().lower() in ("http", "https") and web_url is None:
                    web_url = normalize_web_url(url.toString())
        elif mime_data.hasText():
            text = mime_data.text().strip()
            if text.lower().startswith(("http://", "https://")) and "\n" not in text:
                web_url = normalize_web_url(text)
        return exe_files, web_url

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        exe_files, web_url = self._extract_drop_payload(event.mimeData())
        if exe_files or web_url:
            self._cancel_auto_collapse()
            self._top_bar.set_drop_highlight(True)
            self._orb.set_drop_highlight(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._top_bar.set_drop_highlight(False)
        self._orb.set_drop_highlight(False)
        self._schedule_auto_collapse()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._top_bar.set_drop_highlight(False)
        self._orb.set_drop_highlight(False)
        exe_files, web_url = self._extract_drop_payload(event.mimeData())
        agents = list(self.config["agents"])
        found = False
        existing_paths = {
            os.path.normcase(os.path.normpath(agent["path"])): index
            for index, agent in enumerate(agents)
            if agent.get("path")
        }
        for local in exe_files:
            new_agent = build_agent_from_file(local, agents)
            if not new_agent:
                continue
            norm = os.path.normcase(os.path.normpath(new_agent["path"]))
            index = existing_paths.get(norm)
            if index is None:
                existing_paths[norm] = len(agents)
                agents.append(new_agent)
            elif agents[index].get("enabled", True) is False:
                agents[index] = {**agents[index], "enabled": True}
            found = True

        if found and (agents == self.config["agents"] or self._persist_config(agents=agents)):
            if self._collapsed:
                self._expand_dock()
            anchor = self._anchor_for_current_pos()
            self._rebuild_dock(force_refresh_icons=False)
            self._refresh_state()
            self._reposition_after_rebuild(anchor)
            event.acceptProposedAction()
            self._schedule_auto_collapse()
            return

        if web_url:
            event.acceptProposedAction()
            QTimer.singleShot(
                0,
                lambda u=web_url: self._open_add_dialog(initial_type="web", initial_url=u),
            )
            return

        self._schedule_auto_collapse()

    def _apply_styles(self) -> None:
        theme = self._theme()
        pal = get_palette(theme)
        self._orb.set_theme_and_alpha(theme, self._capsule_alpha())
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
            """
        )

    def _rebuild_dock(self, force_refresh_icons: bool = False) -> None:
        ui = self.config["ui"]
        theme = self._theme()
        orientation = self._orientation()
        is_vert = orientation == "vertical"
        btn_size = int(ui.get("button_size", 46))
        icon_size = int(ui.get("icon_size", 32))
        if force_refresh_icons:
            self._web_icons.invalidate()

        while self._top_bar.row_layout.count():
            item = self._top_bar.row_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._grip:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._buttons.clear()
        self._icons.clear()

        self._top_bar.set_orientation(orientation)
        self._grip.set_theme(theme)
        self._grip.set_orientation(orientation, btn_size)
        self._top_bar.row_layout.addWidget(self._grip)

        btn_list: list[AgentIconButton] = []
        slot_map: dict[str, QWidget] = {}
        for agent in self.config.get("agents", []):
            if agent.get("enabled", True) is False:
                continue
            aid = agent.get("id", "")
            icon = load_agent_icon(
                agent, force_refresh=force_refresh_icons, theme=theme, resolve_web_icon=self._web_icons.resolve
            )
            self._icons[aid] = icon
            btn = AgentIconButton(
                agent=agent,
                icon=icon,
                button_size=btn_size,
                icon_size=icon_size,
                theme=theme,
                orientation=orientation,
                parent=self._top_bar,
            )
            btn.clicked.connect(self._on_agent_clicked)
            btn.double_clicked.connect(self._on_agent_double_clicked)
            btn.right_clicked.connect(self._on_agent_right_clicked)
            if is_vert:
                slot = QWidget(self._top_bar)
                slot.setAttribute(Qt.WA_TranslucentBackground, True)
                slot.setMouseTracking(True)
                slot.setFixedSize(btn.width(), btn_size)
                slot_layout = QVBoxLayout(slot)
                slot_layout.setContentsMargins(0, 0, 0, 0)
                slot_layout.setSpacing(0)
                slot_layout.setAlignment(Qt.AlignCenter)
                slot_layout.addWidget(btn)
                btn.show()
                slot.show()
                self._top_bar.row_layout.addWidget(slot)
                slot_map[aid] = slot
            else:
                btn.show()
                self._top_bar.row_layout.addWidget(btn)
            self._buttons[aid] = btn
            btn_list.append(btn)

        self._top_bar.set_icon_buttons(btn_list)
        self._top_bar.set_slot_widgets(slot_map)
        self._apply_layout_orientation()
        self._last_shelf_sig = None
        self._top_bar.row_layout.activate()
        self._top_bar.adjustSize()
        self._root_layout.activate()
        self.adjustSize()
        self.update()

    def _on_web_icon_ready(self, url: str) -> None:
        for aid, btn in self._buttons.items():
            if url not in (btn.agent.get("icon_source"), btn.agent.get("url")):
                continue
            icon = load_agent_icon(btn.agent, theme=self._theme(), resolve_web_icon=self._web_icons.resolve)
            self._icons[aid] = icon
            btn.set_icon(icon)
        self._last_shelf_sig = None
        self._update_orb_state(self.config["agents"], self._last_notif_map)

    def _rebuild_shelf(
        self,
        agents: list[dict[str, Any]],
        agent_windows: dict[str, list[dict[str, Any]]],
        foreground_hwnd: int,
        notif_map: dict[str, AgentNotificationSummary],
        show_list: bool,
    ) -> bool:
        theme = self._theme()
        orientation = self._orientation()
        is_vert = orientation == "vertical"
        btn_size = int(self.config["ui"].get("button_size", 46))
        card_width = int(self.config["ui"].get("window_chip_width", 138))
        max_cols = self._vertical_max_cols(card_width) if is_vert else 0

        sig_cards = []
        enabled_agents: list[dict[str, Any]] = []
        active_agents: list[dict[str, Any]] = []
        if show_list:
            for ag in agents:
                if ag.get("enabled", True) is False:
                    continue
                enabled_agents.append(ag)
                aid = ag.get("id", "")
                wins = agent_windows.get(aid, [])
                if not wins:
                    if is_vert:
                        sig_cards.append((aid, ()))
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

        new_sig = (
            theme,
            orientation,
            self._vertical_mirrored if is_vert else False,
            card_width,
            btn_size,
            max_cols,
            bool(show_list),
            tuple(sig_cards),
        )
        if new_sig == self._last_shelf_sig:
            return False
        self._last_shelf_sig = new_sig

        while self._shelf_layout.count():
            item = self._shelf_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        if is_vert:
            slot_heights: dict[str, int] = {}
            if show_list and active_agents:
                alpha = self._capsule_alpha()
                horiz_align = Qt.AlignRight if self._vertical_mirrored else Qt.AlignLeft
                for ag in enabled_agents:
                    aid = ag.get("id", "")
                    wins = agent_windows.get(aid, [])
                    if not wins:
                        placeholder = QWidget(self._shelf)
                        placeholder.setAttribute(Qt.WA_TranslucentBackground, True)
                        placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                        placeholder.setFixedSize(1, btn_size)
                        placeholder.show()
                        self._shelf_layout.addWidget(placeholder, 0, horiz_align)
                        continue
                    summary = notif_map.get(aid, AgentNotificationSummary())
                    strip = AgentHorizontalStrip(
                        agent=ag,
                        windows=wins,
                        foreground_hwnd=foreground_hwnd,
                        notif_summary=summary,
                        chip_width=card_width,
                        max_cols=max_cols,
                        theme=theme,
                        alpha=alpha,
                        parent=self._shelf,
                    )
                    strip.window_clicked.connect(self._on_window_chip_clicked)
                    strip.window_right_clicked.connect(self._on_window_chip_right_clicked)
                    row_h = max(btn_size, strip.sizeHint().height())
                    slot_heights[aid] = row_h
                    row_box = QWidget(self._shelf)
                    row_box.setAttribute(Qt.WA_TranslucentBackground, True)
                    row_box.setFixedHeight(row_h)
                    row_layout = QHBoxLayout(row_box)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(0)
                    row_layout.setAlignment(horiz_align | Qt.AlignVCenter)
                    row_layout.addWidget(strip)
                    strip.show()
                    row_box.show()
                    self._shelf_layout.addWidget(row_box, 0, horiz_align)

                self._top_bar.set_slot_heights(slot_heights, btn_size)
                self._shelf_layout.activate()
                self._shelf.adjustSize()
                if not self._collapsed:
                    self._shelf.show()
            else:
                self._top_bar.set_slot_heights({}, btn_size)
                self._shelf.hide()
            return True

        if active_agents:
            alpha = self._capsule_alpha()
            for ag in active_agents:
                aid = ag.get("id", "")
                wins = agent_windows.get(aid, [])
                icon = self._icons.get(aid) or self._default_app_icon
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
                card.show()
                self._shelf_layout.addWidget(card, 0, Qt.AlignTop)
            self._shelf_layout.activate()
            self._shelf.adjustSize()
            if not self._collapsed:
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

    def _save_position(self, anchor: QPoint | None = None) -> bool:
        pos = anchor if anchor is not None else self._anchor_for_current_pos()
        return self._persist_config(ui={"pos": [pos.x(), pos.y()]})

    def _update_orb_state(
        self,
        agents: list[dict[str, Any]],
        notif_map: dict[str, AgentNotificationSummary],
    ) -> None:
        """Keep the collapsed floating orb synced with the most recently notifying Agent."""
        enabled_by_id: dict[str, dict[str, Any]] = {
            a.get("id", ""): a
            for a in agents
            if a.get("enabled", True) is not False and a.get("id")
        }

        alerting = {
            aid: notif_map[aid]
            for aid in enabled_by_id
            if aid in notif_map and notif_map[aid].has_alert
        }

        running_count = sum(
            len(self._agent_windows.get(aid, [])) for aid in enabled_by_id
        )

        if alerting:
            latest_aid = max(alerting, key=lambda aid: alerting[aid].latest_event_order)
            latest_agent = enabled_by_id[latest_aid]
            latest_summary = alerting[latest_aid]
            icon = self._icons.get(latest_aid) or self._default_app_icon
            self._orb.update_state(
                icon=icon,
                running_count=running_count,
                notif_count=sum(summary.total_count for summary in alerting.values()),
                has_alert=True,
                alert_agent_id=latest_aid,
                alert_agent_name=latest_agent.get("name", latest_aid),
                alert_agent_count=len(alerting),
                alert_text=latest_summary.latest_text,
            )
        else:
            self._orb.update_state(
                icon=self._default_app_icon,
                running_count=running_count,
                notif_count=0,
                has_alert=False,
            )

    def _refresh_state(self) -> None:
        if self._closing:
            return
        ui = self.config["ui"]
        show_dot = bool(ui.get("show_running_dot", True))
        show_list = bool(ui.get("show_window_list", True))
        hide_enabled = bool(ui.get("hide_from_taskbar", True))
        agents = self.config.get("agents", [])

        self._agent_windows = get_agent_window_map(agents)
        fg_hwnd = get_foreground_hwnd()
        notif_map = self._notif_tracker.poll(agents, self._agent_windows, fg_hwnd)
        self._last_notif_map = notif_map

        for aid, btn in self._buttons.items():
            wins = self._agent_windows.get(aid, [])
            summary = notif_map.get(aid, AgentNotificationSummary())
            released = (not hide_enabled) or (btn.agent.get("hide_from_taskbar", True) is False)
            btn.update_state(
                window_count=len(wins),
                show_dot=show_dot,
                notif_count=summary.total_count,
                has_alert=summary.has_alert,
                notif_text=summary.latest_text,
                released_to_taskbar=released,
            )

        self._update_orb_state(agents, notif_map)

        shelf_changed = self._rebuild_shelf(
            agents=agents,
            agent_windows=self._agent_windows,
            foreground_hwnd=fg_hwnd,
            notif_map=notif_map,
            show_list=show_list,
        )
        if shelf_changed and not self._collapsed:
            self._root_layout.activate()
            self.adjustSize()
            if self._drag_offset is None:
                if self._is_vertical() and self._vertical_mirrored and self._collapsed_anchor is not None:
                    target_pos = self._expanded_pos_for_anchor(self._collapsed_anchor)
                else:
                    target_pos = self._clamp_expanded_position(self.pos())
                self.move(target_pos)
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
        focus_existing = bool(self.config["ui"].get("focus_existing", True))
        preferred_hwnds: list[int] | None = None
        if focus_existing:
            raw_preferred = self._notif_tracker.get_alert_hwnds()
            if isinstance(raw_preferred, (list, tuple)):
                preferred_hwnds = list(raw_preferred)

        ok, msg = launch_agent(
            agent,
            focus_existing=focus_existing,
            agents=self.config["agents"],
            preferred_hwnds=preferred_hwnds,
        )
        if not ok:
            QMessageBox.warning(self, "AI docker 启动失败", f"{agent.get('name', '')}: {msg}")
            return

        if msg.startswith("focused:"):
            focused_hwnd = get_last_focused_hwnd(aid or agent.get("name") or "")
            if focused_hwnd:
                self._notif_tracker.clear_window(focused_hwnd)
            self._notif_tracker.clear_agent_unattributed(aid)
            QTimer.singleShot(180, self._refresh_state)
        else:
            wins = self._agent_windows.get(aid, [])
            if len(wins) <= 1:
                self._notif_tracker.clear_agent(aid, [w["hwnd"] for w in wins])
            else:
                self._notif_tracker.clear_agent_unattributed(aid)
            QTimer.singleShot(350, self._refresh_state)

    def _on_agent_double_clicked(self, agent: dict[str, Any]) -> None:
        ok, msg = launch_agent(agent, focus_existing=False)
        if not ok:
            QMessageBox.warning(self, "AI docker 启动失败", f"{agent.get('name', '')}: {msg}")
        else:
            QTimer.singleShot(500, self._refresh_state)

    def _on_window_chip_clicked(self, hwnd: int, agent_id: str) -> None:
        self._notif_tracker.clear_window(hwnd)
        self._notif_tracker.clear_agent_unattributed(agent_id)
        focus_hwnd(hwnd)
        QTimer.singleShot(180, self._refresh_state)

    def _on_window_chip_close_requested(self, hwnd: int, agent_id: str) -> None:
        if close_hwnd(hwnd):
            self._notif_tracker.clear_window(hwnd)
            QTimer.singleShot(250, self._refresh_state)
        else:
            QMessageBox.warning(
                self,
                "AI docker 关闭窗口失败",
                "无法关闭该窗口（窗口可能已关闭，或目标进程权限更高）。",
            )
            QTimer.singleShot(150, self._refresh_state)

    def _on_agent_right_clicked(self, agent: dict[str, Any], global_pos: QPoint) -> None:
        self._show_context_menu(global_pos, agent=agent, target_hwnd=None)

    def _on_window_chip_right_clicked(
        self, agent: dict[str, Any], hwnd: int, global_pos: QPoint
    ) -> None:
        self._show_context_menu(global_pos, agent=agent, target_hwnd=hwnd)

    @staticmethod
    def _menu_text(text: str) -> str:
        return text.replace("&", "&&")

    def _build_agent_menu(
        self, agent: dict[str, Any], target_hwnd: int | None = None
    ) -> QMenu:
        """Context menu scoped strictly to one Agent (or one of its open project windows)."""
        menu = AppleGlassMenu(theme=self._theme(), alpha=self._capsule_alpha(), parent=self)
        aid = agent.get("id", "")
        name = agent.get("name", "")
        safe_name = self._menu_text(name)
        open_wins = self._agent_windows.get(aid, [])
        notif_summary = self._last_notif_map.get(aid, AgentNotificationSummary())

        if target_hwnd is not None:
            act_focus = menu.addAction("切换到该窗口")
            act_focus.triggered.connect(lambda: self._on_window_chip_clicked(target_hwnd, aid))
            if target_hwnd in notif_summary.by_hwnd:
                act_clear_win = menu.addAction("清除该窗口通知角标")
                act_clear_win.triggered.connect(
                    lambda: (self._notif_tracker.clear_window(target_hwnd, aid), self._refresh_state())
                )
            act_close = menu.addAction("关闭该窗口")
            act_close.setProperty("destructive", True)
            act_close.triggered.connect(
                lambda checked=False, h=target_hwnd, a=aid: self._on_window_chip_close_requested(h, a)
            )
            menu.addSeparator()
        elif open_wins:
            for w in open_wins[:8]:
                proj = w.get("project") or w.get("title") or name or "窗口"
                hwnd = w["hwnd"]
                act = menu.addAction(self._menu_text(f"切换到窗口：{proj}"))
                act.triggered.connect(
                    lambda checked=False, h=hwnd, a=aid: self._on_window_chip_clicked(h, a)
                )
            menu.addSeparator()

        new_inst = menu.addAction(f"新开实例：{safe_name}")
        new_inst.triggered.connect(lambda: launch_agent(agent, focus_existing=False))

        is_released = agent.get("hide_from_taskbar", True) is False
        global_hide = bool(self.config["ui"].get("hide_from_taskbar", True))
        if is_released:
            rel_act = menu.addAction(f"重新收进悬浮窗：{safe_name}")
            if not global_hide:
                rel_act.setText(f"重新收进悬浮窗：{safe_name}（全局隐藏已关）")
                rel_act.setEnabled(False)
            else:
                rel_act.triggered.connect(
                    lambda checked=False, a=aid: self._set_agent_released(a, False)
                )
        else:
            rel_act = menu.addAction(f"释放到系统任务栏：{safe_name}")
            rel_act.triggered.connect(
                lambda checked=False, a=aid: self._set_agent_released(a, True)
            )

        if target_hwnd is None:
            if notif_summary.has_alert:
                clear_act = menu.addAction("清除通知角标")
                clear_act.triggered.connect(
                    lambda checked=False, a=aid, hs=[w["hwnd"] for w in open_wins]: (
                        self._notif_tracker.clear_agent(a, hs),
                        self._refresh_state(),
                    )
                )

            visible_ids = [
                a.get("id", "")
                for a in self.config.get("agents", [])
                if a.get("enabled", True) is not False
            ]
            visible_count = len(visible_ids)
            cur_vis_idx = visible_ids.index(aid) if aid in visible_ids else -1

            if visible_count > 1 and cur_vis_idx >= 0:
                is_vert = self._is_vertical()
                move_menu = menu.addMenu("调整位置")
                act_prev = move_menu.addAction("向上移一位" if is_vert else "向左移一位")
                act_prev.setEnabled(cur_vis_idx > 0)
                act_prev.triggered.connect(
                    lambda checked=False, a=aid: self._shift_agent(a, -1)
                )
                act_next = move_menu.addAction("向下移一位" if is_vert else "向右移一位")
                act_next.setEnabled(cur_vis_idx < visible_count - 1)
                act_next.triggered.connect(
                    lambda checked=False, a=aid: self._shift_agent(a, 1)
                )
                move_menu.addSeparator()
                act_first = move_menu.addAction("移到最顶" if is_vert else "移到最左")
                act_first.setEnabled(cur_vis_idx > 0)
                act_first.triggered.connect(
                    lambda checked=False, a=aid: self._move_agent_to_visible_index(a, 0)
                )
                act_last = move_menu.addAction("移到最底" if is_vert else "移到最右")
                act_last.setEnabled(cur_vis_idx < visible_count - 1)
                act_last.triggered.connect(
                    lambda checked=False, a=aid, last=visible_count - 1: self._move_agent_to_visible_index(a, last)
                )

            menu.addSeparator()
            hide_icon_act = menu.addAction(f"隐藏该图标：{safe_name}")
            if visible_count <= 1:
                hide_icon_act.setEnabled(False)
                hide_icon_act.setText("隐藏该图标（至少保留 1 个）")
            else:
                hide_icon_act.triggered.connect(
                    lambda checked=False, a=aid: self._set_agent_visible(a, False)
                )

        return menu

    def _build_global_menu(self) -> QMenu:
        """Compact grouped context menu for the drag grip / empty capsule / collapsed orb."""
        menu = AppleGlassMenu(theme=self._theme(), alpha=self._capsule_alpha(), parent=self)

        # 1. Quick actions
        collapse_now_act = menu.addAction(
            "立即展开悬浮窗" if self._collapsed else "立即收缩为悬浮图标"
        )
        collapse_now_act.triggered.connect(self._toggle_collapsed_now)

        is_dark = self._theme() == "dark"
        theme_act = menu.addAction("切换外观：macOS 浅色毛玻璃" if is_dark else "切换外观：macOS 深色磨砂")
        theme_act.triggered.connect(self._toggle_theme)

        has_any_alert = any(s.has_alert for s in self._last_notif_map.values())
        clear_all_action = menu.addAction("清除全部通知角标")
        clear_all_action.setEnabled(has_any_alert)
        clear_all_action.triggered.connect(self._clear_all_notifications)

        menu.addSeparator()

        # 2. Grouped submenus
        agent_menu = menu.addMenu("Agent 管理")
        discover_act = agent_menu.addAction("扫描本机新安装的 Agent...")
        discover_act.triggered.connect(self._open_discover_dialog)

        add_web_act = agent_menu.addAction("添加网页 Agent (URL)...")
        add_web_act.triggered.connect(lambda: self._open_add_dialog(initial_type="web"))

        add_act = agent_menu.addAction("添加桌面 / CLI Agent...（或拖入 .exe/.lnk/.url）")
        add_act.triggered.connect(lambda: self._open_add_dialog(initial_type="exe"))

        agent_menu.addSeparator()

        all_agents = self.config.get("agents", [])
        hidden_count = sum(1 for a in all_agents if a.get("enabled", True) is False)
        vis_title = (
            f"显示 / 隐藏图标（已隐藏 {hidden_count} 个）"
            if hidden_count > 0
            else "显示 / 隐藏图标"
        )
        vis_menu = agent_menu.addMenu(vis_title)
        visible_total = len(all_agents) - hidden_count
        for item_agent in all_agents:
            item_id = item_agent.get("id", "")
            item_name = item_agent.get("name", item_id)
            is_shown = item_agent.get("enabled", True) is not False
            act = vis_menu.addAction(self._menu_text(item_name))
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

        enabled_agents = [a for a in all_agents if a.get("enabled", True) is not False]
        released_count = sum(1 for a in enabled_agents if a.get("hide_from_taskbar", True) is False)
        rel_title = (
            f"释放指定 Agent 到任务栏（已释放 {released_count} 个）"
            if released_count > 0
            else "释放指定 Agent 到任务栏"
        )
        rel_menu = agent_menu.addMenu(rel_title)
        for item_agent in enabled_agents:
            item_id = item_agent.get("id", "")
            item_name = item_agent.get("name", item_id)
            is_rel = item_agent.get("hide_from_taskbar", True) is False
            act = rel_menu.addAction(self._menu_text(item_name))
            act.setCheckable(True)
            act.setChecked(is_rel)
            act.triggered.connect(
                lambda checked=False, a=item_id: self._set_agent_released(a, bool(checked))
            )
        if released_count > 0:
            rel_menu.addSeparator()
            reclaim_all_act = rel_menu.addAction("全部重新收进悬浮窗")
            reclaim_all_act.triggered.connect(self._reclaim_all_released)

        behavior_menu = menu.addMenu("显示与行为")
        is_vert = self._is_vertical()
        layout_menu = behavior_menu.addMenu("布局方向")
        horiz_act = layout_menu.addAction("横向布局（顶栏图标 + 下方卡片）")
        horiz_act.setCheckable(True)
        horiz_act.setChecked(not is_vert)
        horiz_act.triggered.connect(lambda checked=False: self._set_orientation("horizontal"))
        vert_act = layout_menu.addAction("纵向布局（侧边图标 + 同行窗口）")
        vert_act.setCheckable(True)
        vert_act.setChecked(is_vert)
        vert_act.triggered.connect(lambda checked=False: self._set_orientation("vertical"))

        auto_collapse_on = self._auto_collapse_enabled()
        auto_collapse_act = behavior_menu.addAction("空闲自动收缩为悬浮图标")
        auto_collapse_act.setCheckable(True)
        auto_collapse_act.setChecked(auto_collapse_on)
        auto_collapse_act.triggered.connect(self._toggle_auto_collapse)

        cur_sec = self._auto_collapse_seconds()
        delay_menu = behavior_menu.addMenu(f"自动收缩等待时间（{cur_sec} 秒）")
        for sec in (5, 8, 15, 30):
            d_act = delay_menu.addAction(f"{sec} 秒")
            d_act.setCheckable(True)
            d_act.setChecked(cur_sec == sec)
            d_act.triggered.connect(
                lambda checked=False, s=sec: self._set_auto_collapse_seconds(s)
            )

        behavior_menu.addSeparator()

        show_list_action = behavior_menu.addAction("下方展开已打开项目窗口")
        show_list_action.setCheckable(True)
        show_list_action.setChecked(bool(self.config["ui"].get("show_window_list", True)))
        show_list_action.triggered.connect(self._toggle_show_window_list)

        hide_action = behavior_menu.addAction("自动隐藏已收纳 Agent 的任务栏图标")
        hide_action.setCheckable(True)
        hide_action.setChecked(bool(self.config["ui"].get("hide_from_taskbar", True)))
        hide_action.triggered.connect(self._toggle_hide_from_taskbar)

        focus_action = behavior_menu.addAction("点击图标时优先唤醒已开窗口")
        focus_action.setCheckable(True)
        focus_action.setChecked(bool(self.config["ui"].get("focus_existing", True)))
        focus_action.triggered.connect(self._toggle_focus_existing)

        sys_title = (
            f"配置与系统（有 {self._available_update.behind_count} 个新更新）"
            if self._available_update is not None
            else "配置与系统"
        )
        system_menu = menu.addMenu(sys_title)
        reload_action = system_menu.addAction("重新加载配置与图标")
        reload_action.triggered.connect(self._reload_all)

        edit_action = system_menu.addAction("打开配置文件 (agents.json)")
        edit_action.triggered.connect(lambda: os.startfile(str(CONFIG_PATH)))

        if self._updater.in_flight:
            update_action = system_menu.addAction("正在检查更新...")
            update_action.setEnabled(False)
        else:
            update_label = (
                f"发现新更新（{self._available_update.behind_count} 条）..."
                if self._available_update is not None
                else "检查更新..."
            )
            update_action = system_menu.addAction(update_label)
            update_action.triggered.connect(self._trigger_check_updates)

        system_menu.addSeparator()

        autostart_action = system_menu.addAction("开机自动启动")
        autostart_action.setCheckable(True)
        autostart_action.setChecked(is_autostart_enabled())
        autostart_action.triggered.connect(self._toggle_autostart)

        menu.addSeparator()

        # 3. Exit
        quit_action = menu.addAction("退出 AI docker (还原任务栏图标)")
        quit_action.setProperty("destructive", True)
        quit_action.triggered.connect(self._quit_app)

        return menu

    def _show_context_menu(
        self,
        global_pos: QPoint,
        agent: dict[str, Any] | None = None,
        target_hwnd: int | None = None,
    ) -> None:
        menu = (
            self._build_agent_menu(agent, target_hwnd=target_hwnd)
            if agent is not None
            else self._build_global_menu()
        )
        self._menu_open = True
        self._cancel_auto_collapse()
        try:
            menu.exec(global_pos)
        finally:
            self._menu_open = False
            menu.deleteLater()
            self._schedule_auto_collapse()

    def _open_discover_dialog(self) -> None:
        self._dialog_open = True
        self._cancel_auto_collapse()
        dlg: DiscoverAgentsDialog | None = None
        try:
            dlg = DiscoverAgentsDialog(self.config.get("agents", []), theme=self._theme(), parent=self)
            if dlg.exec() and dlg.selected_agents:
                if not self._persist_config(agents=[*self.config["agents"], *dlg.selected_agents]):
                    return
                if self._collapsed:
                    self._expand_dock()
                anchor = self._anchor_for_current_pos()
                self._rebuild_dock(force_refresh_icons=False)
                self._refresh_state()
                self._reposition_after_rebuild(anchor)
        finally:
            if dlg is not None:
                dlg.deleteLater()
            self._dialog_open = False
            self._schedule_auto_collapse()

    def _open_add_dialog(self, initial_type: str = "exe", initial_url: str = "") -> None:
        self._dialog_open = True
        self._cancel_auto_collapse()
        dlg: AddAgentDialog | None = None
        try:
            dlg = AddAgentDialog(
                self.config.get("agents", []),
                theme=self._theme(),
                parent=self,
                initial_type=initial_type,
                initial_url=initial_url,
            )
            if dlg.exec() and dlg.created_agent:
                if not self._persist_config(agents=[*self.config["agents"], dlg.created_agent]):
                    return
                if self._collapsed:
                    self._expand_dock()
                anchor = self._anchor_for_current_pos()
                self._rebuild_dock(force_refresh_icons=False)
                self._refresh_state()
                self._reposition_after_rebuild(anchor)
        finally:
            if dlg is not None:
                dlg.deleteLater()
            self._dialog_open = False
            self._schedule_auto_collapse()

    def _toggle_collapsed_now(self) -> None:
        if self._collapsed:
            self._expand_dock()
        else:
            self._collapse_dock(force=True)

    def _toggle_auto_collapse(self, checked: bool) -> None:
        if not self._persist_config(ui={"auto_collapse": bool(checked)}):
            return
        if not checked:
            self._cancel_auto_collapse()
            self._expand_dock()
        else:
            self._schedule_auto_collapse()

    def _set_auto_collapse_seconds(self, seconds: int) -> None:
        if self._persist_config(ui={"auto_collapse_seconds": max(2, int(seconds)), "auto_collapse": True}):
            self._schedule_auto_collapse()

    def _set_orientation(self, orientation: str) -> None:
        target = "vertical" if orientation == "vertical" else "horizontal"
        if target == self._orientation():
            return
        anchor = self._anchor_for_current_pos()
        if not self._persist_config(ui={"orientation": target}):
            return
        if not self._collapsed:
            self._collapsed_anchor = anchor
        self._rebuild_dock(force_refresh_icons=False)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)

    def _toggle_theme(self) -> None:
        theme = "light" if self._theme() == "dark" else "dark"
        anchor = self._anchor_for_current_pos()
        if not self._persist_config(ui={"theme": theme}):
            return
        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=False)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)

    def _move_agent_to_visible_index(self, agent_id: str, new_visible_idx: int) -> None:
        agents = list(self.config["agents"])
        visible_slots = [i for i, a in enumerate(agents) if a.get("enabled", True) is not False]
        visible_items = [agents[i] for i in visible_slots]

        cur_vis_idx = next(
            (i for i, a in enumerate(visible_items) if a.get("id") == agent_id), -1
        )
        if cur_vis_idx < 0:
            return
        clamped = max(0, min(len(visible_items) - 1, int(new_visible_idx)))
        if clamped == cur_vis_idx:
            return

        moved = visible_items.pop(cur_vis_idx)
        visible_items.insert(clamped, moved)
        for slot_idx, item in zip(visible_slots, visible_items):
            agents[slot_idx] = item

        if not self._persist_config(agents=agents):
            return
        anchor = self._anchor_for_current_pos()
        self._rebuild_dock(force_refresh_icons=False)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)

    def _shift_agent(self, agent_id: str, delta: int) -> None:
        agents = self.config.get("agents", [])
        visible_ids = [a.get("id", "") for a in agents if a.get("enabled", True) is not False]
        if agent_id not in visible_ids:
            return
        cur_idx = visible_ids.index(agent_id)
        self._move_agent_to_visible_index(agent_id, cur_idx + delta)

    def _set_agent_visible(self, agent_id: str, visible: bool) -> None:
        agents = list(self.config["agents"])
        if not visible:
            visible_count = sum(1 for a in agents if a.get("enabled", True) is not False)
            if visible_count <= 1:
                return
        changed = False
        for index, a in enumerate(agents):
            if a.get("id") == agent_id:
                if (a.get("enabled", True) is not False) != visible:
                    agents[index] = {**a, "enabled": visible}
                    changed = True
                break
        if not changed or not self._persist_config(agents=agents):
            return
        anchor = self._anchor_for_current_pos()
        self._rebuild_dock(force_refresh_icons=False)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)

    def _show_all_agents(self) -> None:
        previous = self.config["agents"]
        agents = [{**agent, "enabled": True} if agent.get("enabled") is False else agent for agent in previous]
        if agents == previous or not self._persist_config(agents=agents):
            return
        anchor = self._anchor_for_current_pos()
        self._rebuild_dock(force_refresh_icons=False)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)

    def _toggle_show_window_list(self, checked: bool) -> None:
        if self._persist_config(ui={"show_window_list": bool(checked)}):
            self._refresh_state()

    def _toggle_hide_from_taskbar(self, checked: bool) -> None:
        if not self._persist_config(ui={"hide_from_taskbar": bool(checked)}):
            return
        if not checked:
            self._taskbar_hider.restore_all()
        self._refresh_state()

    def _set_agent_released(self, agent_id: str, released: bool) -> None:
        """Release a specific Agent back to the native Windows taskbar, or reclaim it into the dock."""
        agents = list(self.config["agents"])
        for index, agent in enumerate(agents):
            if agent.get("id") != agent_id:
                continue
            was_released = agent.get("hide_from_taskbar", True) is False
            if was_released != released:
                agents[index] = {**agent, "hide_from_taskbar": not released}
                if self._persist_config(agents=agents):
                    self._refresh_state()
            return

    def _reclaim_all_released(self) -> None:
        previous = self.config["agents"]
        agents = [
            {**agent, "hide_from_taskbar": True} if agent.get("hide_from_taskbar") is False else agent
            for agent in previous
        ]
        if agents != previous and self._persist_config(agents=agents):
            self._refresh_state()

    def _toggle_focus_existing(self, checked: bool) -> None:
        self._persist_config(ui={"focus_existing": bool(checked)})

    def _clear_all_notifications(self) -> None:
        for agent in self.config.get("agents", []):
            aid = agent.get("id", "")
            wins = self._agent_windows.get(aid, [])
            self._notif_tracker.clear_agent(aid, [w["hwnd"] for w in wins])
        self._refresh_state()

    def _reload_all(
        self,
        _checked: bool = False,
        *,
        force_refresh_icons: bool = True,
        silent: bool = False,
    ) -> None:
        try:
            new_cfg = load_config()
        except ConfigLoadError as exc:
            if not silent:
                QMessageBox.warning(
                    self,
                    "AI docker 配置加载失败",
                    f"agents.json 解析失败，已保留当前配置：\n{exc}",
                )
            return
        anchor = self._anchor_for_current_pos()
        self.config = new_cfg
        if not self._collapsed:
            self._collapsed_anchor = anchor
        self._apply_styles()
        self._rebuild_dock(force_refresh_icons=force_refresh_icons)
        self._refresh_state()
        self._reposition_after_rebuild(anchor)
        if not self._auto_collapse_enabled():
            self._cancel_auto_collapse()
            self._expand_dock()
        else:
            self._schedule_auto_collapse()

    def _trigger_check_updates(self) -> None:
        # Always run a fresh check so dirty-tree or manual git pull states never stay stale
        self._updater.check_async(silent=False)

    def _on_update_check_finished(self, result: UpdateCheckResult, silent: bool) -> None:
        if result.status == "update_available":
            self._available_update = result
            if not silent:
                self._open_update_dialog(result)
            return

        if result.status == "up_to_date":
            self._available_update = None

        if silent:
            return

        self._dialog_open = True
        self._cancel_auto_collapse()
        try:
            if result.status == "up_to_date":
                QMessageBox.information(self, "AI docker 检查更新", result.message)
            else:
                QMessageBox.warning(self, "AI docker 检查更新", result.message)
        finally:
            self._dialog_open = False
            self._schedule_auto_collapse()

    def _open_update_dialog(self, result: UpdateCheckResult) -> None:
        self._dialog_open = True
        self._cancel_auto_collapse()
        dlg: UpdateDialog | None = None
        should_quit = False
        try:
            dlg = UpdateDialog(result, theme=self._theme(), parent=self)
            if dlg.exec():
                ok, detail = launch_updater_script(wait_pid=os.getpid(), gui_mode=True, update=result)
                if ok:
                    should_quit = True
                else:
                    QMessageBox.warning(self, "AI docker 更新失败", detail)
        finally:
            if dlg is not None:
                dlg.deleteLater()
            self._dialog_open = False
            if not should_quit:
                self._schedule_auto_collapse()

        if should_quit:
            self._quit_app()

    def _toggle_autostart(self, checked: bool) -> None:
        try:
            set_autostart(bool(checked))
        except Exception as exc:
            QMessageBox.warning(self, "开机自启设置失败", str(exc))

    def _cleanup_resources(self) -> None:
        self._closing = True
        atexit.unregister(self._cleanup_resources)
        self._web_icons.invalidate()
        self._updater.invalidate()
        if hasattr(self, "_startup_update_timer"):
            self._startup_update_timer.stop()
        if hasattr(self, "_collapse_timer"):
            self._collapse_timer.stop()
        if hasattr(self, "_status_timer"):
            self._status_timer.stop()
        if self._cached_hwnd:
            user32.RemovePropW(self._cached_hwnd, _INSTALL_PROPERTY)
        if self._shellhook_registered and self._cached_hwnd:
            try:
                user32.DeregisterShellHookWindow(wintypes.HWND(self._cached_hwnd))
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
        app = QApplication.instance()
        if app is not None and not app.quitOnLastWindowClosed():
            app.quit()

    # --- Window dragging (left grip, empty capsule area & collapsed orb) ---
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._cancel_auto_collapse()
            self._drag_origin = self.pos()
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
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
            origin = self._drag_origin
            self._drag_origin = None
            if origin is not None and self.pos() == origin:
                self._schedule_auto_collapse()
                event.accept()
                return
            prev_anchor = self._collapsed_anchor
            prev_mirrored = self._vertical_mirrored
            anchor = self._anchor_for_current_pos()
            flipped = False
            if self._is_vertical() and not self._collapsed:
                should_mirror = self._vertical_mirror_right(anchor)
                if should_mirror != self._vertical_mirrored:
                    flipped = True
                    self._collapsed_anchor = anchor
                    self._apply_layout_orientation(mirror_right=should_mirror)
                    self._last_shelf_sig = None
                    self._refresh_state()
                    self.adjustSize()
                    self.move(self._expanded_pos_for_anchor(anchor))
                    anchor = self._anchor_for_current_pos()
            if self._save_position(anchor):
                self._collapsed_anchor = anchor
            else:
                self._collapsed_anchor = prev_anchor
                if flipped:
                    self._apply_layout_orientation(mirror_right=prev_mirrored)
                    self._last_shelf_sig = None
                    self._refresh_state()
                    self._root_layout.activate()
                    self.adjustSize()
                if origin is not None:
                    self.move(origin)
            self._schedule_auto_collapse()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._show_context_menu(event.globalPos())
        event.accept()
