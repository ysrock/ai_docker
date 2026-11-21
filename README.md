# AI docker

面向 Windows 10/11 的轻量化 macOS 风格置顶悬浮窗，将本机各类 AI Agent（Win32 桌面端、MSIX 商店应用、终端 CLI、网页应用）集中收纳在一个双层毛玻璃 HUD 中，自动隐藏 Windows 任务栏中的重复运行图标，并按当前工作项目向下展开窗口列表与实时通知角标。

## 功能亮点

- **macOS 风格双层玻璃界面与双主题**
  - 第一行采用等距排列的玻璃胶囊图标栏，内置 **60fps Dock 悬停波浪放大动效**（鼠标移开后自动停表，静止 0 CPU 占用），搭配经典的 4px 运行状态圆点与苹果红通知徽章。
  - 支持 **macOS 深色磨砂 / 浅色毛玻璃** 右键一键切换；MSIX 商店应用图标自动按主题切换浅色/深色反色无底板版本（`altform-unplated` / `altform-lightunplated`）。
  - 第二行按运行中的 Agent 横向展开独立磨砂项目卡片，自动从窗口标题提取当前工作的项目名，点击即可直达对应窗口；空闲区域完全透明且鼠标可穿透。
- **自动探测与多种零门槛添加方式**
  - 首次运行且本地尚无 `agents.json` 时，自动扫描本机已安装的常见 AI 工具并生成配置。
  - 支持右键 **“扫描本机新安装的 Agent...”** 一键增量发现桌面快捷方式、常见安装目录、MSIX 注册表与 `PATH` 命令行工具。
  - 支持将任意 `.exe` / `.lnk` 直接拖拽到悬浮窗上松手入库，或通过右键 **“添加新 Agent...”** 弹窗添加桌面程序、网页 URL 或 CLI 命令。
- **原生任务栏自动隐藏与安全还原**
  - 通过 Windows 官方 Shell COM 接口 `ITaskbarList`（`DeleteTab` / `AddTab`）自动隐藏已收纳 Agent 的运行中任务栏图标，无需替换 `explorer.exe`，无需管理员权限。
  - 取消勾选开关、隐藏某个 Agent 图标或退出程序时（通过 Qt 退出信号与 `atexit` 多重保障），自动将所有隐藏窗口还原回 Windows 任务栏。
- **三路实时通知角标联动**
  - 整合 Windows 推送通知库 `wpndatabase.db` 只读增量监听、Windows Shell Hook（`RegisterShellHookWindow` 捕获 `HSHELL_FLASH` 窗口闪烁）与窗口标题未读数字解析，将新通知同步展示在顶栏图标及对应项目卡片上；切换回对应窗口后自动消除角标。
- **真实高清图标提取**
  - Win32 `.exe`/`.lnk` 通过 `PrivateExtractIconsW` 提取最高 256x256 RGBA 图标并本地缓存；MSIX 应用通过 Windows AppModel API 动态定位当前版本安装目录读取高分 PNG（应用升级后不失效）；网页应用自动拉取并缓存站点图标；CLI 工具自动叠加 `>_` 角标。

## 环境要求

