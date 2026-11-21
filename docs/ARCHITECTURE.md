# AI Doker 架构与底层实现说明

本文档说明 AI Doker 的模块职责与 Windows 桌面集成机制，方便二次开发与维护。

## 1. 模块职责总览

| 模块 | 职责 |
| --- | --- |
| `main.py` | 单实例 Win32 互斥锁（`Local\AI_Doker_FloatingDock_Mutex`）、高分屏缩放策略初始化、应用图标设置 |
| `ai_doker/config.py` | 读写本地 `agents.json`；当配置文件不存在时自动触发 `discover_new_agents([])` 完成首启初始化 |
| `ai_doker/discovery.py` | 快速扫描桌面/开始菜单快捷方式、常见程序目录（浅层）、AppModel 注册表 MSIX 家族和 `PATH` 命令行，以及拖入 `.exe`/`.lnk` 解析 |
| `ai_doker/icons.py` | Win32 `PrivateExtractIconsW` RGBA 提取（带 legacy mask 兜底）、MSIX AppModel 动态目录 Logo 定位与深浅主题反色选择、网页 Favicon 拉取与本地 PNG 缓存、圆角裁剪与 CLI `>_` 角标合成 |
| `ai_doker/launcher.py` | 顶层可见窗口枚举（过滤 `WS_EX_TOOLWINDOW` 与 DWM Cloaked 窗口）、`QueryFullProcessImageNameW` 进程名解析、项目名提取、多窗口轮切与前台锁兜底唤醒、多类型进程启动与开机自启快捷方式管理 |
| `ai_doker/taskbar.py` | 通过 `pywin32` 原生 `ITaskbarList` COM 接口管理运行中窗口的任务栏隐藏（`DeleteTab`）与还原（`AddTab`），内置 `Shell_TrayWnd` 句柄变化检测以兼容 `explorer.exe` 重启 |
| `ai_doker/notifications.py` | 聚合 Windows 本地通知库 `wpndatabase.db` 水位线增量 Toast/Badge、Windows Shell Hook `HSHELL_FLASH` 闪烁事件与窗口标题未读数字，并按项目名归属到具体窗口 |
| `ai_doker/dialogs.py` | 适配深/浅双主题的本机扫描导入弹窗（`DiscoverAgentsDialog`）与手动添加弹窗（`AddAgentDialog`） |
| `ai_doker/dock_window.py` | 双层毛玻璃 HUD 主窗口（`TopDockCapsule` + `AgentWorkspaceCard`）、60fps 余弦波浪放大动画、文件拖放接收、右键菜单与全局状态轮询 |

## 2. 关键 Windows 底层机制

### 2.1 无边框置顶且不占原生任务栏
主窗口组合使用 `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool` 与 `Qt.WA_TranslucentBackground`：
- `Qt.Tool` 确保悬浮窗自身不会在原生任务栏产生按钮；
- 透明窗口模式下，未绘制像素区域（例如第二行各项目卡片之间的空隙与未运行列下方）天然保持鼠标事件穿透，不遮挡底层工作窗口。

### 2.2 任务栏隐藏与安全还原 (`ITaskbarList`)
`TaskbarHider` 调用 Windows Shell 官方 COM 组件 `CLSID_TaskbarList` / `IID_ITaskbarList`：
- 周期性调用 `sync_hidden_windows(target_hwnds)`：自动剔除已关闭的无效句柄、将不再匹配或已被禁用的窗口通过 `AddTab` 立即还原、并对目标窗口幂等执行 `DeleteTab` 防止 Electron/Chromium 窗口事件导致任务栏图标回弹。
- 每次调用前检测 `FindWindow("Shell_TrayWnd", None)`，若句柄变化（例如 `explorer.exe` 重启或开机自启时序竞态）则自动重建 COM 实例。
- 通过 Qt `aboutToQuit`、`closeEvent` 与 Python `atexit` 三重钩子保障退出时调用 `restore_all()`。

### 2.3 窗口唤醒与 Windows 前台锁兜底
`focus_hwnd()` 依次执行：
1. 若目标窗口处于最小化状态则 `SW_RESTORE`，否则 `SW_SHOW`；
2. 尝试通过 `AttachThreadInput` 临时关联前台线程与目标线程输入队列后调用 `BringWindowToTop` + `SetForegroundWindow`；
3. 若仍命中 Windows 前台锁异常，采用 `SetWindowPos(HWND_TOPMOST)` 随即 `SetWindowPos(HWND_NOTOPMOST)` 的 Z-order 脉冲兜底，确保目标窗口稳定浮起且不误触发重复启动。

### 2.4 实时通知角标三路融合
`NotificationTracker` 在不读取账号凭证的前提下融合三路信号：
1. **Windows 推送通知库**：只读打开 `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`，启动时记录 `MAX(Id)` 作为水位线，仅增量消费启动后新到达的 Toast，并按匹配到的项目名将角标精准归属到对应窗口；
2. **Shell Hook 闪烁事件**：在 `AgentDockWindow.showEvent()` 注册 `RegisterShellHookWindow`，通过 `nativeEvent()` 拦截 `WM_SHELLHOOKMESSAGE` 中的 `HSHELL_FLASH`（`0x8006`）与 `HSHELL_WINDOWACTIVATED`；
3. **窗口标题未读计数**：解析标题前缀的 `(N)` / `[N]` 数字，同时排除 IDE 未保存文件标记 `●`。

当目标窗口被点击或成为前台窗口时，自动清除对应角标。

### 2.5 60fps 波浪放大与 0 闲置开销
`TopDockCapsule` 在鼠标移动时按余弦钟形曲线计算每个图标的目标缩放倍率（峰值 1.26x，相邻图标联动），由 16ms `QTimer` 做平滑插值；一旦所有图标收敛至目标倍率（误差 `< 0.003`），定时器立即停止，静止状态下不消耗 CPU。
