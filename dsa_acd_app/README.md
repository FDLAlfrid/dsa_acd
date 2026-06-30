# DeepSeek Agent

A cross-platform desktop AI agent powered by DeepSeek API, built with Flet.

## Features

- 💬 **Chat** - Conversational AI with streaming output
- 📁 **File Manager** - Browse, open, edit, and preview files (text, code, images)
- 💾 **Session Management** - Create, rename, search, and delete conversation sessions
- ⚙️ **Customizable** - System prompt, quick prompts, theme toggle, model selection
- 🔍 **Balance Check** - Query DeepSeek API account balance

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

## License

GPL 3.0
