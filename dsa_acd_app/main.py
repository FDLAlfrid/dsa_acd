import flet as ft
import os, json, time, gc, base64, re
try: from openai import OpenAI
except: OpenAI = None

APP_DIR = os.path.expanduser("~/.deepseek_sessions")
SESSION_DIR = os.path.join(APP_DIR, "sessions")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
for d in [APP_DIR, SESSION_DIR]: os.makedirs(d, exist_ok=True)

BINARY_EXTS = {".exe", ".dll", ".bin", ".dat", ".pyc", ".pyd", ".so", ".dylib", ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"}

DEFAULT_AGENT_ROLES = {
    "通用助手": "你是一个AI助手，可以读写本地文件、运行命令，请按用户指令操作。",
    "编程专家": "你是一位资深程序员，擅长代码编写、调试、重构和技术方案设计。回复要专业、准确，提供可运行的代码示例。",
    "翻译官": "你是一位专业翻译，精通中英文互译。请保持原文风格和语气，专业术语要准确。只返回译文，不需要解释。",
    "写作助手": "你是一位写作专家，擅长各类文书创作。包括文章、报告、邮件等。注意语言的流畅性和逻辑性。",
    "代码审查员": "你是一位严格的代码审查专家，每次审查要指出：1)潜在bug 2)性能问题 3)安全风险 4)可改进之处。",
    "数据分析师": "你是一位数据分析专家，擅长数据解读、趋势分析和可视化建议。请用数据说话，给出 actionable 的洞察。",
    "教师": "你是一位耐心且知识渊博的老师。善于用简单的方式解释复杂概念，会通过提问引导用户思考。",
}

def session_path(name): return os.path.join(SESSION_DIR, f"{name}.json")

def list_sessions():
    if not os.path.isdir(SESSION_DIR): return []
    files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(SESSION_DIR, x)), reverse=True)
    return [f[:-5] for f in files]

def delete_session(name):
    path = session_path(name)
    if os.path.exists(path):
        for _ in range(3):
            try: os.remove(path); return
            except PermissionError: gc.collect(); time.sleep(0.1)
            except: return

