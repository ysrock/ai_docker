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

### 里程碑 9：异步图标加载、事件定序与全量审查修复
- **异步网页图标与回归测试体系**：
  - `ai_docker/icons.py` 引入 `WebIconLoader` 将网页图标拉取移至后台 `QThreadPool` 线程并原子替换缓存文件，彻底消除主线程网络卡顿；`_WebIconRequest.run()` 改为全异常兜底防止 `_in_flight` 死锁。
  - `ai_docker/notifications.py` 引入全局单调事件序号 `latest_event_order` 与精确窗口归属排序，并修复两处通知边界问题：标题未读数或系统 Badge 减少时同步下调忽略水位不再误触发红点；多窗口 Agent 切焦或点击时自动清除未归属的 Agent 级 Toast / Badge 且保留其它窗口的定向通知。
  - 新增 `tests/test_regressions.py`（9 项 `unittest` 回归用例）。
- **任务栏样式过滤、路径匹配与配置校验加固**：
  - `ai_docker/taskbar.py` 增加 `_has_taskbar_style()` 仅对有效任务栏窗口执行 `DeleteTab` / `AddTab` 并保留失败句柄待重试。
  - `ai_docker/launcher.py` 支持按进程真实路径区分同名多安装实例，使用 `os.path.realpath()` 兼容符号链接/联接/短路径及 Squirrel 版本化子目录结构（`app-*`）；`ai_docker/discovery.py` 增加 VS Code 关键字识别。
  - `ai_docker/config.py` 收紧并补全 `ui` 与 `agents` 字段校验（排除 `bool` 绕过、收紧像素上限、补全 `args`/`command`/`cwd` 等校验）。
- **交互边界与内存释放修复**：
  - `ai_docker/dock_window.py` 修复手动点击“立即收缩为悬浮图标”后因菜单关闭立即 `enterEvent` 弹回的问题（450ms 冷却，移出即复位）；修复靠右锚点启动或增删图标时展开态出屏问题；为扫描/添加弹窗补充 `deleteLater()` 与 `DiscoverAgentsDialog` 关闭时 `invalidate()`；统一封装 `_persist_config()` 容错处理文件占用异常。
  - 修复关闭扫描/添加弹窗或 `QMessageBox` 导致 `AI docker` 整体退出的严重缺陷：`main.py` 改为 `app.setQuitOnLastWindowClosed(False)`（因 `AgentDockWindow` 使用 `Qt.Tool` 不计入 Qt 非工具窗口计数），并在 `AgentDockWindow.closeEvent()` 显式退出事件循环；通过弹窗新增 Agent 后若处于收缩态自动展开以即时展示新图标。
- **右键菜单 macOS 毛玻璃主题统一**：
  - 在 `ai_docker/theme.py` 新增 `AppleGlassMenu(QMenu)`，启用无边框透明窗口并直接复用 `paint_apple_glass` 绘制圆角毛玻璃面板与多层投影（彻底消除 Windows 原生 `QMenu` 的直角黑/灰底边），同步自绘抗锯齿苹果蓝勾选对勾、子菜单右箭头、发丝分隔线与退出项红色警示样式，所有嵌套子菜单自动继承深/浅双主题与透明度。
  - 重构全局右键菜单分组（`ai_docker/dock_window.py`）：一级菜单收拢为 3 项高频快捷操作 + 3 个分组子菜单（`Agent 管理` / `显示与行为` / `配置与系统`）+ 退出项，高度缩减超 50%；单 Agent 菜单的窗口切换文案优化为“切换到窗口：{项目名}”避免歧义。
