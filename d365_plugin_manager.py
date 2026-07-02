"""Dynamics 365 插件程序集 / 插件类型 / 处理步骤 — Web API 封装。"""

from __future__ import annotations

import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

ProgressCallback = Callable[[str, int, int, str], None]

PLUGIN_STAGE_LABELS: Dict[int, str] = {
    10: "PreValidation (预验证)",
    20: "PreOperation (前置)",
    40: "PostOperation (后置)",
    50: "PostOperation (Deprecated)",
}

PLUGIN_MODE_LABELS: Dict[int, str] = {
    0: "同步",
    1: "异步",
}

PLUGIN_ISOLATION_LABELS: Dict[int, str] = {
    1: "None",
    2: "Sandbox",
    3: "External",
}

PLUGIN_ASSEMBLY_ALLOWLIST: Tuple[str, ...] = (
    "SanyD365.D365Extension.Sales",
    "SanyD365.D365ExtensionApi.Sales",
)


def _clean_guid(value: Any) -> str:
    return str(value or "").strip().strip("{}")


def _parse_entity_id_from_response(resp: requests.Response, *id_keys: str) -> str:
    entity_id_header = resp.headers.get("OData-EntityId") or resp.headers.get("Location") or ""
    match = re.search(r"\(([^)]+)\)", entity_id_header)
    if match:
        return match.group(1).strip("'\"{}")
    try:
        body = resp.json()
    except ValueError:
        return ""
    for key in id_keys:
        if body.get(key):
            return _clean_guid(body[key])
    return ""


