import flet as ft
import os, json, time, gc, base64, re, subprocess, sys, platform, shutil, threading, asyncio, hashlib, socket, getpass, requests
try: from openai import OpenAI
except: OpenAI = None

VERSION = "0.1.1"
APP_NAME = "DeepSeek Agent"
APP_DIR = os.path.join(os.path.expanduser("~"), ".deepseek_sessions")
SESSION_DIR = os.path.join(APP_DIR, "sessions")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
SECURE_KEY_PATH = os.path.join(APP_DIR, "secure_key.dat")
MEMORY_DIR = os.path.join(APP_DIR, "memory")  # 长记忆池目录

# CJK 字体：确保中文符号（顿号、引号等）正确渲染
if platform.system() == "Windows":
    FONT_CJK = "Microsoft YaHei"
elif platform.system() == "Darwin":
    FONT_CJK = "PingFang SC"
else:
    FONT_CJK = "Noto Sans CJK SC"

os.makedirs(SESSION_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)

if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.deepseek.agent")
    except:
        pass

# ============================================================
# MCP (Model Context Protocol) 客户端 - 与 Trae 共用 MCP 配置
# ============================================================
MCP_CONFIG_PATH = os.path.join(os.path.expandvars("%APPDATA%"), "Trae CN", "User", "mcp.json")
_mcp_servers = {}
_mcp_tools = {}
_mcp_lock = threading.Lock()

def _mcp_which(cmd):
    if os.path.isabs(cmd) and os.path.exists(cmd):
        return cmd
    for ext in [".cmd", ".bat", ".exe", ""]:
        p = shutil.which(cmd + ext)
        if p: return p
    return cmd

def _read_mcp_config():
    if not os.path.exists(MCP_CONFIG_PATH):
        return {}
    try:
        with open(MCP_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as ex:
        print(f"[MCP] 读取配置失败: {ex}")
        return {}

def _get_enabled_mcp_servers():
    config = _read_mcp_config()
    servers = config.get("mcpServers", {})
    enabled = {}
    for name, cfg in servers.items():
        if cfg.get("disabled", False): continue
        if cfg.get("enabled", True) is False: continue
        enabled[name] = cfg
    return enabled

class _MCPStdioClient:
    def __init__(self, name, command, args):
        self.name = name
        self.command = command
        self.args = args
        self.process = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False
        self._tools = []

    def start(self):
        try:
            cmd_path = _mcp_which(self.command)
            cmd_args = [cmd_path] + self.args
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            self.process = subprocess.Popen(
                cmd_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                startupinfo=si,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                text=True, encoding="utf-8", bufsize=1,
            )
            return True
        except Exception as e:
            print(f"[MCP] 启动 {self.name} 失败: {e}")
            self.process = None
            return False

    def stop(self):
        if self.process:
            try: self.process.stdin.close(); self.process.wait(timeout=3)
            except:
                try: self.process.kill()
                except: pass
            self.process = None
        self._initialized = False
        self._tools = []

    def _send(self, method, params=None):
        if not self.process or self.process.poll() is not None:
            return None
        with self._lock:
            self._request_id += 1
            req = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}}
            try:
                self.process.stdin.write(json.dumps(req) + "\n")
                self.process.stdin.flush()
                resp_line = self.process.stdout.readline()
                if resp_line: return json.loads(resp_line)
            except Exception as e:
                print(f"[MCP] {self.name} 请求失败: {e}")
            return None

    def initialize(self):
        if not self.start(): return False
        resp = self._send("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "deepseek-agent", "version": "1.0.0"},
        })
        if resp and "result" in resp:
            try:
                self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                self.process.stdin.flush()
            except: pass
            self._initialized = True
            return True
        return False

    def list_tools(self):
        if not self._initialized: return []
        resp = self._send("tools/list")
        if resp and "result" in resp:
            self._tools = resp["result"].get("tools", [])
            return self._tools
        return []

    def call_tool(self, tool_name, arguments):
        if not self._initialized: return None
        resp = self._send("tools/call", {"name": tool_name, "arguments": arguments})
        if resp and "result" in resp: return resp["result"]
        elif resp and "error" in resp: return {"error": resp["error"]}
        return None

def _discover_mcp_tools():
    global _mcp_servers, _mcp_tools
    with _mcp_lock:
        _mcp_tools = {}
        servers = _get_enabled_mcp_servers()
        if not servers: return []
        has_npx = _mcp_which("npx") or _mcp_which("npx.cmd")
        if not has_npx:
            print("[MCP] 未检测到 npx，跳过 MCP 工具加载")
            return []
        result = []
        for srv_name, srv_cfg in servers.items():
            if "url" in srv_cfg and "command" not in srv_cfg: continue
            command = srv_cfg.get("command", "")
            args = srv_cfg.get("args", [])
            if not command: continue
            if srv_name in _mcp_servers:
                _mcp_servers[srv_name].stop()
            client = _MCPStdioClient(srv_name, command, args)
            if not client.initialize():
                print(f"[MCP] {srv_name} 初始化失败，跳过")
                continue
            tools = client.list_tools()
            if not tools:
                print(f"[MCP] {srv_name} 没有工具，跳过")
                client.stop()
                continue
            _mcp_servers[srv_name] = client
            for tool in tools:
                full_name = f"mcp_{srv_name}_{tool['name']}"
                _mcp_tools[full_name] = (srv_name, tool["name"])
                result.append((full_name, tool))
        return result

def _call_mcp_tool(full_name, arguments):
    with _mcp_lock:
        if full_name not in _mcp_tools:
            return {"error": f"未知 MCP 工具: {full_name}"}
        srv_name, tool_name = _mcp_tools[full_name]
        if srv_name not in _mcp_servers:
            return {"error": f"MCP 服务器 {srv_name} 未连接"}
        result = _mcp_servers[srv_name].call_tool(tool_name, arguments)
        return result or {"error": "调用失败"}

def _shutdown_mcp():
    with _mcp_lock:
        for name, client in list(_mcp_servers.items()):
            client.stop()
        _mcp_servers.clear()
        _mcp_tools.clear()
# ============================================================

# API Key 安全存储（机器绑定加密）
# ============================================================

def _derive_machine_key():
    """基于机器信息派生加密密钥（同机器可解密，不同机器无法解密）"""
    machine_id = f"{socket.gethostname()}:{getpass.getuser()}:dsa_acd_salt_v1"
    return hashlib.pbkdf2_hmac('sha256', machine_id.encode(), b'deepseek_agent_fixed_salt', 100000, dklen=32)

def _encrypt_api_key(plaintext):
    """加密 API Key"""
    if not plaintext: return None
    key = _derive_machine_key()
    # XOR 加密
    plain_bytes = plaintext.encode('utf-8')
    encrypted = bytes([plain_bytes[i] ^ key[i % len(key)] for i in range(len(plain_bytes))])
    return base64.b64encode(encrypted).decode('ascii')

def _decrypt_api_key(cipher_b64):
    """解密 API Key"""
    if not cipher_b64: return None
    try:
        key = _derive_machine_key()
        encrypted = base64.b64decode(cipher_b64)
        decrypted = bytes([encrypted[i] ^ key[i % len(key)] for i in range(len(encrypted))])
        return decrypted.decode('utf-8')
    except Exception:
        return None

def _save_secure_api_key(api_key):
    """将加密后的 API Key 保存到磁盘"""
    if not api_key:
        if os.path.exists(SECURE_KEY_PATH):
            try: os.remove(SECURE_KEY_PATH)
            except: pass
        return
    encrypted = _encrypt_api_key(api_key)
    if encrypted:
        try:
            with open(SECURE_KEY_PATH, "w", encoding="utf-8") as f:
                json.dump({"key": encrypted}, f)
        except: pass

