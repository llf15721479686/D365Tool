"""Plugin Registration Tool 风格界面。"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from d365_plugin_manager import (
    D365PluginManager,
    PLUGIN_ASSEMBLY_ALLOWLIST,
    PLUGIN_ISOLATION_LABELS,
    PLUGIN_MODE_LABELS,
    PLUGIN_STAGE_LABELS,
)


class PluginRegistrationPanel:
    def __init__(self, gui: Any, parent: ttk.Frame) -> None:
        self.gui = gui
        self.parent = parent
        self.manager: Optional[D365PluginManager] = None
        self.hierarchy: List[Dict[str, Any]] = []
        self.node_map: Dict[str, Dict[str, Any]] = {}
        self.selected_node: Optional[Dict[str, Any]] = None
        self._loading = False
        self._load_token = 0
        self._build()

    def refresh(self) -> None:
        if self._loading:
            return
        self._load_hierarchy()

    def _build(self) -> None:
        from d365_field_creator import load_config, load_environments

        self.parent.rowconfigure(4, weight=1)
        self.parent.columnconfigure(0, weight=1)

        ttk.Label(self.parent, text="插件注册 (Sales)", font=("", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        ttk.Label(
            self.parent,
            text="仅展示程序集：SanyD365.D365Extension.Sales、SanyD365.D365ExtensionApi.Sales",
            foreground="#666",
        ).grid(row=0, column=0, sticky="e", padx=8, pady=(8, 4))

        environments = load_environments(self.gui._get_config_path())
        self.plugin_env_map: Dict[str, Dict[str, str]] = {}
        env_names: List[str] = []
        for env in environments:
            name = env["name"]
            if name not in self.plugin_env_map:
                self.plugin_env_map[name] = env
                env_names.append(name)
        current_env = self.gui._get_current_environment()
        cfg = load_config(self.gui._get_config_path())
        default_env_name = str(cfg.get("environment_name", "")).strip()
        if default_env_name not in self.plugin_env_map and env_names:
            for env in environments:
                if env["org_url"].rstrip("/").lower() == current_env["org_url"].rstrip("/").lower():
                    default_env_name = env["name"]
                    break
            else:
                default_env_name = env_names[0]

        env_frame = ttk.Frame(self.parent, padding=(8, 0, 8, 4))
        env_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(env_frame, text="目标环境").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.plugin_env_var = tk.StringVar(value=default_env_name)
        self.plugin_env_url_var = tk.StringVar()
        plugin_env_combo = ttk.Combobox(
            env_frame,
            textvariable=self.plugin_env_var,
            state="readonly",
            values=env_names,
            width=18,
        )
        plugin_env_combo.grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(env_frame, textvariable=self.plugin_env_url_var, foreground="#666").grid(
            row=0, column=2, sticky="w"
        )

        def _update_plugin_env_label(_event: Any = None) -> None:
            env = self.plugin_env_map.get(self.plugin_env_var.get().strip(), current_env)
            self.plugin_env_url_var.set(env.get("org_url", ""))

        def _on_plugin_env_changed(_event: Any = None) -> None:
            _update_plugin_env_label()
            env_name = self.plugin_env_var.get().strip()
            env_url = self.plugin_env_map.get(env_name, {}).get("org_url", "")
            self.manager = None
            self.hierarchy = []
            self._clear_detail_panes()
            for item in self.plugin_tree.get_children():
                self.plugin_tree.delete(item)
            self.node_map.clear()
            self.status_var.set(f"已切换至 [{env_name}]，正在重新加载...")
            self.gui._log_op(
                "plugin",
                "switch_environment",
                "info",
                f"插件管理切换目标环境: {env_name}",
                environment_name=env_name,
                target_org_url=env_url,
            )
            self.refresh()

        plugin_env_combo.bind("<<ComboboxSelected>>", _on_plugin_env_changed)
        _update_plugin_env_label()

        action_frame = ttk.LabelFrame(self.parent, text="操作", padding=(6, 4))
        action_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))
        action_frame.columnconfigure(0, weight=1)

        btn_bar = ttk.Frame(action_frame)
        btn_bar.grid(row=0, column=0, sticky="w")
        ttk.Button(btn_bar, text="刷新", command=self.refresh, width=10).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(btn_bar, text="注册程序集", command=self._register_assembly, width=12).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="注册插件类型", command=self._register_plugin_type, width=12).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="注册步骤", command=self._register_step, width=10).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="更新程序集", command=self._update_assembly, width=12).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="启用步骤", command=lambda: self._set_step_enabled(True), width=10).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="禁用步骤", command=lambda: self._set_step_enabled(False), width=10).pack(
            side="left", padx=3
        )
        ttk.Button(btn_bar, text="注销", command=self._unregister_selected, width=8).pack(
            side="left", padx=3
        )

        self.status_var = tk.StringVar(value="请选择节点后操作。")
        ttk.Label(action_frame, textvariable=self.status_var, foreground="#666").grid(
            row=0, column=1, sticky="e", padx=(12, 4)
        )

        search_frame = ttk.Frame(self.parent, padding=(8, 0, 8, 4))
        search_frame.grid(row=3, column=0, sticky="ew")
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="筛选").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew")
        search_entry.bind("<Return>", lambda _e: self.refresh())
        ttk.Button(search_frame, text="筛选", command=self.refresh, width=8).grid(
            row=0, column=2, padx=(8, 0)
        )

        body = ttk.PanedWindow(self.parent, orient=tk.VERTICAL)
        body.grid(row=4, column=0, sticky="nsew", padx=8, pady=(4, 8))
        self.body = body

        tree_frame = ttk.LabelFrame(body, text="Sales 插件树", padding=4)
        body.add(tree_frame, weight=3)
        self.tree_frame = tree_frame
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.plugin_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self.plugin_tree.grid(row=0, column=0, sticky="nsew")
        tree_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.plugin_tree.yview)
        tree_y.grid(row=0, column=1, sticky="ns")
        self.plugin_tree.configure(yscrollcommand=tree_y.set)
        self.plugin_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.plugin_tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        detail_notebook = ttk.Notebook(body)
        body.add(detail_notebook, weight=2)

        details_tab = ttk.Frame(detail_notebook, padding=4)
        detail_notebook.add(details_tab, text="详情")
        details_tab.rowconfigure(0, weight=1)
        details_tab.columnconfigure(0, weight=1)

        columns = (
            "kind",
            "name",
            "typename",
            "message",
            "entity",
            "stage",
            "mode",
            "status",
            "modifiedon",
        )
        self.detail_tree = ttk.Treeview(details_tab, columns=columns, show="headings", height=10)
        headings = {
            "kind": "类型",
            "name": "名称",
            "typename": "类名/消息",
            "message": "消息",
            "entity": "实体",
            "stage": "阶段",
            "mode": "模式",
            "status": "状态",
            "modifiedon": "修改时间",
        }
        widths = {
            "kind": 80,
            "name": 220,
            "typename": 260,
            "message": 80,
            "entity": 100,
            "stage": 120,
            "mode": 60,
            "status": 70,
            "modifiedon": 140,
        }
        for col in columns:
            self.detail_tree.heading(col, text=headings[col])
            self.detail_tree.column(col, width=widths[col], stretch=(col in {"name", "typename"}))
        self.detail_tree.grid(row=0, column=0, sticky="nsew")
        detail_y = ttk.Scrollbar(details_tab, orient="vertical", command=self.detail_tree.yview)
        detail_y.grid(row=0, column=1, sticky="ns")
        detail_x = ttk.Scrollbar(details_tab, orient="horizontal", command=self.detail_tree.xview)
        detail_x.grid(row=1, column=0, sticky="ew")
        self.detail_tree.configure(yscrollcommand=detail_y.set, xscrollcommand=detail_x.set)

        props_tab = ttk.Frame(detail_notebook, padding=4)
        detail_notebook.add(props_tab, text="属性")
        props_tab.rowconfigure(0, weight=1)
        props_tab.columnconfigure(0, weight=1)
        self.props_text = tk.Text(props_tab, wrap="word", height=10)
        self.props_text.grid(row=0, column=0, sticky="nsew")
        props_scroll = ttk.Scrollbar(props_tab, orient="vertical", command=self.props_text.yview)
        props_scroll.grid(row=0, column=1, sticky="ns")
        self.props_text.configure(yscrollcommand=props_scroll.set, state="disabled")

    def _parse_step_message_entity(self, step_name: str) -> Tuple[str, str]:
        text = (step_name or "").strip()
        if " of " in text.lower():
            left, entity = text.rsplit(" of ", 1)
            message = left.split(":")[-1].strip()
            return message, entity.strip()
        return "", ""

    def _show_loading_overlay(self, message: str, detail: str = "") -> None:
        self._loading = True
        text = detail or message
        self.status_var.set(text)
        self.gui.root.update_idletasks()

    def _hide_loading_overlay(self) -> None:
        self._loading = False

    def _get_plugin_environment(self) -> Dict[str, str]:
        env_name = self.plugin_env_var.get().strip()
        env = self.plugin_env_map.get(env_name)
        if env is None:
            raise RuntimeError(f"未找到环境配置: {env_name}")
        return env

    def _get_creator(self) -> Any:
        return self.gui._create_creator_for_environment(self._get_plugin_environment())

    def _get_manager(self) -> D365PluginManager:
        return D365PluginManager(self._get_creator())

    def _plugin_env_context(self) -> Tuple[str, str]:
        env_name = self.plugin_env_var.get().strip()
        env_url = self.plugin_env_map.get(env_name, {}).get("org_url", "")
        return env_name, env_url

    def _load_hierarchy(self) -> None:
        keyword = self.search_var.get().strip()
        self._load_token += 1
        load_token = self._load_token
        env_name, env_url = self._plugin_env_context()
        self._show_loading_overlay(f"正在连接 [{env_name}] 并加载 Sales 插件...", "准备读取 Sales 程序集")

        def progress(stage: str, current: int, total: int, message: str) -> None:
            if load_token != self._load_token:
                return
            self.gui.root.after(0, lambda msg=message: self.status_var.set(msg))

        def worker() -> None:
            try:
                manager = self._get_manager()
                hierarchy = manager.list_plugin_hierarchy(
                    keyword=keyword,
                    assembly_names=list(PLUGIN_ASSEMBLY_ALLOWLIST),
                    progress_callback=progress,
                )
                if load_token != self._load_token:
                    return

                def on_done() -> None:
                    if load_token != self._load_token:
                        return
                    self.manager = manager
                    self.hierarchy = hierarchy
                    self.status_var.set("正在构建插件树...")
                    self.gui.root.update_idletasks()
                    self._render_tree()
                    assembly_count = len(hierarchy)
                    type_count = sum(len(item["plugin_types"]) for item in hierarchy)
                    step_count = sum(item["step_count"] for item in hierarchy)
                    self._hide_loading_overlay()
                    self.status_var.set(
                        f"[{env_name}] 已加载 Sales 插件：{assembly_count} 个程序集, {type_count} 个类型, {step_count} 个步骤。"
                    )
                    self.gui._log_op(
                        "plugin",
                        "load_hierarchy",
                        "success",
                        f"加载插件列表: {assembly_count} 程序集, {type_count} 类型, {step_count} 步骤",
                        details={"keyword": keyword, "assembly_count": assembly_count, "environment_name": env_name},
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_done)
            except Exception as exc:
                def on_error(msg: str = str(exc)) -> None:
                    if load_token != self._load_token:
                        return
                    self._hide_loading_overlay()
                    self.status_var.set(f"加载失败: {msg}")
                    self.gui._append_log(f"加载插件失败: {msg}")
                    self.gui._log_op(
                        "plugin",
                        "load_hierarchy",
                        "failed",
                        "加载插件列表失败",
                        error_message=msg,
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _insert_step_nodes(self, type_node_id: str, node: Dict[str, Any]) -> None:
        plugin_type = node["plugin_type"]
        assembly = node["assembly"]
        steps = node.get("steps") or []
        for step in steps:
            step_id = str(step.get("sdkmessageprocessingstepid", "")).strip("{}")
            step_node_id = f"step:{step_id}"
            if self.plugin_tree.exists(step_node_id):
                continue
            step_name = str(step.get("name") or step_id)
            status = D365PluginManager.format_step_status(step.get("statecode"))
            self.plugin_tree.insert(
                type_node_id,
                "end",
                iid=step_node_id,
                text=f"(Step) {step_name} [{status}]",
            )
            self.node_map[step_node_id] = {
                "kind": "step",
                "assembly": assembly,
                "plugin_type": plugin_type,
                "step": step,
            }
        node["steps_rendered"] = True

    def _on_tree_open(self, _event: Any = None) -> None:
        node_id = self.plugin_tree.focus()
        if not node_id:
            return
        node = self.node_map.get(node_id)
        if not node or node.get("kind") != "plugin_type":
            return
        if node.get("steps_rendered"):
            return
        placeholder_id = f"placeholder:{str(node['plugin_type'].get('plugintypeid', '')).strip('{}')}"
        if self.plugin_tree.exists(placeholder_id):
            self.plugin_tree.delete(placeholder_id)
        self._insert_step_nodes(node_id, node)

    def _render_tree(self) -> None:
        for item in self.plugin_tree.get_children():
            self.plugin_tree.delete(item)
        self.node_map.clear()

        for item in self.hierarchy:
            assembly = item["assembly"]
            assembly_id = str(assembly.get("pluginassemblyid", "")).strip("{}")
            assembly_name = str(assembly.get("name") or assembly_id)
            node_id = f"assembly:{assembly_id}"
            isolation = PLUGIN_ISOLATION_LABELS.get(int(assembly.get("isolationmode") or 0), "")
            type_count = len(item["plugin_types"])
            label = f"(Assembly) {assembly_name}"
            if isolation:
                label += f" [{isolation}]"
            if type_count:
                label += f" ({type_count} 插件)"
            self.plugin_tree.insert("", "end", iid=node_id, text=label, open=True)
            self.node_map[node_id] = {"kind": "assembly", "assembly": assembly}

            for type_node in item["plugin_types"]:
                plugin_type = type_node["plugin_type"]
                type_id = str(plugin_type.get("plugintypeid", "")).strip("{}")
                type_node_id = f"type:{type_id}"
                typename = str(plugin_type.get("typename") or plugin_type.get("name") or type_id)
                step_rows = type_node.get("steps") or []
                type_label = f"(Plugin) {typename}"
                if step_rows:
                    type_label += f" ({len(step_rows)} 步骤)"
                self.plugin_tree.insert(
                    node_id,
                    "end",
                    iid=type_node_id,
                    text=type_label,
                    open=False,
                )
                self.node_map[type_node_id] = {
                    "kind": "plugin_type",
                    "assembly": assembly,
                    "plugin_type": plugin_type,
                    "steps": step_rows,
                    "steps_rendered": False,
                }
                if step_rows:
                    placeholder_id = f"placeholder:{type_id}"
                    self.plugin_tree.insert(
                        type_node_id,
                        "end",
                        iid=placeholder_id,
                        text="▶ 展开查看步骤",
                    )

        self._clear_detail_panes()

    def _clear_detail_panes(self) -> None:
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        self.props_text.configure(state="normal")
        self.props_text.delete("1.0", "end")
        self.props_text.insert("1.0", "请在上方树中选择程序集、插件类型或步骤。")
        self.props_text.configure(state="disabled")
        self.selected_node = None

    def _on_tree_select(self, _event: Any = None) -> None:
        selection = self.plugin_tree.selection()
        if not selection:
            self._clear_detail_panes()
            return
        node_id = selection[0]
        if node_id.startswith("placeholder:"):
            return
        node = self.node_map.get(node_id)
        if not node:
            return
        self.selected_node = node
        self._render_details(node)
        self._render_properties(node)

    def _render_details(self, node: Dict[str, Any]) -> None:
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        kind = node["kind"]
        manager = self.manager or self._get_manager()

        if kind == "assembly":
            assembly = node["assembly"]
            assembly_id = str(assembly.get("pluginassemblyid", "")).strip("{}")
            for item in self.hierarchy:
                if str(item["assembly"].get("pluginassemblyid", "")).strip("{}") != assembly_id:
                    continue
                for type_node in item["plugin_types"]:
                    plugin_type = type_node["plugin_type"]
                    self.detail_tree.insert(
                        "",
                        "end",
                        values=(
                            "Plugin",
                            str(plugin_type.get("friendlyname") or plugin_type.get("name") or ""),
                            str(plugin_type.get("typename") or ""),
                            "",
                            "",
                            "",
                            "",
                            "",
                            str(plugin_type.get("modifiedon") or "")[:19].replace("T", " "),
                        ),
                    )
                break
        elif kind == "plugin_type":
            plugin_type = node["plugin_type"]
            type_id = str(plugin_type.get("plugintypeid", "")).strip("{}")
            for item in self.hierarchy:
                for type_node in item["plugin_types"]:
                    if str(type_node["plugin_type"].get("plugintypeid", "")).strip("{}") != type_id:
                        continue
                    for step in type_node["steps"]:
                        message_name, entity_name = self._parse_step_message_entity(str(step.get("name") or ""))
                        self.detail_tree.insert(
                            "",
                            "end",
                            values=(
                                "Step",
                                str(step.get("name") or ""),
                                "",
                                message_name,
                                entity_name,
                                manager.format_stage(step.get("stage")),
                                manager.format_mode(step.get("mode")),
                                manager.format_step_status(step.get("statecode")),
                                str(step.get("modifiedon") or "")[:19].replace("T", " "),
                            ),
                        )
                    break
        elif kind == "step":
            step = node["step"]
            message_name, entity_name = self._parse_step_message_entity(str(step.get("name") or ""))
            self.detail_tree.insert(
                "",
                "end",
                values=(
                    "Step",
                    str(step.get("name") or ""),
                    "",
                    message_name,
                    entity_name,
                    manager.format_stage(step.get("stage")),
                    manager.format_mode(step.get("mode")),
                    manager.format_step_status(step.get("statecode")),
                    str(step.get("modifiedon") or "")[:19].replace("T", " "),
                ),
            )

    def _render_properties(self, node: Dict[str, Any]) -> None:
        lines: List[str] = []
        kind = node["kind"]
        if kind == "assembly":
            assembly = node["assembly"]
            lines.extend(
                [
                    f"类型: Assembly",
                    f"名称: {assembly.get('name', '')}",
                    f"版本: {assembly.get('version', '')}",
                    f"Culture: {assembly.get('culture', '')}",
                    f"PublicKeyToken: {assembly.get('publickeytoken', '')}",
                    f"IsolationMode: {PLUGIN_ISOLATION_LABELS.get(int(assembly.get('isolationmode') or 0), assembly.get('isolationmode'))}",
                    f"Managed: {assembly.get('ismanaged', '')}",
                    f"ModifiedOn: {assembly.get('modifiedon', '')}",
                    f"Description: {assembly.get('description', '')}",
                    f"AssemblyId: {assembly.get('pluginassemblyid', '')}",
                ]
            )
        elif kind == "plugin_type":
            plugin_type = node["plugin_type"]
            assembly = node["assembly"]
            lines.extend(
                [
                    "类型: Plugin Type",
                    f"FriendlyName: {plugin_type.get('friendlyname', '')}",
                    f"Name: {plugin_type.get('name', '')}",
                    f"TypeName: {plugin_type.get('typename', '')}",
                    f"Assembly: {assembly.get('name', '')}",
                    f"WorkflowActivity: {plugin_type.get('isworkflowactivity', False)}",
                    f"ModifiedOn: {plugin_type.get('modifiedon', '')}",
                    f"Description: {plugin_type.get('description', '')}",
                    f"PluginTypeId: {plugin_type.get('plugintypeid', '')}",
                ]
            )
        elif kind == "step":
            step = node["step"]
            message_name, entity_name = self._parse_step_message_entity(str(step.get("name") or ""))
            manager = self.manager or self._get_manager()
            lines.extend(
                [
                    "类型: Sdk Message Processing Step",
                    f"Name: {step.get('name', '')}",
                    f"Message: {message_name}",
                    f"Primary Entity: {entity_name}",
                    f"Stage: {manager.format_stage(step.get('stage'))}",
                    f"Mode: {manager.format_mode(step.get('mode'))}",
                    f"Rank: {step.get('rank', '')}",
                    f"Status: {manager.format_step_status(step.get('statecode'))}",
                    f"FilteringAttributes: {step.get('filteringattributes', '')}",
                    f"Configuration: {step.get('configuration', '')}",
                    f"ModifiedOn: {step.get('modifiedon', '')}",
                    f"Description: {step.get('description', '')}",
                    f"StepId: {step.get('sdkmessageprocessingstepid', '')}",
                ]
            )
        self.props_text.configure(state="normal")
        self.props_text.delete("1.0", "end")
        self.props_text.insert("1.0", "\n".join(lines))
        self.props_text.configure(state="disabled")

    def _selected_context(self) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        selection = self.plugin_tree.selection()
        if not selection:
            return None, None
        return selection[0], self.node_map.get(selection[0])

    def _register_assembly(self) -> None:
        dll_path = filedialog.askopenfilename(
            title="选择插件程序集 (.dll)",
            filetypes=[("Plugin Assembly", "*.dll"), ("All Files", "*.*")],
        )
        if not dll_path:
            return
        dialog = tk.Toplevel(self.gui.root)
        dialog.title("注册程序集")
        dialog.geometry("520x260")
        dialog.transient(self.gui.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        default_name = os.path.splitext(os.path.basename(dll_path))[0]
        name_var = tk.StringVar(value=default_name)
        desc_var = tk.StringVar()
        isolation_var = tk.StringVar(value="Sandbox")
        ttk.Label(frame, text="程序集文件").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(frame, text=dll_path, wraplength=380).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="名称").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=name_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="描述").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=desc_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="隔离模式").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(
            frame,
            textvariable=isolation_var,
            state="readonly",
            values=list(PLUGIN_ISOLATION_LABELS.values()),
        ).grid(row=3, column=1, sticky="w", pady=4)

        def submit() -> None:
            isolation_map = {v: k for k, v in PLUGIN_ISOLATION_LABELS.items()}
            isolation_mode = isolation_map.get(isolation_var.get(), 2)
            dialog.destroy()
            self._run_action(
                "register_assembly",
                lambda mgr: mgr.register_assembly(
                    dll_path,
                    name=name_var.get(),
                    description=desc_var.get(),
                    isolation_mode=isolation_mode,
                ),
                f"已注册程序集: {name_var.get()}",
            )

        btns = ttk.Frame(dialog)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="注册", command=submit).pack(side="left")
        ttk.Button(btns, text="取消", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _register_plugin_type(self) -> None:
        _, node = self._selected_context()
        assembly_id = ""
        if node and node.get("kind") == "assembly":
            assembly_id = str(node["assembly"].get("pluginassemblyid", "")).strip("{}")
        elif node and node.get("kind") == "plugin_type":
            assembly_id = str(node["assembly"].get("pluginassemblyid", "")).strip("{}")
        if not assembly_id:
            messagebox.showwarning("提示", "请先在树中选择一个程序集或插件类型。", parent=self.gui.root)
            return
        dialog = tk.Toplevel(self.gui.root)
        dialog.title("注册插件类型")
        dialog.geometry("560x300")
        dialog.transient(self.gui.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        name_var = tk.StringVar()
        friendly_var = tk.StringVar()
        typename_var = tk.StringVar()
        desc_var = tk.StringVar()
        ttk.Label(frame, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=name_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="Friendly Name").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=friendly_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="Type Name").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=typename_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(
            frame,
            text="示例: MyCompany.Plugins.AccountCreatePlugin",
            foreground="#666",
        ).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="Description").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=desc_var).grid(row=4, column=1, sticky="ew", pady=4)

        def submit() -> None:
            if not typename_var.get().strip():
                messagebox.showwarning("提示", "Type Name 不能为空。", parent=dialog)
                return
            dialog.destroy()
            self._run_action(
                "register_plugin_type",
                lambda mgr: mgr.register_plugin_type(
                    assembly_id,
                    name=name_var.get().strip() or typename_var.get().strip().split(".")[-1],
                    friendly_name=friendly_var.get().strip() or name_var.get().strip(),
                    typename=typename_var.get().strip(),
                    description=desc_var.get().strip(),
                ),
                f"已注册插件类型: {typename_var.get().strip()}",
            )

        btns = ttk.Frame(dialog)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="注册", command=submit).pack(side="left")
        ttk.Button(btns, text="取消", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _register_step(self) -> None:
        _, node = self._selected_context()
        type_id = ""
        if node and node.get("kind") == "plugin_type":
            type_id = str(node["plugin_type"].get("plugintypeid", "")).strip("{}")
        elif node and node.get("kind") == "step":
            type_id = str(node["plugin_type"].get("plugintypeid", "")).strip("{}")
        if not type_id:
            messagebox.showwarning("提示", "请先在树中选择一个插件类型或步骤。", parent=self.gui.root)
            return

        dialog = tk.Toplevel(self.gui.root)
        dialog.title("注册处理步骤")
        dialog.geometry("560x380")
        dialog.transient(self.gui.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        message_var = tk.StringVar(value="Create")
        entity_var = tk.StringVar(value=self.gui.vars.get("entity_logical_name", tk.StringVar()).get().split("(", 1)[0].strip())
        stage_var = tk.StringVar(value=PLUGIN_STAGE_LABELS[40])
        mode_var = tk.StringVar(value=PLUGIN_MODE_LABELS[0])
        rank_var = tk.StringVar(value="1")
        name_var = tk.StringVar()
        filter_attrs_var = tk.StringVar()
        config_var = tk.StringVar()

        ttk.Label(frame, text="消息").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(
            frame,
            textvariable=message_var,
            values=["Create", "Update", "Delete", "Retrieve", "RetrieveMultiple", "Associate", "Disassociate"],
            width=28,
        ).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="实体逻辑名").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=entity_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="阶段").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=stage_var, state="readonly", values=list(PLUGIN_STAGE_LABELS.values())).grid(
            row=2, column=1, sticky="ew", pady=4
        )
        ttk.Label(frame, text="模式").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=mode_var, state="readonly", values=list(PLUGIN_MODE_LABELS.values())).grid(
            row=3, column=1, sticky="ew", pady=4
        )
        ttk.Label(frame, text="Rank").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=rank_var).grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="步骤名称").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=name_var).grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="Filtering Attributes").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=filter_attrs_var).grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="Configuration").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=config_var).grid(row=7, column=1, sticky="ew", pady=4)

        stage_map = {v: k for k, v in PLUGIN_STAGE_LABELS.items()}
        mode_map = {v: k for k, v in PLUGIN_MODE_LABELS.items()}

        def submit() -> None:
            if not entity_var.get().strip():
                messagebox.showwarning("提示", "实体逻辑名不能为空。", parent=dialog)
                return
            try:
                rank = int(rank_var.get().strip() or "1")
            except ValueError:
                messagebox.showwarning("提示", "Rank 必须是整数。", parent=dialog)
                return
            dialog.destroy()
            self._run_action(
                "register_step",
                lambda mgr: mgr.register_step(
                    type_id,
                    message_name=message_var.get().strip(),
                    entity_logical_name=entity_var.get().strip(),
                    stage=stage_map.get(stage_var.get(), 40),
                    mode=mode_map.get(mode_var.get(), 0),
                    rank=rank,
                    name=name_var.get().strip(),
                    filtering_attributes=filter_attrs_var.get().strip(),
                    configuration=config_var.get().strip(),
                ),
                f"已注册步骤: {message_var.get()} / {entity_var.get()}",
            )

        btns = ttk.Frame(dialog)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="注册", command=submit).pack(side="left")
        ttk.Button(btns, text="取消", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _update_assembly(self) -> None:
        _, node = self._selected_context()
        if not node or node.get("kind") != "assembly":
            messagebox.showwarning("提示", "请先选择要更新的程序集。", parent=self.gui.root)
            return
        assembly_id = str(node["assembly"].get("pluginassemblyid", "")).strip("{}")
        dll_path = filedialog.askopenfilename(
            title="选择新的插件程序集 (.dll)",
            filetypes=[("Plugin Assembly", "*.dll"), ("All Files", "*.*")],
        )
        if not dll_path:
            return
        if not messagebox.askyesno(
            "确认更新",
            f"将用所选 DLL 更新程序集 [{node['assembly'].get('name', '')}]，是否继续？",
            parent=self.gui.root,
        ):
            return
        self._run_action(
            "update_assembly",
            lambda mgr: mgr.update_assembly(assembly_id, dll_path),
            f"已更新程序集: {node['assembly'].get('name', '')}",
        )

    def _set_step_enabled(self, enabled: bool) -> None:
        _, node = self._selected_context()
        if not node or node.get("kind") != "step":
            messagebox.showwarning("提示", "请先选择要启用/禁用的步骤。", parent=self.gui.root)
            return
        step_id = str(node["step"].get("sdkmessageprocessingstepid", "")).strip("{}")
        label = "启用" if enabled else "禁用"
        self._run_action(
            "set_step_enabled",
            lambda mgr: mgr.set_step_enabled(step_id, enabled),
            f"已{label}步骤: {node['step'].get('name', '')}",
        )

    def _unregister_selected(self) -> None:
        _, node = self._selected_context()
        if not node:
            messagebox.showwarning("提示", "请先选择要注销的节点。", parent=self.gui.root)
            return
        kind = node["kind"]
        if kind == "assembly":
            target = str(node["assembly"].get("name", ""))
            action = lambda mgr: mgr.delete_assembly(str(node["assembly"].get("pluginassemblyid", "")).strip("{}"))
        elif kind == "plugin_type":
            target = str(node["plugin_type"].get("typename", ""))
            action = lambda mgr: mgr.delete_plugin_type(str(node["plugin_type"].get("plugintypeid", "")).strip("{}"))
        elif kind == "step":
            target = str(node["step"].get("name", ""))
            action = lambda mgr: mgr.delete_step(str(node["step"].get("sdkmessageprocessingstepid", "")).strip("{}"))
        else:
            return
        if not messagebox.askyesno("确认注销", f"确定注销 [{target}] 吗？此操作不可撤销。", parent=self.gui.root):
            return
        self._run_action("unregister", action, f"已注销: {target}")

    def _run_action(self, action_name: str, fn: Any, success_message: str) -> None:
        env_name, env_url = self._plugin_env_context()
        self.status_var.set(f"正在 [{env_name}] 环境执行...")

        def worker() -> None:
            try:
                manager = self._get_manager()
                fn(manager)

                def on_done() -> None:
                    self.gui._append_log(f"[{env_name}] {success_message}")
                    self.gui._log_op(
                        "plugin",
                        action_name,
                        "success",
                        f"[{env_name}] {success_message}",
                        environment_name=env_name,
                        target_org_url=env_url,
                    )
                    self.refresh()

                self.gui.root.after(0, on_done)
            except Exception as exc:
                msg = str(exc)

                def on_error() -> None:
                    self.status_var.set(f"操作失败: {msg}")
                    self.gui._append_log(f"[{env_name}] 插件操作失败: {msg}")
                    self.gui._log_op(
                        "plugin",
                        action_name,
                        "failed",
                        f"插件操作失败: {action_name}",
                        error_message=msg,
                        environment_name=env_name,
                        target_org_url=env_url,
                    )

                self.gui.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()