class D365PluginManager:
    """Plugin Registration Tool 等效 Web API 操作。"""

    def __init__(self, creator: Any) -> None:
        self.creator = creator

    def _normalize_next_url(self, url: str) -> str:
        if not url:
            return ""
        if url.startswith("http"):
            return url
        org_base = self.creator.auth.org_url.rstrip("/")
        if url.startswith("/"):
            return org_base + url
        return f"{self.creator.api_base.rstrip('/')}/{url.lstrip('/')}"

    def _fetch_all_pages(
        self,
        relative_url: str,
        *,
        progress_label: str = "",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        next_url = self._normalize_next_url(relative_url)
        page_index = 0
        while next_url:
            page_index += 1
            resp = requests.get(next_url, headers=self.creator.headers, timeout=300)
            if resp.status_code >= 400:
                raise RuntimeError(f"GET failed: HTTP {resp.status_code}, {resp.text}")
            data = resp.json()
            page_rows = data.get("value", [])
            items.extend(page_rows)
            if progress_callback and progress_label:
                progress_callback(progress_label, len(items), max(len(items), page_index * 5000), f"已读取 {len(items)} 条")
            next_url = self._normalize_next_url(data.get("@odata.nextLink") or "")
        return items

    def _escape_odata_string(self, value: str) -> str:
        return value.replace("'", "''")

    def _build_guid_or_filter(self, field: str, guids: List[str]) -> str:
        parts = [f"{field} eq {_clean_guid(guid)}" for guid in guids if _clean_guid(guid)]
        return " or ".join(parts)

    def _fetch_steps_for_types(
        self,
        type_ids: List[str],
        *,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[Dict[str, Any]]:
        cleaned_ids = [_clean_guid(type_id) for type_id in type_ids if _clean_guid(type_id)]
        if not cleaned_ids:
            return []
        select = (
            "sdkmessageprocessingstepid,name,stage,mode,rank,statecode,statuscode,"
            "filteringattributes,configuration,description,_plugintypeid_value,modifiedon,"
            "_sdkmessageid_value,_sdkmessagefilterid_value"
        )
        chunk_size = 80
        chunks = [cleaned_ids[index : index + chunk_size] for index in range(0, len(cleaned_ids), chunk_size)]
        total_batches = len(chunks)
        steps: List[Dict[str, Any]] = []
        done_batches = 0

        def fetch_chunk(chunk: List[str]) -> List[Dict[str, Any]]:
            step_filter = self._build_guid_or_filter("_plugintypeid_value", chunk)
            if not step_filter:
                return []
            return self._fetch_all_pages(
                f"{self.creator.api_base}/sdkmessageprocessingsteps"
                f"?$select={select}&$filter={step_filter}&$orderby=stage asc,rank asc",
            )

        workers = min(8, max(total_batches, 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_chunk, chunk) for chunk in chunks]
            for future in as_completed(futures):
                steps.extend(future.result())
                done_batches += 1
                if progress_callback:
                    progress_callback(
                        "steps",
                        done_batches,
                        total_batches,
                        f"正在加载处理步骤 ({done_batches}/{total_batches} 批)...",
                    )
        return steps

    def list_plugin_hierarchy(
        self,
        keyword: str = "",
        *,
        assembly_names: Optional[List[str]] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> List[Dict[str, Any]]:
        def report(stage: str, current: int, total: int, message: str) -> None:
            if progress_callback:
                progress_callback(stage, current, total, message)

        allowed_names = assembly_names or list(PLUGIN_ASSEMBLY_ALLOWLIST)
        name_filter = " or ".join(
            f"name eq '{self._escape_odata_string(name)}'" for name in allowed_names if name.strip()
        )
        if not name_filter:
            return []

        report("assemblies", 0, 1, "正在加载 Sales 程序集...")
        assemblies = self._fetch_all_pages(
            f"{self.creator.api_base}/pluginassemblies"
            f"?$select=pluginassemblyid,name,version,culture,publickeytoken,isolationmode,"
            f"sourcetype,ismanaged,modifiedon,description"
            f"&$filter={name_filter}&$orderby=name asc",
            progress_label="assemblies",
            progress_callback=progress_callback,
        )
        assembly_ids = [_clean_guid(assembly.get("pluginassemblyid")) for assembly in assemblies]
        assembly_ids = [assembly_id for assembly_id in assembly_ids if assembly_id]
        if not assembly_ids:
            report("done", 0, 0, "未找到指定的 Sales 程序集。")
            return []

        type_filter = self._build_guid_or_filter("_pluginassemblyid_value", assembly_ids)
        report("types", 0, 1, f"正在加载插件类型（{len(assemblies)} 个程序集）...")
        plugin_types = self._fetch_all_pages(
            f"{self.creator.api_base}/plugintypes"
            f"?$select=plugintypeid,name,friendlyname,typename,description,"
            f"_pluginassemblyid_value,modifiedon,isworkflowactivity"
            f"&$filter={type_filter}&$orderby=typename asc",
            progress_label="types",
            progress_callback=progress_callback,
        )
        type_ids = [_clean_guid(plugin_type.get("plugintypeid")) for plugin_type in plugin_types]
        report("steps", 0, 1, f"正在并行加载处理步骤（{len(plugin_types)} 个插件类型）...")
        steps = self._fetch_steps_for_types(type_ids, progress_callback=progress_callback)
        report("build", 0, 1, "正在整理数据...")

        types_by_assembly: Dict[str, List[Dict[str, Any]]] = {}
        for plugin_type in plugin_types:
            assembly_id = _clean_guid(plugin_type.get("_pluginassemblyid_value"))
            types_by_assembly.setdefault(assembly_id, []).append(plugin_type)

        steps_by_type: Dict[str, List[Dict[str, Any]]] = {}
        for step in steps:
            type_id = _clean_guid(step.get("_plugintypeid_value"))
            steps_by_type.setdefault(type_id, []).append(step)

        kw = keyword.strip().lower()
        hierarchy: List[Dict[str, Any]] = []
        allowed_name_set = {name.lower() for name in allowed_names}
        for assembly in assemblies:
            assembly_id = _clean_guid(assembly.get("pluginassemblyid"))
            assembly_name = str(assembly.get("name") or "")
            if assembly_name.lower() not in allowed_name_set:
                continue
            type_rows = types_by_assembly.get(assembly_id, [])
            step_count = sum(len(steps_by_type.get(_clean_guid(t.get("plugintypeid")), [])) for t in type_rows)
            if kw and kw not in assembly_name.lower():
                type_match = any(
                    kw in str(t.get("typename", "")).lower() or kw in str(t.get("name", "")).lower()
                    for t in type_rows
                )
                step_match = any(
                    kw in str(s.get("name", "")).lower()
                    for t in type_rows
                    for s in steps_by_type.get(_clean_guid(t.get("plugintypeid")), [])
                )
                if not type_match and not step_match:
                    continue

            type_nodes: List[Dict[str, Any]] = []
            for plugin_type in type_rows:
                type_id = _clean_guid(plugin_type.get("plugintypeid"))
                step_rows = steps_by_type.get(type_id, [])
                type_nodes.append(
                    {
                        "plugin_type": plugin_type,
                        "steps": step_rows,
                    }
                )

            hierarchy.append(
                {
                    "assembly": assembly,
                    "plugin_types": type_nodes,
                    "step_count": step_count,
                }
            )
        report(
            "done",
            len(hierarchy),
            len(hierarchy),
            f"数据准备完成：{len(hierarchy)} 程序集, {len(plugin_types)} 类型, {len(steps)} 步骤",
        )
        return hierarchy

    def list_sdk_messages(self) -> List[Dict[str, str]]:
        rows = self._fetch_all_pages(
            f"{self.creator.api_base}/sdkmessages"
            "?$select=sdkmessageid,name&$filter=isactive eq true&$orderby=name asc"
        )
        return [
            {"sdkmessageid": _clean_guid(row.get("sdkmessageid")), "name": str(row.get("name") or "")}
            for row in rows
            if row.get("name")
        ]

    def find_sdk_message_filter(self, message_name: str, entity_logical_name: str) -> Dict[str, str]:
        message_name = message_name.strip()
        entity_logical_name = entity_logical_name.strip()
        if not message_name or not entity_logical_name:
            raise ValueError("消息名称和实体逻辑名不能为空。")
        url = (
            f"{self.creator.api_base}/sdkmessagefilters"
            f"?$select=sdkmessagefilterid,name,primaryobjecttypecode"
            f"&$filter=primaryobjecttypecode eq '{entity_logical_name}' and sdkmessageid/name eq '{message_name}'"
            f"&$expand=sdkmessageid($select=sdkmessageid,name)"
        )
        rows = self._fetch_all_pages(url)
        if not rows:
            raise RuntimeError(f"未找到消息过滤器: {message_name} / {entity_logical_name}")
        row = rows[0]
        message = row.get("sdkmessageid") or {}
        return {
            "sdkmessagefilterid": _clean_guid(row.get("sdkmessagefilterid")),
            "sdkmessageid": _clean_guid(message.get("sdkmessageid") or row.get("_sdkmessageid_value")),
            "message_name": str(message.get("name") or message_name),
            "entity_logical_name": str(row.get("primaryobjecttypecode") or entity_logical_name),
        }

    def register_assembly(
        self,
        dll_path: str,
        *,
        name: str = "",
        description: str = "",
        isolation_mode: int = 2,
        source_type: int = 0,
    ) -> str:
        if not os.path.isfile(dll_path):
            raise FileNotFoundError(f"找不到插件程序集: {dll_path}")
        assembly_name = name.strip() or os.path.splitext(os.path.basename(dll_path))[0]
        with open(dll_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")
        payload = {
            "name": assembly_name,
            "content": content_b64,
            "sourcetype": source_type,
            "isolationmode": isolation_mode,
            "description": description.strip(),
        }
        headers = dict(self.creator.headers)
        headers["Prefer"] = "return=representation"
        url = f"{self.creator.api_base}/pluginassemblies"
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=300)
        if resp.status_code >= 400:
            raise RuntimeError(f"注册程序集失败: HTTP {resp.status_code}, {resp.text}")
        assembly_id = _parse_entity_id_from_response(resp, "pluginassemblyid")
        if not assembly_id:
            raise RuntimeError("注册程序集成功，但未能解析 pluginassemblyid。")
        return assembly_id

    def update_assembly(self, assembly_id: str, dll_path: str) -> None:
        assembly_id = _clean_guid(assembly_id)
        if not assembly_id:
            raise ValueError("assembly_id 不能为空。")
        if not os.path.isfile(dll_path):
            raise FileNotFoundError(f"找不到插件程序集: {dll_path}")
        with open(dll_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")
        url = f"{self.creator.api_base}/pluginassemblies({assembly_id})"
        resp = requests.patch(
            url,
            headers=self.creator.headers,
            data=json.dumps({"content": content_b64}),
            timeout=300,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"更新程序集失败: HTTP {resp.status_code}, {resp.text}")

    def register_plugin_type(
        self,
        assembly_id: str,
        *,
        name: str,
        friendly_name: str,
        typename: str,
        description: str = "",
        is_workflow_activity: bool = False,
    ) -> str:
        assembly_id = _clean_guid(assembly_id)
        payload = {
            "name": name.strip(),
            "friendlyname": friendly_name.strip(),
            "typename": typename.strip(),
            "description": description.strip(),
            "isworkflowactivity": is_workflow_activity,
            "pluginassemblyid@odata.bind": f"/pluginassemblies({assembly_id})",
        }
        headers = dict(self.creator.headers)
        headers["Prefer"] = "return=representation"
        url = f"{self.creator.api_base}/plugintypes"
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f"注册插件类型失败: HTTP {resp.status_code}, {resp.text}")
        type_id = _parse_entity_id_from_response(resp, "plugintypeid")
        if not type_id:
            raise RuntimeError("注册插件类型成功，但未能解析 plugintypeid。")
        return type_id

    def register_step(
        self,
        plugin_type_id: str,
        *,
        message_name: str,
        entity_logical_name: str,
        stage: int = 40,
        mode: int = 0,
        rank: int = 1,
        name: str = "",
        description: str = "",
        filtering_attributes: str = "",
        configuration: str = "",
        supported_deployment: int = 0,
    ) -> str:
        plugin_type_id = _clean_guid(plugin_type_id)
        filter_info = self.find_sdk_message_filter(message_name, entity_logical_name)
        step_name = name.strip() or f"{message_name} of {entity_logical_name}"
        payload: Dict[str, Any] = {
            "name": step_name,
            "description": description.strip(),
            "stage": stage,
            "mode": mode,
            "rank": rank,
            "supporteddeployment": supported_deployment,
            "asyncautodelete": False,
            "plugintypeid@odata.bind": f"/plugintypes({plugin_type_id})",
            "sdkmessageid@odata.bind": f"/sdkmessages({filter_info['sdkmessageid']})",
            "sdkmessagefilterid@odata.bind": f"/sdkmessagefilters({filter_info['sdkmessagefilterid']})",
        }
        if filtering_attributes.strip():
            payload["filteringattributes"] = filtering_attributes.strip()
        if configuration.strip():
            payload["configuration"] = configuration.strip()
        headers = dict(self.creator.headers)
        headers["Prefer"] = "return=representation"
        url = f"{self.creator.api_base}/sdkmessageprocessingsteps"
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f"注册处理步骤失败: HTTP {resp.status_code}, {resp.text}")
        step_id = _parse_entity_id_from_response(resp, "sdkmessageprocessingstepid")
        if not step_id:
            raise RuntimeError("注册处理步骤成功，但未能解析 sdkmessageprocessingstepid。")
        return step_id

    def set_step_enabled(self, step_id: str, enabled: bool) -> None:
        step_id = _clean_guid(step_id)
        payload = {"statecode": 0, "statuscode": 1} if enabled else {"statecode": 1, "statuscode": 2}
        url = f"{self.creator.api_base}/sdkmessageprocessingsteps({step_id})"
        resp = requests.patch(url, headers=self.creator.headers, data=json.dumps(payload), timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"更新步骤状态失败: HTTP {resp.status_code}, {resp.text}")

    def delete_step(self, step_id: str) -> None:
        step_id = _clean_guid(step_id)
        url = f"{self.creator.api_base}/sdkmessageprocessingsteps({step_id})"
        self.creator._api_delete(url)

    def delete_plugin_type(self, plugin_type_id: str) -> None:
        plugin_type_id = _clean_guid(plugin_type_id)
        steps = self._fetch_all_pages(
            f"{self.creator.api_base}/sdkmessageprocessingsteps"
            f"?$select=sdkmessageprocessingstepid&$filter=_plugintypeid_value eq {plugin_type_id}"
        )
        for step in steps:
            self.delete_step(_clean_guid(step.get("sdkmessageprocessingstepid")))
        url = f"{self.creator.api_base}/plugintypes({plugin_type_id})"
        self.creator._api_delete(url)

    def delete_assembly(self, assembly_id: str) -> None:
        assembly_id = _clean_guid(assembly_id)
        plugin_types = self._fetch_all_pages(
            f"{self.creator.api_base}/plugintypes"
            f"?$select=plugintypeid&$filter=_pluginassemblyid_value eq {assembly_id}"
        )
        for plugin_type in plugin_types:
            self.delete_plugin_type(_clean_guid(plugin_type.get("plugintypeid")))
        url = f"{self.creator.api_base}/pluginassemblies({assembly_id})"
        self.creator._api_delete(url)

    @staticmethod
    def format_step_status(statecode: Any) -> str:
        return "已启用" if int(statecode or 0) == 0 else "已禁用"

    @staticmethod
    def format_stage(stage: Any) -> str:
        return PLUGIN_STAGE_LABELS.get(int(stage or 0), str(stage))

    @staticmethod
    def format_mode(mode: Any) -> str:
        return PLUGIN_MODE_LABELS.get(int(mode or 0), str(mode))
