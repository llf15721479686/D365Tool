"""实体翻译 GUI 面板 —— 支持百度翻译 API 和 有道智云 API。"""

from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Set

from d365_translation_manager import D365TranslationManager, LANGUAGE_LCID, get_lang_label, normalize_language_name

MAX_RETRIES = 3
RETRY_DELAY_S = 0.5
OPENAI_FALLBACK_MODEL = "gpt-5.4-mini"
OPENAI_MAX_RETRIES = 6
OPENAI_RETRY_DELAY_S = 2.0


class TranslationPanel:
    """实体翻译面板。"""

    def __init__(self, gui: Any, parent: ttk.Frame) -> None:
        self.gui = gui
        self.parent = parent
        self.translator: Optional[D365TranslationManager] = None
        self.source_texts: List[str] = []
        self.translation_data: Dict[str, Dict[str, str]] = {}
        self.selected_languages: Set[str] = set()
        self._selected_cell: Optional[tuple[str, int]] = None
        self._cell_highlight: Optional[tk.Label] = None
        self._import_tree: Optional[ET.ElementTree] = None
        self._import_source_path: Optional[Path] = None
        self._import_result: Optional[Dict[str, int]] = None
        self._openai_missing_warned = False
        self._loading = False
        self._build()

    def _build(self) -> None:
        # 标题行固定；输入区和结果区按 1:2 分配剩余高度。
        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(1, weight=1, uniform="translation_body")
        self.parent.rowconfigure(2, weight=2, uniform="translation_body")

        # ==================== 标题行 ====================
        title_frame = ttk.Frame(self.parent, padding=(8, 6, 8, 4))
        title_frame.grid(row=0, column=0, sticky="ew")
        ttk.Label(title_frame, text="实体翻译", font=("", 11, "bold")).pack(
            side="left", padx=(0, 12)
        )
        self.status_var = tk.StringVar(value="准备就绪。请在配置中设置百度/有道 API。")
        ttk.Label(title_frame, textvariable=self.status_var, foreground="#666").pack(
            side="left", padx=(12, 0)
        )

        # ==================== 输入区域（占1/3高度） ====================
        input_frame = ttk.LabelFrame(self.parent, text="输入待翻译文本（每行一个词）", padding=4)
        input_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        input_frame.columnconfigure(0, weight=1)
        input_frame.rowconfigure(0, weight=1)

        self.input_text = tk.Text(input_frame, wrap="word", relief="solid", borderwidth=1)
        self.input_text.grid(row=0, column=0, sticky="nsew")
        input_scroll = ttk.Scrollbar(input_frame, orient="vertical", command=self.input_text.yview)
        input_scroll.grid(row=0, column=1, sticky="ns")
        self.input_text.configure(yscrollcommand=input_scroll.set)

        # ==================== 中间区域：左侧翻译列配置 + 右侧表格（占2/3高度） ====================
        middle_frame = ttk.Frame(self.parent)
        middle_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))
        # 左侧配置窄一些，右侧表格宽一些
        middle_frame.columnconfigure(0, weight=0, minsize=200)
        middle_frame.columnconfigure(1, weight=1)
        middle_frame.rowconfigure(0, weight=1)

        # --- 左侧：翻译列配置 ---
        left_frame = ttk.LabelFrame(middle_frame, text="翻译列配置", padding=(6, 4))
        left_frame.grid(row=0, column=0, sticky="nswe", padx=(0, 6))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(1, weight=1)

        select_all_frame = ttk.Frame(left_frame)
        select_all_frame.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Button(select_all_frame, text="全选", command=self._select_all_langs, width=6).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(select_all_frame, text="全不选", command=self._deselect_all_langs, width=6).pack(
            side="left"
        )

        # 语言复选框容器（带滚动）
        lang_canvas_frame = ttk.Frame(left_frame)
        lang_canvas_frame.grid(row=1, column=0, sticky="nsew")
        lang_canvas_frame.columnconfigure(0, weight=1)
        lang_canvas_frame.rowconfigure(0, weight=1)

        lang_canvas = tk.Canvas(lang_canvas_frame, highlightthickness=0, width=160)
        lang_canvas.grid(row=0, column=0, sticky="nsew")
        lang_scroll = ttk.Scrollbar(
            lang_canvas_frame, orient="vertical", command=lang_canvas.yview
        )
        lang_scroll.grid(row=0, column=1, sticky="ns")
        lang_canvas.configure(yscrollcommand=lang_scroll.set)

        lang_inner = ttk.Frame(lang_canvas)
        lang_canvas_window = lang_canvas.create_window((0, 0), window=lang_inner, anchor="nw")

        self.lang_vars: Dict[str, tk.BooleanVar] = {}
        for idx, lang in enumerate(D365TranslationManager.AVAILABLE_LANGUAGES):
            var = tk.BooleanVar(value=False)
            self.lang_vars[lang] = var
            cb = ttk.Checkbutton(
                lang_inner,
                text=lang,
                variable=var,
                command=self._on_lang_toggle,
            )
            cb.grid(row=idx, column=0, sticky="w", padx=4, pady=1)

        def _configure_lang_inner(_event: Any = None) -> None:
            lang_canvas.configure(scrollregion=lang_canvas.bbox("all"))

        def _configure_lang_canvas(_event: Any = None) -> None:
            lang_canvas.itemconfig(lang_canvas_window, width=lang_canvas.winfo_width())

        lang_inner.bind("<Configure>", _configure_lang_inner)
        lang_canvas.bind("<Configure>", _configure_lang_canvas)

        def _on_lang_mousewheel(event: Any) -> None:
            lang_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        lang_canvas.bind("<MouseWheel>", _on_lang_mousewheel)
        lang_inner.bind("<MouseWheel>", _on_lang_mousewheel)

        # --- 右侧：按钮 + 结果表格 ---
        right_frame = ttk.Frame(middle_frame)
        right_frame.grid(row=0, column=1, sticky="nsew")
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(1, weight=1)

        # 按钮行
        btn_bar = ttk.Frame(right_frame)
        btn_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(btn_bar, text="一键翻译", command=self._translate_all, width=12).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_bar, text="一键复制", command=self._copy_selected, width=12).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_bar, text="导入文件", command=self._translate_import_file, width=10).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_bar, text="导出文件", command=self._export_import_file, width=10).pack(
            side="left", padx=(0, 6)
        )
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="0%")
        progress_wrap = ttk.Frame(btn_bar)
        progress_wrap.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.progress_bar = ttk.Progressbar(
            progress_wrap,
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_wrap, textvariable=self.progress_text_var, width=6).grid(
            row=0, column=1, sticky="e", padx=(6, 0)
        )
        progress_wrap.columnconfigure(0, weight=1)

        # 结果表格
        result_frame = ttk.LabelFrame(right_frame, text="翻译结果", padding=2)
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        self.result_columns: List[str] = []
        self.result_tree = self._create_grid_treeview(result_frame)
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        self.result_y_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self._scroll_result_y)
        self.result_y_scroll.grid(row=0, column=1, sticky="ns")
        self.result_x_scroll = ttk.Scrollbar(result_frame, orient="horizontal", command=self._scroll_result_x)
        self.result_x_scroll.grid(row=1, column=0, sticky="ew")
        self.result_tree.configure(
            yscrollcommand=self._on_result_yview,
            xscrollcommand=self._on_result_xview,
        )
        self._cell_highlight = tk.Label(
            self.result_tree,
            anchor="w",
            background="white",
            borderwidth=0,
            padx=3,
            highlightbackground="#e00000",
            highlightcolor="#e00000",
            highlightthickness=2,
        )
        self._cell_highlight.bind("<Button-1>", lambda _event: "break")
        self.result_tree.bind("<Configure>", lambda _event: self._position_selected_cell())

    def _create_grid_treeview(self, parent: ttk.Frame) -> ttk.Treeview:
        """创建带单元格网格线的 Treeview，支持单元格选择。"""
        style = ttk.Style()
        style.configure(
            "Grid.Treeview",
            background="white",
            foreground="black",
            fieldbackground="white",
            rowheight=24,
        )
        style.configure(
            "Grid.Treeview.Heading",
            background="#e0e0e0",
            foreground="black",
            font=("", 9, "bold"),
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Grid.Treeview",
            background=[("selected", "#0078d4")],
            foreground=[("selected", "white")],
        )

        tree = ttk.Treeview(
            parent,
            show="headings",
            selectmode="extended",
            style="Grid.Treeview",
        )
        tree.bind("<Button-1>", self._on_result_row_click)
        tree.bind("<Double-1>", self._on_cell_double_click)
        return tree

    def _scroll_result_y(self, *args: Any) -> None:
        self.result_tree.yview(*args)
        self._position_selected_cell()

    def _scroll_result_x(self, *args: Any) -> None:
        self.result_tree.xview(*args)
        self._position_selected_cell()

    def _on_result_yview(self, *args: Any) -> None:
        self.result_y_scroll.set(*args)
        self._position_selected_cell()

    def _on_result_xview(self, *args: Any) -> None:
        self.result_x_scroll.set(*args)
        self._position_selected_cell()

    def _on_result_row_click(self, _event: Any) -> None:
        """单击用于选择一行或多行；双击才进入单元格选择。"""
        self._hide_selected_cell()

    def _on_cell_double_click(self, event: Any) -> str:
        """双击单元格时，只用红框标记当前单元格。"""
        tree = self.result_tree
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            self._hide_selected_cell()
            return "break"
        column = tree.identify_column(event.x)
        item = tree.identify_row(event.y)
        if not item or not column:
            self._hide_selected_cell()
            return "break"
        col_idx = int(column.replace("#", "")) - 1
        values = tree.item(item, "values")
        if col_idx < 0 or col_idx >= len(values):
            self._hide_selected_cell()
            return "break"
        tree.selection_remove(tree.selection())
        tree.focus(item)
        self._selected_cell = (item, col_idx)
        self._position_selected_cell()
        cell_value = values[col_idx]
        col_name = self.result_columns[col_idx] if col_idx < len(self.result_columns) else ""
        self.status_var.set(f"选中单元格: {col_name} = {cell_value}")
        return "break"

    def _position_selected_cell(self) -> None:
        if not self._cell_highlight or not self._selected_cell:
            return
        item, col_idx = self._selected_cell
        if not self.result_tree.exists(item):
            self._hide_selected_cell()
            return
        column_id = f"#{col_idx + 1}"
        bbox = self.result_tree.bbox(item, column_id)
        if not bbox:
            self._cell_highlight.place_forget()
            return
        values = self.result_tree.item(item, "values")
        text = values[col_idx] if col_idx < len(values) else ""
        x, y, width, height = bbox
        self._cell_highlight.configure(text=text)
        self._cell_highlight.place(x=x, y=y, width=width, height=height)
        self._cell_highlight.lift()

    def _hide_selected_cell(self) -> None:
        self._selected_cell = None
        if self._cell_highlight:
            self._cell_highlight.place_forget()

    def _on_lang_toggle(self) -> None:
        self.selected_languages = {lang for lang, var in self.lang_vars.items() if var.get()}

    def _select_all_langs(self) -> None:
        for var in self.lang_vars.values():
            var.set(True)
        self._on_lang_toggle()

    def _deselect_all_langs(self) -> None:
        for var in self.lang_vars.values():
            var.set(False)
        self._on_lang_toggle()

    def _init_translator(self) -> bool:
        config_path = self.gui._get_config_path()
        api_cfg = D365TranslationManager.read_translation_api_config(config_path)
        self.translator = D365TranslationManager(
            baidu_app_id=api_cfg["baidu_app_id"],
            baidu_secret_key=api_cfg["baidu_secret_key"],
            baidu_api_url=api_cfg["baidu_api_url"],
            youdao_app_key=api_cfg["youdao_app_key"],
            youdao_app_secret=api_cfg["youdao_app_secret"],
            youdao_api_url=api_cfg["youdao_api_url"],
        )
        return True

    def _translate_with_retry(self, text: str, lang: str) -> str:
        """带重试机制的翻译；失败时返回空串，由调用方决定兜底。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self.translator.translate(text, lang)
                if result:
                    return result
            except Exception:
                pass
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
        return ""

    def _translate_all(self) -> None:
        if self._loading:
            return

        raw_text = self.input_text.get("1.0", "end").strip()
        if not raw_text:
            messagebox.showinfo("提示", "请先输入待翻译的文本。", parent=self.gui.root)
            return

        self.source_texts = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not self.source_texts:
            messagebox.showinfo("提示", "请输入至少一个词。", parent=self.gui.root)
            return

        active_langs = self._active_languages_sorted()
        if not active_langs:
            messagebox.showwarning("提示", "请先在左侧勾选至少一个目标语言。", parent=self.gui.root)
            return

        self._loading = True
        self.translator = None
        total = len(self.source_texts) * len(active_langs)
        self._update_progress(0, total)
        self.status_var.set("正在翻译...")

        def worker() -> None:
            results: Dict[str, Dict[str, str]] = {}
            cache_hits = 0
            api_calls = 0
            done = 0
            try:
                for text in self.source_texts:
                    results[text] = {}
                    for lang in active_langs:
                        cached = self._get_cached_translation(text, lang)
                        if cached is not None:
                            translated = cached
                            cache_hits += 1
                        else:
                            if self.translator is None:
                                self._init_translator()
                            translated = self._translate_with_retry(text, lang)
                            if translated:
                                self._save_cached_translation(text, lang, translated)
                            else:
                                translated = text
                            api_calls += 1
                        results[text][lang] = translated
                        done += 1
                        self.gui.root.after(
                            0,
                            lambda d=done, t=total: self._update_progress(d, t),
                        )
            except Exception as exc:
                self.gui.root.after(0, lambda e=exc: self._on_translate_failed(e))
                return

            def on_done() -> None:
                self._loading = False
                self.translation_data = results
                self._rebuild_result_table()
                self._update_progress(total, total)
                error_texts = [
                    t for t in self.source_texts
                    if t in results and all(not results[t].get(l, "") for l in active_langs)
                ]
                summary = (
                    f"翻译完成，共 {len(self.source_texts)} 个词，{len(active_langs)} 种语言。"
                    f" 缓存命中 {cache_hits} 条，新翻译 {api_calls} 条。"
                )
                if error_texts:
                    summary += f" 其中 {len(error_texts)} 个词全部翻译失败。"
                self.status_var.set(summary)
                self.gui._append_log(
                    f"[实体翻译] 完成：{len(self.source_texts)} 个词 → {len(active_langs)} 种语言"
                    + f"，缓存命中 {cache_hits}，新翻译 {api_calls}"
                    + (f"，{len(error_texts)} 个词失败" if error_texts else "")
                )
                if hasattr(self.gui, "_refresh_translation_records_panel"):
                    self.gui._refresh_translation_records_panel()

            self.gui.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _active_languages_sorted(self) -> List[str]:
        return sorted(
            self.selected_languages,
            key=lambda x: D365TranslationManager.AVAILABLE_LANGUAGES.index(x)
            if x in D365TranslationManager.AVAILABLE_LANGUAGES
            else 999,
        )

    def _update_progress(self, done: int, total: int) -> None:
        pct = int(done / total * 100) if total else 0
        self.progress_var.set(pct)
        self.progress_text_var.set(f"{pct}%")

    def _on_translate_failed(self, exc: Exception) -> None:
        self._loading = False
        self.status_var.set(f"翻译失败: {exc}")
        messagebox.showerror("翻译失败", str(exc), parent=self.gui.root)
        self.gui._append_log(f"[实体翻译] 失败：{exc}")

    def _get_cached_translation(self, text: str, lang: str) -> Optional[str]:
        if not hasattr(self.gui, "op_logger"):
            return None
        try:
            return self.gui.op_logger.get_cached_translation(text, normalize_language_name(lang))
        except Exception as exc:
            self.gui._append_log(f"[实体翻译] 读取缓存失败：{exc}")
            return None

    def _save_cached_translation(self, text: str, lang: str, translated: str) -> None:
        if not hasattr(self.gui, "op_logger"):
            return
        try:
            clean_lang = normalize_language_name(lang)
            self.gui.op_logger.upsert_cached_translation(
                text,
                clean_lang,
                get_lang_label(clean_lang),
                self._normalize_cell_value(translated),
            )
        except Exception as exc:
            self.gui._append_log(f"[实体翻译] 保存缓存失败：{exc}")

    def _translate_text_cached(self, text: str, lang: str) -> tuple[str, bool, bool]:
        source = self._normalize_cell_value(text)
        cached = self._get_cached_translation(source, lang)
        if cached is not None:
            if self._needs_openai_fallback(source, cached, lang):
                openai_result = self._translate_with_openai(source, lang)
                if openai_result and not self._needs_openai_fallback(source, openai_result, lang):
                    self._save_cached_translation(source, lang, openai_result)
                    return openai_result, False, False
                return "", False, True
            return cached, True, False
        if self.translator is None:
            self._init_translator()
        translated = self._translate_with_retry(source, lang)
        if self._needs_openai_fallback(source, translated, lang):
            openai_result = self._translate_with_openai(source, lang)
            if openai_result and not self._needs_openai_fallback(source, openai_result, lang):
                translated = openai_result
            else:
                return "", False, True
        if translated:
            self._save_cached_translation(source, lang, translated)
            return translated, False, False
        return "", False, True

    def _needs_openai_fallback(self, source_text: str, translated_text: str, lang: str) -> bool:
        source = self._normalize_for_compare(source_text)
        translated = self._normalize_for_compare(translated_text)
        return bool(source and translated and source == translated and normalize_language_name(lang) != "汉语")

    @staticmethod
    def _normalize_for_compare(value: str) -> str:
        return "".join(str(value).split()).casefold()

    def _read_openai_config(self) -> tuple[str, str, str]:
        api_key = os.environ.get("D365TOOL_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        model = os.environ.get("D365TOOL_OPENAI_MODEL") or OPENAI_FALLBACK_MODEL
        api_url = os.environ.get("D365TOOL_OPENAI_API_URL") or "https://api.openai.com/v1/responses"
        if not api_key:
            try:
                config_path = self.gui._get_config_path()
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                api_key = str(
                    cfg.get("openai_api_key")
                    or cfg.get("OpenAIAPIKey")
                    or cfg.get("D365ToolAPI")
                    or ""
                ).strip()
                model = str(cfg.get("openai_model") or cfg.get("OpenAIModel") or model).strip()
                api_url = str(cfg.get("openai_api_url") or cfg.get("OpenAIApiUrl") or api_url).strip()
            except Exception:
                pass
        return api_key.strip(), model.strip() or OPENAI_FALLBACK_MODEL, api_url.strip()

    def _translate_with_openai(self, text: str, lang: str) -> str:
        api_key, model, api_url = self._read_openai_config()
        if not api_key:
            if not self._openai_missing_warned:
                self.gui._append_log("[实体翻译] OpenAI API Key 未配置，重复文本将保留原翻译。请设置环境变量 D365TOOL_OPENAI_API_KEY 或 config.json 的 openai_api_key。")
                self._openai_missing_warned = True
            return ""
        prompt = (
            "Translate the following Chinese source text into the target language for Microsoft Dynamics 365 UI labels. "
            "Return only the translation text, no explanations, no quotes. "
            "Keep product names, schema names, placeholders, numbers, and punctuation when appropriate.\n"
            f"Target language: {lang}\n"
            f"Chinese source: {text}"
        )
        payload = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt}
                    ],
                }
            ],
            "temperature": 0.1,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            api_url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error = ""
        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                translated = self._normalize_cell_value(self._extract_openai_text(result))
                if translated:
                    return translated
                last_error = "返回结果为空"
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.reason}"
                retry_after = exc.headers.get("Retry-After") if exc.headers else ""
                if exc.code != 429 and 500 > exc.code:
                    break
                delay = self._openai_retry_delay(attempt, retry_after)
                self.gui._append_log(
                    f"[实体翻译] OpenAI 兜底翻译重试 {attempt}/{OPENAI_MAX_RETRIES}: {text} → {lang} ({last_error})，等待 {delay:.1f}s"
                )
                time.sleep(delay)
            except Exception as exc:
                last_error = str(exc)
                delay = self._openai_retry_delay(attempt, "")
                self.gui._append_log(
                    f"[实体翻译] OpenAI 兜底翻译重试 {attempt}/{OPENAI_MAX_RETRIES}: {text} → {lang} ({last_error})，等待 {delay:.1f}s"
                )
                time.sleep(delay)
        self.gui._append_log(f"[实体翻译] OpenAI 兜底翻译最终失败: {text} → {lang} ({last_error})")
        return ""

    def _openai_retry_delay(self, attempt: int, retry_after: str) -> float:
        try:
            if retry_after:
                return min(60.0, max(1.0, float(retry_after)))
        except ValueError:
            pass
        return min(60.0, OPENAI_RETRY_DELAY_S * (2 ** max(0, attempt - 1)))

    def _extract_openai_text(self, result: Dict[str, Any]) -> str:
        text = result.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        parts: List[str] = []
        for item in result.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict):
                    value = content.get("text")
                    if isinstance(value, str):
                        parts.append(value)
        return "".join(parts).strip()

    def _lcid_to_lang(self) -> Dict[str, str]:
        return {lcid: lang for lang, lcid in LANGUAGE_LCID.items()}

    def _selected_import_langs(self) -> List[str]:
        selected_lcids = {LANGUAGE_LCID.get(normalize_language_name(lang), "") for lang in self._active_languages_sorted()}
        selected_lcids.discard("")
        selected_lcids.discard("2052")
        lcid_to_lang = self._lcid_to_lang()
        return [lcid_to_lang[lcid] for lcid in selected_lcids if lcid in lcid_to_lang]

    def _rebuild_result_table(self) -> None:
        """根据当前选中的语言重建表格，列标题带 D365 LCID 编码。
        
        注意：删除源词列，只显示翻译列。
        """
        tree = self.result_tree
        self._hide_selected_cell()
        for item in tree.get_children():
            tree.delete(item)

        active_langs = self._active_languages_sorted()
        # 完全删除源词列，只保留翻译列
        self.result_columns = active_langs

        tree["columns"] = self.result_columns

        # 设置列宽
        col_width = max(140, min(220, 1000 // len(active_langs))) if active_langs else 200
        for lang in active_langs:
            tree.heading(lang, text=get_lang_label(lang))
            tree.column(lang, width=col_width, anchor="w", stretch=True)

        # 填充数据（只填充翻译结果，不填充源词）
        for text in self.source_texts:
            if text in self.translation_data:
                values = []
                has_any = False
                for lang in active_langs:
                    val = self._normalize_cell_value(self.translation_data[text].get(lang, ""))
                    values.append(val)
                    if val:
                        has_any = True
                if has_any:
                    tree.insert("", "end", values=tuple(values))

    @staticmethod
    def _normalize_cell_value(value: str) -> str:
        """让每个翻译结果粘贴到 Excel 时始终只占一个单元格。"""
        return " ".join(str(value).replace("\t", " ").splitlines()).strip()

    def _translate_import_file(self) -> None:
        if self._loading:
            return
        target_langs = self._selected_import_langs()
        if not target_langs:
            messagebox.showwarning("提示", "请先在左侧勾选至少一个非中文目标语言。", parent=self.gui.root)
            return
        file_path = filedialog.askopenfilename(
            parent=self.gui.root,
            title="选择 CrmTranslations.xml",
            filetypes=[("XML 文件", "*.xml"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._loading = True
        self.translator = None
        self._import_tree = None
        self._import_source_path = None
        self._import_result = None
        src_path = Path(file_path)
        self._update_progress(0, 1)
        self.status_var.set("正在导入并翻译文件...")

        def worker() -> None:
            try:
                tree, result = self._translate_crm_translations_file(src_path, target_langs)
            except Exception as exc:
                self.gui.root.after(0, lambda e=exc: self._on_translate_failed(e))
                return

            def on_done() -> None:
                self._loading = False
                self._import_tree = tree
                self._import_source_path = src_path
                self._import_result = result
                self._update_progress(result["done"], result["total"])
                self.status_var.set(
                    f"文件已导入并翻译：更新 {result['updated']}，缓存命中 {result['cache_hits']}，新翻译 {result['api_calls']}，保留原文 {result['failed']}。请点击导出文件。"
                )
                self.gui._append_log(
                    f"[实体翻译] 文件已导入并翻译：{src_path}，更新 {result['updated']}，缓存命中 {result['cache_hits']}，新翻译 {result['api_calls']}，保留原文 {result['failed']}"
                )
                if hasattr(self.gui, "_refresh_translation_records_panel"):
                    self.gui._refresh_translation_records_panel()
                messagebox.showinfo("完成", "文件已导入并翻译完成，请点击“导出文件”保存。", parent=self.gui.root)

            self.gui.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _export_import_file(self) -> None:
        if self._loading:
            return
        if self._import_tree is None or self._import_source_path is None:
            messagebox.showinfo("提示", "请先点击“导入文件”完成翻译。", parent=self.gui.root)
            return
        output_path = filedialog.asksaveasfilename(
            parent=self.gui.root,
            title="导出翻译后的文件",
            initialdir=str(self._import_source_path.parent),
            initialfile=self._import_source_path.name,
            defaultextension=".xml",
            filetypes=[("XML 文件", "*.xml"), ("所有文件", "*.*")],
        )
        if not output_path:
            return
        target_path = Path(output_path)
        try:
            marker = '<?mso-application progid="Excel.Sheet"?>'
            source_head = self._import_source_path.read_text(encoding="utf-8", errors="ignore")[:300]
            should_restore_excel_marker = marker in source_head
            self._import_tree.write(str(target_path), encoding="utf-8", xml_declaration=True)
            if should_restore_excel_marker:
                self._ensure_excel_processing_instruction(target_path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.gui.root)
            return
        self.status_var.set(f"文件已导出：{target_path}")
        self.gui._append_log(f"[实体翻译] 已导出翻译文件：{target_path}")
        messagebox.showinfo("完成", f"文件已导出：\n{target_path}", parent=self.gui.root)

    def _translate_crm_translations_file(
        self,
        input_path: Path,
        target_langs: List[str],
    ) -> tuple[ET.ElementTree, Dict[str, int]]:
        ns = {
            "ss": "urn:schemas-microsoft-com:office:spreadsheet",
        }
        ET.register_namespace("", "urn:schemas-microsoft-com:office:spreadsheet")
        ET.register_namespace("o", "urn:schemas-microsoft-com:office:office")
        ET.register_namespace("x", "urn:schemas-microsoft-com:office:excel")
        ET.register_namespace("ss", "urn:schemas-microsoft-com:office:spreadsheet")
        ET.register_namespace("html", "http://www.w3.org/TR/REC-html40")
        tree = ET.parse(str(input_path))
        root = tree.getroot()
        target_lcids = {LANGUAGE_LCID[lang]: lang for lang in target_langs if lang in LANGUAGE_LCID}
        tasks = self._collect_translation_cells(root, ns, target_lcids)
        if not tasks:
            raise RuntimeError("没有找到可翻译的语言编码列，请确认文件表头包含中文源列 2052 和已勾选语言的 LCID 编码。")
        total = len(tasks)
        cache_hits = 0
        api_calls = 0
        updated = 0
        failed = 0
        done = 0
        self.gui.root.after(0, lambda: self._update_progress(0, total))
        for source_text, lang, data_elem in tasks:
            original_text = str(data_elem.text or "").strip()
            translated, from_cache, failed_translate = self._translate_text_cached(source_text, lang)
            if from_cache:
                cache_hits += 1
            elif failed_translate:
                failed += 1
            else:
                api_calls += 1
            final_text = translated or original_text or source_text
            data_elem.text = self._normalize_cell_value(final_text)
            data_elem.set("{urn:schemas-microsoft-com:office:spreadsheet}Type", "String")
            updated += 1
            done += 1
            self.gui.root.after(0, lambda d=done, t=total: self._update_progress(d, t))
        return tree, {"total": total, "done": done, "updated": updated, "cache_hits": cache_hits, "api_calls": api_calls, "failed": failed}

    def _restore_excel_processing_instruction(self, input_path: Path, output_path: Path) -> None:
        source_head = input_path.read_text(encoding="utf-8", errors="ignore")[:300]
        marker = '<?mso-application progid="Excel.Sheet"?>'
        if marker in source_head:
            self._ensure_excel_processing_instruction(output_path)

    def _ensure_excel_processing_instruction(self, output_path: Path) -> None:
        marker = '<?mso-application progid="Excel.Sheet"?>'
        output_text = output_path.read_text(encoding="utf-8")
        if marker in output_text:
            return
        xml_decl_end = output_text.find("?>")
        if xml_decl_end >= 0:
            insert_at = xml_decl_end + 2
            output_text = output_text[:insert_at] + "\n" + marker + output_text[insert_at:]
        else:
            output_text = marker + "\n" + output_text
        output_path.write_text(output_text, encoding="utf-8")

    def _collect_translation_cells(
        self,
        root: ET.Element,
        ns: Dict[str, str],
        target_lcids: Dict[str, str],
    ) -> List[tuple[str, str, ET.Element]]:
        tasks: List[tuple[str, str, ET.Element]] = []
        ss_index = "{urn:schemas-microsoft-com:office:spreadsheet}Index"
        for worksheet in root.findall("ss:Worksheet", ns):
            table = worksheet.find("ss:Table", ns)
            if table is None:
                continue
            rows = table.findall("ss:Row", ns)
            if not rows:
                continue
            header_cells = self._cells_by_column(rows[0], ss_index)
            col_to_lcid: Dict[int, str] = {}
            source_col = 0
            for col_idx, cell in header_cells.items():
                text = self._cell_text(cell, ns).strip()
                if text == "2052":
                    source_col = col_idx
                if text in target_lcids:
                    col_to_lcid[col_idx] = text
            if not source_col or not col_to_lcid:
                continue
            for row in rows[1:]:
                cells = self._cells_by_column(row, ss_index)
                source_text = self._cell_text(cells.get(source_col), ns).strip()
                if not source_text:
                    continue
                for col_idx, lcid in col_to_lcid.items():
                    cell = cells.get(col_idx)
                    if cell is None:
                        continue
                    data_elem = cell.find("ss:Data", ns)
                    if data_elem is None:
                        data_elem = ET.SubElement(cell, "{urn:schemas-microsoft-com:office:spreadsheet}Data")
                        data_elem.set("{urn:schemas-microsoft-com:office:spreadsheet}Type", "String")
                    tasks.append((source_text, target_lcids[lcid], data_elem))
        return tasks

    def _cells_by_column(self, row: ET.Element, ss_index_attr: str) -> Dict[int, ET.Element]:
        cells: Dict[int, ET.Element] = {}
        col_idx = 1
        for cell in list(row):
            if not str(cell.tag).endswith("Cell"):
                continue
            raw_index = cell.get(ss_index_attr)
            if raw_index:
                try:
                    col_idx = int(raw_index)
                except ValueError:
                    pass
            cells[col_idx] = cell
            col_idx += 1
        return cells

    def _cell_text(self, cell: Optional[ET.Element], ns: Dict[str, str]) -> str:
        if cell is None:
            return ""
        data = cell.find("ss:Data", ns)
        if data is None or data.text is None:
            return ""
        return str(data.text)

    def _copy_selected(self) -> None:
        """复制表格中选中的行（制表符分隔，可直接粘贴到 Excel）。"""
        tree = self.result_tree

        if self._selected_cell:
            item_id, col_idx = self._selected_cell
            values = tree.item(item_id, "values")
            if col_idx < len(values):
                self.gui.root.clipboard_clear()
                self.gui.root.clipboard_append(self._normalize_cell_value(str(values[col_idx])))
                self.status_var.set("已复制选中单元格到剪贴板，可直接粘贴到 Excel。")
                return

        selection = tree.selection()
        if not selection:
            items = tree.get_children()
            if not items:
                messagebox.showinfo("提示", "表格中没有数据。", parent=self.gui.root)
                return
            selection = items

        lines: List[str] = []

        for item_id in selection:
            values = tree.item(item_id, "values")
            if values:
                lines.append("\t".join(self._normalize_cell_value(str(v)) for v in values))

        self.gui.root.clipboard_clear()
        self.gui.root.clipboard_append("\n".join(lines))
        self.status_var.set(f"已复制 {len(selection)} 行到剪贴板，可直接粘贴到 Excel。")
        self.gui._append_log(f"[实体翻译] 已复制 {len(selection)} 行翻译结果到剪贴板")
