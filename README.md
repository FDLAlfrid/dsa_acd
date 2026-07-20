<div align="right">🌐 <a href="#deepseek-agent">English</a> · <a href="#中文">中文</a></div>

# DeepSeek Agent v0.0.1

A cross-platform desktop AI agent powered by DeepSeek API, built with Flet.
Supports function calling with MCP tool integration, co-sharing MCP server configs with Trae IDE.

## Features

- 💬 **Chat** — Conversational AI with streaming output
- 🤖 **Agent Mode** — AI can autonomously execute tools: read/write files, run commands, call MCP servers
- 🔧 **System Tools** — Auto-detects and registers npm, git, and other system commands
- 🔌 **MCP Integration** — Shares MCP server configs with Trae IDE, auto-detects available servers
- 📁 **File Manager** — Browse, open, edit, preview files (text, code, images)
- 💾 **Session Management** — Create, rename, search, delete conversation sessions
- 🔍 **Balance Check** — Query DeepSeek API account balance
- 🎭 **Agent Roles** — Switch between customizable presets (Programmer, Translator, Teacher, etc.)
- ⚙️ **Customizable** — System prompt, quick prompts, dark/light theme, model selection
- 🧹 **Privacy Safe** — API key stays in memory only, never saved to disk
- 🌍 **Cross-Platform** — Graceful degradation when tools are not available

## Agent Mode

The AI can autonomously call tools via DeepSeek's function calling API. When the model decides a tool is needed, it requests execution, the result is fed back, and the conversation continues.

### Built-in Tools

| Tool | Condition | Description |
|---|---|---|
| `read_file_content` | Always | Read local files |
| `write_file_content` | Always | Write files |
| `list_files` | Always | List directory contents |
| `run_command` | Always | Execute terminal commands |
| `get_balance` | Always | Check API balance |
| `npm_run` | npm installed | Run npm commands (install, build, test...) |
| `git_run` | git installed | Run Git commands (status, log, diff...) |

### MCP Tool Integration