- Windows 10 (1809+) 或 Windows 11（64 位）
- Python 3.12+（推荐使用 [uv](https://docs.astral.sh/uv/) 管理虚拟环境）
- 可选：Windows Terminal（用于更优雅地拉起 CLI 类 Agent）与 Chrome/Edge（用于网页应用独立窗口模式）

## 快速开始

### 1. 克隆并安装依赖

```powershell
git clone https://github.com/ysrock/ai_docker.git
cd ai_docker
uv sync
```

### 2. 编译无黑框原生启动器（推荐）

```powershell
.\build_launcher.ps1
```

脚本会调用 Windows 自带的 .NET 编译器生成约 40 KB 的原生无黑框 `AI docker.exe`（内嵌专属图标），并在开始菜单注册 `AI docker` 快捷方式。完成后直接双击 `AI docker.exe` 或按 `Win` 键搜索 `AI docker` 即可静默启动。

### 3. 直接命令行启动

```powershell
.\.venv\Scripts\python.exe main.py
```

## 日常使用指南

- **拖动整体位置**：按住左侧点阵手柄（或顶栏胶囊空白边缘）拖动，松手自动记录坐标（多屏环境下若断开副屏，启动时会自动复位到主屏安全区域）。
- **调整图标顺序**：直接按住第一行任意 Agent 图标左右拖动（目标位置会亮起蓝色竖线指示器），松手自动保存新顺序并同步更新下方项目卡片顺序；也可右键该图标在 **“调整位置”** 子菜单中选择向左移、向右移、移到最左或移到最右。
- **窗口切换与新开实例**：
  - 点击下方项目卡片：直达该具体项目窗口并消除其通知角标。
  - **单击**第一行图标：未运行时启动；单窗口时直接置顶唤醒；多窗口时连续点击在多个窗口间轮流切换。
  - **双击**第一行图标：直接新开一个对应 Agent 实例（Cursor / Antigravity IDE / Windsurf / Trae 等编辑器自动带 `--new-window`，网页应用自动带 `--new-window --app=...`，CLI 工具自动新开 Windows Terminal 窗口）。
- **拖拽添加**：直接从桌面或资源管理器把 `.exe` / `.lnk` 拖到第一行图标栏上松手即可收录。
- **右键菜单操作**：
  - 在顶部图标上右键：快速切换到该 Agent 的任意窗口、新开实例、或一键隐藏该图标。
  - 在悬浮窗任意位置右键：扫描本机新装 Agent、手动添加新 Agent、显示/隐藏图标管理、切换深/浅色主题、折叠/展开项目卡片、开关任务栏自动隐藏、清除全部通知角标、开关开机自启。

## 项目目录结构

```text
ai_docker/
├── main.py                       # 程序入口（单实例互斥锁 + 高分屏配置）
├── start_dock.pyw                # 无控制台 Python 入口
├── build_launcher.ps1            # 一键编译原生 AI docker.exe 并创建开始菜单快捷方式
├── launcher_src/
│   └── AiDockerLauncher.cs        # WinExe 极简无黑框启动器源码
├── assets/
│   ├── ai_docker.ico              # 多分辨率应用图标
│   └── ai_docker.png              # 应用图标预览图
├── ai_docker/
│   ├── config.py                 # 配置读写与首启自动探测初始化
│   ├── discovery.py              # 本机 AI Agent 自动扫描与 .lnk/.exe 解析
│   ├── icons.py                  # 高清真实图标提取、主题反色适配与本地缓存
│   ├── launcher.py               # 窗口枚举、项目名解析、智能置顶与多类型启动
│   ├── taskbar.py                # 基于 ITaskbarList 的任务栏隐藏与安全还原
│   ├── notifications.py          # WPN 通知库 + Shell Hook + 标题未读聚合追踪
│   ├── dialogs.py                # 扫描导入弹窗与手动添加弹窗
│   └── dock_window.py            # 双层毛玻璃 HUD、60fps 波浪放大与交互控制
└── docs/
    ├── ARCHITECTURE.md           # 模块架构与 Windows 底层机制说明
    └── PROGRESS.md               # 工程里程碑记录
```

## 配置说明 (`agents.json`)

本地配置文件 `agents.json` 已被 `.gitignore` 忽略，仅保存在你的本机目录中，不会上传到代码仓库。右键悬浮窗 -> **打开配置文件 (agents.json)** 即可直接编辑，保存后点 **重新加载配置与图标** 生效。

### `ui` 全局参数

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `theme` | `"dark"` | 主题风格：`"dark"`（深色磨砂）或 `"light"`（浅色毛玻璃） |
| `icon_size` | `32` | 顶部栏基础图标尺寸（像素） |
| `button_size` | `46` | 顶部栏图标按钮尺寸（像素） |
| `opacity` | `0.92` | 玻璃背景不透明度（`0.65` - `0.98`） |
| `window_chip_width` | `138` | 下方项目卡片宽度（像素） |
| `show_window_list` | `true` | 是否在下方展开运行中的项目窗口卡片 |
| `hide_from_taskbar` | `true` | 是否自动隐藏已收纳 Agent 的 Windows 任务栏图标 |
| `focus_existing` | `true` | 点击顶部图标时优先唤醒已有窗口而非重复启动 |
| `show_running_dot` | `true` | 是否在运行中图标底部显示 4px 状态圆点 |
| `pos` | `null` | 悬浮窗左上角坐标 `[x, y]`，拖动后自动写入 |

### `agents` 条目字段

支持 4 种 `type`：

- `exe`：Win32 桌面程序，常用字段 `path`、`args`、`cwd`、`match_process`。
- `msix`：Windows Store / MSIX 应用，常用字段 `aumid`（`PackageFamilyName!AppId`）、`match_process`。
- `cli`：终端命令行 Agent，常用字段 `command`、`terminal_title`、`cwd`、`cli_badge`、`icon_source`、`match_process`、`match_title`。
- `web`：网页版 Agent，常用字段 `url`、`app_mode`（`true` 使用浏览器独立应用窗口）、`browser`、`match_process`、`match_title`。

通用可选字段：

- `enabled`：`false` 可在悬浮窗隐藏该图标并自动恢复其原生任务栏显示。
- `hide_from_taskbar`：设为 `false` 可让单个 Agent 豁免任务栏自动隐藏。
- `notification_handlers`：关联的 Windows 通知 Handler ID 列表，用于精准捕获该应用的 Toast 通知。
- `notification_keywords`：共享进程（如 Chrome、Windows Terminal）的通知关键字过滤列表。

## 隐私与安全边界

- **纯本地运行**：无遥测、无云端上报、不读取或存储任何 AI 平台的账号凭证与对话内容。
- **只读通知监听**：对 Windows 本地通知库 `wpndatabase.db` 仅以 SQLite `mode=ro` 只读方式增量读取新通知标题以驱动角标，不修改系统数据库。
- **不侵入系统壳层**：不替换 `explorer.exe`，不修改系统注册表启动壳层；所有隐藏的任务栏窗口在程序退出时自动还原。
- **Windows UIPI 权限边界**：普通用户权限运行时无法隐藏“以管理员身份运行”的目标窗口任务栏图标，属于 Windows 系统安全隔离机制。

## 常见问题

1. **某个 Agent 的窗口没有从任务栏隐藏？**
   检查 `agents.json` 中该条目的 `match_process` 是否与任务管理器中的真实 `.exe` 进程名一致；若该程序以管理员身份运行，需保持普通权限启动或在需要时调整权限。
2. **退出 AI docker 后窗口找不到了？**
   正常通过右键菜单“退出 AI docker”或关闭窗口退出时会自动调用 `ITaskbarList::AddTab` 还原全部窗口；若遇异常强杀进程，按 `Alt + Tab` 切换或重新启动一次 AI docker 并取消勾选“自动隐藏任务栏图标”即可全部恢复。
3. **不想隐藏某一个具体软件的任务栏图标？**
   在本地 `agents.json` 对应条目中添加 `"hide_from_taskbar": false`，然后右键点“重新加载配置与图标”。

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