def _load_secure_api_key():
    """从磁盘加载并解密 API Key"""
    if not os.path.exists(SECURE_KEY_PATH):
        return ""
    try:
        with open(SECURE_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _decrypt_api_key(data.get("key", "")) or ""
    except Exception:
        return ""

BINARY_EXTS = {".exe", ".dll", ".bin", ".dat", ".pyc", ".pyd", ".so", ".dylib", ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"}

DEFAULT_AGENT_ROLES = {
    "通用助手": {
        "prompt": "你是一个通用AI助手，可以读写本地文件、运行命令、调用工具。请按用户指令操作，用中文回复。",
        "skills": ["总结内容", "翻译成中文", "解释概念"],
    },
    "编程专家": {
        "prompt": "你是一位资深全栈程序员，精通 Python/JavaScript/Go/Rust/Java/C++ 等主流语言。\n\n你的工作方式：\n1. 先理解用户需求，必要时主动提问澄清\n2. 编写可运行、可维护的代码，附带关键注释\n3. 指出潜在的性能、安全、兼容性问题\n4. 代码示例要完整，不要省略关键部分\n\n回复用中文，代码用英文。",
        "skills": ["代码审查", "找Bug", "性能优化", "重构代码", "写注释", "写测试", "解释代码"],
    },
    "翻译官": {
        "prompt": "你是一位专业翻译，精通中英互译，也支持日/韩/法/德/西等主要语言。\n\n翻译规则：\n1. 保持原文风格、语气和结构\n2. 专业术语准确，不随意意译\n3. 只返回译文，不添加解释（除非用户要求）\n4. 遇到歧义时标注多种可能译法",
        "skills": ["中译英", "英译中", "多语言翻译", "润色中文"],
    },
    "写作助手": {
        "prompt": "你是一位写作专家，擅长各类文书创作：文章、报告、邮件、演讲稿、技术文档等。\n\n写作原则：\n1. 先确认目标读者和用途\n2. 结构清晰，逻辑连贯\n3. 语言流畅自然，避免生硬翻译腔\n4. 根据场景调整语气（正式/轻松/专业）\n5. 可主动提供改进建议",
        "skills": ["写文章", "写报告", "写邮件", "润色改写", "扩写缩写"],
    },
    "代码审查员": {
        "prompt": "你是一位严格的代码审查专家，专注代码质量与安全。\n\n审查清单（每次必查）：\n1. 潜在 Bug：空指针、边界条件、并发问题\n2. 性能问题：不必要的循环、内存泄漏、N+1查询\n3. 安全风险：注入漏洞、敏感信息泄露、权限问题\n4. 可改进之处：命名、结构、可读性\n\n用「✅ 通过 / ⚠️ 建议 / ❌ 必须修复」标注每条意见。",
        "skills": ["代码审查", "安全审查", "性能分析", "Bug定位"],
    },
    "数据分析师": {
        "prompt": "你是一位数据分析专家，擅长数据解读、统计分析和可视化建议。\n\n分析流程：\n1. 先理解数据结构和业务背景\n2. 用统计方法发现规律和异常\n3. 给出 actionable 的洞察，不只是描述\n4. 建议合适的可视化方案（图表类型、工具）\n\n用数据说话，避免主观臆断。",
        "skills": ["数据分析", "趋势解读", "可视化建议", "统计检验"],
    },
    "教师": {
        "prompt": "你是一位耐心且知识渊博的老师，擅长用简单方式解释复杂概念。\n\n教学方法：\n1. 先用一句话概括核心概念\n2. 用类比和例子帮助理解\n3. 分层讲解：从简单到深入\n4. 提问引导用户思考，而非直接给答案\n5. 总结关键要点，便于记忆\n\n保持鼓励和耐心，让学生感到学习是有趣的。",
        "skills": ["解释概念", "举例说明", "引导思考", "知识梳理"],
    },
    "产品经理": {
        "prompt": "你是一位经验丰富的产品经理，擅长需求分析、功能设计和用户体验优化。\n\n工作方式：\n1. 先理解用户场景和痛点\n2. 分析竞品和行业最佳实践\n3. 设计清晰的功能规格和用户流程\n4. 评估优先级和实施难度\n5. 用结构化文档呈现（用户故事、验收标准、原型建议）\n\n平衡用户需求、技术可行性和商业价值。",
        "skills": ["需求分析", "功能设计", "竞品分析", "PRD撰写", "用户体验"],
    },
    "DevOps工程师": {
        "prompt": "你是一位DevOps/SRE工程师，精通CI/CD、容器化、云服务和自动化运维。\n\n专业领域：\n- Docker/K8s 容器编排\n- CI/CD 流水线（GitHub Actions, Jenkins, GitLab CI）\n- 云服务（AWS, Azure, GCP, 阿里云）\n- 监控和日志（Prometheus, Grafana, ELK）\n- IaC（Terraform, Ansible, Pulumi）\n\n回复要给出可执行的配置示例。",
        "skills": ["CI/CD配置", "Docker编排", "K8s部署", "监控告警", "自动化脚本"],
    },
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

def create_session(name, system_prompt=None):
    path = session_path(name)
    if os.path.exists(path): return False
    if system_prompt is None:
        system_prompt = get_default_system_prompt()
    msgs = [{"role": "system", "content": system_prompt}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)
    return True

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

def write_file(path, content, progress_callback=None):
    """写入文件，支持进度回调。progress_callback(percent, written, total)"""
    try:
        total = len(content)
        if total > 512 * 1024 and progress_callback:
            # 大于 512KB 时分块写入并报告进度
            chunk_size = 64 * 1024  # 64KB per chunk
            written = 0
            with open(path, "w", encoding="utf-8") as f:
                for i in range(0, total, chunk_size):
                    chunk = content[i:i + chunk_size]
                    f.write(chunk)
                    written += len(chunk)
                    progress_callback(int(written / total * 100), written, total)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            if progress_callback:
                progress_callback(100, total, total)
        return True
    except Exception as ex:
        return str(ex)

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

def _is_cmd_available(cmd):
    """检测命令是否可用（跨平台）"""
    try:
        import shutil
        found = shutil.which(cmd) or shutil.which(cmd + ".cmd") or shutil.which(cmd + ".exe")
        return found is not None
    except Exception:
        return False

TOOLS = [
    {"type": "function", "function": {"name": "read_file_content", "description": "读取本地文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file_content", "description": "写入文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "列出目录文件", "parameters": {"type": "object", "properties": {"dir": {"type": "string"}}, "required": ["dir"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "运行终端命令", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "get_balance", "description": "查询 DeepSeek API 余额", "parameters": {"type": "object", "properties": {}}}},
]

# 环境检测：按需注册系统工具
_AVAILABLE_TOOLS = {"npm": False, "git": False, "mcp": False}

if _is_cmd_available("npm"):
    TOOLS.append({"type": "function", "function": {"name": "npm_run", "description": "运行 npm 命令（install/build/test等）", "parameters": {"type": "object", "properties": {"args": {"type": "string", "description": "npm 参数，如 'install express' 或 'run build' "}}, "required": ["args"]}}})
    _AVAILABLE_TOOLS["npm"] = True

if _is_cmd_available("git"):
    TOOLS.append({"type": "function", "function": {"name": "git_run", "description": "运行 Git 命令（status/log/diff等）", "parameters": {"type": "object", "properties": {"args": {"type": "string", "description": "git 参数，如 'status' 或 'log --oneline -5' "}}, "required": ["args"]}}})
    _AVAILABLE_TOOLS["git"] = True

# 动态加载 MCP 工具（需要 npx 可用）
def _load_mcp_tools():
    """加载 Trae 共用的 MCP 工具定义"""
    try:
        mcp_tools = _discover_mcp_tools()
        if not mcp_tools:
            print("[MCP] 未发现可用的 MCP 工具")
            return
        for full_name, tool_def in mcp_tools:
            TOOLS.append({
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": f"[MCP:{tool_def.get('description', full_name)}]",
                    "parameters": tool_def.get("inputSchema", {"type": "object", "properties": {}}),
                }
            })
        _AVAILABLE_TOOLS["mcp"] = True
        print(f"[MCP] 已加载 {len(mcp_tools)} 个 MCP 工具")
    except Exception as ex:
        print(f"[MCP] 加载工具失败: {ex}")

def _enable_mcp():
    """启用 MCP 工具"""
    if not state.get("mcp_enabled", False):
        return
    # 避免重复加载
    if _AVAILABLE_TOOLS.get("mcp", False):
        _shutdown_mcp()
        TOOLS[:] = [t for t in TOOLS if not t.get("function", {}).get("name", "").startswith("mcp_")]
        _AVAILABLE_TOOLS["mcp"] = False
    _load_mcp_tools()

def _disable_mcp():
    """禁用 MCP 工具"""
    _shutdown_mcp()
    TOOLS[:] = [t for t in TOOLS if not t.get("function", {}).get("name", "").startswith("mcp_")]
    _AVAILABLE_TOOLS["mcp"] = False
    print("[MCP] 已禁用所有 MCP 工具")

def get_default_system_prompt(): return "你是一个AI助手，可以读写本地文件、运行命令，请按用户指令操作。"

# ============================================================
# 长记忆池 - 对话摘要持久化
# ============================================================
def _memory_path(session_name): return os.path.join(MEMORY_DIR, f"{session_name}.json")

def load_memory(session_name):
    """加载会话的长记忆摘要"""
    path = _memory_path(session_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"summary": "", "key_points": [], "last_updated": ""}

def save_memory(session_name, memory_data):
    """保存会话的长记忆摘要"""
    memory_data["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(_memory_path(session_name), "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
    except: pass

def build_context_with_memory(messages, session_name):
    """构建带记忆的上下文：记忆摘要 + 最近N轮对话"""
    memory = load_memory(session_name)
    summary = memory.get("summary", "")
    if not summary:
        return messages  # 无记忆，直接返回原始消息

    # 在系统消息后插入记忆上下文
    result = []
    for m in messages:
        result.append(m)
        if m["role"] == "system":
            memory_msg = {"role": "system", "content": f"[历史对话摘要]\n{summary}\n\n[以上为之前对话的摘要，请结合以上上下文理解用户的后续问题]"}
            result.append(memory_msg)
            break
    return result

def auto_summarize_if_needed(session_name, messages):
    """如果对话过长，自动生成摘要存入记忆池"""
    # 超过 20 轮对话时触发摘要
    user_msgs = [m for m in messages if m["role"] == "user"]
    if len(user_msgs) < 20: return

    # 检查是否已有近期摘要（1小时内不重复生成）
    memory = load_memory(session_name)
    last_ts = memory.get("last_updated", "")
    if last_ts:
        try:
            last_t = time.mktime(time.strptime(last_ts, "%Y-%m-%d %H:%M:%S"))
            if time.time() - last_t < 3600: return
        except: pass

    # 异步生成摘要（不阻塞用户操作）
    def do_summarize():
        try:
            if not state.get("client"): return
            # 取前 2/3 的消息用于摘要
            split = int(len(messages) * 0.67)
            old_msgs = messages[:split]
            summary_prompt = "请用中文简要总结以下对话的内容（200字以内），提取关键信息点：\n\n"
            for m in old_msgs:
                if m["role"] in ("user", "assistant"):
                    content = m.get("content", "")
                    if isinstance(content, str) and content.strip():
                        summary_prompt += f"[{m['role']}]: {content[:500]}\n"
            r = state["client"].chat.completions.create(
                model=state["model"],
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=500
            )
            summary = r.choices[0].message.content
            memory["summary"] = summary
            key_points = [line.strip("- ") for line in summary.split("\n") if line.strip().startswith(("-", "1.", "2.", "3.", "4.", "5."))]
            memory["key_points"] = key_points[:10]
            save_memory(session_name, memory)
            print(f"[记忆] 已生成摘要: {session_name}")
        except Exception as ex:
            print(f"[记忆] 摘要生成失败: {ex}")

    threading.Thread(target=do_summarize, daemon=True).start()

# ============================================================

def get_role_prompt(role_name):
    """获取 Agent 角色的系统提示词"""
    roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
    role = roles.get(role_name, DEFAULT_AGENT_ROLES.get("通用助手", {}))
    if isinstance(role, dict):
        return role.get("prompt", get_default_system_prompt())
    return role  # 兼容旧格式

def get_role_skills(role_name):
    """获取 Agent 角色的技能列表"""
    roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
    role = roles.get(role_name, DEFAULT_AGENT_ROLES.get("通用助手", {}))
    if isinstance(role, dict):
        return role.get("skills", [])
    return []

def get_api_params():
    """根据 thinking_enabled 返回 API 调用参数"""
    params = {
        "max_tokens": state.get("max_tokens", 2048),
    }
    if state.get("thinking_enabled", False):
        params["extra_body"] = {"thinking": {"type": "enabled"}}
        if state.get("reasoning_effort"):
            params["reasoning_effort"] = state["reasoning_effort"]
    else:
        params["extra_body"] = {"thinking": {"type": "disabled"}}
        params["temperature"] = state.get("temperature", 0.7)
    return params

_tool_progress = None  # 全局进度回调，在工具执行时被赋值

def execute_tool_call(fn_name, fn_args):
    """执行工具调用，返回字符串结果"""
    try:
        if fn_name == "read_file_content":
            return read_file(fn_args.get("path", ""))
        elif fn_name == "write_file_content":
            return write_file(fn_args.get("path", ""), fn_args.get("content", ""), _tool_progress)
        elif fn_name == "list_files":
            return list_dir(fn_args.get("dir", os.getcwd()))
        elif fn_name == "run_command":
            return run_cmd(fn_args.get("cmd", ""))
        elif fn_name == "get_balance":
            return check_balance()
        elif fn_name == "npm_run":
            return run_cmd(f"npm {fn_args.get('args', '')}")
        elif fn_name == "git_run":
            return run_cmd(f"git {fn_args.get('args', '')}")
        elif fn_name.startswith("mcp_"):
            result = _call_mcp_tool(fn_name, fn_args)
            if isinstance(result, dict):
                if "error" in result:
                    return f"[MCP错误] {json.dumps(result['error'], ensure_ascii=False)}"
                # 提取 content 数组中的文本
                content = result.get("content", [])
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
                return "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
            return str(result)
        else:
            return f"未知工具: {fn_name}"
    except Exception as ex:
        return f"工具执行错误: {ex}"

def run_cmd(cmd):
    """执行命令并返回结果"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=state.get("current_path", os.getcwd()))
        out = r.stdout.strip() or r.stderr.strip()
        # 检测命令不存在的情况
        if r.returncode != 0:
            cmd_name = cmd.split()[0] if cmd.split() else cmd
            if "not recognized" in out or "not found" in out.lower() or "no such file" in out.lower():
                return f"未找到命令 '{cmd_name}'，请确认已安装并加入 PATH 环境变量"
        return out[:8000] if len(out) > 8000 else (out or f"命令执行完毕（退出码: {r.returncode}）")
    except subprocess.TimeoutExpired:
        return "命令执行超时（30秒）"
    except FileNotFoundError:
        cmd_name = cmd.split()[0] if cmd.split() else cmd
        return f"未找到命令 '{cmd_name}'，请确认已安装并加入 PATH 环境变量"
    except Exception as ex:
        return f"命令执行失败: {ex}"

def handle_api_response(msg_list, ac, text_control=None, session_name=""):
    """
    处理 API 响应：如果流式有内容直接返回，否则处理 tool_calls 循环
    返回 (assistant_msgs, tool_call_names)
    """
    if ac:
        return [{"role": "assistant", "content": ac}], []

    assistant_msgs = []
    tool_calls_used = []
    current_msgs = list(msg_list)  # 不修改原始 msg_list
    # 注入长记忆上下文
    if session_name:
        current_msgs = build_context_with_memory(current_msgs, session_name)
    max_rounds = 10  # 真正的 Agent 多轮工具调用
    for _ in range(max_rounds):
        r = state["client"].chat.completions.create(model=state["model"], messages=current_msgs, tools=TOOLS, tool_choice="auto", **get_api_params())
        msg = r.choices[0].message
        if msg.content:
            assistant_msgs.append({"role": "assistant", "content": msg.content})
            if text_control:
                tc_text = "[已调用工具: " + ", ".join(tool_calls_used) + "]\n\n" if tool_calls_used else ""
                text_control.value = tc_text + msg.content
            return assistant_msgs, tool_calls_used
        if msg.tool_calls:
            tc_list = []
            for tc in msg.tool_calls:
                tc_list.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}})
            current_msgs.append({"role": "assistant", "content": None, "tool_calls": tc_list})
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                tool_calls_used.append(fn_name)
                try:
                    fn_args = json.loads(tc.function.arguments)
                except:
                    fn_args = {}
                if text_control:
                    round_info = f"第{len(tool_calls_used)}步" if len(tool_calls_used) > 1 else ""
                    text_control.value = f"⚙️ {round_info}调用: {fn_name}..."
                    text_control.color = ft.Colors.BLUE_400 if state["theme_mode"] == ft.ThemeMode.DARK else ft.Colors.BLUE_700
                # 设置进度回调（仅文件写入类工具使用）
                if fn_name == "write_file_content" and text_control:
                    def _on_progress(percent, written, total):
                        text_control.value = f"⚙️ 写入文件: {percent}% ({written//1024}KB / {total//1024}KB)"
                        text_control.color = ft.Colors.BLUE_400 if state["theme_mode"] == ft.ThemeMode.DARK else ft.Colors.BLUE_700
                    global _tool_progress
                    _tool_progress = _on_progress
                tool_result = execute_tool_call(fn_name, fn_args)
                _tool_progress = None  # 清除回调
                current_msgs.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
            continue
        break
    assistant_msgs.append({"role": "assistant", "content": ""})
    return assistant_msgs, tool_calls_used

def check_balance():
    """查询 API 余额"""
    try:
        if not state.get("client"):
            return "请先配置 API Key"
        r = state["client"].balance.retrieve()
        if r and hasattr(r, 'balance'):
            return f"余额: {r.balance} {r.currency}"
        return "无法获取余额信息"
    except Exception as ex:
        return f"查询失败: {ex}"

def add_ref_file(path):
    """全局添加引用文件（聊天页和文件页均可调用）"""
    try:
        content = read_file(path)
        if content.startswith(("文件过大", "文件不存在", "无权限读取", "读取失败")):
            return False, content
        if not any(f["path"] == path for f in state["ref_files"]):
            state["ref_files"].append({"path": path, "content": content})
            return True, None
        return False, "已存在"
    except Exception as ex: return False, f"添加失败: {ex}"

def remove_ref_file(path):
    state["ref_files"][:] = [f for f in state["ref_files"] if f["path"] != path]

def get_theme_colors():
    is_dark = state["theme_mode"] == ft.ThemeMode.DARK
    return {"bg": ft.Colors.GREY_100 if not is_dark else "#1a1a2e", "card": ft.Colors.WHITE if not is_dark else "#16213e", "text": ft.Colors.BLACK87 if not is_dark else ft.Colors.WHITE70, "text_hint": ft.Colors.GREY if not is_dark else ft.Colors.GREY_400, "border": ft.Colors.GREY_300 if not is_dark else "#0f3460", "selected": ft.Colors.BLUE_100 if not is_dark else "#1a3a6a", "code_bg": ft.Colors.GREY_200 if not is_dark else "#0d1b2a", "user_msg": ft.Colors.BLUE_50 if not is_dark else "#1a2744", "assistant_msg": ft.Colors.GREY_100 if not is_dark else "#16213e", "error_msg": ft.Colors.RED_50 if not is_dark else "#2a1a1a", "primary": ft.Colors.BLUE if not is_dark else ft.Colors.CYAN_400}

def get_layout_size(p):
    w = p.width if p.width > 0 else 1200
    if w >= 1200: return {"sidebar_width": 220, "file_width": 250, "spacing": 12, "padding": 12}
    elif w >= 1000: return {"sidebar_width": 200, "file_width": 220, "spacing": 10, "padding": 10}
    else: return {"sidebar_width": 180, "file_width": 200, "spacing": 8, "padding": 8}

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
    page.title = f"DeepSeek Agent v{VERSION}"
    page.padding = 0; page.spacing = 0

    # 窗口关闭时清理 MCP 连接
    def on_window_close(e):
        try:
            _shutdown_mcp()
            print("[MCP] 已关闭所有连接")
        except:
            pass
    page.on_close = on_window_close

    # 图标（确保任务栏和标题栏图标一致）
    try:
        if getattr(sys, 'frozen', False):
            icon_path = os.path.join(sys._MEIPASS, "icon.ico")
        else:
            icon_path = os.path.join(os.path.dirname(__file__), "icon.ico")
        page.window_icon = icon_path
    except: pass

    state = {"api_key": "", "model": "deepseek-v4-flash", "theme_mode": ft.ThemeMode.LIGHT, "current_session": "", "messages": [], "current_path": os.getcwd(), "system_prompt": get_default_system_prompt(), "quick_prompts": [("总结", "请总结一下以上内容"), ("翻译", "请翻译成中文"), ("代码审查", "请审查以下代码")], "client": None, "max_context_tokens": 32000, "max_tokens": 2048, "temperature": 0.7, "thinking_enabled": False, "reasoning_effort": "high", "agent_role": "通用助手", "agent_roles": dict(DEFAULT_AGENT_ROLES), "ref_files": [], "mcp_enabled": False, "balance_result": "", "conn_result": ""}

    async def _safe_update():
        """安全更新页面 - 可在任何线程中通过 page.run_task 调用"""
        page.update()

    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                # 迁移旧版明文 API Key 到加密存储
                old_plain_key = saved.pop("api_key", "").strip()
                if old_plain_key and not os.path.exists(SECURE_KEY_PATH):
                    _save_secure_api_key(old_plain_key)
                    # 回写 settings.json 去掉 api_key 字段
                    with open(SETTINGS_PATH, "w", encoding="utf-8") as fw:
                        json.dump(saved, fw, ensure_ascii=False, indent=2)
                state.update(saved)
        except: pass

    # 从加密文件加载 API Key
    secure_key = _load_secure_api_key()
    if secure_key:
        state["api_key"] = secure_key
        print("[安全] 已从加密存储加载 API Key")

    try:
        state["client"] = OpenAI(api_key=state["api_key"], base_url="https://api.deepseek.com/v1") if OpenAI else None
    except: pass

    # 启动时根据设置加载 MCP 工具
    if state.get("mcp_enabled", False):
        _load_mcp_tools()

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

        ref_chips = ft.Row(wrap=True, spacing=3)

        def show_ref_detail(f):
            """点击引用标签：查看/编辑文件内容，支持重新发送"""
            lines = f["content"].count('\n') + 1
            chars = len(f["content"])
            edit_field = ft.TextField(value=f["content"], multiline=True, min_lines=6, max_lines=20, expand=True, border=ft.InputBorder.OUTLINE, color=colors["text"])
            info_text = ft.Text(f"{os.path.basename(f['path'])} ({lines}行, {chars}字符)", size=12, color=colors["text_hint"])
            def save_edit():
                f["content"] = edit_field.value
                update_ref_chips()
                dlg.open = False; page.update()
                show_toast(page, "引用内容已更新，可重新发送")
            def resend_with_ref():
                f["content"] = edit_field.value
                update_ref_chips()
                dlg.open = False; page.update()
                if state["messages"]:
                    last_user = None
                    for i in range(len(state["messages"])-1, -1, -1):
                        if state["messages"][i]["role"] == "user":
                            last_user = i; break
                    if last_user is not None:
                        state["messages"] = state["messages"][:last_user+1]
                        state["messages"][-1]["ref_files"] = [{"path": rf["path"], "content": rf["content"]} for rf in state["ref_files"]]
                        state["messages"][-1]["content"] = f"[重新发送] {state['messages'][-1]['content']}"
                        save_msgs(state["current_session"], state["messages"])
                        update_session_info()
                        load_chat()
                        send_chat(None)
                        return
                show_toast(page, "请先发送一条消息再重新发送")
            dlg = ft.AlertDialog(title=ft.Text(f"引用: {os.path.basename(f['path'])}"), content=ft.Column([info_text, edit_field], spacing=10, expand=True), actions=[ft.TextButton("关闭"), ft.Button("保存修改"), ft.Button("重新发送", bgcolor=ft.Colors.BLUE)])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: save_edit()
            dlg.actions[2].on_click = lambda e: resend_with_ref()
            show_dialog(page, dlg)

        def update_ref_chips():
            ref_chips.controls.clear()
            for f in state["ref_files"]:
                lines = f["content"].count('\n') + 1
                ref_chips.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(f"{os.path.basename(f['path'])} ({lines}行)", size=11, weight=ft.FontWeight.W_500),
                            ft.Text(f["path"], size=9, color=colors["text_hint"])
                        ], spacing=0, expand=True),
                        ft.IconButton(ft.Icons.EDIT, icon_size=14, tooltip="查看/编辑", on_click=lambda e, rf=f: show_ref_detail(rf)),
                        ft.IconButton(ft.Icons.CLOSE, icon_size=14, on_click=lambda e, p=f["path"]: (remove_ref_file(p), update_ref_chips()))
                    ], spacing=2),
                    bgcolor=colors["selected"], border_radius=8, padding=ft.Padding.only(left=8, top=4, right=8, bottom=4)
                ))
            ref_chips.update()

        layout = get_layout_size(page)
        chat_list = ft.ListView(expand=True, spacing=layout["spacing"], padding=layout["padding"])

        def resend_message(idx):
            if not state["api_key"]:
                show_toast(page, "请先在设置页面配置 API Key")
                return
            if not check_api_key(state["api_key"]):
                show_toast(page, "API Key 格式不正确，请检查")
                return
            if not state["client"]: show_toast(page, "请先设置 API Key"); return
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
                    msg_list = build_context_with_memory(msg_list, state["current_session"])
                    resp = state["client"].chat.completions.create(model=state["model"], messages=msg_list, stream=True, tools=TOOLS, tool_choice="auto", **get_api_params())
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    ac = ""
                    rc = ""
                    text_control = ft.Text("正在思考...", size=13, color=colors["text_hint"])
                    reasoning_control = ft.Text("", size=12, color=colors["text_hint"], italic=True)
                    reasoning_container = ft.Container(padding=ft.Padding.only(left=8, top=4, right=8, bottom=4), border_radius=4, bgcolor=colors["card"], content=reasoning_control, visible=False)
                    msg_container = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Column([ft.Text("AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"], font_family=FONT_CJK), reasoning_container, text_control], spacing=4))
                    chat_list.controls.append(msg_container)
                    page.run_task(_safe_update)
                    for c in resp:
                        if c.choices:
                            rc_delta = getattr(c.choices[0].delta, 'reasoning_content', None)
                            if rc_delta:
                                rc += rc_delta
                                reasoning_control.value = f"思考过程:\n{rc}"
                                reasoning_container.visible = True
                                page.run_task(_safe_update)
                            elif c.choices[0].delta.content:
                                ac += c.choices[0].delta.content
                                text_control.value = ac
                                text_control.color = colors["text"]
                                chat_list.scroll_to(offset=0, duration=0)
                                page.run_task(_safe_update)
                    if ac:
                        state["messages"].append({"role": "assistant", "content": ac})
                    else:
                        # 显示工具调用提示
                        text_control.value = "正在调用工具..."
                        text_control.color = colors["text_hint"]
                        page.run_task(_safe_update)
                        results, tool_calls = handle_api_response(msg_list, ac, text_control, state["current_session"])
                        # 添加工具调用指示器
                        if tool_calls:
                            tc_indicator = ft.Container(
                                padding=ft.Padding.only(left=10, top=6, right=10, bottom=6),
                                border_radius=6,
                                bgcolor=colors["selected"],
                                content=ft.Row([
                                    ft.Icon(ft.Icons.BUILD, size=14, color=colors["primary"]),
                                    ft.Text(f"已调用: {', '.join(tool_calls)}", size=12, color=colors["primary"], weight=ft.FontWeight.W_500)
                                ], spacing=6)
                            )
                            chat_list.controls.insert(-1, tc_indicator)
                        for r in results:
                            state["messages"].append(r)
                    save_msgs(state["current_session"], state["messages"])
                    auto_summarize_if_needed(state["current_session"], state["messages"])
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
                    page.run_task(_safe_update)
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
            items.append(ft.PopupMenuItem(content="复制内容", on_click=lambda e: (page.clipboard.set(state["messages"][idx].get("content", "")) or show_toast(page, "已复制"))))
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
                        ref_chips_for_display.controls.append(ft.Container(content=ft.Row([ft.Icon(ft.Icons.FILE_OPEN), ft.Text(os.path.basename(rf["path"]), size=11)]), bgcolor=colors["primary"], border_radius=4, padding=ft.Padding.only(left=6, top=2, right=6, bottom=2)))
                    content_controls.insert(0, ft.Row([ft.Text("引用文件:", size=11, weight=ft.FontWeight.W_500), ref_chips_for_display], spacing=4, wrap=True))

                message_row = ft.Row([ft.Column([ft.Text("你" if is_user else "AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"], font_family=FONT_CJK)] + content_controls, spacing=4, expand=True), make_message_popup(idx, is_user)], spacing=5)
                if jump_btn:
                    message_row.controls.insert(0, jump_btn)
                chat_list.controls.append(ft.Container(padding=10, border_radius=8, bgcolor=colors["user_msg"] if is_user else colors["assistant_msg"], content=message_row))
            update_session_info()
            page.run_task(_safe_update)

        def format_content(content, colors):
            """格式化消息内容：代码块高亮、链接可点击、CJK字体"""
            # 先提取链接，保护起来
            link_pattern = r'(https?://[^\s<>"{}|\\^`\[\]]+)'
            protected = {}
            counter = [0]
            def protect_link(m):
                key = f"__LINK_{counter[0]}__"
                protected[key] = m.group(1)
                counter[0] += 1
                return key
            content_with_placeholders = re.sub(link_pattern, protect_link, content)

            parts = re.split(r'(```[\w]*\n?[\s\S]*?```)', content_with_placeholders)
            controls = []
            for part in parts:
                if part.startswith("```"):
                    lang = part.split('\n')[0].replace('`', '').strip()
                    code = '\n'.join(part.split('\n')[1:-1])
                    controls.append(ft.Container(padding=8, border_radius=5, bgcolor=colors["code_bg"], content=ft.Column([ft.Row([ft.Text(lang or "code", size=10, weight=ft.FontWeight.W_500, font_family=FONT_CJK), ft.IconButton(ft.Icons.COPY, icon_size=14, on_click=lambda e, c=code: page.clipboard.set(c) or show_toast(page, "已复制"))], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Text(code, size=12, font_family="Consolas")], spacing=4)))
                elif part.strip():
                    # 还原链接并创建可点击的文本
                    text = part.strip()
                    for key, url in protected.items():
                        text = text.replace(key, url)
                    # 检查是否包含链接
                    if re.search(link_pattern, text):
                        link_controls = []
                        last_end = 0
                        for m in re.finditer(link_pattern, text):
                            if m.start() > last_end:
                                link_controls.append(ft.Text(text[last_end:m.start()], size=13, color=colors["text"], font_family=FONT_CJK))
                            link_controls.append(ft.TextButton(m.group(1), on_click=lambda e, u=m.group(1): page.launch_url(u), style=ft.ButtonStyle(padding=ft.Padding.only(left=2, right=2, top=0, bottom=0), color=colors["primary"]), icon=ft.Icons.OPEN_IN_NEW, icon_size=12))
                            last_end = m.end()
                        if last_end < len(text):
                            link_controls.append(ft.Text(text[last_end:], size=13, color=colors["text"], font_family=FONT_CJK))
                        controls.append(ft.Row(link_controls, wrap=True, spacing=0))
                    else:
                        controls.append(ft.Text(text, size=13, color=colors["text"], font_family=FONT_CJK))
            return controls or [ft.Text("", size=13)]

        def send_chat(e):
            if not state["api_key"]:
                show_toast(page, "请先在设置页面配置 API Key")
                return
            if not check_api_key(state["api_key"]):
                show_toast(page, "API Key 格式不正确，请检查")
                return
            if not state["current_session"]: show_toast(page, "请先选择或新建会话"); return
            if not state["client"]: show_toast(page, "请先设置 API Key"); return
            text = chat_input.value.strip()
            if not text: return
            send_btn.disabled = True
            send_btn.text = "发送中..."
            page.update()

            ref_files_data = []
            if state["ref_files"]:
                ref_files_data = [{"path": f["path"], "content": f["content"]} for f in state["ref_files"]]

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
                    msg_list = build_context_with_memory(msg_list, state["current_session"])
                    resp = state["client"].chat.completions.create(model=state["model"], messages=msg_list, stream=True, tools=TOOLS, tool_choice="auto", **get_api_params())
                    if chat_list.controls and chat_list.controls[-1] == t:
                        chat_list.controls.remove(t)
                    ac = ""
                    rc = ""
                    text_control = ft.Text("正在思考...", size=13, color=colors["text_hint"])
                    reasoning_control = ft.Text("", size=12, color=colors["text_hint"], italic=True)
                    reasoning_container = ft.Container(padding=ft.Padding.only(left=8, top=4, right=8, bottom=4), border_radius=4, bgcolor=colors["card"], content=reasoning_control, visible=False)
                    msg_container = ft.Container(padding=10, border_radius=8, bgcolor=colors["assistant_msg"], content=ft.Column([ft.Text("AI", size=12, weight=ft.FontWeight.BOLD, color=colors["primary"], font_family=FONT_CJK), reasoning_container, text_control], spacing=4))
                    chat_list.controls.append(msg_container)
                    page.run_task(_safe_update)
                    for c in resp:
                        if c.choices:
                            rc_delta = getattr(c.choices[0].delta, 'reasoning_content', None)
                            if rc_delta:
                                rc += rc_delta
                                reasoning_control.value = f"思考过程:\n{rc}"
                                reasoning_container.visible = True
                                page.run_task(_safe_update)
                            elif c.choices[0].delta.content:
                                ac += c.choices[0].delta.content
                                text_control.value = ac
                                text_control.color = colors["text"]
                                chat_list.scroll_to(offset=0, duration=0)
                                page.run_task(_safe_update)
                    if ac:
                        state["messages"].append({"role": "assistant", "content": ac})
                    else:
                        # 显示工具调用提示
                        text_control.value = "正在调用工具..."
                        text_control.color = colors["text_hint"]
                        page.run_task(_safe_update)
                        results, tool_calls = handle_api_response(msg_list, ac, text_control, state["current_session"])
                        # 添加工具调用指示器
                        if tool_calls:
                            tc_indicator = ft.Container(
                                padding=ft.Padding.only(left=10, top=6, right=10, bottom=6),
                                border_radius=6,
                                bgcolor=colors["selected"],
                                content=ft.Row([
                                    ft.Icon(ft.Icons.BUILD, size=14, color=colors["primary"]),
                                    ft.Text(f"已调用: {', '.join(tool_calls)}", size=12, color=colors["primary"], weight=ft.FontWeight.W_500)
                                ], spacing=6)
                            )
                            chat_list.controls.insert(-1, tc_indicator)
                        for r in results:
                            state["messages"].append(r)
                    save_msgs(state["current_session"], state["messages"])
                    auto_summarize_if_needed(state["current_session"], state["messages"])
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
                    chat_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["error_msg"], content=ft.Text(error_text, color=ft.Colors.RED, size=12, font_family=FONT_CJK)))
                    page.run_task(_safe_update)
                finally:
                    send_btn.disabled = False
                    send_btn.text = "发送"
                    page.run_task(_safe_update)

            import threading
            threading.Thread(target=do_send, daemon=True).start()

        # == Agent 选择器 + 技能按钮 + 深度思考开关 ==
        agent_names = list(state.get("agent_roles", DEFAULT_AGENT_ROLES).keys())
        agent_dropdown = ft.Dropdown(value=state.get("agent_role", "通用助手"), options=[ft.dropdown.Option(k) for k in agent_names], width=130, text_size=12, border=ft.InputBorder.OUTLINE, color=colors["text"])
        def on_agent_change(e):
            state["agent_role"] = agent_dropdown.value
            state["system_prompt"] = get_role_prompt(agent_dropdown.value)
            # 更新当前会话的 system prompt
            if state["messages"] and state["messages"][0].get("role") == "system":
                state["messages"][0]["content"] = state["system_prompt"]
                save_msgs(state["current_session"], state["messages"])
            refresh_skills_bar()
            page.update()
        agent_dropdown.on_change = on_agent_change

        skills_bar = ft.Row([], spacing=4, wrap=True)

        def refresh_skills_bar():
            skills_bar.controls.clear()
            skills = get_role_skills(state.get("agent_role", "通用助手"))
            for s in skills:
                def make_skill_click(sn):
                    return lambda e: use_skill(sn)
                skills_bar.controls.append(ft.Container(padding=ft.Padding.only(left=8, right=8, top=2, bottom=2), border_radius=10, bgcolor=colors["primary"], content=ft.Text(s, size=11, color=ft.Colors.WHITE), on_click=make_skill_click(s)))
            page.update()

        def use_skill(skill_name):
            chat_input.value = f"[{skill_name}] "
            chat_input.focus()
            page.update()

        refresh_skills_bar()

        # 深度思考开关
        deep_think_switch = ft.Switch(value=state.get("thinking_enabled", False), label="深度思考", label_position=ft.LabelPosition.RIGHT)
        deep_think_label = ft.Text("", size=11, color=colors["text_hint"])
        def on_deep_think_change(e):
            state["thinking_enabled"] = deep_think_switch.value
            deep_think_label.value = "开启 (reasoning_effort=high)" if state["thinking_enabled"] else "关闭"
            deep_think_label.color = ft.Colors.GREEN if state["thinking_enabled"] else colors["text_hint"]
            page.update()
        deep_think_switch.on_change = on_deep_think_change
        deep_think_label.value = "开启 (reasoning_effort=high)" if state.get("thinking_enabled", False) else "关闭"
        deep_think_label.color = ft.Colors.GREEN if state.get("thinking_enabled", False) else colors["text_hint"]

        quick_bar = ft.Row([agent_dropdown, ft.Container(expand=True, content=skills_bar), deep_think_switch, deep_think_label], spacing=8, alignment=ft.MainAxisAlignment.START)

        chat_input = ft.TextField(hint_text="输入消息...", expand=True, multiline=True, min_lines=1, max_lines=4, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"], font_family=FONT_CJK), text_style=ft.TextStyle(font_family=FONT_CJK))
        send_btn = ft.Button("发送", on_click=send_chat, width=70)
        session_info_bar = ft.Text("", size=11, color=colors["text_hint"])

        def update_session_info():
            tokens = calc_tokens_count(state["messages"])
            chars = sum(len(m.get("content", "")) for m in state["messages"])
            session_info_bar.value = f"{state['current_session']} | ~{tokens} tokens | {chars} 字" if state['current_session'] else "请选择会话"
            session_info_bar.update()
        header = ft.Container(padding=10, bgcolor=colors["card"], content=ft.Row([ft.Column([ft.Text(f"DeepSeek Agent v{VERSION}", size=17, weight=ft.FontWeight.BOLD, color=colors["text"], font_family=FONT_CJK), session_info_bar], spacing=0), ft.Row([ft.IconButton(ft.Icons.DELETE, icon_size=16, tooltip="清空", on_click=clear_chat), ft.IconButton(ft.Icons.DELETE_FOREVER, icon_size=16, tooltip="删除会话", on_click=del_current_session), ft.IconButton(ft.Icons.DARK_MODE, icon_size=16, tooltip="切换主题", on_click=toggle_theme)], spacing=2)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN))

        # == 左侧会话列表（类 ChatGPT 布局）==
        sidebar_search = ft.TextField(hint_text="搜索...", border=ft.InputBorder.OUTLINE, color=colors["text"], hint_style=ft.TextStyle(color=colors["text_hint"]), text_size=11, on_change=lambda e: refresh_sidebar())
        sidebar_list = ft.ListView(expand=True, spacing=1)

        def refresh_sidebar():
            sidebar_list.controls.clear()
            sessions = list_sessions()
            query = sidebar_search.value.strip().lower() if sidebar_search.value else ""
            if query: sessions = [s for s in sessions if query in s.lower()]
            if not sessions:
                sidebar_list.controls.append(ft.Container(padding=20, content=ft.Text("暂无会话" if not query else "无匹配", size=12, color=colors["text_hint"])))
            for s in sessions:
                is_current = s == state["current_session"]
                sp = session_path(s)
                info = ""
                if os.path.exists(sp):
                    try:
                        with open(sp, "r", encoding="utf-8") as f: msgs = json.load(f)
                        cnt = sum(1 for m in msgs if m.get("role") != "system")
                        tk = calc_tokens_count(msgs)
                        info = f"{cnt}条 · ~{tk}tk"
                    except: pass
                def make_switch(name):
                    return lambda e: switch_session(name)
                def make_rename(name):
                    return lambda e: rename_session_dialog(name)
                def make_delete(name):
                    return lambda e: delete_session_dialog(name)
                sidebar_list.controls.append(ft.Container(
                    padding=6, border_radius=6,
                    bgcolor=colors["selected"] if is_current else colors["card"],
                    content=ft.Row([
                        ft.Column([
                            ft.Text(s, size=12, weight=ft.FontWeight.BOLD if is_current else ft.FontWeight.NORMAL, color=colors["text"]),
                            ft.Text(info, size=10, color=colors["text_hint"])
                        ], spacing=0, expand=True),
                        ft.PopupMenuButton(items=[ft.PopupMenuItem(content="重命名", on_click=make_rename(s)), ft.PopupMenuItem(content="删除", on_click=make_delete(s))], icon=ft.Icons.MORE_VERT, icon_size=14)
                    ], spacing=2),
                    on_click=make_switch(s)
                ))
            sidebar_list.update()

        def switch_session(name):
            if name == state["current_session"]: return
            save_current_state()
            state["current_session"] = name
            state["messages"] = load_msgs(name)
            # 同步系统提示词：如果当前有系统提示词且会话第一个消息是 system，则更新
            if state["messages"] and state["messages"][0].get("role") == "system":
                state["messages"][0]["content"] = state["system_prompt"]
            show_chat()

        def rename_session_dialog(name):
            ni = ft.TextField(label="新名称", value=name, border=ft.InputBorder.OUTLINE, width=250)
            dlg = ft.AlertDialog(title=ft.Text("重命名"), content=ft.Column([ni], spacing=10), actions=[ft.TextButton("取消"), ft.Button("确认")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and ni.value.strip() != name and rename_session(name, ni.value.strip()) or True) or
                show_chat()
            )
            show_dialog(page, dlg)

        def delete_session_dialog(name):
            def do_del():
                delete_session(name)
                if state["current_session"] == name:
                    sessions = list_sessions()
                    state["current_session"] = sessions[0] if sessions else ""
                    state["messages"] = load_msgs(sessions[0]) if sessions else []
                show_chat()
            confirm_dialog(page, "确认删除", f"确定删除会话「{name}」？", do_del)

        def create_new_session(e):
            ni = ft.TextField(label="会话名称", hint_text="输入名称", autofocus=True, border=ft.InputBorder.OUTLINE, width=250)
            dlg = ft.AlertDialog(title=ft.Text("新建会话"), content=ft.Column([ni], spacing=10), actions=[ft.TextButton("取消"), ft.Button("创建")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and create_session(ni.value.strip(), state["system_prompt"]) or True) or
                show_chat()
            )
            show_dialog(page, dlg)

        sidebar = ft.Container(width=layout["sidebar_width"], padding=8, bgcolor=colors["card"], content=ft.Column([
            ft.Row([ft.Button("+ 新建", on_click=create_new_session, height=32), ft.IconButton(ft.Icons.REFRESH, icon_size=16, tooltip="刷新", on_click=lambda e: refresh_sidebar())], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            sidebar_search, sidebar_list
        ], spacing=4, expand=True))

        # == 文件引用按钮（输入框旁）==
        file_ref_btn = ft.IconButton(ft.Icons.ATTACH_FILE, icon_size=20, tooltip="引用文件（跳转到文件页面）", on_click=lambda e: (save_current_state(), show_files()))

        main_column.controls.extend([header, ft.Row([sidebar, ft.Container(content=chat_list, expand=True)], expand=True, spacing=0), ft.Container(padding=layout["padding"], content=ft.Column([ref_chips, quick_bar, ft.Row([file_ref_btn, chat_input, send_btn], spacing=layout["spacing"])], spacing=layout["spacing"])), nav_bar(colors)])
        page.update()
        update_session_info()
        load_chat()
        update_ref_chips()
        refresh_sidebar()

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
            else: items.append(ft.PopupMenuItem(content="打开", on_click=lambda e: open_file(fp)))
            items.append(ft.PopupMenuItem(content="打开所在文件夹", on_click=lambda e: subprocess.Popen(['explorer', os.path.dirname(fp)])))
            items.append(ft.PopupMenuItem(content="引用到聊天", on_click=lambda e, p=fp: on_ref_file(p)))
            items.append(ft.PopupMenuItem(content="引用并返回聊天", on_click=lambda e, p=fp: (on_ref_file(p), save_current_state(), show_chat())))
            return ft.PopupMenuButton(items=items, icon=ft.Icons.MORE_VERT, icon_size=16)

        def on_ref_file(p):
            ok, msg = add_ref_file(p)
            if ok:
                show_toast(page, f"已引用: {os.path.basename(p)} ({os.path.getsize(p):,} 字节)")
            else:
                show_toast(page, msg)
            load_files(state["current_path"])

        def make_file_row(filename, fp, icon, icon_color):
            is_refed = any(rf["path"] == fp for rf in state["ref_files"])
            return ft.Container(padding=6, border_radius=4, bgcolor=colors["selected"] if is_refed else colors["card"], content=ft.Row([ft.Icon(icon, size=16, color=icon_color), ft.Text(filename, size=13, color=colors["text"], expand=True), ft.Text("已引用", size=10, color=ft.Colors.GREEN) if is_refed else ft.Text(""), make_file_popup(filename, fp)], spacing=5), on_click=lambda e, p=fp: open_file(p))

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
            if file_content.read_only: page.clipboard.set(current_file_path); show_toast(page, "已复制文件路径"); return
            result = write_file(current_file_path, file_content.value)
            if result is True: show_toast(page, "已保存")
            else: show_toast(page, f"保存失败: {result}")

        save_copy_btn = ft.Button("保存", on_click=save_current_file)
        ref_and_back_btn = ft.Button("引用并返回聊天", on_click=lambda e: (
            add_ref_file(current_file_path) if current_file_path else None,
            save_current_state(),
            show_chat()
        ), bgcolor=ft.Colors.BLUE_200)
        file_content_wrapper.controls = [ft.Row([save_copy_btn, ref_and_back_btn, ft.Text("", expand=True), ft.IconButton(ft.Icons.COPY, icon_size=16, tooltip="复制内容", on_click=lambda e: (page.clipboard.set(file_content.value) or show_toast(page, "已复制")) if file_content.value else None)], spacing=8), file_content]
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

        # ===== 自动保存函数 =====
        def auto_save_settings():
            """自动保存设置到文件（不含 API Key）"""
            save_data = {k: state[k] for k in ["model", "theme_mode", "system_prompt", "quick_prompts", "agent_role", "agent_roles", "max_context_tokens", "max_tokens", "temperature", "thinking_enabled", "reasoning_effort", "mcp_enabled"]}
            save_data["theme_mode"] = save_data["theme_mode"].value if hasattr(save_data["theme_mode"], 'value') else save_data["theme_mode"]
            try:
                with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                    json.dump(save_data, f, ensure_ascii=False, indent=2)
            except: pass

        def on_api_key_change(e):
            key = api_key_input.value.strip()
            state["api_key"] = key
            if key and check_api_key(key):
                try:
                    state["client"] = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1") if OpenAI else None
                except: pass
                _save_secure_api_key(key)  # 自动加密保存
            else:
                state["client"] = None
                _save_secure_api_key("")  # 清空加密存储
        api_key_input.on_change = on_api_key_change

        def on_model_change(e):
            state["model"] = model_dropdown.value
            auto_save_settings()
        model_dropdown.on_change = on_model_change
        
        # Agent 角色/场景（可自定义）
        role_names = list(state.get("agent_roles", DEFAULT_AGENT_ROLES).keys())
        role_dropdown = ft.Dropdown(label="Agent 角色/场景", value=state.get("agent_role", "通用助手"), options=[ft.dropdown.Option(k) for k in role_names], border=ft.InputBorder.OUTLINE, color=colors["text"])
        role_list = ft.ListView(expand=True, spacing=5)
        def on_role_change(e):
            role = role_dropdown.value
            state["agent_role"] = role
            system_prompt_input.value = get_role_prompt(role)
            auto_save_settings()
            page.update()
        role_dropdown.on_change = on_role_change

        def load_role_list_ui():
            role_list.controls.clear()
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            for i, (rname, role_data) in enumerate(roles.items()):
                if isinstance(role_data, dict):
                    rprompt = role_data.get("prompt", "")
                    rskills = role_data.get("skills", [])
                else:
                    rprompt = role_data
                    rskills = []
                skills_str = " | ".join(rskills) if rskills else "无技能"
                role_list.controls.append(ft.Container(padding=8, border_radius=8, bgcolor=colors["card"], content=ft.Row([ft.Column([ft.Text(f"{i+1}. {rname}", size=13, weight=ft.FontWeight.W_500, color=colors["text"]), ft.Text(f"技能: {skills_str}", size=11, color=colors["text_hint"])], spacing=2, expand=True), ft.IconButton(ft.Icons.EDIT, icon_size=16, tooltip="编辑", on_click=lambda e, idx=i: edit_role(idx)), ft.IconButton(ft.Icons.DELETE, icon_size=16, tooltip="删除", on_click=lambda e, idx=i: delete_role(idx))], spacing=5)))
            page.update()

        def edit_role(idx):
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            rname = list(roles.keys())[idx]
            role_data = roles[rname]
            if isinstance(role_data, dict):
                rprompt = role_data.get("prompt", "")
                rskills = ",".join(role_data.get("skills", []))
            else:
                rprompt = role_data
                rskills = ""
            ni = ft.TextField(label="名称", value=rname, border=ft.InputBorder.OUTLINE)
            pi = ft.TextField(label="提示词", value=rprompt, multiline=True, min_lines=3, border=ft.InputBorder.OUTLINE)
            si = ft.TextField(label="技能 (逗号分隔)", value=rskills, border=ft.InputBorder.OUTLINE, hint_text="例如: 代码审查,找Bug,写注释")
            dlg = ft.AlertDialog(title=ft.Text("编辑角色"), content=ft.Column([ni, pi, si], spacing=10), actions=[ft.TextButton("取消"), ft.Button("保存")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and pi.value.strip() and (
                    (ni.value.strip() != rname and ni.value.strip() not in roles) or ni.value.strip() == rname
                ) and (
                    roles.pop(rname, None),
                    roles.__setitem__(ni.value.strip(), {"prompt": pi.value.strip(), "skills": [s.strip() for s in si.value.split(",") if s.strip()]})
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
            si = ft.TextField(label="技能 (逗号分隔)", hint_text="例如: 辩论,反驳,论证", border=ft.InputBorder.OUTLINE)
            dlg = ft.AlertDialog(title=ft.Text("新增角色"), content=ft.Column([ni, pi, si], spacing=10), actions=[ft.TextButton("取消"), ft.Button("添加")])
            dlg.actions[0].on_click = lambda e: (setattr(dlg, 'open', False), page.update())
            dlg.actions[1].on_click = lambda e: (
                setattr(dlg, 'open', False) or
                (ni.value.strip() and pi.value.strip() and ni.value.strip() not in state.setdefault("agent_roles", {}) and
                 state["agent_roles"].__setitem__(ni.value.strip(), {"prompt": pi.value.strip(), "skills": [s.strip() for s in si.value.split(",") if s.strip()]}) or True) or update_role_ui())
            show_dialog(page, dlg)

        def update_role_ui():
            roles = state.get("agent_roles", DEFAULT_AGENT_ROLES)
            role_dropdown.options = [ft.dropdown.Option(k) for k in roles.keys()]
            load_role_list_ui()
            page.update()

        system_prompt_input = ft.TextField(label="系统提示词 (System Prompt)", value=state["system_prompt"], expand=True, multiline=True, min_lines=4, max_lines=8, border=ft.InputBorder.OUTLINE, color=colors["text"], hint_text="设置AI助手的默认行为和约束...", hint_style=ft.TextStyle(color=colors["text_hint"]))
        def on_system_prompt_blur(e):
            state["system_prompt"] = system_prompt_input.value.strip()
            # 同步更新当前会话的 system prompt 并保存
            if state["messages"] and state["messages"][0].get("role") == "system":
                state["messages"][0]["content"] = state["system_prompt"]
                save_msgs(state["current_session"], state["messages"])
            auto_save_settings()
        system_prompt_input.on_blur = on_system_prompt_blur
        quick_prompts_list = ft.ListView(expand=True, spacing=5)

        # 上下文长度控制（手动输入 + 参考上限 + 字数换算）
        context_input = ft.TextField(label="最大上下文 (tokens)", value=str(state.get("max_context_tokens", 32000)), border=ft.InputBorder.OUTLINE, color=colors["text"], hint_text="推荐 32000~128000", hint_style=ft.TextStyle(color=colors["text_hint"]), width=200)
        context_hint = ft.Text("≈ 中文: 约 2.1万字 | 英文: 约 8k词 | 上限: 128K", size=11, color=colors["text_hint"])
        context_unit = ft.Text("Token 换算: 1 token ≈ 0.7 中文字 ≈ 0.3 英文单词 | 中文: tokens÷0.7≈字数 | 英文: tokens÷0.3≈词数", size=10, color=colors["text_hint"])
        def on_context_change(e):
            try:
                v = int(context_input.value.strip())
                if v < 1000: v = 1000
                if v > 128000: v = 128000
                state["max_context_tokens"] = v
                context_input.value = str(v)
                cn = int(v * 0.7)
                en = int(v * 0.3)
                context_hint.value = f"≈ 中文: 约 {cn/10000:.1f}万字 | 英文: 约 {en/1000:.0f}k词 | 上限: 128K"
                auto_save_settings()
                page.update()
            except: pass
        context_input.on_change = on_context_change

        # 回复长度控制（手动输入 + 参考上限 + 字数换算）
        max_tokens_input = ft.TextField(label="最大回复长度 (tokens)", value=str(state.get("max_tokens", 2048)), border=ft.InputBorder.OUTLINE, color=colors["text"], hint_text="推荐 512~16384", hint_style=ft.TextStyle(color=colors["text_hint"]), width=200)
        max_tokens_hint = ft.Text("≈ 中文: 约 1433字 | 英文: 约 614词 | 上限: 16K", size=11, color=colors["text_hint"])
        def on_max_tokens_change(e):
            try:
                v = int(max_tokens_input.value.strip())
                if v < 100: v = 100
                if v > 16384: v = 16384
                state["max_tokens"] = v
                max_tokens_input.value = str(v)
                cn = int(v * 0.7)
                en = int(v * 0.3)
                max_tokens_hint.value = f"≈ 中文: 约 {cn}字 | 英文: 约 {en}词 | 上限: 16K"
                auto_save_settings()
                page.update()
            except: pass
        max_tokens_input.on_change = on_max_tokens_change

        # 深度思考开关（设置页）
        thinking_switch = ft.Switch(value=state.get("thinking_enabled", False), label="深度思考模式", label_position=ft.LabelPosition.RIGHT)
        thinking_label = ft.Text("", size=12, color=colors["text"])
        def update_thinking_label():
            if state.get("thinking_enabled", False):
                ef = state.get("reasoning_effort", "high")
                thinking_label.value = f"开启 (reasoning_effort={ef})，温度等参数不生效"
            else:
                thinking_label.value = f"关闭，使用温度: {state.get('temperature', 0.7)}"
        update_thinking_label()
        def on_thinking_change(e):
            state["thinking_enabled"] = thinking_switch.value
            update_thinking_label()
            auto_save_settings()
            page.update()
        thinking_switch.on_change = on_thinking_change

        # 温度调节（仅思考模式关闭时生效）
        temperature_slider = ft.Slider(value=state.get("temperature", 0.7), min=0.1, max=2.0, divisions=19, label="{value}", width=200)
        temperature_value = ft.Text(f"{state.get('temperature', 0.7)}", size=12, color=colors["text"])
        def on_temp_slider_change(e):
            state["temperature"] = temperature_slider.value
            temperature_value.value = f"{temperature_slider.value}"
            update_thinking_label()
            auto_save_settings()
            page.update()
        temperature_slider.on_change = on_temp_slider_change

        # MCP 工具开关（默认关闭）
        mcp_switch = ft.Switch(value=state.get("mcp_enabled", False), label="MCP 工具集成", label_position=ft.LabelPosition.RIGHT)
        mcp_label = ft.Text("", size=12, color=colors["text"])
        def update_mcp_label():
            if state.get("mcp_enabled", False):
                mcp_count = sum(1 for t in TOOLS if t.get("function", {}).get("name", "").startswith("mcp_"))
                mcp_label.value = f"开启 (已加载 {mcp_count} 个 MCP 工具，与 Trae IDE 共用配置)"
                mcp_label.color = ft.Colors.GREEN
            else:
                mcp_label.value = "关闭（不加载 MCP 工具，仅使用内置工具）"
                mcp_label.color = colors["text_hint"]
        update_mcp_label()
        def on_mcp_change(e):
            state["mcp_enabled"] = mcp_switch.value
            if state["mcp_enabled"]:
                _enable_mcp()
            else:
                _disable_mcp()
            update_mcp_label()
            auto_save_settings()
            page.update()
        mcp_switch.on_change = on_mcp_change

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

        balance_text = ft.Text(state.get("balance_result", ""), size=13, color=colors["text"])
        balance_btn = ft.Button("查询余额")
        conn_status_text = ft.Text(state.get("conn_result", ""), size=12, color=colors["text"])
        conn_test_btn = ft.Button("测试连接")

        def check_balance(e):
            if not state["api_key"]: show_toast(page, "请先设置 API Key"); return
            balance_text.value = "查询中..."; balance_btn.disabled = True; page.update()
            page.run_task(_do_check_balance)

        async def _do_check_balance():
            loop = asyncio.get_running_loop()
            try:
                r = await loop.run_in_executor(
                    None,
                    lambda: requests.get(
                        "https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {state['api_key']}", "Accept": "application/json"},
                        timeout=10
                    )
                )
                if r.status_code == 200:
                    data = r.json()
                    bal = data.get("balance_infos", [])
                    if bal:
                        total = sum(float(b.get("total_balance", 0)) for b in bal)
                        balance_text.value = f"余额: ¥{total:.2f}"; balance_text.color = ft.Colors.GREEN
                    else: balance_text.value = f"余额信息: {data}"
                else: balance_text.value = f"查询失败: HTTP {r.status_code}"
            except Exception as ex: balance_text.value = f"查询失败: {ex}"
            balance_btn.disabled = False
            state["balance_result"] = balance_text.value
            page.update()
        balance_btn.on_click = check_balance

        def test_connectivity(e):
            if not state["api_key"]: show_toast(page, "请先设置 API Key"); return
            if not state["client"]: show_toast(page, "客户端未初始化，请检查 API Key 格式"); return
            conn_status_text.value = "测试中..."; conn_status_text.color = ft.Colors.GREY; conn_test_btn.disabled = True; page.update()
            page.run_task(_do_test_connectivity)

        async def _do_test_connectivity():
            loop = asyncio.get_running_loop()
            try:
                r = await loop.run_in_executor(
                    None,
                    lambda: state["client"].chat.completions.create(
                        model=state["model"], messages=[{"role": "user", "content": "ping"}], max_tokens=1
                    )
                )
                if r:
                    conn_status_text.value = "✓ 连接成功"; conn_status_text.color = ft.Colors.GREEN
                else:
                    conn_status_text.value = "✗ 连接失败"; conn_status_text.color = ft.Colors.RED
            except Exception as ex:
                conn_status_text.value = f"✗ 连接失败: {str(ex)[:30]}"; conn_status_text.color = ft.Colors.RED
            conn_test_btn.disabled = False
            state["conn_result"] = conn_status_text.value
            page.update()
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
                if os.path.exists(SECURE_KEY_PATH):
                    try: os.remove(SECURE_KEY_PATH)
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

            auto_save_settings()
            _save_secure_api_key(state["api_key"])  # 加密保存 API Key

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
            context_input, context_hint, context_unit,
            max_tokens_input, max_tokens_hint,
            ft.Row([thinking_switch, thinking_label], spacing=10),
            ft.Container(padding=ft.Padding.only(left=20, top=2, bottom=2), content=ft.Text("思考模式开启时 API 先返回推理链（reasoning_content），再返回最终答案。开启后 temperature/top_p 等参数不生效。", size=10, color=colors["text_hint"], italic=True)),
            ft.Row([ft.Text("温度", size=12, color=colors["text_hint"]), temperature_slider, temperature_value], spacing=8),
            ft.Container(padding=ft.Padding.only(left=20, top=2, bottom=2), content=ft.Text("温度控制输出随机性（0.1~2.0）。低值输出更确定/稳定，高值输出更有创造性。仅思考模式关闭时生效。", size=10, color=colors["text_hint"], italic=True)),
            ft.Divider(),
            ft.Text("MCP 工具集成", size=14, weight=ft.FontWeight.W_500, color=colors["text"]),
            ft.Row([mcp_switch, mcp_label], spacing=10),
            ft.Container(padding=ft.Padding.only(left=20, top=2, bottom=2), content=ft.Text("与 Trae IDE 共用 MCP 服务器配置。需要 npx 可用，启动时自动检测已安装的 MCP 服务器。", size=10, color=colors["text_hint"], italic=True)),
            ft.Divider(),
            ft.Row([ft.Text("快捷提示词", size=14, weight=ft.FontWeight.W_500, color=colors["text"]), ft.IconButton(ft.Icons.ADD, icon_size=18, tooltip="添加", on_click=add_quick_prompt)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            quick_prompts_list,
            ft.Row([ft.Button("保存设置", on_click=save_settings, width=140), ft.Button("清理会话", on_click=clear_cache, width=140, bgcolor=ft.Colors.RED_200)], spacing=8),
            ft.Row([ft.Button("清除所有数据", on_click=clear_all_data, width=200, bgcolor=ft.Colors.RED), ft.IconButton(ft.Icons.FOLDER_OPEN, icon_size=18, tooltip="打开数据文件夹", on_click=lambda e: subprocess.Popen(['explorer', APP_DIR]))], spacing=8),
            ft.Text(f"数据目录: {APP_DIR}", size=10, color=colors["text_hint"]),
            ft.Divider(),
            ft.Row([ft.Text(f"DeepSeek Agent v{VERSION}", size=12, color=colors["text_hint"], font_family=FONT_CJK), ft.TextButton("GitHub", icon=ft.Icons.OPEN_IN_NEW, on_click=lambda e: page.launch_url("https://github.com/FDLAlfrid/dsa_acd"), style=ft.ButtonStyle(padding=ft.Padding.only(left=4, right=4, top=0, bottom=0), color=colors["primary"]))], spacing=8, alignment=ft.MainAxisAlignment.START),
        ], spacing=12, scroll=ft.ScrollMode.AUTO), expand=True), nav_bar(colors)])
        page.update()
        load_role_list_ui()
        load_quick_prompts_ui()

    # 启动
    sessions = list_sessions()
    if sessions: state["current_session"] = sessions[0]; state["messages"] = load_msgs(sessions[0])
    show_chat()

ft.run(main)
