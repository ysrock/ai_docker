from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .discovery import build_agent_from_file, discover_new_agents, slugify_id
from .icons import load_agent_icon


def dialog_stylesheet(theme: str = "dark") -> str:
    if theme == "light":
        return """
            QDialog {
                background-color: #f5f5f7;
                color: #1d1d1f;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
            }
            QLabel {
                color: #1d1d1f;
            }
            QLabel#subLabel {
                color: #6e6e73;
                font-size: 11px;
            }
            QLineEdit, QComboBox, QListWidget {
                background-color: #ffffff;
                color: #1d1d1f;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                padding: 6px 9px;
                selection-background-color: #007aff;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1.5px solid #007aff;
            }
            QPushButton {
                background-color: #e8e8ed;
                color: #1d1d1f;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #dcdce2;
            }
            QPushButton#primaryBtn {
                background-color: #007aff;
                color: #ffffff;
            }
            QPushButton#primaryBtn:hover {
                background-color: #0066d6;
            }
            QListWidget::item {
                padding: 6px;
                border-radius: 7px;
            }
            QListWidget::item:hover {
                background-color: rgba(0, 122, 255, 22);
            }
        """

    return """
        QDialog {
            background-color: #1c1e26;
            color: #f5f5f7;
            font-family: "Microsoft YaHei UI", "Segoe UI";
            font-size: 12px;
        }
        QLabel {
            color: #f5f5f7;
        }
        QLabel#subLabel {
            color: #98989d;
            font-size: 11px;
        }
        QLineEdit, QComboBox, QListWidget {
            background-color: #262934;
            color: #f5f5f7;
            border: 1px solid rgba(255, 255, 255, 28);
            border-radius: 8px;
            padding: 6px 9px;
            selection-background-color: #0a84ff;
            selection-color: #ffffff;
        }
        QLineEdit:focus, QComboBox:focus {
            border: 1.5px solid #0a84ff;
        }
        QComboBox QAbstractItemView {
            background-color: #262934;
            color: #f5f5f7;
            selection-background-color: #0a84ff;
        }
        QPushButton {
            background-color: rgba(255, 255, 255, 22);
            color: #f5f5f7;
            border: none;
            border-radius: 8px;
            padding: 6px 14px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 34);
        }
        QPushButton#primaryBtn {
            background-color: #0a84ff;
            color: #ffffff;
        }
        QPushButton#primaryBtn:hover {
            background-color: #2191ff;
        }
        QListWidget::item {
            padding: 6px;
            border-radius: 7px;
        }
        QListWidget::item:hover {
            background-color: rgba(10, 132, 255, 36);
        }
    """


