"""发布历史记录 — Dev / UAT 双栏实时刷新界面。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from d365_publish_history_manager import D365PublishHistoryManager, HISTORY_LIMIT


class PublishHistoryPanel:
    REFRESH_INTERVAL_MS = 5000

    HISTORY_COLUMNS: Tuple[str, ...] = (
        "name",
        "start_time",
        "end_time",
        "version",
        "operation",
        "suboperation",
        "result",
        "error_code",
        "publisher",
    )
    HISTORY_HEADINGS: Dict[str, str] = {
        "name": "名称",
        "start_time": "开始时间",
        "end_time": "结束时间",
        "version": "版本",
        "operation": "操作",
        "suboperation": "子操作",
        "result": "结果",
        "error_code": "错误代码",
        "publisher": "发布者",
    }
    HISTORY_WIDTHS: Dict[str, int] = {
        "name": 190,
        "start_time": 130,
        "end_time": 130,
        "version": 90,
        "operation": 70,
        "suboperation": 70,
        "result": 70,
        "error_code": 90,
        "publisher": 120,
    }

    def __init__(self, gui: Any, parent: ttk.Frame) -> None:
        self.gui = gui
        self.parent = parent
        self._env_map: Dict[str, Dict[str, str]] = {}
        self._row_maps: Dict[str, Dict[str, Dict[str, Any]]] = {"dev": {}, "uat": {}}
        self._trees: Dict[str, ttk.Treeview] = {}
        self._refresh_job: Optional[str] = None
        self._loading = False
        self._auto_refresh = tk.BooleanVar(value=True)
        self._build()

    def _build(self) -> None:
        from d365_field_creator import load_environments

        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(1, weight=1)

        environments = load_environments(self.gui._get_config_path())
        dev_env: Optional[Dict[str, str]] = None
        uat_env: Optional[Dict[str, str]] = None
        for env in environments:
            name = str(env.get("name", "")).strip().lower()
            if name == "dev" and dev_env is None:
                dev_env = env
            elif name == "uat" and uat_env is None:
                uat_env = env
        if dev_env is None and environments:
            dev_env = environments[0]
        if uat_env is None:
            for env in environments:
                if dev_env and env["org_url"].rstrip("/").lower() != dev_env["org_url"].rstrip("/").lower():
                    uat_env = env
                    break
        if dev_env:
            self._env_map["dev"] = dev_env
        if uat_env:
            self._env_map["uat"] = uat_env

        toolbar = ttk.Frame(self.parent, padding=(8, 6, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="发布历史记录", font=("", 10, "bold")).pack(side="left", padx=(0, 12))
        ttk.Button(toolbar, text="立即刷新", command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(
            toolbar,
            text="自动刷新 (5秒)",
            variable=self._auto_refresh,
            command=self._on_auto_refresh_toggle,
        ).pack(side="left", padx=(0, 8))
        self.status_var = tk.StringVar(value="准备加载发布历史...")
        ttk.Label(toolbar, textvariable=self.status_var, foreground="#666").pack(side="left", padx=(12, 0))
        ttk.Label(
            toolbar,
            text="提示：点击错误代码可在下方操作日志查看详情",
            foreground="#888",
        ).pack(side="right")

        main_pane = ttk.PanedWindow(self.parent, orient=tk.VERTICAL)
        main_pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))

        if "dev" in self._env_map:
            dev_frame = self._create_history_frame(
                main_pane,
                "dev",
                f"Dev  ({self._env_map['dev'].get('org_url', '')})",
            )
            main_pane.add(dev_frame, weight=1)
        if "uat" in self._env_map:
            uat_frame = self._create_history_frame(
                main_pane,
                "uat",
                f"UAT ({self._env_map['uat'].get('org_url', '')})",
            )
            main_pane.add(uat_frame, weight=1)

    def _create_history_frame(self, parent: ttk.PanedWindow, key: str, title: str) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text=title, padding=(4, 4))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=self.HISTORY_COLUMNS, show="headings", selectmode="browse")
        for col in self.HISTORY_COLUMNS:
            tree.heading(
                col,
                text=self.HISTORY_HEADINGS[col],
                anchor="w",
                command=lambda c=col, t=tree: self._sort_tree(t, c, False),
            )
            tree.column(col, width=self.HISTORY_WIDTHS[col], anchor="w", stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.bind("<ButtonRelease-1>", lambda event, env_key=key: self._on_history_click(event, env_key))
        tree.tag_configure("failed", foreground="#c0392b")
        tree.tag_configure("running", foreground="#0066cc")
        tree.tag_configure("success", foreground="#1a7f37")
        self._trees[key] = tree
        return frame

    def on_show(self) -> None:
        self.refresh()
        self._schedule_refresh()

    def on_hide(self) -> None:
        self._cancel_refresh()

    def _on_auto_refresh_toggle(self) -> None:
        if self._auto_refresh.get():
            self._schedule_refresh()
        else:
            self._cancel_refresh()

    def _schedule_refresh(self) -> None:
        self._cancel_refresh()
        if not self._auto_refresh.get():
            return
        if self.gui._active_panel != "publish_history":
            return
        self._refresh_job = self.gui.root.after(self.REFRESH_INTERVAL_MS, self._auto_refresh_tick)

    def _cancel_refresh(self) -> None:
        if self._refresh_job is not None:
            try:
                self.gui.root.after_cancel(self._refresh_job)
            except ValueError:
                pass
            self._refresh_job = None

    def _auto_refresh_tick(self) -> None:
        self._refresh_job = None
        if self.gui._active_panel != "publish_history":
            return
        self.refresh(silent=True)
        self._schedule_refresh()

    def refresh(self, silent: bool = False) -> None:
        if self._loading:
            return
        if not self._env_map:
            self.status_var.set("未找到 Dev / UAT 环境配置。")
            return
        self._loading = True
        if not silent:
            self.status_var.set("正在刷新发布历史...")

        targets = list(self._env_map.items())

        def worker() -> None:
            results: Dict[str, Any] = {}
            errors: Dict[str, str] = {}
            for key, env in targets:
                try:
                    from d365_field_creator import create_creator_from_environment

                    creator = create_creator_from_environment(env)
                    manager = D365PublishHistoryManager(creator)
                    results[key] = {
                        "rows": manager.list_recent_history(HISTORY_LIMIT),
                        "env_name": env.get("name", key.upper()),
                    }
                except Exception as exc:
                    errors[key] = str(exc)

            def on_done() -> None:
                self._loading = False
                for key, payload in results.items():
                    self._populate_tree(key, payload["rows"], payload["env_name"])
                for key, msg in errors.items():
                    self._clear_tree(key)
                    env_name = self._env_map.get(key, {}).get("name", key.upper())
                    self.gui._append_log(f"[{env_name}] 发布历史加载失败: {msg}")
                if errors and not results:
                    self.status_var.set("发布历史加载失败，请查看下方操作日志。")
                else:
                    summary_parts = []
                    for key, payload in results.items():
                        env_name = payload["env_name"]
                        running = sum(1 for row in payload["rows"] if row.get("result") == "进行中")
                        failed = sum(1 for row in payload["rows"] if row.get("result") == "失败")
                        summary_parts.append(
                            f"{env_name}: {len(payload['rows'])} 条"
                            + (f"，进行中 {running}" if running else "")
                            + (f"，失败 {failed}" if failed else "")
                        )
                    self.status_var.set(" | ".join(summary_parts) + " | 最近刷新完成")
                if not silent:
                    self.gui._log_op(
                        "publish_history",
                        "refresh",
                        "success" if not errors else "failed",
                        "刷新发布历史",
                        details={
                            "dev_count": len(results.get("dev", {}).get("rows", [])),
                            "uat_count": len(results.get("uat", {}).get("rows", [])),
                            "errors": errors,
                        },
                    )

            self.gui.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _clear_tree(self, key: str) -> None:
        tree = self._trees.get(key)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._row_maps[key] = {}

    def _sort_tree(self, tree: ttk.Treeview, column: str, reverse: bool) -> None:
        items = list(tree.get_children(""))
        items.sort(key=lambda item_id: str(tree.set(item_id, column) or "").lower(), reverse=reverse)
        for index, item_id in enumerate(items):
            tree.move(item_id, "", index)
        tree.heading(column, command=lambda: self._sort_tree(tree, column, not reverse))

    def _populate_tree(self, key: str, rows: List[Dict[str, Any]], env_name: str) -> None:
        tree = self._trees.get(key)
        if tree is None:
            return
        self._clear_tree(key)
        row_map = self._row_maps[key]
        for row in rows:
            item_id = row.get("id") or f"{key}_{len(row_map)}"
            values = tuple(row.get(col, "") for col in self.HISTORY_COLUMNS)
            tag = ""
            result = row.get("result", "")
            if result == "失败":
                tag = "failed"
            elif result == "进行中":
                tag = "running"
            elif result == "成功":
                tag = "success"
            tree.insert("", "end", iid=item_id, values=values, tags=(tag,) if tag else ())
            row_map[item_id] = {**row, "environment_name": env_name}

    def _on_history_click(self, event: Any, env_key: str) -> None:
        tree = self._trees.get(env_key)
        if tree is None:
            return
        region = tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        column_id = tree.identify_column(event.x)
        try:
            col_index = int(column_id.replace("#", "")) - 1
        except ValueError:
            return
        if col_index < 0 or col_index >= len(self.HISTORY_COLUMNS):
            return
        if self.HISTORY_COLUMNS[col_index] != "error_code":
            return
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        row = self._row_maps.get(env_key, {}).get(item_id)
        if not row:
            return
        error_code = str(row.get("error_code", "")).strip()
        if not error_code:
            return
        from d365_publish_history_manager import D365PublishHistoryManager

        detail = D365PublishHistoryManager.format_error_detail(
            row,
            str(row.get("environment_name", env_key.upper())),
        )
        self.gui._append_log("—— 发布历史错误详情 ——")
        self.gui._append_log(detail)
        self.gui._log_op(
            "publish_history",
            "view_error",
            "info",
            f"查看错误代码 {error_code}",
            details={"environment": row.get("environment_name"), "error_code": error_code},
            environment_name=str(row.get("environment_name", "")),
        )
