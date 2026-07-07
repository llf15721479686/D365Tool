"""D365 field/plugin change record panels."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests

from pagination_helper import PaginationBar


def _fmt_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        raw = text[:19].replace("T", " ")
        try:
            return (datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return raw


def _display_lookup(row: Dict[str, Any], field: str) -> str:
    return str(row.get(f"{field}@OData.Community.Display.V1.FormattedValue") or row.get(field) or "")


def _clean_guid(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        for key in ("systemuserid", "ownerid", "Id", "id", "value"):
            if value.get(key):
                return _clean_guid(value.get(key))
        return ""
    text = str(value).strip().strip("{}")
    return text.lower()


def _odata_text(value: str) -> str:
    return value.replace("'", "''")


def _domain_label(user: Dict[str, Any]) -> str:
    domain = str(user.get("domainname") or "").strip()
    if domain:
        return domain.split("\\")[-1]
    email = str(user.get("internalemailaddress") or "").strip()
    if email:
        return email.split("@")[0]
    return str(user.get("fullname") or "").strip()


class D365ChangeHistoryPanel:
    COLUMNS: Tuple[str, ...] = ("modifiedon", "modifiedby", "component", "name", "logical_name", "details")
    HEADINGS: Dict[str, str] = {
        "modifiedon": "修改时间",
        "modifiedby": "修改人",
        "component": "类型",
        "name": "名称",
        "logical_name": "逻辑名/类名",
        "details": "详情",
    }
    WIDTHS: Dict[str, int] = {
        "modifiedon": 145,
        "modifiedby": 130,
        "component": 110,
        "name": 200,
        "logical_name": 280,
        "details": 420,
    }

    def __init__(self, gui: Any, parent: ttk.Frame, kind: str) -> None:
        self.gui = gui
        self.parent = parent
        self.kind = kind
        self.rows: List[Dict[str, Any]] = []
        self._loading = False
        self._build()

    def _build(self) -> None:
        self.parent.rowconfigure(0, weight=0)
        self.parent.rowconfigure(1, weight=0)
        self.parent.rowconfigure(2, weight=1)
        self.parent.rowconfigure(3, weight=0)
        self.parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(self.parent, padding=(6, 6, 6, 2))
        toolbar.grid(row=0, column=0, sticky="ew")
        for index in range(8):
            toolbar.columnconfigure(index, weight=0)
        toolbar.columnconfigure(8, weight=1)

        title = "字段变更记录" if self.kind == "field" else "插件变更记录"
        ttk.Label(toolbar, text=title, font=("", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 12))

        ttk.Label(toolbar, text="环境").grid(row=0, column=1, sticky="w", padx=(0, 4))
        self.env_var = tk.StringVar()
        self.env_combo = ttk.Combobox(toolbar, textvariable=self.env_var, state="readonly", width=16)
        self.env_combo.grid(row=0, column=2, sticky="w", padx=(0, 12))
        self.env_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_filter_changed())

        self.entity_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.modifiedby_var = tk.StringVar()
        if self.kind == "field":
            ttk.Label(toolbar, text="表名").grid(row=0, column=3, sticky="w", padx=(0, 4))
            entity_entry = ttk.Entry(toolbar, textvariable=self.entity_var, width=26)
            entity_entry.grid(row=0, column=4, sticky="w", padx=(0, 8))
            entity_entry.bind("<Return>", lambda _event: self._on_filter_changed())
            ttk.Label(toolbar, text="字段").grid(row=0, column=5, sticky="w", padx=(0, 4))
            keyword_entry = ttk.Entry(toolbar, textvariable=self.keyword_var, width=24)
            keyword_entry.grid(row=0, column=6, sticky="w", padx=(0, 8))
            self._fill_default_entity()
        else:
            ttk.Label(toolbar, text="插件/类/步骤").grid(row=0, column=3, sticky="w", padx=(0, 4))
            keyword_entry = ttk.Entry(toolbar, textvariable=self.keyword_var, width=28)
            keyword_entry.grid(row=0, column=4, sticky="w", padx=(0, 8))
            ttk.Label(toolbar, text="修改人").grid(row=0, column=5, sticky="w", padx=(0, 4))
            modifiedby_entry = ttk.Entry(toolbar, textvariable=self.modifiedby_var, width=24)
            modifiedby_entry.grid(row=0, column=6, sticky="w", padx=(0, 8))
            modifiedby_entry.bind("<Return>", lambda _event: self._on_filter_changed())

        keyword_entry.bind("<Return>", lambda _event: self._on_filter_changed())
        ttk.Button(toolbar, text="刷新", command=self._on_filter_changed).grid(row=0, column=7, sticky="w")

        tip = "请输入表名和字段名后刷新。" if self.kind == "field" else "请输入插件名称、类名或步骤名称后刷新。"
        self.status_var = tk.StringVar(value=tip)
        ttk.Label(self.parent, textvariable=self.status_var, foreground="#666").grid(
            row=1, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        frame = ttk.LabelFrame(self.parent, text=title, padding=4)
        frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 4))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=self.COLUMNS, show="headings", selectmode="browse")
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.HEADINGS[col])
            self.tree.column(col, width=self.WIDTHS[col], stretch=(col == "details"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.pager = PaginationBar(self.parent, default_page_size=2000, on_change=self.refresh)
        self.pager.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))
        self._load_environments()

    def _fill_default_entity(self) -> None:
        vars_map = getattr(self.gui, "vars", {}) or {}
        if "entity_logical_name" in vars_map:
            self.entity_var.set(str(vars_map["entity_logical_name"].get()).strip())
        if self.entity_var.get():
            return
        try:
            from d365_field_creator import load_config

            cfg = load_config(self.gui._get_config_path())
            self.entity_var.set(str(cfg.get("entity_logical_name", "")).strip())
        except Exception:
            pass

    def _load_environments(self) -> None:
        from d365_field_creator import load_environments

        envs = load_environments(self.gui._get_config_path())
        self.env_map = {str(env.get("name") or env.get("org_url") or "环境"): env for env in envs}
        values = list(self.env_map.keys())
        self.env_combo.configure(values=values)
        if values and not self.env_var.get():
            self.env_var.set(values[0])

    def on_show(self) -> None:
        if self.kind == "field":
            self._fill_default_entity()

    def _on_filter_changed(self) -> None:
        self.pager.page = 1
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        env = self.env_map.get(self.env_var.get()) if hasattr(self, "env_map") else None
        if not env:
            self.status_var.set("未找到环境配置。")
            return

        keyword = self.keyword_var.get().strip()
        modifiedby_keyword = self.modifiedby_var.get().strip() if self.kind == "plugin" else ""
        entity = self.entity_var.get().strip() if self.kind == "field" else ""
        if self.kind == "field" and (not entity or not keyword):
            self.status_var.set("请输入表名和字段名后刷新，避免一次查询过多字段。")
            self._set_rows([], 0)
            return
        if self.kind == "plugin" and not keyword and not modifiedby_keyword:
            self.status_var.set("请输入插件名称、类名或步骤名称后刷新，避免一次查询过多插件数据。")
            self._set_rows([], 0)
            return

        self._loading = True
        self.status_var.set("正在加载变更记录...")
        page_size = self.pager.page_size()
        offset = self.pager.offset()

        def worker() -> None:
            try:
                from d365_field_creator import create_creator_from_environment

                creator = create_creator_from_environment(env)
                if self.kind == "field":
                    rows, total = self._load_field_rows(creator, entity, keyword, page_size, offset)
                else:
                    rows, total = self._load_plugin_rows(creator, keyword, modifiedby_keyword, page_size, offset)
                error = ""
            except Exception as exc:
                rows, total, error = [], 0, str(exc)

            def done() -> None:
                self._loading = False
                if error:
                    self.status_var.set(f"加载失败: {error}")
                    self._set_rows([], 0)
                    return
                self._set_rows(rows, total)
                env_name = str(env.get("name") or self.env_var.get())
                self.status_var.set(f"{env_name}: 显示 {len(rows)} 条 / 共 {total} 条")

            self.gui.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _request_json(self, creator: Any, url: str) -> Dict[str, Any]:
        headers = dict(creator.headers)
        headers["Prefer"] = 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
        resp = requests.get(url, headers=headers, timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:800]}")
        return resp.json()

    def _load_field_rows(
        self, creator: Any, entity: str, field_name: str, top: int, skip: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        select = "LogicalName,SchemaName,DisplayName,AttributeType,MetadataId,IsCustomAttribute,ModifiedOn"
        rows = self._load_exact_attribute(creator, entity, field_name, select)
        if not rows:
            rows = self._load_matching_attributes(creator, entity, field_name, select)
        self._attach_domain_users(creator, rows)
        rows.sort(key=lambda r: (r.get("modifiedon") or "", r.get("logical_name") or ""), reverse=True)
        total = len(rows)
        return rows[skip : skip + top], total

    def _load_exact_attribute(
        self, creator: Any, entity: str, field_name: str, select: str
    ) -> List[Dict[str, Any]]:
        safe_entity = _odata_text(entity)
        safe_field = _odata_text(field_name)
        url = (
            f"{creator.api_base}/EntityDefinitions(LogicalName='{safe_entity}')"
            f"/Attributes(LogicalName='{safe_field}')?$select={select}"
        )
        try:
            item = self._request_json(creator, url)
        except Exception:
            return []
        return [self._field_row(item)] if item else []

    def _load_matching_attributes(
        self, creator: Any, entity: str, field_name: str, select: str
    ) -> List[Dict[str, Any]]:
        safe_entity = _odata_text(entity)
        url = (
            f"{creator.api_base}/EntityDefinitions(LogicalName='{safe_entity}')/Attributes"
            f"?$select={select}&$orderby=LogicalName asc"
        )
        try:
            data = self._request_json(creator, url)
        except Exception:
            fallback_select = "LogicalName,SchemaName,DisplayName,AttributeType,MetadataId,IsCustomAttribute"
            url = (
                f"{creator.api_base}/EntityDefinitions(LogicalName='{safe_entity}')/Attributes"
                f"?$select={fallback_select}&$orderby=LogicalName asc"
            )
            data = self._request_json(creator, url)

        keyword = field_name.lower()
        rows: List[Dict[str, Any]] = []
        for item in data.get("value", []):
            logical = str(item.get("LogicalName") or "")
            schema = str(item.get("SchemaName") or "")
            display = self._display_label(item.get("DisplayName"))
            if keyword not in f"{logical} {schema} {display}".lower():
                continue
            rows.append(self._field_row(item))
        return rows

    def _field_row(self, item: Dict[str, Any]) -> Dict[str, Any]:
        logical = str(item.get("LogicalName") or "")
        schema = str(item.get("SchemaName") or "")
        display = self._display_label(item.get("DisplayName"))
        user_id = _clean_guid(
            item.get("_modifiedby_value")
            or item.get("modifiedby")
            or item.get("ModifiedBy")
            or item.get("modifiedby_value")
        )
        return {
            "modifiedon": str(item.get("ModifiedOn") or item.get("modifiedon") or ""),
            "modifiedby": "",
            "modifiedby_id": user_id,
            "component": "字段",
            "name": display,
            "logical_name": logical,
            "details": f"Schema={schema}; Type={item.get('AttributeType', '')}; Custom={item.get('IsCustomAttribute', '')}",
        }

    def _load_plugin_rows(self, creator: Any, keyword: str, modifiedby_keyword: str, top: int, skip: int) -> Tuple[List[Dict[str, Any]], int]:
        safe_kw = _odata_text(keyword)
        has_keyword = bool(keyword.strip())
        queries = [
            (
                "pluginassemblies",
                "程序集",
                "pluginassemblyid,name,version,modifiedon,_modifiedby_value",
                f"contains(name,'{safe_kw}')" if has_keyword else "",
            ),
            (
                "plugintypes",
                "插件类型",
                "plugintypeid,name,typename,friendlyname,modifiedon,_modifiedby_value",
                f"contains(name,'{safe_kw}') or contains(typename,'{safe_kw}') or contains(friendlyname,'{safe_kw}')" if has_keyword else "",
            ),
            (
                "sdkmessageprocessingsteps",
                "处理步骤",
                "sdkmessageprocessingstepid,name,stage,mode,rank,modifiedon,_modifiedby_value",
                f"contains(name,'{safe_kw}')" if has_keyword else "",
            ),
        ]
        rows: List[Dict[str, Any]] = []
        for table, label, select, filter_text in queries:
            filter_query = quote(filter_text, safe="(),='$") if filter_text else ""
            filter_part = f"&$filter={filter_query}" if filter_query else ""
            url = (
                f"{creator.api_base}/{table}?$select={select}"
                f"{filter_part}"
                f"&$orderby=modifiedon desc&$top=500"
            )
            data = self._request_json(creator, url)
            for item in data.get("value", []):
                name = str(item.get("friendlyname") or item.get("name") or "")
                logical = str(item.get("typename") or item.get("name") or "")
                rows.append(
                    {
                        "modifiedon": str(item.get("modifiedon") or ""),
                        "modifiedby": _display_lookup(item, "_modifiedby_value"),
                        "modifiedby_id": _clean_guid(item.get("_modifiedby_value")),
                        "component": label,
                        "name": name,
                        "logical_name": logical,
                        "details": self._plugin_details(table, item),
                    }
                )
        self._attach_domain_users(creator, rows)
        modifier_keyword = modifiedby_keyword.strip().lower()
        if modifier_keyword:
            rows = [
                row
                for row in rows
                if modifier_keyword in str(row.get("modifiedby", "")).lower()
                or modifier_keyword in str(row.get("modifiedby_id", "")).lower()
            ]
        rows.sort(key=lambda r: r.get("modifiedon") or "", reverse=True)
        total = len(rows)
        return rows[skip : skip + top], total

    def _attach_domain_users(self, creator: Any, rows: List[Dict[str, Any]]) -> None:
        user_ids = [str(row.get("modifiedby_id") or "") for row in rows if row.get("modifiedby_id")]
        user_map = self._load_user_map(creator, user_ids)
        for row in rows:
            user_id = str(row.get("modifiedby_id") or "")
            if user_id and user_map.get(user_id):
                row["modifiedby"] = user_map[user_id]
            elif not row.get("modifiedby"):
                row["modifiedby"] = user_id

    def _load_user_map(self, creator: Any, user_ids: Iterable[str]) -> Dict[str, str]:
        ids = sorted({_clean_guid(user_id) for user_id in user_ids if _clean_guid(user_id)})
        result: Dict[str, str] = {}
        for index in range(0, len(ids), 20):
            chunk = ids[index : index + 20]
            filter_text = " or ".join(f"systemuserid eq {user_id}" for user_id in chunk)
            url = (
                f"{creator.api_base}/systemusers"
                f"?$select=systemuserid,domainname,internalemailaddress,fullname"
                f"&$filter={quote(filter_text, safe='()=$')}"
            )
            try:
                data = self._request_json(creator, url)
            except Exception:
                continue
            for user in data.get("value", []):
                user_id = _clean_guid(user.get("systemuserid"))
                label = _domain_label(user)
                if user_id and label:
                    result[user_id] = label
        return result

    def _set_rows(self, rows: List[Dict[str, Any]], total: int) -> None:
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    _fmt_time(row.get("modifiedon")),
                    str(row.get("modifiedby", "")),
                    str(row.get("component", "")),
                    str(row.get("name", "")),
                    str(row.get("logical_name", "")),
                    str(row.get("details", "")),
                ),
            )
        self.pager.set_total(total)

    def _display_label(self, label_obj: Any) -> str:
        if not isinstance(label_obj, dict):
            return ""
        user_label = label_obj.get("UserLocalizedLabel") or {}
        if isinstance(user_label, dict) and user_label.get("Label"):
            return str(user_label.get("Label"))
        labels = label_obj.get("LocalizedLabels") or []
        if labels and isinstance(labels[0], dict):
            return str(labels[0].get("Label") or "")
        return ""

    def _plugin_details(self, table: str, item: Dict[str, Any]) -> str:
        if table == "pluginassemblies":
            return f"Version={item.get('version', '')}"
        if table == "plugintypes":
            return f"TypeName={item.get('typename', '')}"
        return f"Stage={item.get('stage', '')}; Mode={item.get('mode', '')}; Rank={item.get('rank', '')}"
