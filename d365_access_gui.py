"""用户 → 角色 → 实体权限追溯界面。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from d365_access_manager import D365AccessManager, ENTITY_SCOPE_OPTIONS


class UserAccessPanel:
    def __init__(self, gui: Any, parent: ttk.Frame) -> None:
        self.gui = gui
        self.parent = parent
        self.manager: Optional[D365AccessManager] = None
        self.access_env_map: Dict[str, Dict[str, str]] = {}
        self._users: List[Dict[str, str]] = []
        self._roles: List[Dict[str, str]] = []
        self._entities: List[Dict[str, Any]] = []
        self._selected_user_id = ""
        self._selected_role_id = ""
        self._loading_users = False
        self._loading_roles = False
        self._loading_entities = False
        self._build()

    def _build(self) -> None:
        from d365_field_creator import (
            ENTITY_ACCESS_RIGHT_COLUMNS,
            PRIVILEGE_DEPTH_LABELS_CN,
            load_config,
            load_environments,
        )

        self._entity_columns = ENTITY_ACCESS_RIGHT_COLUMNS
        self._depth_labels = PRIVILEGE_DEPTH_LABELS_CN

        self.parent.columnconfigure(0, weight=1)
        self.parent.rowconfigure(1, weight=1)

        top_bar = ttk.Frame(self.parent, padding=(8, 4, 8, 2))
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.columnconfigure(5, weight=1)

        environments = load_environments(self.gui._get_config_path())
        env_names: List[str] = []
        for env in environments:
            name = env["name"]
            if name not in self.access_env_map:
                self.access_env_map[name] = env
                env_names.append(name)
        current_env = self.gui._get_current_environment()
        cfg = load_config(self.gui._get_config_path())
        default_env_name = str(cfg.get("environment_name", "")).strip()
        if default_env_name not in self.access_env_map and env_names:
            for env in environments:
                if env["org_url"].rstrip("/").lower() == current_env["org_url"].rstrip("/").lower():
                    default_env_name = env["name"]
                    break
            else:
                default_env_name = env_names[0]

        ttk.Label(top_bar, text="用户角色追溯", font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        ttk.Label(top_bar, text="目标环境").grid(row=0, column=1, sticky="w", padx=(0, 4))
        self.access_env_var = tk.StringVar(value=default_env_name)
        self.access_env_url_var = tk.StringVar()
        env_combo = ttk.Combobox(
            top_bar,
            textvariable=self.access_env_var,
            state="readonly",
            values=env_names,
            width=12,
        )
        env_combo.grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Label(top_bar, textvariable=self.access_env_url_var, foreground="#666").grid(
            row=0, column=3, sticky="w", padx=(0, 12)
        )
        ttk.Button(top_bar, text="加载用户", command=self._load_users).grid(row=0, column=4, sticky="w")
        ttk.Label(top_bar, text="筛选用户").grid(row=0, column=5, sticky="e", padx=(8, 4))
        self.user_filter_var = tk.StringVar()
        user_filter_entry = ttk.Entry(top_bar, textvariable=self.user_filter_var, width=14)
        user_filter_entry.grid(row=0, column=6, sticky="w")
        user_filter_entry.bind("<KeyRelease>", lambda _e: self._apply_user_filter())
        ttk.Label(top_bar, text="筛选表").grid(row=0, column=7, sticky="w", padx=(8, 4))
        self.entity_filter_var = tk.StringVar()
        entity_filter_entry = ttk.Entry(top_bar, textvariable=self.entity_filter_var, width=14)
        entity_filter_entry.grid(row=0, column=8, sticky="w")
        entity_filter_entry.bind("<KeyRelease>", lambda _e: self._render_entity_table())
        self.entity_scope_var = tk.StringVar(value="仅显示分配的表")
        entity_scope_combo = ttk.Combobox(
            top_bar,
            textvariable=self.entity_scope_var,
            state="readonly",
            values=list(ENTITY_SCOPE_OPTIONS),
            width=16,
        )
        entity_scope_combo.grid(row=0, column=9, sticky="w", padx=(4, 0))
        entity_scope_combo.bind("<<ComboboxSelected>>", lambda _e: self._render_entity_table())
        self.status_var = tk.StringVar(value="请选择环境后点击「加载用户」。")
        ttk.Label(top_bar, textvariable=self.status_var, foreground="#666").grid(
            row=1, column=0, columnspan=10, sticky="w", pady=(2, 0)
        )

        main_pane = ttk.PanedWindow(self.parent, orient=tk.HORIZONTAL)
        main_pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=(2, 2))

        user_frame = ttk.LabelFrame(main_pane, text="用户", padding=(4, 4))
        main_pane.add(user_frame, weight=1)
        user_frame.rowconfigure(0, weight=1)
        user_frame.columnconfigure(0, weight=1)
        self.user_tree = ttk.Treeview(
            user_frame,
            columns=("fullname", "domain", "email", "business_unit", "jobtitle"),
            show="headings",
            selectmode="browse",
        )
        self.user_tree.heading("fullname", text="名称")
        self.user_tree.heading("domain", text="用户名")
        self.user_tree.heading("email", text="邮箱")
        self.user_tree.heading("business_unit", text="业务部门")
        self.user_tree.heading("jobtitle", text="标题")
        self.user_tree.column("fullname", width=120, anchor="w")
        self.user_tree.column("domain", width=130, anchor="w")
        self.user_tree.column("email", width=150, anchor="w")
        self.user_tree.column("business_unit", width=160, anchor="w")
        self.user_tree.column("jobtitle", width=120, anchor="w")
        self.user_tree.grid(row=0, column=0, sticky="nsew")
        user_scroll = ttk.Scrollbar(user_frame, orient="vertical", command=self.user_tree.yview)
        user_scroll.grid(row=0, column=1, sticky="ns")
        self.user_tree.configure(yscrollcommand=user_scroll.set)
        self.user_tree.bind("<<TreeviewSelect>>", self._on_user_selected)

        role_frame = ttk.LabelFrame(main_pane, text="安全角色", padding=(4, 4))
        main_pane.add(role_frame, weight=1)
        role_frame.rowconfigure(0, weight=1)
        role_frame.columnconfigure(0, weight=1)
        self.role_tree = ttk.Treeview(role_frame, show="tree", selectmode="browse")
        self.role_tree.heading("#0", text="角色名称", anchor="w")
        self.role_tree.column("#0", width=220, anchor="w", stretch=True)
        self.role_tree.grid(row=0, column=0, sticky="nsew")
        role_scroll = ttk.Scrollbar(role_frame, orient="vertical", command=self.role_tree.yview)
        role_scroll.grid(row=0, column=1, sticky="ns")
        self.role_tree.configure(yscrollcommand=role_scroll.set)
        self.role_tree.bind("<<TreeviewSelect>>", self._on_role_selected)

        entity_frame = ttk.LabelFrame(main_pane, text="角色可访问的数据表", padding=(4, 4))
        main_pane.add(entity_frame, weight=2)
        entity_frame.rowconfigure(0, weight=1)
        entity_frame.columnconfigure(0, weight=1)

        entity_cols = ["logical_name", "display_name"] + [verb for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS]
        entity_headings = {
            "logical_name": "逻辑名",
            "display_name": "显示名",
            **{verb: label for label, verb in ENTITY_ACCESS_RIGHT_COLUMNS},
        }
        self.entity_tree = ttk.Treeview(entity_frame, columns=entity_cols, show="headings", selectmode="browse")
        for col in entity_cols:
            self.entity_tree.heading(col, text=entity_headings[col], anchor="w")
            width = 72 if col not in {"logical_name", "display_name"} else (130 if col == "logical_name" else 150)
            self.entity_tree.column(col, width=width, anchor="w")
        self.entity_tree.grid(row=0, column=0, sticky="nsew")
        entity_y_scroll = ttk.Scrollbar(entity_frame, orient="vertical", command=self.entity_tree.yview)
        entity_y_scroll.grid(row=0, column=1, sticky="ns")
        entity_x_scroll = ttk.Scrollbar(entity_frame, orient="horizontal", command=self.entity_tree.xview)
        entity_x_scroll.grid(row=1, column=0, sticky="ew")
        self.entity_tree.configure(yscrollcommand=entity_y_scroll.set, xscrollcommand=entity_x_scroll.set)

        footer = ttk.Frame(self.parent, padding=(8, 0, 8, 4))
        footer.grid(row=2, column=0, sticky="ew")
        self.detail_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.detail_var, foreground="#666").pack(side="left")
        ttk.Label(
            footer,
            text="权限深度：无 / 用户 / 部门 / 子部门 / 组织",
            foreground="#888",
        ).pack(side="right")

        def _update_env_label(_event: Any = None) -> None:
            env = self.access_env_map.get(self.access_env_var.get().strip(), current_env)
            self.access_env_url_var.set(env.get("org_url", ""))

        def _on_env_changed(_event: Any = None) -> None:
            _update_env_label()
            self.manager = None
            self._users = []
            self._roles = []
            self._entities = []
            self._selected_user_id = ""
            self._selected_role_id = ""
            self._clear_tree(self.user_tree)
            self._clear_tree(self.role_tree)
            self._clear_tree(self.entity_tree)
            env_name = self.access_env_var.get().strip()
            env_url = self.access_env_map.get(env_name, {}).get("org_url", "")
            self.status_var.set(f"已切换至 [{env_name}]，请重新加载用户。")
            self.detail_var.set("")
            self.gui._log_op(
                "access_inspector",
                "switch_environment",
                "info",
                f"用户角色追溯切换环境: {env_name}",
                environment_name=env_name,
                target_org_url=env_url,
            )

        env_combo.bind("<<ComboboxSelected>>", _on_env_changed)
        _update_env_label()

    def _get_environment(self) -> Dict[str, str]:
        env_name = self.access_env_var.get().strip()
        env = self.access_env_map.get(env_name)
        if env is None:
            raise RuntimeError(f"未找到环境配置: {env_name}")
        return env

    def _get_manager(self) -> D365AccessManager:
        if self.manager is None:
            creator = self.gui._create_creator_for_environment(self._get_environment())
            self.manager = D365AccessManager(creator)
        return self.manager

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children())

    def _apply_user_filter(self) -> None:
        keyword = self.user_filter_var.get().strip().lower()
        self._clear_tree(self.user_tree)
        for user in self._users:
            haystack = " ".join(
                [
                    user.get("fullname", ""),
                    user.get("domainname", ""),
                    user.get("email", ""),
                    user.get("business_unit", ""),
                    user.get("jobtitle", ""),
                ]
            ).lower()
            if keyword and keyword not in haystack:
                continue
            self.user_tree.insert(
                "",
                "end",
                iid=user["user_id"],
                values=(
                    user.get("fullname", ""),
                    user.get("domainname", ""),
                    user.get("email", ""),
                    user.get("business_unit", ""),
                    user.get("jobtitle", ""),
                ),
            )

    def _load_users(self) -> None:
        if self._loading_users:
            return
        env_name = self.access_env_var.get().strip()
        env_url = self.access_env_map.get(env_name, {}).get("org_url", "")
        self._loading_users = True
        self.status_var.set(f"正在 [{env_name}] 加载用户列表...")
        self.gui._log_op(
            "access_inspector",
            "load_users",
            "started",
            f"开始加载用户列表: {env_name}",
            environment_name=env_name,
            target_org_url=env_url,
        )

        def worker() -> None:
            try:
                manager = self._get_manager()
                manager.clear_caches()
                users = manager.list_users(keyword="", only_enabled=True)

                def on_done() -> None:
                    self._loading_users = False
                    self._users = users
                    self._roles = []
                    self._entities = []
                    self._selected_user_id = ""
                    self._selected_role_id = ""
                    self._clear_tree(self.role_tree)
                    self._clear_tree(self.entity_tree)
                    self._apply_user_filter()
                    self.status_var.set(f"已加载 {len(users)} 个启用用户，请选择用户查看角色。")
                    self.detail_var.set("")
                    self.gui._log_op(
                        "access_inspector",
                        "load_users",
                        "success",
                        f"已加载用户 {len(users)} 个",
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_done)
            except Exception as exc:
                msg = str(exc)

                def on_error() -> None:
                    self._loading_users = False
                    self.status_var.set(f"加载用户失败: {msg}")
                    self.gui._append_log(f"加载用户失败: {msg}")
                    self.gui._log_op(
                        "access_inspector",
                        "load_users",
                        "failed",
                        "加载用户列表失败",
                        environment_name=env_name,
                        target_org_url=env_url,
                        error_message=msg,
                    )

                self.gui.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_user_selected(self, _event: Any = None) -> None:
        selection = self.user_tree.selection()
        if not selection:
            return
        user_id = selection[0]
        if user_id == self._selected_user_id:
            return
        self._selected_user_id = user_id
        user = next((item for item in self._users if item["user_id"] == user_id), None)
        user_label = user.get("fullname", user_id) if user else user_id
        self._load_roles(user_id, user_label)

    def _load_roles(self, user_id: str, user_label: str) -> None:
        if self._loading_roles:
            return
        env_name = self.access_env_var.get().strip()
        env_url = self.access_env_map.get(env_name, {}).get("org_url", "")
        self._loading_roles = True
        self._selected_role_id = ""
        self._entities = []
        self._clear_tree(self.role_tree)
        self._clear_tree(self.entity_tree)
        self.status_var.set(f"正在加载用户 [{user_label}] 的安全角色...")
        self.gui._log_op(
            "access_inspector",
            "load_user_roles",
            "started",
            f"加载用户角色: {user_label}",
            environment_name=env_name,
            target_org_url=env_url,
        )

        def worker() -> None:
            try:
                roles = self._get_manager().get_user_roles(user_id)

                def on_done() -> None:
                    self._loading_roles = False
                    self._roles = roles
                    for role in roles:
                        self.role_tree.insert("", "end", iid=role["role_id"], text=role["name"])
                    self.status_var.set(f"用户 [{user_label}] 拥有 {len(roles)} 个角色，请选择角色查看数据表。")
                    self.detail_var.set(
                        f"用户: {user_label} | 角色数: {len(roles)}"
                    )
                    self.gui._log_op(
                        "access_inspector",
                        "load_user_roles",
                        "success",
                        f"用户 [{user_label}] 角色数: {len(roles)}",
                        details={"user_id": user_id, "role_count": len(roles)},
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_done)
            except Exception as exc:
                msg = str(exc)

                def on_error() -> None:
                    self._loading_roles = False
                    self.status_var.set(f"加载角色失败: {msg}")
                    self.gui._append_log(f"加载用户角色失败: {msg}")
                    self.gui._log_op(
                        "access_inspector",
                        "load_user_roles",
                        "failed",
                        f"加载用户 [{user_label}] 角色失败",
                        environment_name=env_name,
                        target_org_url=env_url,
                        error_message=msg,
                    )

                self.gui.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_role_selected(self, _event: Any = None) -> None:
        selection = self.role_tree.selection()
        if not selection:
            return
        role_id = selection[0]
        if role_id == self._selected_role_id:
            return
        self._selected_role_id = role_id
        role = next((item for item in self._roles if item["role_id"] == role_id), None)
        role_name = role.get("name", role_id) if role else role_id
        self._load_entities(role_id, role_name)

    def _load_entities(self, role_id: str, role_name: str) -> None:
        if self._loading_entities:
            return
        env_name = self.access_env_var.get().strip()
        env_url = self.access_env_map.get(env_name, {}).get("org_url", "")
        self._loading_entities = True
        self._clear_tree(self.entity_tree)
        self.status_var.set(f"正在分析角色 [{role_name}] 的数据表权限...")
        self.gui._log_op(
            "access_inspector",
            "load_role_entities",
            "started",
            f"加载角色数据表权限: {role_name}",
            environment_name=env_name,
            target_org_url=env_url,
        )

        def worker() -> None:
            try:
                entities = self._get_manager().get_role_entity_matrix(role_id)

                def on_done() -> None:
                    self._loading_entities = False
                    self._entities = entities
                    self._render_entity_table()
                    assigned_count = sum(
                        1 for item in entities if D365AccessManager.entity_has_assignment(item)
                    )
                    self.status_var.set(
                        f"角色 [{role_name}] 共 {len(entities)} 张表，已分配 {assigned_count} 张。"
                    )
                    user = next(
                        (item for item in self._users if item["user_id"] == self._selected_user_id),
                        None,
                    )
                    user_label = user.get("fullname", "") if user else ""
                    self.detail_var.set(
                        f"用户: {user_label} | 角色: {role_name} | 数据表: {assigned_count}/{len(entities)}"
                    )
                    self.gui._log_op(
                        "access_inspector",
                        "load_role_entities",
                        "success",
                        f"角色 [{role_name}] 数据表数: {len(entities)}",
                        details={"role_id": role_id, "entity_count": len(entities)},
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_done)
            except Exception as exc:
                msg = str(exc)

                def on_error() -> None:
                    self._loading_entities = False
                    self.status_var.set(f"加载数据表权限失败: {msg}")
                    self.gui._append_log(f"加载角色数据表权限失败: {msg}")
                    self.gui._log_op(
                        "access_inspector",
                        "load_role_entities",
                        "failed",
                        f"加载角色 [{role_name}] 数据表权限失败",
                        environment_name=env_name,
                        target_org_url=env_url,
                        error_message=msg,
                    )

                self.gui.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _render_entity_table(self) -> None:
        self._clear_tree(self.entity_tree)
        keyword = self.entity_filter_var.get().strip().lower()
        scope = self.entity_scope_var.get().strip() or "仅显示分配的表"
        shown = 0
        for entity in self._entities:
            assigned = D365AccessManager.entity_has_assignment(entity)
            if scope == "仅显示分配的表" and not assigned:
                continue
            if scope == "仅显示未分配的表" and assigned:
                continue
            logical_name = entity.get("logical_name", "")
            display_name = entity.get("display_name", "")
            haystack = f"{logical_name} {display_name}".lower()
            if keyword and keyword not in haystack:
                continue
            permissions = entity.get("permissions", {})
            values = [logical_name, display_name]
            for _, verb in self._entity_columns:
                depth = permissions.get(verb)
                values.append(self._depth_labels.get(depth, "") if depth is not None else "")
            self.entity_tree.insert("", "end", values=values)
            shown += 1
        if self._entities and self._selected_role_id:
            self.detail_var.set(
                (self.detail_var.get().split("| 当前显示")[0]).strip()
                + f" | 当前显示: {shown} 张"
            )