- **内置更新检查与一键更新重启**：
  - 新增 `ai_docker/updater.py`（`check_for_updates`、`UpdateChecker`、`launch_updater_script`）与根目录 `update.ps1`：启动后延迟 6 秒在后台 `QThreadPool` 静默检查 `origin/<branch>` 是否有新提交，绝不阻塞主线程；有更新时在全局右键菜单“配置与系统”下提示新更新数量。
  - 新增 `UpdateDialog`（`ai_docker/dialogs.py`）展示待拉取的提交日志并拦截未提交源码修改；用户点击“一键更新并重启”后拉起独立分离的 `update.ps1` 并优雅退出当前实例（先还原任务栏图标），随后快进拉取（`git pull --ff-only`，完整保留本地 `agents.json`）、按需 `uv sync` 与重编 `AI docker.exe` 并自动重启。
  - 在 `tests/test_regressions.py` 新增真实本地裸仓库 + `update.ps1` 端到端回归用例与弹窗/菜单联动测试（累计 13 项测试全部通过）。
  - 审查加固更新器与菜单细节：`update.ps1` 改为 UTF-8 BOM 兼容 Windows PowerShell 5.1、改用 Win32 `FindWindowW` + `WM_CLOSE` 优雅关闭 `Qt.Tool` 窗口、统一 `Invoke-Native` 避免 `uv sync` stderr 误报、GUI 更新失败自动恢复拉起旧实例；`updater.py` 注入 `GIT_TERMINAL_PROMPT=0`、识别 `@{u}` 真实上游与 `ahead_count` 分叉拦截；点击检查更新始终重新拉取避免过期缓存；`AppleGlassMenu` 按是否有勾选栏/子菜单动态对称留白并对动态名称中的 `&` 做转义。
- **独立添加网页 Agent 入口与 URL 拖拽支持**：
  - 在全局右键菜单 `Agent 管理` 下新增独立入口 **“添加网页 Agent (URL)...”**，点击直接进入网页添加页并自动读取剪贴板中的 `http(s)` 网址预填。
  - 升级 `AddAgentDialog`（`ai_docker/dialogs.py`）：完整保留带路径与查询参数的 URL，智能提取显示名（自动跳过 `www`/`chat`/`app` 等通用前缀），新增可选的“窗口关键字”与“图标来源”字段，并自动检测本机 Chrome / Edge 匹配进程与通知通道。
  - 升级 `AgentDockWindow` 拖放处理（`ai_docker/dock_window.py`）：除 `.exe`/`.lnk` 外，新增支持直接拖入浏览器地址栏链接或 Windows `.url` 快捷方式自动弹出预填好的网页 Agent 添加弹窗。
- **面向 AI Agent 与自动化的 CLI 接口 + Win32 热重载 IPC**：
  - 新增零 Qt 依赖的 `ai_docker/cli.py`、`ai_docker/__main__.py`、`ai-docker.cmd`、`ai_docker/ipc.py` 与 `ai_docker/web_utils.py`，并将 `validate_config()` 抽离至 `ai_docker/config.py` 供 GUI 与 CLI 共用校验。
  - 提供 `schema` / `status` / `list` / `add (web|exe|cli|msix)` / `remove` / `show` / `hide` / `release` / `reclaim` / `move` / `open` / `discover` / `config (get|set)` / `dock (start|stop|reload|expand|collapse|clear-notifications)` / `update (check|apply)` 完整命令集，全命令支持 `--json` 结构化输出与 `--config` 隔离路径。
  - 在 `AgentDockWindow.nativeEvent()` 接入 `AI_docker_CLI_Control` Win32 IPC 消息：CLI 修改配置后自动热通知正在运行的悬浮窗实时重载，无需重启且不与运行态配置冲突。
  - 在 `tests/test_regressions.py` 新增 `CliTests` 与 `test_cli_ipc_controls_live_dock_window`（累计 19 项回归测试）。