Inspired by [TraeHelper](https://github.com/FDLAlfrid/TraeHelper), this project reads Trae IDE's `mcp.json` configuration to share MCP server setups. When npx (Node.js) is available, MCP servers defined in `mcp.json` are auto-discovered and started.

Each MCP server is started as a subprocess and communicates via JSON-RPC over stdio. If a server fails to start, it's silently skipped without affecting others. The actual available MCP servers depend on the user's Trae IDE configuration.

### Graceful Degradation

- No Node.js → npm and MCP tools are not registered
- No git → git tools are not registered
- No npx → MCP servers are skipped
- Minimum environment → only basic file I/O and command execution tools

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Build EXE (v0.0.1)

> **重要**：打包前必须先激活 conda 环境（或 venv），否则 PyInstaller 会打包错误的依赖。

```bash
# 1. 激活环境
conda activate deepseek_agent      # Conda 用户
# 或 venv\Scripts\activate         # venv 用户

# 2. 安装依赖
pip install flet openai requests pyinstaller

# 3. 一键打包（必须包含 --collect-data，否则运行时缺少 icons.json）
pyinstaller --name "DeepSeekAgent" --icon=icon.ico --version-file=version_info.txt ^
  --onefile --windowed --hidden-import=flet_desktop --hidden-import=flet ^
  --collect-data flet --collect-data flet_desktop main.py --specpath .
```

### 版本号修改

EXE 版本号由 `version_info.txt` 控制，打包后右键 EXE → 属性 → 详细信息可见。修改时只需改 **3 处**：

| 位置 | 说明 | 示例 |
|------|------|------|
| `filevers=(0, 0, 1, 0)` | 文件版本（4 位元组） | 改 `(0, 0, 2, 0)` |
| `prodvers=(0, 0, 1, 0)` | 产品版本（同上） | 改 `(0, 0, 2, 0)` |
| `StringStruct(u'FileVersion', u'0.0.1')` | 文件版本字符串 | 改 `u'0.0.2'` |
| `StringStruct(u'ProductVersion', u'0.0.1')` | 产品版本字符串 | 改 `u'0.0.2'` |

同时同步修改 `main.py` 中的 `VERSION = "0.0.1"` 常量（窗口标题栏显示）。

## Data Directory

`~/.deepseek_sessions/` — chat history & preferences. **API key is encrypted with a machine-bound key** (hostname + username) and stored in `secure_key.dat`. Cannot be decrypted on a different machine.

## License

GPL 3.0

---

# 中文

基于 DeepSeek API 的跨平台桌面 AI 助手，使用 Flet 构建。
支持函数调用和 MCP 工具集成，与 Trae IDE 共用 MCP 服务器配置。

## 功能特性

- 💬 **聊天** — 流式输出的 AI 对话
- 🤖 **Agent 模式** — AI 可自主调用工具：读写文件、运行命令、调用 MCP 服务器
- � **系统工具** — 自动检测并注册 npm、git 等系统命令
- 🔌 **MCP 集成** — 与 Trae IDE 共用 MCP 服务器配置，自动检测已安装的 MCP 服务器
- �📁 **文件管理** — 浏览、打开、编辑、预览文件（文本、代码、图片）
- 💾 **会话管理** — 创建、重命名、搜索、删除对话会话
- 🔍 **余额查询** — 查询 DeepSeek API 账户余额
- 🎭 **Agent 角色** — 切换可自定义的预设角色（程序员、翻译、教师等）
- ⚙️ **个性化设置** — 系统提示词、快捷提示词、深色/浅色主题、模型选择
- 🧹 **隐私安全** — API 密钥通过机器绑定加密存储，同机器可自动恢复，其他机器无法解密
- 🌍 **跨平台兼容** — 工具不可用时优雅降级，不影响基础功能

## Agent 模式

AI 通过 DeepSeek 的函数调用 API 自主执行工具。当模型判断需要调用工具时，会请求执行，结果被反馈给模型，对话继续进行。

### 内置工具

| 工具 | 条件 | 说明 |
|---|---|---|
| `read_file_content` | 始终可用 | 读取本地文件 |
| `write_file_content` | 始终可用 | 写入文件 |
| `list_files` | 始终可用 | 列出目录内容 |
| `run_command` | 始终可用 | 执行终端命令 |
| `get_balance` | 始终可用 | 查询 API 余额 |
| `npm_run` | npm 已安装 | 运行 npm 命令（install, build, test...） |
| `git_run` | git 已安装 | 运行 Git 命令（status, log, diff...） |

### MCP 工具集成

借鉴 [TraeHelper](https://github.com/FDLAlfrid/TraeHelper) 项目的思路，读取 Trae IDE 的 `mcp.json` 配置文件，实现 MCP 服务器配置共享。当 npx（Node.js）可用时，自动发现并启动 `mcp.json` 中定义的 MCP 服务器。

每个 MCP 服务器作为子进程启动，通过 stdio 上的 JSON-RPC 通信。某个服务器启动失败时会静默跳过，不影响其他服务器。实际可用的 MCP 服务器取决于用户的 Trae IDE 配置。

### 优雅降级

- 无 Node.js → 不注册 npm 和 MCP 工具
- 无 git → 不注册 git 工具
- 无 npx → 跳过 MCP 服务器
- 最小环境 → 仅提供基础文件读写和命令执行工具

## 安装运行

```bash
pip install -r requirements.txt
python main.py
```

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico ^
  --hidden-import=flet_desktop ^
  --exclude-module=PyQt5 --exclude-module=PyQt6 ^
  #这里可以添加其他需要排除的模块，上一行是举例
  main.py
```

如需最小体积，使用干净的虚拟环境：

```bash
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

或者使用 Conda：

```bash
conda create -n deepseek_agent python=3.10
conda activate deepseek_agent
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

### 关于 PyInstaller 模块选择

PyInstaller 使用 **黑名单排除** (`--exclude-module`) 是因为 Python 的依赖解析很复杂 — 包通常有隐式依赖，这些依赖不会被直接导入。白名单方式会遗漏这些依赖，导致运行时错误。

如需精确控制，使用 **spec 文件**：

```bash
pyinstaller --name "DeepSeekAgent" --icon=icon.ico main.py --specpath .
```

编辑 `DeepSeekAgent.spec` 自定义 `excludes`、`hiddenimports` 和 `datas`，然后：

```bash
pyinstaller DeepSeekAgent.spec
```

## 数据目录

`~/.deepseek_sessions/` — 聊天记录和偏好设置。**API 密钥通过机器绑定密钥加密存储**（主机名+用户名），加密后保存为 `secure_key.dat`，其他机器无法解密。

## 许可证

GPL 3.0
