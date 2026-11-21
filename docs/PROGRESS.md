# AI docker 工程里程碑记录

## v0.1.0 - 初始公开版本（2026-11-21）

### 里程碑 1：基础架构与真实图标提取
- 确定采用 Python + PySide6 + pywin32 技术栈，通过 `uv` 管理独立虚拟环境。
- 实现 `ai_docker/config.py`、`ai_docker/icons.py`、`ai_docker/launcher.py`、`ai_docker/dock_window.py` 核心分层。
- 解决三类应用真实高清图标提取：
  - Win32 `.exe`/`.lnk` 通过 `PrivateExtractIconsW` 提取高分 RGBA 图标并兼容 legacy AND mask；
  - MSIX 应用通过 Windows AppModel API 动态定位当前版本安装目录读取高分 PNG，不依赖硬编码版本号路径；
  - 网页应用拉取站点图标并本地缓存，CLI 工具叠加 `>_` 终端角标并统一做圆角裁剪。
- 修复 Windows 前台锁下 `SetForegroundWindow` 抛错导致误判启动新实例的问题，加入 `HWND_TOPMOST` / `HWND_NOTOPMOST` 置顶兜底。

### 里程碑 2：原生任务栏自动隐藏与多窗口切换
- 新增 `ai_docker/taskbar.py`：基于 `pywin32` 原生 `ITaskbarList` COM 接口实现运行中 Agent 窗口的自动隐藏、定时防回弹、禁用/切离自动还原、以及 `Shell_TrayWnd` 变更自动重连。
- 在退出路径加入 `aboutToQuit`、`closeEvent`、`atexit` 三重还原保障。
- 升级 `ai_docker/launcher.py` 支持单 Agent 多窗口匹配与连续点击轮流切换。

### 里程碑 3：按项目展开窗口卡片与三路通知角标
- 新增 `ai_docker/notifications.py`：聚合 Windows 推送通知库 `wpndatabase.db` 只读水位线增量监听、`RegisterShellHookWindow` (`HSHELL_FLASH`) 闪烁钩子与标题未读计数，实现通知按项目名精准归属到具体窗口并在切焦后自动消标。
- 升级 `extract_project_name()`：从常见 IDE、桌面客户端与终端窗口标题中提取当前工作项目名，过滤零宽字符、纯省略号及非路径斜杠片段。
- 顶部图标右键新增一键隐藏与“显示 / 隐藏图标”子菜单，带至少保留 1 个可见图标的防误藏保护。

### 里程碑 4：macOS 双层毛玻璃视觉重设计与原生无黑框启动器
- 重构界面为“第一行严格等距玻璃胶囊 + 第二行独立磨砂项目卡片”双层 HUD，消除列宽不均与双层套娃边框问题。
- 实现 60fps 余弦波浪放大动效（静止自动停表）与 macOS 深色磨砂 / 浅色毛玻璃双主题切换，并为 MSIX 图标接入深浅主题反色自适应（`altform-unplated` / `altform-lightunplated`）。
- 新增 `launcher_src/AiDockerLauncher.cs` 与 `build_launcher.ps1`，使用 Windows 内置 `csc.exe` 编译约 40 KB 的原生无黑框 `AI docker.exe`（内嵌多分辨率 `assets/ai_docker.ico`）并注册开始菜单快捷方式。

### 里程碑 5：自动探测、拖放/弹窗添加与开源发布整理
- 新增 `ai_docker/discovery.py` 与 `ai_docker/dialogs.py`：支持一键增量探测本机桌面端/MSIX/CLI 工具（过滤安装器与修复程序杂项）、直接拖入 `.exe`/`.lnk` 添加、以及图形化弹窗手动添加。
- 升级 `config.py` 支持首次无 `agents.json` 启动时自动探测本机环境生成配置；将个人配置与本地编译产物加入 `.gitignore`。
- 完成公开仓库文档整理、隐私审计与 MIT 许可补充。

### 里程碑 6：图标拖拽换位与右键快捷调序
- 升级 `ai_docker/dock_window.py`：
  - 顶部图标改为支持左键直接左右拖拽换位（拖动超过 8px 进入换位模式，图标微透明虚线框提示，目标空隙绘制苹果蓝竖线落点指示器），左侧点阵手柄专注拖动整个悬浮窗并增加悬停微亮提示。
  - 顶部图标右键菜单新增“调整位置”子菜单（向左移一位 / 向右移一位 / 移到最左 / 移到最右），自动保持已隐藏 Agent 原位不变，仅重排可见槽位并持久化到 `agents.json`。