- **多窗口 Agent 点击优先跳转最新提醒窗口**：
  - `ai_docker/notifications.py`：`WindowNotificationState` 新增 `latest_event_order`，`AgentNotificationSummary` 新增 `alert_hwnds_by_recency` 属性，`NotificationTracker` 新增 `_window_alert_info()` 与 `get_alert_hwnds()`，并调整 `on_shell_flash()` 在已有闪烁窗口再次闪烁时刷新事件顺序。
  - `ai_docker/launcher.py`：抽离 `select_target_window()` 与 `get_last_focused_hwnd()`，为 `launch_agent()` 增加 `preferred_hwnds` 参数；存在多个运行窗口时优先跳转最新有提醒且未在前台（或已最小化）的窗口，全部提醒消标后恢复原有轮切顺序。
  - `ai_docker/dock_window.py`：更新 `AgentDockWindow._on_agent_clicked()`，点击后仅精准清除实际跳转窗口的提醒与 Agent 级未归属提醒，保留其余窗口的未读提醒以便连续点击顺延查看。
  - 在 `tests/test_regressions.py` 新增 `test_window_alert_recency_combines_toast_flash_and_title_and_refreshes_on_reflash` 与 `test_multi_window_agent_click_focuses_newest_alerting_window_first`（累计 21 项回归测试全部通过）。
- **纵向布局（侧边图标栏 + 同行横向窗口铺开）**：
  - `ai_docker/config.py` 与 `ai_docker/cli.py` 新增 `ui.orientation`（`horizontal` / `vertical`）配置校验与 CLI 支持。
  - `ai_docker/dock_window.py`：改造 `DragGrip`、`AgentIconButton` 与 `TopDockCapsule` 支持横/纵双方向与巢状坐标计算；新增 `AgentHorizontalStrip` 在纵向模式下将同一 Agent 的多扇窗口紧邻对应图标横向铺开并按屏幕可用宽度自动折行（4-6 列），同时动态同步左侧图标槽位高度保证严格零错位。
  - 停靠屏幕右半边时自动镜像为“图标在右、窗口条向左铺开”，并在全局菜单“显示与行为 -> 布局方向”支持一键切换，单 Agent 调整位置菜单同步适配上下移动文案。
  - 在 `tests/test_regressions.py` 新增 `test_vertical_layout_aligns_window_strips_and_mirrors_on_right_edge` 与配置/CLI 方向校验（累计 22 项回归测试）。
- **单窗口右键关闭与代码审查修复**：
  - `ai_docker/launcher.py` 新增异步 `close_hwnd()`（基于 Win32 `PostMessage(..., WM_CLOSE, 0, 0)` 安全关闭指定窗口），并在 `ai_docker/dock_window.py` 窗口芯片右键菜单新增“关闭该窗口”红色警示项及失败提示。
  - 修复审查发现的多处边界问题：`_reload_all()` 先取当前锚点再更新配置、`_set_orientation()` 与 `mouseReleaseEvent()` 同步更新锚点并在保存失败时完整回滚镜像布局、`_vertical_max_cols()` 下限调整为 `1` 避免窄屏溢屏、纵向槽位开启 `setMouseTracking(True)`、重建旧控件先隐藏脱离父级并清理未使用的 `hashlib` 导入。
  - 同步更新 `docs/ARCHITECTURE.md` 并在 `tests/test_regressions.py` 新增 `test_window_context_menu_closes_window_and_preserves_other_alerts` 与 `test_vertical_reload_anchor_cross_screen_rollback_and_max_cols`（累计 24 项回归测试）。
- **拖拽手柄双击一键收缩为悬浮图标**：
  - `ai_docker/dock_window.py`：`DragGrip` 新增 `double_clicked` 信号及 `mouseDoubleClickEvent()` 响应，双击左侧/顶部拖拽点阵区立即调用 `_collapse_dock(force=True)` 收缩为单个悬浮图标（含 450ms 防弹回冷却并保持纵向靠右镜像锚点不偏移）；同时在 `mouseReleaseEvent()` 增加原地未移动判定跳过冗余配置写盘。
  - 在 `tests/test_regressions.py` 新增 `test_drag_grip_double_click_collapses_to_orb_without_saving_config`（累计 25 项回归测试）。