def save_msgs(name, msgs):
    with open(session_path(name), "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)

def load_msgs(name):
    path = session_path(name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return [{"role": "system", "content": get_default_system_prompt()}]

def read_file(path):
    try:
        size = os.path.getsize(path)
        if size > 1024 * 1024 * 5:
            return f"文件过大: {path} ({size/1024/1024:.1f}MB，最大支持5MB)"
        for enc in ["utf-8-sig", "utf-8", "gbk", "latin-1"]:
            try:
                with open(path, "r", encoding=enc) as f: return f.read()
            except UnicodeDecodeError: continue
        import base64
        with open(path, "rb") as f:
            b64_content = base64.b64encode(f.read()).decode()
            return f"[二进制文件] {path}\n[Base64编码]\n{b64_content}"
    except FileNotFoundError: return f"文件不存在: {path}"
    except PermissionError: return f"无权限读取: {path}"
    except Exception as ex: return f"读取失败: {path} - {str(ex)}"

def write_file(path, content):
    try:
        with open(path, "w", encoding="utf-8") as f: f.write(content); return True
    except Exception as ex: return str(ex)

def calc_tokens_count(msgs):
    """估算消息的总token数"""
    total = 0
    for m in msgs:
        c = m.get("content", "")
        if isinstance(c, str):
            total += len(c) * 1.5
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    total += len(part.get("text", "")) * 1.5
    return int(total)

TOOLS = [
    {"type": "function", "function": {"name": "read_file_content", "description": "读取本地文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file_content", "description": "写入文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "列出目录文件", "parameters": {"type": "object", "properties": {"dir": {"type": "string"}}, "required": ["dir"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "运行终端命令", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "get_balance", "description": "查询 DeepSeek API 余额", "parameters": {"type": "object", "properties": {}}}},
]

def get_default_system_prompt(): return "你是一个AI助手，可以读写本地文件、运行命令，请按用户指令操作。"

def get_theme_colors():
    is_dark = state["theme_mode"] == ft.ThemeMode.DARK
    return {"bg": ft.Colors.GREY_100 if not is_dark else "#1a1a2e", "card": ft.Colors.WHITE if not is_dark else "#16213e", "text": ft.Colors.BLACK87 if not is_dark else ft.Colors.WHITE70, "text_hint": ft.Colors.GREY if not is_dark else ft.Colors.GREY_400, "border": ft.Colors.GREY_300 if not is_dark else "#0f3460", "selected": ft.Colors.BLUE_100 if not is_dark else "#1a3a6a", "code_bg": ft.Colors.GREY_200 if not is_dark else "#0d1b2a", "user_msg": ft.Colors.BLUE_50 if not is_dark else "#1a2744", "assistant_msg": ft.Colors.GREY_100 if not is_dark else "#16213e", "error_msg": ft.Colors.RED_50 if not is_dark else "#2a1a1a", "primary": ft.Colors.BLUE if not is_dark else ft.Colors.CYAN_400}

def get_layout_size(p):
    w = p.width if p.width > 0 else 1200
    if w >= 1200: return {"chat_width": 200, "file_width": 250, "spacing": 12, "padding": 12}
    elif w >= 1000: return {"chat_width": 180, "file_width": 220, "spacing": 10, "padding": 10}
    else: return {"chat_width": 150, "file_width": 200, "spacing": 8, "padding": 8}

def show_toast(page, msg):
    page.snack_bar = ft.SnackBar(ft.Text(msg))
    page.snack_bar.open = True; page.update()

def show_dialog(page, dlg):
    page.overlay.append(dlg); dlg.open = True; page.update()

def confirm_dialog(page, title, content, on_confirm):
    def handler(e):
        if e.control.data == "yes": on_confirm()
        dlg.open = False; page.update()
    dlg = ft.AlertDialog(title=ft.Text(title), content=ft.Text(content), actions=[ft.TextButton("取消", data="no", on_click=handler), ft.Button("确定", data="yes", on_click=handler, bgcolor=ft.Colors.RED)])
    show_dialog(page, dlg)

def check_api_key(key):
    if not key: return False
    if not key.startswith("sk-"): return False
    return True

# 嵌入图标 Base64 数据（避免 PyInstaller 打包后找不到文件）
ICON_B64 = "AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAgIAAAMAAAACAAAAAAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAACIAAAAAAAAAiAAAAAAAAACIAAAAAAAAAAgAAAAAAAAAIAAAAAAAAAAiAAAAAAAAAIgAAAAAAAAAiAAAAAAAAAAAAAAAgAAAAAAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAiAAAAIgAAAAAAAAAiAAAAAAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAAIAAAACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAAAAAAAIAAAAAAAAAAiAAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAiAAAAAAAAAIAAAAAAAAAAAAAAAAAAAAAAAIAAAACAAAAIgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAiAAAAIgAAACAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAAAAAAAAAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAACAAAAAAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD//wAA//8AAP//AADAAwAA4AcAAOAHAADAAwAAwAMAAMADAADgBwAA4AcAAOAHAADgBwAA4AcAAP//AAD//wAA//8AAA=="

def main(page: ft.Page):
    global state
    page.title = "DeepSeek Agent"
    page.padding = 0; page.spacing = 0

    # 图标（嵌入 base64，确保任务栏和标题栏图标一致）
    try:
        page.icon = f"data:image/x-icon;base64,{ICON_B64}"
    except: pass

    state = {"api_key": "", "model": "deepseek-v4-flash", "theme_mode": ft.ThemeMode.LIGHT, "current_session": "", "messages": [], "current_path": os.getcwd(), "system_prompt": get_default_system_prompt(), "quick_prompts": [("总结", "请总结一下以上内容"), ("翻译", "请翻译成中文"), ("代码审查", "请审查以下代码")], "client": None, "max_context_tokens": 32000, "max_tokens": 2048, "temperature": 0.7, "agent_role": "通用助手", "agent_roles": dict(DEFAULT_AGENT_ROLES)}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                # 不从文件读取 API Key（隐私安全，仅内存持有）
                saved.pop("api_key", None)
                state.update(saved)
        except: pass

    try:
        state["client"] = OpenAI(api_key=state["api_key"], base_url="https://api.deepseek.com/v1") if OpenAI else None
    except: pass

    page.theme_mode = state["theme_mode"]
    colors = get_theme_colors(); page.bgcolor = colors["bg"]
    main_column = ft.Column(expand=True, spacing=0)
    page.add(main_column)
    current_view = {"name": "chat"}

    def apply_theme():
        colors = get_theme_colors()
        page.theme_mode = state["theme_mode"]; page.bgcolor = colors["bg"]
        if current_view["name"] == "chat": show_chat()
        elif current_view["name"] == "files": show_files()
        elif current_view["name"] == "sessions": show_sessions()
        elif current_view["name"] == "settings": show_settings()

    def toggle_theme(e):
        state["theme_mode"] = ft.ThemeMode.DARK if state["theme_mode"] == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        apply_theme()

    def save_current_state():
        if state["current_session"] and state["messages"]:
            save_msgs(state["current_session"], state["messages"])

    def nav_bar(colors):
        def nav_to(view_name):
            save_current_state()
            if view_name == "chat": show_chat()
            elif view_name == "files": show_files()
            elif view_name == "sessions": show_sessions()
            elif view_name == "settings": show_settings()
        return ft.Container(padding=6, bgcolor=colors["card"], content=ft.Row([ft.Button("聊天", on_click=lambda e: nav_to("chat"), expand=True), ft.Button("文件", on_click=lambda e: nav_to("files"), expand=True), ft.Button("会话", on_click=lambda e: nav_to("sessions"), expand=True), ft.Button("设置", on_click=lambda e: nav_to("settings"), expand=True)], spacing=5))

    # ========== 聊天页面 ==========
    def show_chat():
        current_view["name"] = "chat"
        colors = get_theme_colors()
        main_column.controls.clear()

        ref_files = []
        ref_chips = ft.Row(wrap=True, spacing=3)

        def update_ref_chips():
            ref_chips.controls.clear()
            for f in ref_files:
                lines = f["content"].count('\n') + 1
                ref_chips.controls.append(ft.Container(content=ft.Row([ft.Column([ft.Text(f"{os.path.basename(f['path'])} ({lines}行)", size=11, weight=ft.FontWeight.W_500), ft.Text(f["path"], size=9, color=colors["text_hint"])], spacing=0, expand=True), ft.IconButton(ft.Icons.CLOSE, icon_size=14, on_click=lambda e, p=f["path"]: remove_ref_file(p))], spacing=2), bgcolor=colors["card"], border_radius=8, padding=ft.padding.symmetric(4, 8)))
            ref_chips.update()

        def add_ref_file(path):
            try:
                content = read_file(path)
                if content.startswith(("文件过大", "文件不存在", "无权限读取", "读取失败")):
                    show_toast(page, content)
                    return
                if not any(f["path"] == path for f in ref_files):
                    ref_files.append({"path": path, "content": content})
                    update_ref_chips()
                    lines = content.count('\n') + 1
                    chars = len(content)
                    show_toast(page, f"已引用: {os.path.basename(path)} ({lines}行, {chars}字符)")
            except Exception as ex: show_toast(page, f"添加失败: {ex}")

        def remove_ref_file(path):
            ref_files[:] = [f for f in ref_files if f["path"] != path]
            update_ref_chips()

        layout = get_layout_size(page)
        chat_list = ft.ListView(expand=True, spacing=layout["spacing"], padding=layout["padding"])

        def resend_message(idx):
            if not state["api_key"]:
                show_toast(page, "请先在设置页面配置 API Key")
                return
            if not check_api_key(state["api_key"]):
                show_toast(page, "API Key 格式不正确，请检查")
                return
            if not state["client"]: show_toast(page, "请先保存设置以初始化连接"); return
            msg = state["messages"][idx]
            if msg["role"] != "user": return
            state["messages"] = state["messages"][:idx+1]
            save_msgs(state["current_session"], state["messages"])
            update_session_info()
            load_chat()
            t = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Row([ft.ProgressRing(width=16, height=16), ft.Text("重新发送中...", size=13)], spacing=8))
            chat_list.controls.append(t); page.update()

            def do_resend():
                try:
                    msg_list = []
                    for m in state["messages"]:
                        if m["role"] == "system":
                            msg_list.append(m)
                        else:
                            content = m.get("content", "")
                            ref_files_for_api = m.get("ref_files", [])
                            if ref_files_for_api:
                                ref_parts = []
                                for f in ref_files_for_api:
                                    ref_parts.append(f"【引用文件】{f['path']}\n{f['content']}")
                                ref_content = "\n\n".join(ref_parts) + "\n\n"
                                content = ref_content + content
                            msg_list.append({"role": m["role"], "content": content})
                    resp = state["client"].chat.completions.create(model=state["model"], messages=msg_list, stream=True, tools=TOOLS, tool_choice="auto", max_tokens=state.get("max_tokens", 2048), temperature=state.get("temperature", 0.7))
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    ac = ""
                    text_control = ft.Text("", size=13, color=colors["text"])
                    msg_container = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Column([ft.Text("AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"]), text_control], spacing=4))
                    chat_list.controls.append(msg_container)
                    for c in resp:
                        if c.choices and c.choices[0].delta.content:
                            ac += c.choices[0].delta.content
                            text_control.value = ac
                            chat_list.scroll_to(offset=0, duration=0)
                            page.update()
                    if ac:
                        state["messages"].append({"role": "assistant", "content": ac})
                    else:
                        r = state["client"].chat.completions.create(model=state["model"], messages=msg_list, tools=TOOLS, tool_choice="auto", max_tokens=state.get("max_tokens", 2048), temperature=state.get("temperature", 0.7))
                        state["messages"].append({"role": "assistant", "content": r.choices[0].message.content or ""})
                    save_msgs(state["current_session"], state["messages"])
                    load_chat()
                except Exception as ex:
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    error_msg = str(ex).lower()
                    if "401" in error_msg or "unauthorized" in error_msg or "invalid api key" in error_msg:
                        error_text = "API Key 无效或已过期，请检查设置"
                    elif "429" in error_msg or "rate limit" in error_msg:
                        error_text = "请求过于频繁，请稍后再试"
                    elif "500" in error_msg or "503" in error_msg:
                        error_text = "服务器错误，请稍后再试"
                    else:
                        error_text = f"错误: {ex}"
                    chat_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["error_msg"], content=ft.Text(error_text, color=ft.Colors.RED, size=12)))
                    page.update()
            import threading
            threading.Thread(target=do_resend, daemon=True).start()

        def edit_message(idx):
            msg = state["messages"][idx]
            if msg["role"] == "system": return
            content_input = ft.TextField(label="消息内容", value=msg.get("content", ""), multiline=True, min_lines=3, max_lines=10, expand=True, border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("编辑消息"), content=ft.Column([content_input], expand=True), actions=[ft.TextButton("取消"), ft.Button("保存"), ft.Button("保存并重新发送")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: do_edit(idx, False)
            dlg.actions[2].on_click = lambda e: do_edit(idx, True)
            def do_edit(idx, resend=False):
                new_content = content_input.value.strip()
                if not new_content: show_toast(page, "内容不能为空"); return
                state["messages"][idx]["content"] = new_content
                save_msgs(state["current_session"], state["messages"])
                dlg.open = False
                page.update()
                load_chat()
                show_toast(page, "消息已编辑")
                if resend:
                    resend_message(idx)
            show_dialog(page, dlg)

        def delete_message(idx):
            msg = state["messages"][idx]
            if msg["role"] == "system": return
            def do_delete():
                state["messages"].pop(idx)
                save_msgs(state["current_session"], state["messages"])
                load_chat()
                show_toast(page, "消息已删除")
            confirm_dialog(page, "确认删除", "确定要删除这条消息吗？", do_delete)

        def make_message_popup(idx, is_user):
            items = []
            if is_user:
                items.append(ft.PopupMenuItem(content="编辑", on_click=lambda e: edit_message(idx)))
                items.append(ft.PopupMenuItem(content="重新发送", on_click=lambda e: resend_message(idx)))
            items.append(ft.PopupMenuItem(content="删除", on_click=lambda e: delete_message(idx)))
            items.append(ft.PopupMenuItem(content="复制内容", on_click=lambda e: (page.set_clipboard(state["messages"][idx].get("content", "")) or show_toast(page, "已复制"))))
            return ft.PopupMenuButton(items=items, icon=ft.Icons.MORE_VERT, icon_size=14, tooltip="操作")

        def load_chat():
            chat_list.controls.clear()
            pair_idx = 0
            for idx, msg in enumerate(state["messages"]):
                if msg["role"] == "system": continue
                is_user = msg["role"] == "user"
                content = msg.get("content", "")
                content_controls = format_content(content, colors)

                if is_user:
                    pair_idx += 1
                    jump_btn = ft.IconButton(ft.Icons.LINK, icon_size=12, on_click=lambda e, i=idx: chat_list.scroll_to(offset=0, duration=0), tooltip=f"跳转 #{pair_idx}")
                else:
                    jump_btn = None

                ref_files = msg.get("ref_files", [])
                if ref_files:
                    ref_chips_for_display = ft.Row(wrap=True, spacing=3)
                    for rf in ref_files:
                        ref_chips_for_display.controls.append(ft.Container(content=ft.Row([ft.Icon(ft.Icons.FILE_OPEN), ft.Text(os.path.basename(rf["path"]), size=11)]), bgcolor=colors["primary"], border_radius=4, padding=ft.padding.symmetric(2, 6)))
                    content_controls.insert(0, ft.Row([ft.Text("引用文件:", size=11, weight=ft.FontWeight.W_500), ref_chips_for_display], spacing=4, wrap=True))

                message_row = ft.Row([ft.Column([ft.Text("你" if is_user else "AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"])] + content_controls, spacing=4, expand=True), make_message_popup(idx, is_user)], spacing=5)
                if jump_btn:
                    message_row.controls.insert(0, jump_btn)
                chat_list.controls.append(ft.Container(padding=10, border_radius=8, bgcolor=colors["user_msg"] if is_user else colors["assistant_msg"], content=message_row))
            update_session_info()
            page.update()

        def format_content(content, colors):
            parts = re.split(r'(```[\w]*\n?[\s\S]*?```)', content)
            controls = []
            for part in parts:
                if part.startswith("```"):
                    lang = part.split('\n')[0].replace('`', '').strip()
                    code = '\n'.join(part.split('\n')[1:-1])
                    controls.append(ft.Container(padding=8, border_radius=5, bgcolor=colors["code_bg"], content=ft.Column([ft.Row([ft.Text(lang or "code", size=10, weight=ft.FontWeight.W_500), ft.IconButton(ft.Icons.COPY, icon_size=14, on_click=lambda e, c=code: page.set_clipboard(c) or show_toast(page, "已复制"))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(code, size=12, font_family="Consolas")], spacing=4)))
                elif part.strip(): controls.append(ft.Text(part.strip(), size=13, color=colors["text"]))
            return controls or [ft.Text("", size=13)]

        def send_chat(e):
            if not state["api_key"]:
                show_toast(page, "请先在设置页面配置 API Key")
                return
            if not check_api_key(state["api_key"]):
                show_toast(page, "API Key 格式不正确，请检查")
                return
            if not state["current_session"]: show_toast(page, "请先选择或新建会话"); return
            if not state["client"]: show_toast(page, "请先保存设置以初始化连接"); return
            text = chat_input.value.strip()
            if not text: return
            send_btn.disabled = True
            send_btn.text = "发送中..."
            page.update()

            ref_files_data = []
            if ref_files:
                ref_files_data = [{"path": f["path"], "content": f["content"]} for f in ref_files]

            chat_input.value = ""
            state["messages"].append({"role": "user", "content": text, "ref_files": ref_files_data})
            max_tk = state.get("max_context_tokens", 32000)
            while calc_tokens_count(state["messages"]) > max_tk and len(state["messages"]) > 2:
                for i in range(1, len(state["messages"])):
                    if state["messages"][i]["role"] != "system":
                        state["messages"].pop(i); break
            save_msgs(state["current_session"], state["messages"])
            update_session_info()
            load_chat()
            t = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Row([ft.ProgressRing(width=16, height=16), ft.Text("思考中...", size=13)], spacing=8))
            chat_list.controls.append(t); page.update()

            def do_send():
                try:
                    msg_list = []
                    for m in state["messages"]:
                        if m["role"] == "system":
                            msg_list.append(m)
                        else:
                            content = m.get("content", "")
                            ref_files_for_api = m.get("ref_files", [])
                            if ref_files_for_api:
                                ref_parts = []
                                for f in ref_files_for_api:
                                    ref_parts.append(f"【引用文件】{f['path']}\n{f['content']}")
                                ref_content = "\n\n".join(ref_parts) + "\n\n"
                                content = ref_content + content
                            msg_list.append({"role": m["role"], "content": content})
                    resp = state["client"].chat.completions.create(model=state["model"], messages=msg_list, stream=True, tools=TOOLS, tool_choice="auto", max_tokens=state.get("max_tokens", 2048), temperature=state.get("temperature", 0.7))
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    ac = ""
                    text_control = ft.Text("", size=13, color=colors["text"])
                    msg_container = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Column([ft.Text("AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"]), text_control], spacing=4))
                    chat_list.controls.append(msg_container)
                    for c in resp:
                        if c.choices and c.choices[0].delta.content:
                            ac += c.choices[0].delta.content
                            text_control.value = ac
                            chat_list.scroll_to(offset=0, duration=0)
                            page.update()
                    if ac:
                        state["messages"].append({"role": "assistant", "content": ac})
                    else:
                        r = state["client"].chat.completions.create(model=state["model"], messages=msg_list, tools=TOOLS, tool_choice="auto", max_tokens=state.get("max_tokens", 2048), temperature=state.get("temperature", 0.7))
                        state["messages"].append({"role": "assistant", "content": r.choices[0].message.content or ""})
                    save_msgs(state["current_session"], state["messages"])
                    load_chat()
                except Exception as ex:
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    error_msg = str(ex).lower()
                    if "401" in error_msg or "unauthorized" in error_msg or "invalid api key" in error_msg:
                        error_text = "API Key 无效或已过期，请检查设置"
                    elif "429" in error_msg or "rate limit" in error_msg:
                        error_text = "请求过于频繁，请稍后再试"
                    elif "500" in error_msg or "503" in error_msg:
                        error_text = "服务器错误，请稍后再试"
                    else:
                        error_text = f"错误: {ex}"
                    chat_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["error_msg"], content=ft.Text(error_text, color=ft.Colors.RED, size=12)))
                    page.update()
                finally:
                    send_btn.disabled = False
                    send_btn.text = "发送"
                    page.update()

            import threading
            threading.Thread(target=do_send, daemon=True).start()

        quick_prompts_row = ft.Row(wrap=True, spacing=4)
        def load_quick_prompts():
            quick_prompts_row.controls.clear()
            for label, template in state["quick_prompts"]:
                quick_prompts_row.controls.append(ft.TextButton(label, on_click=lambda e, t=template: setattr(chat_input, 'value', t) or chat_input.update(), style=ft.ButtonStyle(padding=5)))
            page.update()

        chat_input = ft.TextField(hint_text="输入消息...", expand=True, multiline=True, min_lines=1, max_lines=4, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"]))
        send_btn = ft.Button("发送", on_click=send_chat, width=70)
        session_info_bar = ft.Text("", size=11, color=colors["text_hint"])

        def update_session_info():
            tokens = calc_tokens_count(state["messages"])
            chars = sum(len(m.get("content", "")) for m in state["messages"])
            session_info_bar.value = f"{state['current_session']} | ~{tokens} tokens | {chars} 字" if state['current_session'] else "请选择会话"
            session_info_bar.update()
        header = ft.Container(padding=10, bgcolor=colors["card"], content=ft.Row([ft.Column([ft.Text("DeepSeek Agent", size=17, weight=ft.FontWeight.BOLD, color=colors["text"]), session_info_bar], spacing=0), ft.Row([ft.IconButton(ft.Icons.DELETE, icon_size=16, tooltip="清空", on_click=clear_chat), ft.IconButton(ft.Icons.DELETE_FOREVER, icon_size=16, tooltip="删除会话", on_click=del_current_session), ft.IconButton(ft.Icons.DARK_MODE, icon_size=16, tooltip="切换主题", on_click=toggle_theme)], spacing=2)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))
        context_panel = ft.Container(width=layout["chat_width"], padding=8, bgcolor=colors["card"], content=ft.Column([ft.Text("文件引用", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), ft.Button("选择文件", on_click=lambda e: pick_file_dialog(), width=120, height=30)], spacing=5))

        def pick_file_dialog():
            def on_result(e):
                if e.files: add_ref_file(e.files[0].path)
            picker = ft.FilePicker(on_result=on_result)
            page.overlay.append(picker); page.update(); picker.pick_files()

        main_column.controls.extend([header, ft.Row([context_panel, ft.Container(content=chat_list, expand=True)], expand=True, spacing=0), ft.Container(padding=layout["padding"], content=ft.Column([quick_prompts_row, ref_chips, ft.Row([chat_input, send_btn], spacing=layout["spacing"])], spacing=layout["spacing"])), nav_bar(colors)])
        page.update()
        update_session_info()
        load_quick_prompts()
        load_chat()

    def clear_chat():
        def do_clear():
            state["messages"] = [{"role": "system", "content": state["system_prompt"]}]
            save_msgs(state["current_session"], state["messages"])
            show_toast(page, "已清空"); show_chat()
        confirm_dialog(page, "确认清空", "确定要清空当前对话吗？", do_clear)

    def del_current_session():
        if not state["current_session"]: show_toast(page, "请先选择一个会话"); return
        name = state["current_session"]
        def do_delete():
            delete_session(name); sessions = list_sessions()
            if sessions: state["current_session"] = sessions[0]; state["messages"] = load_msgs(sessions[0])
            else: state["current_session"] = ""; state["messages"] = []
            show_toast(page, f"已删除: {name}"); show_chat()
        confirm_dialog(page, "确认删除", f"确定删除会话「{name}」？", do_delete)

    # ========== 文件页面 ==========
    def show_files():
        current_view["name"] = "files"
        colors = get_theme_colors()
        main_column.controls.clear()
        layout = get_layout_size(page)
        file_path_input = ft.TextField(hint_text="输入路径后回车", expand=True, value=state["current_path"], border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"]), on_submit=lambda e: load_files(file_path_input.value.strip() or os.getcwd()))
        file_list = ft.ListView(expand=True, spacing=1)
        file_content = ft.TextField(multiline=True, expand=True, read_only=False, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"]))
        file_content_wrapper = ft.Column([], spacing=5, expand=True)
        file_preview = ft.Container(expand=True, visible=False)
        current_file_path = ""
        def is_image(path): return os.path.splitext(path)[1].lower() in IMAGE_EXTS
        def is_binary(path): return os.path.splitext(path)[1].lower() in BINARY_EXTS

        def make_file_popup(filename, fp):
            items = []
            if is_image(fp): items.append(ft.PopupMenuItem(content="打开图片", on_click=lambda e: open_file(fp)))
            elif is_binary(fp):
                items.append(ft.PopupMenuItem(content="二进制文件", disabled=True))
                items.append(ft.PopupMenuItem(content="打开所在文件夹", on_click=lambda e: os.startfile(os.path.dirname(fp))))
            else: items.append(ft.PopupMenuItem(content="打开", on_click=lambda e: open_file(fp)))
            items.append(ft.PopupMenuItem(content="复制路径", on_click=lambda e: page.set_clipboard(fp) or show_toast(page, "已复制路径")))
            items.append(ft.PopupMenuItem(content="引用到聊天", on_click=lambda e: add_ref_file(fp)))
            return ft.PopupMenuButton(items=items, icon=ft.Icons.MORE_VERT, icon_size=16)

        def make_file_row(filename, fp, icon, icon_color):
            return ft.Container(padding=6, border_radius=4, bgcolor=colors["card"], content=ft.Row([ft.Icon(icon, size=16, color=icon_color), ft.Text(filename, size=13, color=colors["text"], expand=True), make_file_popup(filename, fp)], spacing=5), on_click=lambda e, p=fp: open_file(p))

        def load_files(path=""):
            nonlocal current_file_path
            current_file_path = ""; file_content.value = ""; file_preview.visible = False
            file_list.controls.clear()
            path = path or state["current_path"]
            if not os.path.isdir(path): show_toast(page, "无效目录: " + path); return
            state["current_path"] = path; file_path_input.value = path
            try: items = os.listdir(path)
            except: items = []
            dirs = sorted([d for d in items if os.path.isdir(os.path.join(path, d))], key=str.lower)
            files = sorted([f for f in items if os.path.isfile(os.path.join(path, f))], key=str.lower)
            if path != os.path.abspath(os.sep):
                file_list.controls.append(ft.Container(padding=6, border_radius=4, bgcolor=colors["card"], content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN, size=16, color=ft.Colors.AMBER), ft.Text("..", size=13, color=colors["primary"])], spacing=5), on_click=lambda e: load_files(os.path.dirname(path))))
            for d in dirs:
                fp = os.path.join(path, d)
                file_list.controls.append(ft.Container(padding=6, border_radius=4, bgcolor=colors["card"], content=ft.Row([ft.Icon(ft.Icons.FOLDER, size=16, color=ft.Colors.AMBER), ft.Text(d, size=13, color=colors["text"])], spacing=5), on_click=lambda e, p=fp: load_files(p)))
            for f in files:
                fp = os.path.join(path, f)
                ext = os.path.splitext(f)[1].lower()
                if is_image(fp): icon, icon_color = ft.Icons.IMAGE, ft.Colors.GREEN
                elif is_binary(fp): icon, icon_color = ft.Icons.INSTALL_DESKTOP, ft.Colors.GREY
                else: icon, icon_color = ft.Icons.DESCRIPTION, colors["primary"]
                file_list.controls.append(make_file_row(f, fp, icon, icon_color))
            page.update()

        def open_file(path):
            nonlocal current_file_path
            current_file_path = path
            file_preview.visible = False; file_content.visible = True
            if is_image(path):
                try:
                    with open(path, "rb") as f: img_data = base64.b64encode(f.read()).decode()
                    file_preview.content = ft.Image(src_base64=img_data, fit=ft.ImageFit.CONTAIN, expand=True)
                    file_preview.visible = True; file_content.visible = False
                except Exception as ex: show_toast(page, f"图片加载失败: {ex}")
                page.update(); return
            if is_binary(path):
                size = os.path.getsize(path)
                file_content.value = f"[二进制文件] {os.path.basename(path)} ({size:,} 字节)\n此类型文件无法以文本方式预览。"
                file_content.read_only = True; page.update(); return
            file_content.read_only = False
            content = read_file(path)
            if content.startswith(("无法读取", "错误")): show_toast(page, content); file_content.value = ""
            else: file_content.value = content
            page.update()

        def save_current_file(e):
            nonlocal current_file_path
            if not current_file_path: show_toast(page, "请先选择文件"); return
            if file_content.read_only: page.set_clipboard(current_file_path); show_toast(page, "已复制文件路径"); return
            result = write_file(current_file_path, file_content.value)
            if result is True: show_toast(page, "已保存")
            else: show_toast(page, f"保存失败: {result}")

        save_copy_btn = ft.Button("保存", on_click=save_current_file)
        file_content_wrapper.controls = [ft.Row([save_copy_btn, ft.Text("", expand=True), ft.IconButton(ft.Icons.COPY, icon_size=16, tooltip="复制内容", on_click=lambda e: (page.set_clipboard(file_content.value) or show_toast(page, "已复制")) if file_content.value else None)], spacing=8), file_content]
        main_column.controls.extend([ft.Container(padding=10, bgcolor=colors["card"], content=ft.Row([ft.Icon(ft.Icons.FOLDER, color=colors["primary"]), file_path_input], spacing=8)), ft.Row([ft.Container(content=ft.Column([ft.Text("文件列表", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), file_list], spacing=5), width=layout["file_width"], padding=8), ft.Container(content=ft.Column([file_preview, file_content_wrapper], spacing=5), expand=True, padding=8)], expand=True, spacing=0), nav_bar(colors)])
        page.update()
        load_files(state["current_path"])

    # ========== 会话页面 ==========
    def show_sessions():
        current_view["name"] = "sessions"
        colors = get_theme_colors()
        main_column.controls.clear()

        search_input = ft.TextField(hint_text="搜索会话...", expand=True, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"]), on_submit=lambda e: load_sessions())
        session_list = ft.ListView(expand=True, spacing=1)
        empty_hint = ft.Container(padding=40, content=ft.Column([ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=48, color=colors["text_hint"]), ft.Text("暂无会话", size=15, color=colors["text_hint"], weight=ft.FontWeight.W_500), ft.Text("点击下方按钮新建会话", size=12, color=colors["text_hint"])], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10))

        def session_info(name):
            path = session_path(name)
            if os.path.exists(path):
                mtime = os.path.getmtime(path)
                try:
                    with open(path, "r", encoding="utf-8") as f: msgs = json.load(f)
                    msg_count = sum(1 for m in msgs if m.get("role") != "system")
                    tk = calc_tokens_count(msgs)
                    time_str = time.strftime("%m-%d %H:%M", time.localtime(mtime))
                    return msg_count, time_str, tk
                except: pass
            return 0, "", 0

        def show_new_session_dialog(e):
            new_name_input = ft.TextField(label="会话名称", hint_text="输入名称", autofocus=True, border=ft.InputBorder.OUTLINE, width=300)
            dlg = ft.AlertDialog(title=ft.Text("新建会话"), content=ft.Column([new_name_input], spacing=10), actions=[ft.TextButton("取消"), ft.Button("创建")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: do_create()
            def do_create():
                name = new_name_input.value.strip()
                if not name: show_toast(page, "名称不能为空"); return
                dlg.open = False; page.update()
                state["current_session"] = name
                state["messages"] = [{"role": "system", "content": state["system_prompt"]}]
                save_msgs(name, state["messages"]); show_toast(page, f"已创建: {name}"); show_chat()
            show_dialog(page, dlg)

        def make_delete_btn(session_name):
            def handler(e):
                dlg = ft.AlertDialog(title=ft.Text("确认删除"), content=ft.Text(f"确定要删除会话「{session_name}」吗？"), actions=[ft.TextButton("取消"), ft.Button("删除", bgcolor=ft.Colors.RED)])
                dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
                dlg.actions[1].on_click = lambda e, n=session_name: do_delete(n)
                def do_delete(n):
                    dlg.open = False; page.update(); gc.collect()
                    delete_session(n); gc.collect()
                    sessions = list_sessions()
                    if state["current_session"] == n:
                        if sessions: state["current_session"] = sessions[0]; state["messages"] = load_msgs(sessions[0])
                        else: state["current_session"] = ""; state["messages"] = []
                    show_toast(page, f"已删除: {n}"); load_sessions()
                    if not sessions: show_chat()
                show_dialog(page, dlg)
            return handler

        def make_select_handler(session_name):
            def handler(e):
                state["current_session"] = session_name
                state["messages"] = load_msgs(session_name)
                show_toast(page, f"已切换到: {session_name}"); show_chat()
            return handler

        def make_rename_btn(session_name):
            def handler(e):
                rename_input = ft.TextField(label="新名称", value=session_name, autofocus=True, border=ft.InputBorder.OUTLINE, width=300)
                dlg = ft.AlertDialog(title=ft.Text("重命名会话"), content=ft.Column([rename_input], spacing=10), actions=[ft.TextButton("取消"), ft.Button("确定")])
                dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
                dlg.actions[1].on_click = lambda e: do_rename()
                def do_rename():
                    new_name = rename_input.value.strip()
                    if not new_name: show_toast(page, "名称不能为空"); return
                    if new_name == session_name: dlg.open = False; page.update(); return
                    if new_name in list_sessions(): show_toast(page, "名称已存在"); return
                    dlg.open = False; page.update()
                    os.rename(os.path.join(SESSION_DIR, f"{session_name}.json"), os.path.join(SESSION_DIR, f"{new_name}.json"))
                    if state["current_session"] == session_name: state["current_session"] = new_name
                    show_toast(page, f"已重命名为: {new_name}"); load_sessions()
                show_dialog(page, dlg)
            return handler

        def load_sessions():
            session_list.controls.clear()
            sessions = list_sessions()
            keyword = search_input.value.strip().lower() if search_input.value else ""
            if keyword: sessions = [s for s in sessions if keyword in s.lower()]
            if not sessions: session_list.controls.append(empty_hint)
            else:
                for name in sessions:
                    is_current = name == state["current_session"]
                    cnt, ts, tk = session_info(name)
                    parts = []
                    if cnt: parts.append(f"{cnt}条")
                    if ts: parts.append(ts)
                    if tk: parts.append(f"~{tk}tokens")
                    info_text = " · ".join(parts) if parts else ""
                    session_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["selected"] if is_current else colors["card"], content=ft.Row([ft.Column([ft.Row([ft.Text(name, size=13, color=colors["text"], weight=ft.FontWeight.BOLD if is_current else ft.FontWeight.NORMAL), ft.Text("(当前)", size=11, color=ft.Colors.GREEN) if is_current else ft.Text("")], spacing=4), ft.Text(info_text, size=10, color=colors["text_hint"])], spacing=1, expand=True), ft.IconButton(ft.Icons.EDIT, icon_size=15, tooltip="重命名", on_click=make_rename_btn(name)), ft.IconButton(ft.Icons.DELETE, icon_size=15, tooltip="删除", on_click=make_delete_btn(name))], spacing=3), on_click=make_select_handler(name)))
            page.update()

        def delete_all_sessions(e):
            dlg = ft.AlertDialog(title=ft.Text("确认删除全部"), content=ft.Text("确定要删除所有会话吗？此操作不可恢复。"), actions=[ft.TextButton("取消"), ft.Button("全部删除", bgcolor=ft.Colors.RED)])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: do_delete_all()
            def do_delete_all():
                dlg.open = False; page.update()
                for s in list_sessions(): delete_session(s)
                state["current_session"] = ""; state["messages"] = []
                show_toast(page, "已删除全部会话"); load_sessions(); show_chat()
            show_dialog(page, dlg)

        main_column.controls.extend([ft.Container(padding=12, content=ft.Column([ft.Text("会话管理", size=17, weight=ft.FontWeight.BOLD, color=colors["text"]), ft.Row([ft.Button("新建会话", on_click=show_new_session_dialog, icon=ft.Icons.ADD, width=120), ft.Button("全部删除", on_click=delete_all_sessions, icon=ft.Icons.DELETE_SWEEP, width=120, bgcolor=ft.Colors.RED_200)], spacing=8), search_input], spacing=10)), ft.Container(content=session_list, padding=8, expand=True), nav_bar(colors)])
        page.update()
        load_sessions()

    # ========== 设置页面 ==========
    def show_settings():
        current_view["name"] = "settings"
        colors = get_theme_colors()
        main_column.controls.clear()

        api_key_input = ft.TextField(label="API Key", password=True, value=state["api_key"], expand=True, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_text="sk-...", hint_style=ft.TextStyle(color=colors["text_hint"]))
        model_dropdown = ft.Dropdown(label="模型", value=state["model"], options=[ft.dropdown.Option("deepseek-v4-flash"), ft.dropdown.Option("deepseek-v4-pro")], border=ft.InputBorder.OUTLINE, color=colors["text"])
        
        # Agent 角色/场景（可自定义）
        role_names = list(state.get("agent_roles", DEFAULT_AGENT_ROLES).keys())
        role_dropdown = ft.Dropdown(label="Agent 角色/场景", value=state.get("agent_role", "通用助手"), options=[ft.dropdown.Option(k) for k in role_names], border=ft.InputBorder.OUTLINE, color=colors["text"])
        role_list = ft.ListView(expand=True, spacing=5)
        def on_role_change(e):
            role = role_dropdown.value
            state["agent_role"] = role
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            prompt = roles.get(role, get_default_system_prompt())
            system_prompt_input.value = prompt
            page.update()
        role_dropdown.on_change = on_role_change

        def load_role_list_ui():
            role_list.controls.clear()
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            for i, (rname, rprompt) in enumerate(roles.items()):
                role_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["card"], content=ft.Row([ft.Column([ft.Text(f"{i+1}. {rname}", size=13, weight=ft.FontWeight.W_500, color=colors["text"]), ft.Text(rprompt[:50] + "..." if len(rprompt) > 50 else rprompt, size=11, color=colors["text_hint"])], spacing=2, expand=True), ft.IconButton(ft.Icons.EDIT, icon_size=16, tooltip="编辑", on_click=lambda e, idx=i: edit_role(idx)), ft.IconButton(ft.Icons.DELETE, icon_size=16, tooltip="删除", on_click=lambda e, idx=i: delete_role(idx))], spacing=5)))
            page.update()

        def edit_role(idx):
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            rname = list(roles.keys())[idx]
            rprompt = roles[rname]
            ni = ft.TextField(label="名称", value=rname, border=ft.InputBorder.OUTLINE)
            pi = ft.TextField(label="提示词", value=rprompt, multiline=True, min_lines=3, border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("编辑角色"), content=ft.Column([ni, pi], spacing=10), actions=[ft.TextButton("取消"), ft.Button("保存")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and pi.value.strip() and (
                    (ni.value.strip() != rname and ni.value.strip() not in roles) or ni.value.strip() == rname
                ) and (
                    roles.pop(rname, None), roles.__setitem__(ni.value.strip(), pi.value.strip())
                ) or True) or update_role_ui())
            show_dialog(page, dlg)

        def delete_role(idx):
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            rname = list(roles.keys())[idx]
            if rname == "通用助手": show_toast(page, "通用助手不能删除"); return
            roles.pop(rname, None)
            if state["agent_role"] == rname:
                state["agent_role"] = "通用助手"
                role_dropdown.value = "通用助手"
            update_role_ui()

        def add_role(e):
            ni = ft.TextField(label="名称", hint_text="例如: 辩论手", border=ft.InputBorder.OUTLINE)
            pi = ft.TextField(label="提示词", hint_text="你是一位...", multiline=True, min_lines=3, border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("新增角色"), content=ft.Column([ni, pi], spacing=10), actions=[ft.TextButton("取消"), ft.Button("添加")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and pi.value.strip() and ni.value.strip() not in state.setdefault("agent_roles", {}) and
                 state["agent_roles"].__setitem__(ni.value.strip(), pi.value.strip()) or True) or update_role_ui())
            show_dialog(page, dlg)

        def update_role_ui():
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            role_dropdown.options = [ft.dropdown.Option(k) for k in roles.keys()]
            load_role_list_ui()
            page.update()

        system_prompt_input = ft.TextField(label="系统提示词 (System Prompt)", value=state["system_prompt"], expand=True, multiline=True, min_lines=4, max_lines=8, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_text="设置AI助手的默认行为和约束...", hint_style=ft.TextStyle(color=colors["text_hint"]))
        quick_prompts_list = ft.ListView(expand=True, spacing=5)

        # 上下文长度控制
        context_slider = ft.Slider(min=4000, max=64000, divisions=15, value=state.get("max_context_tokens", 32000), label="{value}")
        context_label = ft.Text(f"最大上下文: {state.get('max_context_tokens', 32000)} tokens", size=12, color=colors["text"])
        def on_context_change(e):
            v = int(context_slider.value)
            state["max_context_tokens"] = v
            context_label.value = f"最大上下文: {v} tokens"
            page.update()
        context_slider.on_change = on_context_change

        # 回复长度控制
        max_tokens_slider = ft.Slider(min=100, max=16384, divisions=32, value=state.get("max_tokens", 2048), label="{value}")
        max_tokens_label = ft.Text(f"最大回复长度: {state.get('max_tokens', 2048)} tokens", size=12, color=colors["text"])
        def on_max_tokens_change(e):
            v = int(max_tokens_slider.value)
            state["max_tokens"] = v
            max_tokens_label.value = f"最大回复长度: {v} tokens"
            page.update()
        max_tokens_slider.on_change = on_max_tokens_change

        # 深度思考开关
        temperature_switch = ft.Switch(value=state.get("temperature", 0.7) > 0.5, label="深度思考模式", label_position=ft.LabelPosition.RIGHT)
        temperature_label = ft.Text(f"当前温度: {state.get('temperature', 0.7)}", size=12, color=colors["text"])
        def on_temperature_change(e):
            v = 0.9 if temperature_switch.value else 0.3
            state["temperature"] = v
            temperature_label.value = f"当前温度: {v}"
            page.update()
        temperature_switch.on_change = on_temperature_change

        def load_quick_prompts_ui():
            quick_prompts_list.controls.clear()
            for i, (label, template) in enumerate(state["quick_prompts"]):
                quick_prompts_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["card"], content=ft.Row([ft.Column([ft.Text(f"{i+1}. {label}", size=13, weight=ft.FontWeight.W_500, color=colors["text"]), ft.Text(template[:50] + "..." if len(template) > 50 else template, size=11, color=colors["text_hint"])], spacing=2, expand=True), ft.IconButton(ft.Icons.EDIT, icon_size=16, tooltip="编辑", on_click=lambda e, idx=i: edit_quick_prompt(idx)), ft.IconButton(ft.Icons.DELETE, icon_size=16, tooltip="删除", on_click=lambda e, idx=i: delete_quick_prompt(idx))], spacing=5)))
            page.update()

        def edit_quick_prompt(idx):
            label, template = state["quick_prompts"][idx]
            li = ft.TextField(label="名称", value=label, border=ft.InputBorder.OUTLINE)
            ti = ft.TextField(label="内容", value=template, multiline=True, min_lines=3, border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("编辑快捷提示词"), content=ft.Column([li, ti], spacing=10), actions=[ft.TextButton("取消"), ft.Button("保存")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (setattr(dlg, 'open', False) or state["quick_prompts"].__setitem__(idx, (li.value.strip(), ti.value.strip())) or load_quick_prompts_ui() or page.update())
            show_dialog(page, dlg)

        def delete_quick_prompt(idx):
            state["quick_prompts"].pop(idx); load_quick_prompts_ui()

        def add_quick_prompt(e):
            li = ft.TextField(label="名称", hint_text="例如: 总结", border=ft.InputBorder.OUTLINE)
            ti = ft.TextField(label="内容", hint_text="例如: 请总结一下", multiline=True, min_lines=3, border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("添加快捷提示词"), content=ft.Column([li, ti], spacing=10), actions=[ft.TextButton("取消"), ft.Button("添加")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (setattr(dlg, 'open', False) or (li.value.strip() and ti.value.strip() and state["quick_prompts"].append((li.value.strip(), ti.value.strip())) or True) or load_quick_prompts_ui() or page.update())
            show_dialog(page, dlg)

        balance_text = ft.Text("", size=13, color=colors["text"])
        balance_btn = ft.Button("查询余额")
        conn_status_text = ft.Text("", size=12, color=colors["text"])
        conn_test_btn = ft.Button("测试连接")

        def check_balance(e):
            if not state["api_key"]: show_toast(page, "请先设置 API Key"); return
            balance_text.value = "查询中..."; balance_btn.disabled = True; page.update()
            import threading
            def do_query():
                import requests
                try:
                    r = requests.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {state['api_key']}", "Accept": "application/json"}, timeout=10)
                    if r.status_code == 200:
                        data = r.json()
                        bal = data.get("balance_infos", [])
                        if bal:
                            total = sum(float(b.get("total_balance", 0)) for b in bal)
                            balance_text.value = f"余额: ¥{total:.2f}"; balance_text.color = ft.Colors.GREEN
                        else: balance_text.value = f"余额信息: {data}"
                    else: balance_text.value = f"查询失败: HTTP {r.status_code}"
                except Exception as ex: balance_text.value = f"查询失败: {ex}"
                balance_btn.disabled = False; page.update()
            threading.Thread(target=do_query, daemon=True).start()
        balance_btn.on_click = check_balance

        def test_connectivity(e):
            if not state["api_key"]: show_toast(page, "请先设置 API Key"); return
            if not state["client"]: show_toast(page, "请先保存设置"); return
            conn_status_text.value = "测试中..."; conn_status_text.color = ft.Colors.GREY; conn_test_btn.disabled = True; page.update()
            import threading
            def do_test():
                try:
                    r = state["client"].chat.completions.create(model=state["model"], messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                    if r:
                        conn_status_text.value = "✓ 连接成功"; conn_status_text.color = ft.Colors.GREEN
                    else:
                        conn_status_text.value = "✗ 连接失败"; conn_status_text.color = ft.Colors.RED
                except Exception as ex:
                    conn_status_text.value = f"✗ 连接失败: {str(ex)[:30]}"; conn_status_text.color = ft.Colors.RED
                conn_test_btn.disabled = False; page.update()
            threading.Thread(target=do_test, daemon=True).start()
        conn_test_btn.on_click = test_connectivity

        def clear_cache(e):
            """清理所有缓存会话数据"""
            dlg = ft.AlertDialog(title=ft.Text("确认清理缓存"), content=ft.Text(f"将删除 {len(list_sessions())} 个会话文件，保留设置。确定继续吗？"), actions=[ft.TextButton("取消"), ft.Button("清理会话", bgcolor=ft.Colors.RED)])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: do_clear_cache()
            def do_clear_cache():
                dlg.open = False; page.update()
                for s in list_sessions(): delete_session(s)
                state["current_session"] = ""; state["messages"] = []
                show_toast(page, "会话缓存已清理"); show_chat()
            show_dialog(page, dlg)

        def clear_all_data(e):
            """删除所有本地数据（会话+设置+密钥）"""
            dlg = ft.AlertDialog(title=ft.Text("确认清除所有数据"), content=ft.Text("将删除全部会话文件、设置文件、API Key 等所有本地数据。此操作不可恢复。"), actions=[ft.TextButton("取消"), ft.Button("全部清除", bgcolor=ft.Colors.RED)])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: do_clear_all()
            def do_clear_all():
                dlg.open = False; page.update()
                for s in list_sessions(): delete_session(s)
                if os.path.exists(SETTINGS_PATH):
                    try: os.remove(SETTINGS_PATH)
                    except: pass
                state["api_key"] = ""; state["client"] = None
                state["current_session"] = ""; state["messages"] = []
                state["system_prompt"] = get_default_system_prompt()
                state["agent_role"] = "通用助手"
                state["agent_roles"] = dict(DEFAULT_AGENT_ROLES)
                state["quick_prompts"] = [("总结", "请总结一下以上内容"), ("翻译", "请翻译成中文"), ("代码审查", "请审查以下代码")]
                show_toast(page, "已清除全部本地数据"); show_chat()
            show_dialog(page, dlg)

        def save_settings(e):
            state["api_key"] = api_key_input.value.strip()
            state["model"] = model_dropdown.value
            state["system_prompt"] = system_prompt_input.value.strip()
            state["agent_role"] = role_dropdown.value
            try:
                state["client"] = OpenAI(api_key=state["api_key"], base_url="https://api.deepseek.com/v1") if OpenAI else None
            except Exception as ex:
                show_toast(page, f"初始化客户端失败: {ex}")
                return

            save_data = {k: state[k] for k in ["model", "theme_mode", "system_prompt", "quick_prompts", "agent_role", "agent_roles", "max_context_tokens", "max_tokens", "temperature"]}
            save_data["theme_mode"] = save_data["theme_mode"].value if hasattr(save_data["theme_mode"], 'value') else save_data["theme_mode"]
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f: json.dump(save_data, f, ensure_ascii=False, indent=2)

            if state["api_key"]:
                import threading
                def test_connectivity():
                    try:
                        r = state["client"].chat.completions.create(model=state["model"], messages=[{"role": "user", "content": "ping"}], max_tokens=1)
                        if r: show_toast(page, "API 连接测试成功")
                    except Exception as ex:
                        show_toast(page, f"API 连接测试失败: {ex}")
                threading.Thread(target=test_connectivity, daemon=True).start()
            show_toast(page, "设置已保存")

        main_column.controls.extend([ft.Container(padding=15, content=ft.Column([
            ft.Text("设置", size=17, weight=ft.FontWeight.BOLD, color=colors["text"]),
            api_key_input, model_dropdown, role_dropdown,
            ft.Row([ft.Text("角色管理", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), ft.IconButton(ft.Icons.ADD, icon_size=18, tooltip="新增角色", on_click=add_role)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            role_list,
            ft.Row([balance_btn, balance_text], spacing=10),
            ft.Row([conn_test_btn, conn_status_text], spacing=10),
            ft.Text("系统提示词", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), system_prompt_input,
            context_label, context_slider,
            max_tokens_label, max_tokens_slider,
            ft.Row([temperature_switch, temperature_label], spacing=10),
            ft.Divider(),
            ft.Row([ft.Text("快捷提示词", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), ft.IconButton(ft.Icons.ADD, icon_size=18, tooltip="添加", on_click=add_quick_prompt)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            quick_prompts_list,
            ft.Row([ft.Button("保存设置", on_click=save_settings, width=140), ft.Button("清理会话", on_click=clear_cache, width=140, bgcolor=ft.Colors.RED_200)], spacing=8),
            ft.Row([ft.Button("清除所有数据", on_click=clear_all_data, width=200, bgcolor=ft.Colors.RED), ft.IconButton(ft.Icons.FOLDER_OPEN, icon_size=18, tooltip="打开数据文件夹", on_click=lambda e: os.startfile(APP_DIR))], spacing=8),
            ft.Text(f"数据目录: {APP_DIR}", size=10, color=colors["text_hint"]),
            ft.Divider(), ft.Text("DeepSeek Agent", size=12, color=colors["text_hint"]),
            ft.TextButton("GitHub: https://github.com/FDLAlfrid/dsa_acd", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda e: page.launch_url("https://github.com/FDLAlfrid/dsa_acd"), style=ft.ButtonStyle(padding=0, color=colors["primary"]))
        ], spacing=12, scroll=ft.ScrollMode.AUTO), expand=True), nav_bar(colors)])
        page.update()
        load_role_list_ui()
        load_quick_prompts_ui()

    # 启动
    sessions = list_sessions()
    if sessions: state["current_session"] = sessions[0]; state["messages"] = load_msgs(sessions[0])
    show_chat()

ft.run(main)