- 升级 `ai_docker/dock_window.py` 与 `ai_docker/launcher.py` 支持**双击顶栏图标新开实例**：
  - `AgentIconButton` 增加 220ms 单击防抖与 `double_clicked` 信号，确保双击新开实例时不误触发单击切换，且不与拖拽换位冲突。
  - 强制新开时对 VS Code 系编辑器自动附加 `--new-window`（支持配置 `new_instance_args` 自定义），对独立网页应用自动附加 `--new-window --app=...`。
- 新增**释放指定 Agent 到系统任务栏 / 重新收进悬浮窗**功能（`ai_docker/dock_window.py`）：
  - 右键任意顶部图标或项目卡片可直接点击“释放到系统任务栏 / 重新收进悬浮窗”，立即通过 `ITaskbarList::AddTab` 还原该 Agent 所有运行窗口并写入 `hide_from_taskbar`。
  - 右键全局菜单新增“释放指定 Agent 到任务栏”子菜单（显示已释放计数并支持“全部重新收进悬浮窗”）；已释放且运行中的 Agent 图标底部显示空心圆环以区别于已收纳的实心圆点。
- 拆分右键菜单作用域（`ai_docker/dock_window.py`）：
  - 在左侧拖动区（`DragGrip` 或顶栏空白边缘）右键显示全局 `AI docker` 菜单（`_build_global_menu`）；
  - 在顶部 Agent 图标或下方项目行上右键仅显示针对该 Agent / 窗口的专属精简菜单（`_build_agent_menu`），且仅在存在未读通知时才展示消角标项。

### 里程碑 7：全量代码审查与健壮性加固
- **Win32 64 位句柄与错误码规范化**：`main.py` 改用 `WinDLL("kernel32", use_last_error=True)` + `ctypes.get_last_error()` 并显式声明 `CreateMutexW`/`CloseHandle` 签名，退出 `finally` 释放互斥锁；`ai_docker/icons.py` 补全 `GetPackagesByPackageFamily` / `GetPackagePathByFullName` 签名并限制网页图标拉取仅允许 `http`/`https`（超时收敛为 2s）。
- **配置原子写入与异常容错**：`ai_docker/config.py` 改用同目录临时文件 + `os.replace()` 原子落盘，新增 `ConfigLoadError`；`_reload_all()` 捕获非法 JSON 并保留当前配置，避免手改配置出错导致崩溃。
- **稳定性与边界修复**：`launcher.py` 捕获子进程 `OSError`；`dock_window.py` 修复拖动区右键双重弹菜单、缓存 `_cached_hwnd` 并注销 `atexit` 防止 Qt 析构后异常、清理无用死代码；`notifications.py` 增加通知库 ID 回落水位线自愈与手动消角标后的 Windows Badge 去重抑制；`discovery.py` 复用单次 `WScript.Shell` COM 实例加速扫描。
- **模块拆分**：将主题色板与玻璃绘制抽离为独立模块 `ai_docker/theme.py`。

### 里程碑 8：空闲自动收缩悬浮图标与通知动态换标
- **紧凑玻璃悬浮图标 (`CollapsedDockOrb`)**：
  - 在 `ai_docker/dock_window.py` 新增 `66x66`（内含 `52x52` macOS 圆角毛玻璃方框）的 `CollapsedDockOrb`，默认显示 `AI docker` 图标与底部运行圆点，支持深色磨砂与浅色毛玻璃双主题，并支持直接拖拽移动、右键全局菜单与拖入 `.exe`/`.lnk`。
  - 鼠标离开悬浮窗超过设定时长后自动收缩，鼠标移入即刻展开完整双层 HUD；靠近屏幕右侧或底部时展开自动向屏幕内侧避让，再次收缩后回归原锚点位置；右键菜单、弹窗、拖窗或拖图标换位期间自动抑制收缩。
- **通知实时动态换标与角标队列**：
  - 维护按最近触发顺序排列的未读通知队列 `_alert_order`：任意 Agent 来新通知时，收缩态悬浮图标实时切换为该 Agent 的真实图标并显示红色警示描边与总未读数字角标；多 Agent 连续触发时始终显示最新通知者，清除后顺延回退到其余未读 Agent，全部清除后恢复 `AI docker` 默认图标。
- **可切换自动收缩 / 一直展开与等待时长**：
  - 在 `ai_docker/config.py` 新增 `auto_collapse`（默认 `true`）与 `auto_collapse_seconds`（默认 `8`）。
  - 在全局右键菜单新增“立即收缩为悬浮图标 / 立即展开悬浮窗”、“空闲自动收缩为悬浮图标”开关及“自动收缩等待时间（5 / 8 / 15 / 30 秒）”子菜单，取消勾选即刻展开并保持常驻展开。

## 后续规划
- 支持自定义全局快捷键呼出/切换悬浮窗。
- 探索支持按需折叠单个 Agent 的项目卡片。
