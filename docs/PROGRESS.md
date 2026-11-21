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

## 后续规划
- 支持自定义全局快捷键呼出/切换悬浮窗。
- 可选屏幕边缘自动贴边收起与鼠标触边呼出模式。