- **Codex (`gpt-6.2-sol` / `high`) 第 1、2 轮全项目审查与闭环修复**：
  - **配置与并发锁**：`ai_docker/config.py` 新增跨进程可重入文件锁 `update_config()`、首次缺失配置建档加锁与重复 ID 校验，`dock_window.py`（添加/发现/拖放）与 `cli.py` 全部在锁内去重；`main.py` 与 `update.ps1` 引入安装级实例锁 `Local\AI_docker_Instance_<hash>` + 更新互斥锁 `Local\AI_docker_Update_<hash>`（只读 `OpenMutexW` 探测），消除启动与更新并发竞态。
  - **启动器与更新器**：`update.ps1` 增加实例互斥锁轮询等待并校验 `Start-AiDocker` 入口与退出码，缺失入口或重启失败时明确报错；`ai_docker/launcher.py` 改用 ctypes `user32.ShowWindowAsync`（修复 `pywin32` 无 `ShowWindowAsync` 属性报错）与 `IsHungAppWindow` 防无响应卡死、`SWP_ASYNCWINDOWPOS`，并同时为 `wt.exe` 命令正文（`-EncodedCommand`）与 `-d` 工作目录分号转义。
  - **包装器与扫描发现**：新增无损 JSON 传参机制的 `ai-docker.ps1` 与 `DisableDelayedExpansion` 优化的 `ai-docker.cmd`，`cli.py` 限制仅扫描 `--` 之前的 `--json`；`ai_docker/discovery.py` 为 `CommandLineToArgvW` 补齐虚拟 `arg0` 避开程序名规则特例，并在自动扫描快捷方式时完整保留 `args` 与 `cwd`。
  - **图标、通知与交互布局**：`ai_docker/icons.py` 通过后台阻塞隔离与异步 socket shutdown 实现 `< 50ms` 取消退出与零主线程阻塞并拦截超大像素图；`ai_docker/notifications.py` 在 SQLite 事务快照内原子推进水位线，`dock_window.py` 在清除全部前刷新最新窗口标题；`dock_window.py` 修复 `PreciseTimer` 双击仲裁并补全窄横向/矮纵向（20 Agent 溢出 `+N` 菜单与任务栏排除）约束，全部 29 项回归测试在真实 Windows 环境验证通过。
  - **其余 5 项低优先级建议项（ADVISORY）清理与收口**：`ai_docker/web_utils.py` 拦截含 `\`、`@` 及主机 `%` 编码的畸形 URL；`ai_docker/icons.py` 新增 `_ico_has_safe_dimensions()` 与 `QImageReader` 受限解码（`<= 256px` / `<= 400万像素`）；`ai_docker/dock_window.py` 对齐共享窗口释放后的任务栏图标展示；`ai_docker/ipc.py` 与 `ai_docker/cli.py` 区分 UIPI 权限拦截（`permission_denied`）与实例未运行；`update.ps1` 分离 `Invoke-Native` 的 `stdout` 与 `stderr` 避免 `GIT_TRACE` 调试输出污染分支/路径判定；`.gitignore` 新增 `.agents.json.lock`。
- **Chrome 任务栏图标强制保留（2026-11-27）**：
  - `ai_docker/config.py` 与 `ai_docker/taskbar.py` 新增 `TASKBAR_HIDE_EXCLUDED_PROCESSES`（包含 `chrome.exe`）及 `is_taskbar_hide_excluded_process()` / `is_taskbar_hide_excluded_agent()` 判定。
  - `ai_docker/dock_window.py` 在 `_refresh_state()` 中强制将 Chrome（`chrome.exe`）相关窗口排除出任务栏隐藏集合（保留悬浮窗的窗口识别、点击切换与通知提醒），并在单 Agent 菜单、全局释放菜单与图标状态点上同步提示“Chrome 保留在任务栏”；`ai_docker/cli.py` 同步 `released_to_taskbar` 状态输出。
  - `tests/test_regressions.py` 新增 `test_chrome_windows_are_never_hidden_from_taskbar` 回归验证（累计 30 项回归测试）。

## 后续规划
- 支持自定义全局快捷键呼出/切换悬浮窗。
- 探索支持按需折叠单个 Agent 的项目卡片。
