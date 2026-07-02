"""D365 本地 JS/HTML 调试面板。

通过 Chrome/Edge DevTools Protocol 拦截指定 D365 WebResource 请求，
把响应临时替换成本地文件内容，用于在 DEV/UAT 中免发布调试脚本或 HTML 页面。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional
import tkinter as tk

from operation_logger import OperationLogger, default_db_path

JS_DEBUG_RULES_FILE = Path(__file__).with_name("js_debug_rules.json")


class SimpleWebSocket:
    """最小 WebSocket 客户端，用于连接本机 Chrome/Edge DevTools。"""

    def __init__(self, ws_url: str, timeout: float = 10) -> None:
        parsed = urllib.parse.urlparse(ws_url)
        if parsed.scheme != "ws":
            raise RuntimeError(f"仅支持 ws:// 调试地址: {ws_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("连接浏览器调试端口失败。")

    def send_json(self, payload: Dict[str, Any]) -> None:
        self._send_text(json.dumps(payload, ensure_ascii=False))

    def _send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv_json(self) -> Optional[Dict[str, Any]]:
        while True:
            first = self.sock.recv(2)
            if not first:
                return None
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                self.sock.sendall(b"\x8a\x00")
                continue
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8", errors="replace"))

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("浏览器调试连接已断开。")
            data += chunk
        return data

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class JsCapturePanel:
    def __init__(self, gui: Any, parent: ttk.Frame) -> None:
        self.gui = gui
        self.parent = parent
        self.browser_proc: Optional[subprocess.Popen[Any]] = None
        self.ws: Optional[SimpleWebSocket] = None
        self.stop_event = threading.Event()
        self.cdp_lock = threading.Lock()
        self.cdp_id = 0
        self.rule_store = self._create_rule_store()
        self.override_rules: List[Dict[str, str]] = self._load_rules()
        self.log_rows: Dict[str, Dict[str, Any]] = {}
        self.navigate_url = ""
        self.debug_port = 0
        self.debug_target_id = ""
        self.file_mtimes: Dict[str, float] = {}
        self.env_options = self._load_env_options()
        self._build()

    def _config_path(self) -> str:
        if hasattr(self.gui, "_get_config_path"):
            return str(self.gui._get_config_path())
        return str(Path(__file__).with_name("config.json"))

    def _create_rule_store(self) -> OperationLogger:
        if hasattr(self.gui, "op_logger"):
            return self.gui.op_logger
        return OperationLogger(default_db_path(self._config_path()))

    def _load_env_options(self) -> List[Dict[str, str]]:
        config_path = ""
        if hasattr(self.gui, "_get_config_path"):
            config_path = str(self.gui._get_config_path())
        if not config_path:
            config_path = str(Path(__file__).with_name("config.json"))
        path = Path(config_path)
        if not path.exists():
            return []
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

        options: List[Dict[str, str]] = []
        seen_urls: set = set()

        def add_env(name: str, org_url: str) -> None:
            clean_url = org_url.strip().rstrip("/")
            clean_name = name.strip() or clean_url
            if not clean_url:
                return
            key = clean_url.lower()
            if key in seen_urls:
                return
            seen_urls.add(key)
            options.append({"name": clean_name, "org_url": clean_url})

        add_env(str(cfg.get("environment_name", "Dev")), str(cfg.get("org_url", "")))
        for item in cfg.get("environments", []):
            if isinstance(item, dict):
                add_env(str(item.get("name", "")), str(item.get("org_url", item.get("D365Url", ""))))
        return options

    def _load_rules(self) -> List[Dict[str, str]]:
        try:
            rows = self.rule_store.list_js_debug_rules()
            if rows:
                return [
                    {
                        "match": str(row.get("match_text", "")),
                        "file": str(row.get("local_file", "")),
                        "mime": str(row.get("mime", "")),
                    }
                    for row in rows
                ]
            if self.rule_store.get_meta("js_debug_rules_json_imported") == "1":
                return []
        except Exception:
            pass

        try:
            if not JS_DEBUG_RULES_FILE.exists():
                return []
            raw = json.loads(JS_DEBUG_RULES_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            rules: List[Dict[str, str]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                match = str(item.get("match", "")).strip()
                local_file = str(item.get("file", "")).strip()
                if match and local_file:
                    rules.append({"match": match, "file": local_file, "mime": str(item.get("mime") or self._guess_mime(Path(local_file)))})
            try:
                self.rule_store.replace_js_debug_rules(rules)
                self.rule_store.set_meta("js_debug_rules_json_imported", "1")
            except Exception:
                pass
            return rules
        except Exception:
            return []

    def _save_rules(self) -> None:
        try:
            self.rule_store.replace_js_debug_rules(self.override_rules)
            self.rule_store.set_meta("js_debug_rules_json_imported", "1")
        except Exception as exc:
            self.status_var.set(f"保存替换规则失败: {exc}")

    def _build(self) -> None:
        self.parent.rowconfigure(3, weight=1)
        self.parent.columnconfigure(0, weight=1)

        toolbar = ttk.LabelFrame(self.parent, text='本地 JS/HTML 调试', padding=10)
        toolbar.grid(row=0, column=0, sticky='ew', padx=8, pady=(8, 4))
        toolbar.columnconfigure(0, weight=1)
        toolbar.columnconfigure(1, weight=0)

        IPADY = 2
        PADX_LABEL = (0, 4)
        PADX_CTRL = (0, 10)
        PADX_BTN = (0, 8)

        form = ttk.Frame(toolbar)
        form.grid(row=0, column=0, sticky='ew')
        form.columnconfigure(3, weight=1)

        action_bar = ttk.Frame(toolbar)
        action_bar.grid(row=0, column=1, rowspan=2, sticky='ne')

        env_names = [env["name"] for env in self.env_options] or ["未配置"]
        ttk.Label(form, text='环境').grid(row=0, column=0, sticky='w', padx=PADX_LABEL, pady=2)
        self.env_var = tk.StringVar(value=env_names[0])
        ttk.Combobox(
            form, textvariable=self.env_var, state='readonly',
            values=env_names, width=14,
        ).grid(row=0, column=1, sticky='w', padx=PADX_CTRL, pady=2, ipady=IPADY)

        ttk.Label(form, text='页面路径').grid(row=0, column=2, sticky='w', padx=PADX_LABEL, pady=2)
        self.path_var = tk.StringVar(value='main.aspx')
        ttk.Entry(form, textvariable=self.path_var).grid(
            row=0, column=3, sticky='ew', padx=PADX_CTRL, pady=2, ipady=IPADY
        )

        ttk.Button(action_bar, text='开始调试', command=self.start_capture, width=10).grid(
            row=0, column=0, padx=PADX_BTN, pady=2, ipady=IPADY
        )
        ttk.Button(action_bar, text='停止', command=self.stop_capture, width=8).grid(
            row=0, column=1, padx=PADX_BTN, pady=2, ipady=IPADY
        )
        ttk.Button(action_bar, text='清空日志', command=self.clear_logs, width=10).grid(
            row=0, column=2, padx=PADX_BTN, pady=2, ipady=IPADY
        )

        ttk.Label(form, text='URL包含').grid(
            row=1, column=0, sticky='w', padx=PADX_LABEL, pady=(8, 2)
        )
        self.match_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.match_var).grid(
            row=1, column=1, sticky='ew', padx=PADX_CTRL, pady=(8, 2), ipady=IPADY
        )

        ttk.Label(form, text='本地文件').grid(
            row=1, column=2, sticky='w', padx=PADX_LABEL, pady=(8, 2)
        )
        self.local_file_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.local_file_var).grid(
            row=1, column=3, sticky='ew', padx=PADX_CTRL, pady=(8, 2), ipady=IPADY
        )

        ttk.Button(action_bar, text='浏览', command=self._browse_local_file, width=8).grid(
            row=1, column=0, padx=PADX_BTN, pady=(8, 2), ipady=IPADY
        )
        ttk.Button(action_bar, text='添加替换', command=self._add_override, width=10).grid(
            row=1, column=1, padx=PADX_BTN, pady=(8, 2), ipady=IPADY
        )
        ttk.Button(action_bar, text='删除选中', command=self._remove_override, width=10).grid(
            row=1, column=2, padx=PADX_BTN, pady=(8, 2), ipady=IPADY
        )

        self.keep_login_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(action_bar, text='保留登录状态', variable=self.keep_login_var).grid(
            row=0, column=3, sticky='w', padx=PADX_BTN, pady=2, ipady=IPADY
        )
        self.auto_reload_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(action_bar, text='保存后重新开始调试', variable=self.auto_reload_var).grid(
            row=0, column=4, sticky='w', padx=PADX_BTN, pady=2, ipady=IPADY
        )
        ttk.Button(action_bar, text='重新开始调试', command=self.start_capture, width=12).grid(
            row=1, column=3, columnspan=2, sticky='e', padx=PADX_BTN, pady=(8, 2), ipady=IPADY
        )

        self.status_var = tk.StringVar(value='添加本地文件替换规则后点击开始调试；刷新 D365 页面时，命中的 JS/HTML 会使用本地文件内容。')
        ttk.Label(self.parent, textvariable=self.status_var, foreground='#666').grid(
            row=1, column=0, sticky='ew', padx=8, pady=(0, 4)
        )

        rules_frame = ttk.LabelFrame(self.parent, text='本地替换规则', padding=(6, 4, 6, 6))
        rules_frame.grid(row=2, column=0, sticky='nsew', padx=8, pady=(0, 6))
        rules_frame.rowconfigure(0, weight=1)
        rules_frame.columnconfigure(0, weight=1)

        self.rules_tree = ttk.Treeview(
            rules_frame,
            columns=('match', 'file', 'mime'),
            show='headings',
            height=4,
        )
        self.rules_tree.heading('match', text='URL包含')
        self.rules_tree.heading('file', text='本地文件')
        self.rules_tree.heading('mime', text='类型')
        self.rules_tree.column('match', width=240, minwidth=140, stretch=True)
        self.rules_tree.column('file', width=620, minwidth=260, stretch=True)
        self.rules_tree.column('mime', width=160, minwidth=120, stretch=False)
        self.rules_tree.grid(row=0, column=0, sticky='nsew')
        rules_scroll = ttk.Scrollbar(rules_frame, orient='vertical', command=self.rules_tree.yview)
        rules_scroll.grid(row=0, column=1, sticky='ns')
        self.rules_tree.configure(yscrollcommand=rules_scroll.set)

        log_frame = ttk.LabelFrame(self.parent, text='调试日志', padding=(6, 4, 6, 6))
        log_frame.grid(row=3, column=0, sticky='nsew', padx=8, pady=(0, 6))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_tree = ttk.Treeview(
            log_frame,
            columns=('time', 'kind', 'status', 'host', 'url', 'file'),
            show='headings',
            height=8,
        )
        for column, title in (
            ('time', '时间'),
            ('kind', '类型'),
            ('status', '状态'),
            ('host', 'Host'),
            ('url', 'URL/消息'),
            ('file', '本地文件'),
        ):
            self.log_tree.heading(column, text=title)
        self.log_tree.column('time', width=90, minwidth=70, stretch=False)
        self.log_tree.column('kind', width=90, minwidth=70, stretch=False)
        self.log_tree.column('status', width=90, minwidth=70, stretch=False)
        self.log_tree.column('host', width=180, minwidth=120, stretch=True)
        self.log_tree.column('url', width=460, minwidth=220, stretch=True)
        self.log_tree.column('file', width=260, minwidth=160, stretch=True)
        self.log_tree.grid(row=0, column=0, sticky='nsew')
        log_scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_tree.yview)
        log_scroll.grid(row=0, column=1, sticky='ns')
        self.log_tree.configure(yscrollcommand=log_scroll.set)
        self.log_tree.tag_configure('error', foreground='#b00020')
        self.log_tree.tag_configure('override', foreground='#0b6bcb')
        self.log_tree.tag_configure('console', foreground='#666666')
        self.log_tree.bind('<<TreeviewSelect>>', self._on_log_selected)

        detail_frame = ttk.LabelFrame(self.parent, text='详情', padding=(6, 4, 6, 6))
        detail_frame.grid(row=4, column=0, sticky='nsew', padx=8, pady=(0, 8))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)

        self.detail_text = tk.Text(detail_frame, height=5, wrap='none')
        self.detail_text.grid(row=0, column=0, sticky='nsew')
        detail_y = ttk.Scrollbar(detail_frame, orient='vertical', command=self.detail_text.yview)
        detail_y.grid(row=0, column=1, sticky='ns')
        detail_x = ttk.Scrollbar(detail_frame, orient='horizontal', command=self.detail_text.xview)
        detail_x.grid(row=1, column=0, sticky='ew')
        self.detail_text.configure(yscrollcommand=detail_y.set, xscrollcommand=detail_x.set)

        self._refresh_rules_tree()

    def _browse_local_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择本地 JS/HTML 文件",
            filetypes=[("JS/HTML/CSS", "*.js *.html *.htm *.css"), ("所有文件", "*.*")],
            parent=self.gui.root,
        )
        if file_path:
            self.local_file_var.set(file_path)
            if not self.match_var.get().strip():
                self.match_var.set(Path(file_path).name)

    def _add_override(self) -> None:
        match = self.match_var.get().strip()
        local_file = self.local_file_var.get().strip().strip('"')
        if not match:
            messagebox.showwarning("缺少匹配条件", "请填写 URL包含，例如 OrderShipmentRequest.js 或 mcs_/Scripts/Sales/Order/OrderShipmentRequest.js。", parent=self.gui.root)
            return
        if not local_file:
            messagebox.showwarning("缺少本地文件", "请选择本地 JS/HTML 文件。", parent=self.gui.root)
            return
        path_obj = Path(local_file)
        if not path_obj.exists() or not path_obj.is_file():
            messagebox.showerror("文件不存在", f"本地文件不存在：{local_file}", parent=self.gui.root)
            return
        if self._is_too_broad_match(match):
            suggested = path_obj.name
            self.match_var.set(suggested)
            messagebox.showwarning(
                "匹配范围太大",
                "URL包含不能填写整个环境地址，否则接口、图片、图标都会被替换成本地脚本。\n\n"
                f"已帮你改成文件名：{suggested}\n"
                "如果实际脚本 URL 不包含这个文件名，请改成更具体的 WebResource 路径。",
                parent=self.gui.root,
            )
            return
        rule = {"match": match, "file": str(path_obj), "mime": self._guess_mime(path_obj)}
        self.override_rules.append(rule)
        self._refresh_rules_tree()
        self._save_rules()
        self.match_var.set("")
        self.local_file_var.set("")
        self.status_var.set("已添加替换规则。启动调试后刷新页面即可生效。")

    def _remove_override(self) -> None:
        selection = self.rules_tree.selection()
        if not selection:
            return
        indexes = sorted((int(item) for item in selection), reverse=True)
        for index in indexes:
            if 0 <= index < len(self.override_rules):
                self.override_rules.pop(index)
        self._refresh_rules_tree()
        self._save_rules()

    def _refresh_rules_tree(self) -> None:
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)
        for index, rule in enumerate(self.override_rules):
            self.rules_tree.insert("", "end", iid=str(index), values=(rule["match"], rule["file"], rule["mime"]))

    def _reload_saved_rules(self) -> None:
        self.override_rules = self._load_rules()
        self._refresh_rules_tree()

    def _valid_rules(self) -> List[Dict[str, str]]:
        valid: List[Dict[str, str]] = []
        missing: List[Dict[str, str]] = []
        for rule in self.override_rules:
            local_file = rule.get("file", "")
            if local_file and Path(local_file).exists():
                valid.append(rule)
            else:
                missing.append(rule)
        if missing:
            detail = {"missing_rules": missing}
            self._add_log_row("规则文件缺失", "跳过", f"{len(missing)} 条", "", detail, error=True)
        return valid

    def start_capture(self) -> None:
        self._reload_saved_rules()
        valid_rules = self._valid_rules()
        self.override_rules = valid_rules
        self._refresh_rules_tree()
        if not self.override_rules:
            if not messagebox.askyesno(
                "未添加替换规则",
                "还没有添加本地替换规则。是否仍然打开浏览器，只查看脚本错误和控制台日志？",
                parent=self.gui.root,
            ):
                return
        self.stop_capture(terminate_browser=True)
        self.stop_event.clear()
        url = self._target_url()
        rule_names = "; ".join(rule.get("match", "") for rule in self.override_rules)
        self._add_log_row("加载替换规则", str(len(self.override_rules)), rule_names or "无", "", {"rules": self.override_rules, "target_url": url}, tag="override")
        try:
            browser_path = self._find_browser()
            port = self._free_port()
            if self.keep_login_var.get():
                profile_dir = Path(tempfile.gettempdir()) / "d365tool-js-debug-chrome-profile"
            else:
                profile_dir = Path(tempfile.gettempdir()) / f"d365tool-js-debug-profile-{port}-{int(time.time())}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.browser_proc = subprocess.Popen(
                [
                    browser_path,
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-session-crashed-bubble",
                    "--disable-features=InfiniteSessionRestore",
                    "--new-window",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.debug_port = port
            ws_url = self._wait_for_page_ws(port)
            self._activate_debug_page()
            self.ws = SimpleWebSocket(ws_url)
            self.navigate_url = url
            self.file_mtimes = self._snapshot_file_mtimes()
            threading.Thread(target=self._capture_loop, daemon=True).start()
            if self.auto_reload_var.get():
                threading.Thread(target=self._watch_local_files, daemon=True).start()
            self.status_var.set(f"正在准备调试，稍后自动打开页面：{url}")
            self._append_log(f"[脚本JS调试] 已启动浏览器调试: {url}")
        except Exception as exc:
            self.status_var.set(f"启动失败: {exc}")
            messagebox.showerror("启动失败", str(exc), parent=self.gui.root)

    def stop_capture(self, terminate_browser: bool = True) -> None:
        self.stop_event.set()
        if self.ws:
            self.ws.close()
            self.ws = None
        if terminate_browser and self.browser_proc:
            try:
                self.browser_proc.terminate()
            except Exception:
                pass
            self.browser_proc = None
        if terminate_browser:
            self.debug_port = 0
            self.debug_target_id = ""
        if terminate_browser:
            self.status_var.set("已停止调试。")

    def clear(self) -> None:
        self.clear_logs()

    def clear_logs(self) -> None:
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        self.log_rows.clear()
        self.detail_text.delete("1.0", "end")

    def _target_url(self) -> str:
        base = ""
        selected_name = self.env_var.get().strip()
        for env in self.env_options:
            if env["name"] == selected_name:
                base = env["org_url"]
                break
        path = self.path_var.get().strip() or "main.aspx"
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not base:
            raise RuntimeError("JS 调试环境未配置，请在 config.json 中配置 org_url 或 environments。")
        return base.rstrip("/") + "/" + path.lstrip("/")

    def _find_browser(self) -> str:
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / r"Google\Chrome\Application\chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / r"Google\Chrome\Application\chrome.exe",
        ]
        for browser_path in candidates:
            if browser_path.exists():
                return str(browser_path)
        raise RuntimeError("未找到 Google Chrome，请先安装 Chrome，或确认 chrome.exe 路径。")

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _activate_debug_page(self) -> None:
        if not self.debug_port or not self.debug_target_id:
            return
        endpoint = f"http://127.0.0.1:{self.debug_port}/json/activate/{self.debug_target_id}"
        try:
            with urllib.request.urlopen(endpoint, timeout=1):
                pass
        except Exception:
            pass

    def _wait_for_page_ws(self, port: int) -> str:
        endpoint = f"http://127.0.0.1:{port}/json/list"
        last_error = ""
        for _ in range(80):
            try:
                with urllib.request.urlopen(endpoint, timeout=1) as resp:
                    pages = json.loads(resp.read().decode("utf-8"))
                candidates = [page for page in pages if page.get("type") == "page" and page.get("webSocketDebuggerUrl")]
                blank_pages = [page for page in candidates if str(page.get("url", "")).startswith("about:blank")]
                selected = (blank_pages or candidates)[-1] if (blank_pages or candidates) else None
                if selected:
                    self.debug_target_id = str(selected.get("id", ""))
                    return str(selected["webSocketDebuggerUrl"])
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise RuntimeError(f"无法连接浏览器调试端口: {last_error}")

    def _capture_loop(self) -> None:
        try:
            self._send_cdp("Network.enable")
            self._send_cdp("Network.setCacheDisabled", {"cacheDisabled": True})
            self._send_cdp("Network.setBypassServiceWorker", {"bypass": True})
            self._send_cdp("Runtime.enable")
            self._send_cdp("Log.enable")
            self._send_cdp("Page.enable")
            self._send_cdp("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
            if self.navigate_url:
                self._send_cdp("Page.navigate", {"url": self.navigate_url})
                self.gui.root.after(0, lambda: self.status_var.set(f"正在调试：{self.navigate_url}"))
            while not self.stop_event.is_set() and self.ws:
                message = self.ws.recv_json()
                if not message:
                    break
                self._handle_cdp_event(message)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.gui.root.after(0, lambda e=exc: self.status_var.set(f"调试连接断开: {e}"))

    def _send_cdp(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if not self.ws:
            return
        with self.cdp_lock:
            self.cdp_id += 1
            self.ws.send_json({"id": self.cdp_id, "method": method, "params": params or {}})

    def _snapshot_file_mtimes(self) -> Dict[str, float]:
        mtimes: Dict[str, float] = {}
        for rule in self.override_rules:
            local_file = rule.get("file", "")
            try:
                mtimes[local_file] = Path(local_file).stat().st_mtime
            except OSError:
                mtimes[local_file] = 0.0
        return mtimes

    def _watch_local_files(self) -> None:
        while not self.stop_event.is_set():
            time.sleep(1.0)
            if not self.ws or not self.auto_reload_var.get():
                continue
            current = self._snapshot_file_mtimes()
            changed_files = [file for file, mtime in current.items() if self.file_mtimes.get(file) not in {None, mtime}]
            if not changed_files:
                self.file_mtimes = current
                continue
            self.file_mtimes = current
            names = "; ".join(Path(file).name for file in changed_files)
            detail = {
                "changed_files": changed_files,
                "action": "start_capture()",
                "note": "本地文件变更后直接复用开始调试按钮的完整逻辑。",
            }
            self._add_log_row("本地文件变更", "重新开始调试", names, "", detail, tag="override")
            self.gui.root.after(0, self.start_capture)
            return

    def _handle_cdp_event(self, message: Dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "Fetch.requestPaused":
            self._handle_fetch_request(params)

    def _handle_fetch_request(self, params: Dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        request = params.get("request", {})
        url = str(request.get("url", ""))
        rule = self._match_rule(url)
        if not rule:
            self._continue_request(request_id)
            return

        local_file = rule["file"]
        try:
            data = Path(local_file).read_bytes()
            encoded = base64.b64encode(data).decode("ascii")
            self._send_cdp(
                "Fetch.fulfillRequest",
                {
                    "requestId": request_id,
                    "responseCode": 200,
                    "responsePhrase": "OK",
                    "responseHeaders": [
                        {"name": "Content-Type", "value": rule["mime"]},
                        {"name": "Cache-Control", "value": "no-store, no-cache, must-revalidate"},
                        {"name": "Pragma", "value": "no-cache"},
                        {"name": "Access-Control-Allow-Origin", "value": "*"},
                    ],
                    "body": encoded,
                },
            )
            detail = {"url": url, "local_file": local_file, "match": rule["match"], "mime": rule["mime"]}
            self._add_log_row("本地替换", "200", url, local_file, detail, tag="override")
        except Exception as exc:
            self._continue_request(request_id)
            detail = {"url": url, "local_file": local_file, "error": str(exc)}
            self._add_log_row("替换失败", "ERR", url, local_file, detail, error=True)

    def _continue_request(self, request_id: str) -> None:
        if request_id:
            self._send_cdp("Fetch.continueRequest", {"requestId": request_id})

    def _match_rule(self, url: str) -> Optional[Dict[str, str]]:
        lower_url = url.lower()
        for rule in self.override_rules:
            match = rule["match"].strip()
            if self._is_too_broad_match(match):
                continue
            if match.lower() in lower_url:
                return rule
        return None

    def _is_too_broad_match(self, match: str) -> bool:
        normalized = match.strip().lower().rstrip("/")
        if not normalized:
            return True
        broad_values = set()
        for env in self.env_options:
            env_url = env.get("org_url", "").strip()
            if not env_url:
                continue
            broad_values.add(env_url.lower().rstrip("/"))
            broad_values.add(urllib.parse.urlparse(env_url).netloc.lower())
        if normalized in broad_values:
            return True
        parsed = urllib.parse.urlparse(normalized)
        if parsed.scheme in {"http", "https"} and parsed.netloc and parsed.path in {"", "/"}:
            return True
        return False

    def _guess_mime(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix == ".js":
            return "application/javascript; charset=utf-8"
        if suffix in {".html", ".htm"}:
            return "text/html; charset=utf-8"
        if suffix == ".css":
            return "text/css; charset=utf-8"
        if suffix == ".json":
            return "application/json; charset=utf-8"
        guessed = mimetypes.guess_type(str(file_path))[0]
        return (guessed or "text/plain") + "; charset=utf-8"

    def _add_log_row(
        self,
        kind: str,
        status: str,
        url_or_text: str,
        local_file: str,
        detail: Any,
        error: bool = False,
        tag: str = "",
    ) -> None:
        row_id = str(time.time_ns())
        parsed = urllib.parse.urlparse(url_or_text)
        host = parsed.netloc if parsed.netloc else ""
        display_url = parsed.path + ("?" + parsed.query if parsed.query else "") if parsed.netloc else url_or_text
        if len(display_url) > 500:
            display_url = display_url[:497] + "..."
        values = (datetime.now().strftime("%H:%M:%S"), kind, status, host, display_url, local_file)
        tags = ("error",) if error or status == "ERR" else ((tag,) if tag else (("console",) if kind in {"Console", "Log"} else ()))
        self.log_rows[row_id] = {"values": values, "detail": detail, "url": url_or_text, "local_file": local_file}
        self.gui.root.after(0, lambda: self.log_tree.insert("", 0, iid=row_id, values=values, tags=tags))

    def _on_log_selected(self, _event: Any = None) -> None:
        selection = self.log_tree.selection()
        self.detail_text.delete("1.0", "end")
        if not selection:
            return
        row = self.log_rows.get(selection[0], {})
        self.detail_text.insert("1.0", json.dumps(row.get("detail", {}), ensure_ascii=False, indent=2, default=str))

    def _append_log(self, text: str) -> None:
        if hasattr(self.gui, "_append_log"):
            self.gui._append_log(text)
