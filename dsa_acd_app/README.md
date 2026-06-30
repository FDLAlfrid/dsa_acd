<div align="right">
  <a href="#deepseek-agent-en">🇺🇸 English</a> · <a href="#deepseek-agent-zh">🇨🇳 中文</a>
</div>

---

<a id="deepseek-agent-en"></a>

# DeepSeek Agent

A cross-platform desktop AI agent powered by DeepSeek API, built with Flet.

## Features

- 💬 **Chat** — Conversational AI with streaming output
- 📁 **File Manager** — Browse, open, edit, preview files (text, code, images)
- 💾 **Session Management** — Create, rename, search, delete conversation sessions
- 🔍 **Balance Check** — Query DeepSeek API account balance
- 🎭 **Agent Roles** — Switch between customizable presets (Programmer, Translator, Teacher, etc.)
- ⚙️ **Customizable** — System prompt, quick prompts, dark/light theme, model selection
- 🧹 **Privacy Safe** — API key stays in memory only, never saved to disk

## Setup

1. Install Python 3.8+
2. Install dependencies:
   ```bash
   pip install flet openai requests
   ```
3. Run:
   ```bash
   python main.py
   ```

## Build EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

## Data Directory

All data is stored at `~/.deepseek_sessions/`:
- `sessions/*.json` — chat history
- `settings.json` — preferences (model, theme, roles, quick prompts)
- **API key is never saved to disk**

## License

GPL 3.0

---

<a id="deepseek-agent-zh"></a>

<div align="right">
  <a href="#deepseek-agent-en">🇺🇸 English</a> · <a href="#deepseek-agent-zh">🇨🇳 中文</a>
</div>

# DeepSeek Agent

基于 DeepSeek API 的跨平台桌面 AI 助手，使用 Flet 构建。

## 功能特性

- 💬 **聊天** — 流式输出的 AI 对话
- 📁 **文件管理** — 浏览、打开、编辑、预览文件（文本、代码、图片）
- 💾 **会话管理** — 创建、重命名、搜索、删除对话会话
- 🔍 **余额查询** — 查询 DeepSeek API 账户余额
- 🎭 **Agent 角色** — 切换可自定义的预设角色（程序员、翻译、教师等）
- ⚙️ **个性化设置** — 系统提示词、快捷提示词、深色/浅色主题、模型选择
- 🧹 **隐私安全** — API 密钥仅保存在内存中，从不写入磁盘

## 安装运行

1. 安装 Python 3.8+
2. 安装依赖：
   ```bash
   pip install flet openai requests
   ```
3. 运行：
   ```bash
   python main.py
   ```

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

## 数据目录

所有数据保存在 `~/.deepseek_sessions/`：
- `sessions/*.json` — 聊天记录
- `settings.json` — 偏好设置（模型、主题、角色、快捷提示词）
- **API 密钥从不写入磁盘**

## 许可证

GPL 3.0
