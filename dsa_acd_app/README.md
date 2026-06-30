<div align="right">🌐 <a href="#deepseek-agent">English</a> · <a href="#deepseek-agent-中文">中文</a></div>

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

```bash
pip install flet openai requests
python main.py
```

## Build EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

## Data Directory

`~/.deepseek_sessions/` — chat history & preferences. **API key is never saved to disk.**

## License

GPL 3.0

---

# DeepSeek Agent 中文

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

```bash
pip install flet openai requests
python main.py
```

## 打包为 EXE

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "DeepSeekAgent" --icon=icon.ico main.py
```

## 数据目录

`~/.deepseek_sessions/` — 聊天记录和偏好设置。**API 密钥从不写入磁盘。**

## 许可证

GPL 3.0