class AddAgentDialog(QDialog):
    """Apple-styled dialog to manually add a Desktop (.exe/.lnk), Web (URL), or CLI Agent."""

    def __init__(
        self,
        existing_agents: list[dict[str, Any]],
        theme: str = "dark",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._existing = existing_agents
        self.created_agent: dict[str, Any] | None = None

        self.setWindowTitle("添加新 Agent")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(430)
        self.setStyleSheet(dialog_stylesheet(theme))

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title_lbl = QLabel("添加新 Agent 到悬浮窗")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700;")
        sub_lbl = QLabel("支持本地桌面程序、网页版或终端命令行；也可直接将 .exe / .lnk 拖到悬浮窗上。")
        sub_lbl.setObjectName("subLabel")
        sub_lbl.setWordWrap(True)
        root.addWidget(title_lbl)
        root.addWidget(sub_lbl)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("类型"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["桌面程序 (.exe / .lnk)", "网页应用 (URL)", "终端 CLI 命令"])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 1)
        root.addLayout(type_row)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：Windsurf / DeepSeek / Aider")
        name_form = QFormLayout()
        name_form.setContentsMargins(0, 0, 0, 0)
        name_form.addRow("显示名称", self._name_edit)
        root.addLayout(name_form)

        self._stack = QStackedWidget()

        # Page 0: Desktop exe / lnk
        p_exe = QWidget()
        f_exe = QFormLayout(p_exe)
        f_exe.setContentsMargins(0, 0, 0, 0)
        exe_row = QHBoxLayout()
        self._exe_edit = QLineEdit()
        self._exe_edit.setPlaceholderText("选择 .exe 或 .lnk 文件路径")
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_exe)
        exe_row.addWidget(self._exe_edit, 1)
        exe_row.addWidget(browse_btn)
        f_exe.addRow("程序路径", exe_row)
        self._stack.addWidget(p_exe)

        # Page 1: Web URL
        p_web = QWidget()
        f_web = QFormLayout(p_web)
        f_web.setContentsMargins(0, 0, 0, 0)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://chat.deepseek.com")
        self._app_mode_chk = QCheckBox("以 Chrome 独立应用窗口打开（推荐）")
        self._app_mode_chk.setChecked(True)
        f_web.addRow("网页地址", self._url_edit)
        f_web.addRow("", self._app_mode_chk)
        self._stack.addWidget(p_web)

        # Page 2: CLI command
        p_cli = QWidget()
        f_cli = QFormLayout(p_cli)
        f_cli.setContentsMargins(0, 0, 0, 0)
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("例如：aider / codex / claude")
        self._cwd_edit = QLineEdit(str(Path.home()))
        self._cli_icon_edit = QLineEdit()
        self._cli_icon_edit.setPlaceholderText("可选：官网 URL（如 https://aider.chat）或本地图标路径")
        f_cli.addRow("启动命令", self._cmd_edit)
        f_cli.addRow("工作目录", self._cwd_edit)
        f_cli.addRow("图标来源", self._cli_icon_edit)
        self._stack.addWidget(p_cli)

        root.addWidget(self._stack)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("添加到悬浮窗")
        ok_btn.setObjectName("primaryBtn")
        ok_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def _on_type_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择程序或快捷方式",
            str(Path.home()),
            "应用程序 (*.exe *.lnk);;所有文件 (*.*)",
        )
        if path:
            self._exe_edit.setText(path)
            if not self._name_edit.text().strip():
                self._name_edit.setText(Path(path).stem)

    def _on_submit(self) -> None:
        idx = self._type_combo.currentIndex()
        existing_ids = {a.get("id", "") for a in self._existing}
        custom_name = self._name_edit.text().strip()

        if idx == 0:
            raw_path = self._exe_edit.text().strip().strip('"')
            agent = build_agent_from_file(raw_path, self._existing)
            if not agent:
                QMessageBox.warning(self, "路径无效", "请选择真实存在的 .exe 或 .lnk 文件。")
                return
            if custom_name:
                agent["name"] = custom_name
                agent["id"] = slugify_id(custom_name, existing_ids)
            self.created_agent = agent
            self.accept()
            return

        if idx == 1:
            url = self._url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "地址为空", "请输入网页地址。")
                return
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            host = urlparse(url).netloc or "web-agent"
            name = custom_name or host.split(".")[0].capitalize()
            self.created_agent = {
                "id": slugify_id(name, existing_ids),
                "name": name,
                "type": "web",
                "url": url,
                "app_mode": self._app_mode_chk.isChecked(),
                "match_process": "chrome.exe",
                "match_title": name,
                "notification_handlers": ["Chrome"],
                "notification_keywords": [name.lower()],
                "enabled": True,
            }
            self.accept()
            return

        if idx == 2:
            cmd = self._cmd_edit.text().strip()
            if not cmd:
                QMessageBox.warning(self, "命令为空", "请输入 CLI 启动命令。")
                return
            name = custom_name or cmd.split()[0]
            cwd = self._cwd_edit.text().strip() or str(Path.home())
            entry: dict[str, Any] = {
                "id": slugify_id(f"{name}-cli", existing_ids),
                "name": name,
                "type": "cli",
                "command": cmd,
                "terminal_title": name,
                "cwd": cwd,
                "cli_badge": True,
                "match_process": "WindowsTerminal.exe",
                "match_title": name,
                "notification_handlers": ["Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"],
                "notification_keywords": [name.lower()],
                "enabled": True,
            }
            icon_src = self._cli_icon_edit.text().strip()
            if icon_src:
                entry["icon_source"] = icon_src
            self.created_agent = entry
            self.accept()


class DiscoverAgentsDialog(QDialog):
    """Apple-styled dialog showing newly discovered local AI Agents with 1-click import."""

    def __init__(
        self,
        existing_agents: list[dict[str, Any]],
        theme: str = "dark",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_agents: list[dict[str, Any]] = []
        self._candidates = discover_new_agents(existing_agents)

        self.setWindowTitle("扫描本机新安装的 Agent")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(460, 340)
        self.setStyleSheet(dialog_stylesheet(theme))

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        header = QLabel(
            f"发现 {len(self._candidates)} 个尚未收录的本机 Agent"
            if self._candidates
            else "未发现新的本机 AI Agent"
        )
        header.setStyleSheet("font-size: 15px; font-weight: 700;")
        sub = QLabel(
            "勾选需要加入悬浮窗的项目（支持桌面应用、MSIX 商店应用和命令行 CLI）："
            if self._candidates
            else "你本机已扫描到的常见 AI Agent 都已在悬浮窗中；如果安装了冷门工具，可直接将其 .exe/.lnk 拖到悬浮窗或点击“添加新 Agent”。"
        )
        sub.setObjectName("subLabel")
        sub.setWordWrap(True)
        root.addWidget(header)
        root.addWidget(sub)

        self._list = QListWidget()
        self._list.setIconSize(QSize(26, 26))
        type_map = {"exe": "桌面应用", "msix": "商店应用", "cli": "终端 CLI", "web": "网页应用"}
        for cand in self._candidates:
            detail = cand.get("path") or cand.get("aumid") or cand.get("command") or ""
            tlabel = type_map.get(cand.get("type", "exe"), "应用")
            item = QListWidgetItem(f"{cand['name']}  [{tlabel}]\n{detail}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setIcon(load_agent_icon(cand, force_refresh=False, theme=theme))
            item.setData(Qt.UserRole, cand)
            self._list.addItem(item)
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        if self._candidates:
            add_btn = QPushButton("加入所选 Agent")
            add_btn.setObjectName("primaryBtn")
            add_btn.clicked.connect(self._on_confirm)
            btn_row.addWidget(add_btn)
        root.addLayout(btn_row)

    def _on_confirm(self) -> None:
        chosen: list[dict[str, Any]] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                chosen.append(item.data(Qt.UserRole))
        self.selected_agents = chosen
        self.accept()
