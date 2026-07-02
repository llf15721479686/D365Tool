import argparse
import base64
import json
import re
import hashlib
import hmac
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import webbrowser
import platform

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from operation_logger import OperationLogger, default_db_path, sanitize_details
from d365_plugin_gui import PluginRegistrationPanel
from d365_access_gui import UserAccessPanel
from d365_publish_history_gui import PublishHistoryPanel
from d365_translation_gui import TranslationPanel
from d365_translation_manager import D365TranslationManager
from d365_js_capture_gui import JsCapturePanel
from d365_change_history_gui import D365ChangeHistoryPanel
from pagination_helper import PaginationBar


LOGICAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")
SCHEMA_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,79}$")
EMBEDDED_DEFAULT_CONFIG: Dict[str, Any] = {
    "tenant_id": "",
    "client_id": "",
    "client_secret": "",
    "org_url": "",
    "schema_file": "schema.json",
    "entity_logical_name": "",
    "solution_unique_name": "",
    "publisher_prefix": "mcs",
    "activation_secret": "",
    "baidu_app_id": "",
    "baidu_secret_key": "",
    "baidu_api_url": "",
    "youdao_app_key": "",
    "youdao_app_secret": "",
    "youdao_api_url": "",
    "openai_api_key": "",
    "openai_model": "gpt-5.4-mini",
    "openai_api_url": "https://api.openai.com/v1/responses",
}
FIELD_TYPE_LABEL_TO_VALUE: Dict[str, str] = {
    "单行文本": "string",
    "多行文本": "memo",
    "整数": "integer",
    "小数": "decimal",
    "浮点数": "double",
    "货币": "money",
    "日期时间": "datetime",
    "是/否": "boolean",
    "下拉选项": "picklist",
    "查找": "lookup",
    "文件": "file",
}
FIELD_TYPE_VALUE_TO_LABEL: Dict[str, str] = {v: k for k, v in FIELD_TYPE_LABEL_TO_VALUE.items()}
REQUIRED_LEVEL_LABEL_TO_VALUE: Dict[str, str] = {
    "可选": "None",
    "建议": "Recommended",
    "必填": "ApplicationRequired",
}
ENTITY_PERMISSION_LABELS: List[str] = ["读", "写", "创建", "删除", "追加", "共享"]
ENTITY_ACCESS_RIGHT_COLUMNS: List[tuple] = [
    ("创建", "Create"),
    ("读", "Read"),
    ("写", "Write"),
    ("删除", "Delete"),
    ("追加", "Append"),
    ("追加到", "AppendTo"),
    ("分派", "Assign"),
    ("共享", "Share"),
]
PRIVILEGE_VERB_BY_LABEL: Dict[str, str] = {label: verb for label, verb in ENTITY_ACCESS_RIGHT_COLUMNS}
PRIVILEGE_DEPTH_OPTIONS: List[str] = ["Basic", "Local", "Deep", "Global"]
PRIVILEGE_DEPTH_NAMES: List[str] = ["Basic", "Local", "Deep", "Global"]
PRIVILEGE_DEPTH_LABELS_CN: Dict[Optional[int], str] = {
    None: "无",
    0: "用户",
    1: "部门",
    2: "子部门",
    3: "组织",
}
PRIVILEGE_DEPTH_CYCLE: List[Optional[int]] = [None, 0, 1, 2, 3]
CHINESE_LANGUAGE_CODES = {2052, 1028, 3076}
ENGLISH_LANGUAGE_CODE = 1033
TABLE_QUERY_FIELD_COLUMNS: Tuple[str, ...] = (
    "logical_name",
    "schema_name",
    "display_name_zh",
    "display_name_en",
    "attribute_type",
    "required_level",
    "is_custom",
    "valid_for_create",
    "valid_for_update",
    "valid_for_read",
)
TABLE_QUERY_FIELD_HEADINGS: Dict[str, str] = {
    "logical_name": "逻辑名",
    "schema_name": "Schema名",
    "display_name_zh": "显示名(中文)",
    "display_name_en": "显示名(英文)",
    "attribute_type": "类型",
    "required_level": "必填",
    "is_custom": "自定义",
    "valid_for_create": "可创建",
    "valid_for_update": "可更新",
    "valid_for_read": "可读取",
}
TABLE_QUERY_FIELD_WIDTHS: Dict[str, int] = {
    "logical_name": 130,
    "schema_name": 130,
    "display_name_zh": 130,
    "display_name_en": 150,
    "attribute_type": 110,
    "required_level": 60,
    "is_custom": 52,
    "valid_for_create": 52,
    "valid_for_update": 52,
    "valid_for_read": 52,
}
TABLE_QUERY_PAGE_SIZE_OPTIONS: List[str] = ["50", "100", "200"]
TABLE_QUERY_DEFAULT_PAGE_SIZE = 100

PANEL_LABELS: Dict[str, str] = {
    "connection": "连接配置",
    "field": "字段创建",
    "field_changes": "字段变更记录",
    "local_table_store": "本地表存储",
    "translation": "实体翻译",
    "translation_records": "翻译记录列表",
    "table_query": "数据表查询",
    "access_inspector": "用户角色追溯",
    "permission": "实体权限管理",
    "deploy": "批量发版",
    "publish_history": "发布历史记录",
    "logs": "操作日志",
    "plugin": "插件注册",
    "plugin_changes": "插件变更记录",
    "js_capture": "脚本JS调试",
}

LOG_CATEGORY_LABELS: Dict[str, str] = {
    "system": "系统",
    "navigation": "导航",
    "connection": "连接配置",
    "field": "字段创建",
    "field_changes": "字段变更记录",
    "local_table_store": "本地表存储",
    "translation": "实体翻译",
    "table_query": "数据表查询",
    "access_inspector": "用户角色追溯",
    "permission": "权限管理",
    "deploy": "批量发版",
    "publish_history": "发布历史",
    "ui": "界面消息",
    "plugin": "插件注册",
    "plugin_changes": "插件变更记录",
    "js_capture": "脚本JS调试",
}

LOG_STATUS_LABELS: Dict[str, str] = {
    "started": "开始",
    "success": "成功",
    "failed": "失败",
    "cancelled": "取消",
    "info": "信息",
}

PAYMENT_REQUIRED_AMOUNT = 30
PAYMENT_QR_IMAGE_URL = "https://mp-22e7468a-898b-4fd0-b8ef-c58cd290ba45.cdn.bspapp.com/图片/pay.jpg"
PAYMENT_UNLOCK_FILE = Path.home() / ".d365tool-payment-unlock.json"
TRIAL_DAYS = 1
# 仅你自己知道的签名密钥，用于生成/校验激活码（请勿泄露）
ACTIVATION_SECRET = ""


@dataclass
class D365Auth:
    tenant_id: str
    client_id: str
    client_secret: str
    org_url: str


class D365FieldCreator:
    def __init__(self, auth: D365Auth, schema_file: str) -> None:
        self.auth = auth
        self.schema_file = schema_file
        self.token = self._get_token()
        self.api_base = auth.org_url.rstrip("/") + "/api/data/v9.2"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        self.customization_retry_attempts = 6
        self.customization_retry_interval_seconds = 15

    def _get_token(self) -> str:
        token_url = f"https://login.microsoftonline.com/{self.auth.tenant_id}/oauth2/v2.0/token"
        normalized_org_url = self.auth.org_url.rstrip("/")
        data = {
            "grant_type": "client_credentials",
            "client_id": self.auth.client_id,
            "client_secret": self.auth.client_secret,
            "scope": f"{normalized_org_url}/.default",
        }
        resp = requests.post(token_url, data=data, timeout=60)
        if resp.status_code != 200:
            error_message = f"Get token failed: HTTP {resp.status_code}"
            try:
                body = resp.json()
                aad_error = body.get("error")
                aad_desc = body.get("error_description")
                aad_codes = body.get("error_codes")
                trace_id = body.get("trace_id")
                correlation_id = body.get("correlation_id")
                if aad_error:
                    error_message += f", error={aad_error}"
                if aad_desc:
                    error_message += f", description={aad_desc}"
                if aad_codes:
                    error_message += f", error_codes={aad_codes}"
                if trace_id:
                    error_message += f", trace_id={trace_id}"
                if correlation_id:
                    error_message += f", correlation_id={correlation_id}"
            except ValueError:
                error_message += f", response={resp.text}"
            raise RuntimeError(error_message)
        return resp.json()["access_token"]

    def run(self) -> None:
        with open(self.schema_file, "r", encoding="utf-8") as f:
            schema = json.load(f)
        self._validate_schema(schema)

        entity_name = schema["entity_logical_name"]
        solution = schema["solution_unique_name"]

        for field in schema["fields"]:
            print(self.create_field(entity_name=entity_name, solution=solution, field=field))

    def create_field(self, entity_name: str, solution: str, field: Dict[str, Any]) -> str:
        logical_name = field["logical_name"]
        if self._exists(entity_name, logical_name):
            return f"Skip existing field: {logical_name}"

        if str(field.get("field_type", "")).strip().lower() == "lookup":
            self._create_lookup_relationship(entity_name=entity_name, solution=solution, field=field)
            return f"创建字段: {logical_name}"

        payload = self._build_payload(field)
        url = (
            f"{self.api_base}/EntityDefinitions(LogicalName='{entity_name}')/Attributes"
            f"?MSCRM.SolutionUniqueName={solution}"
        )
        resp = self._post_with_customization_retry(url, payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Create field failed: {logical_name}, {resp.status_code}, {resp.text}")
        return f"创建字段: {logical_name}"

    def _create_lookup_relationship(self, entity_name: str, solution: str, field: Dict[str, Any]) -> None:
        logical_name = field["logical_name"]
        cfg = field.get("lookup", {})
        target_entity = str(cfg.get("target_entity", "")).strip()
        if not target_entity:
            raise ValueError("lookup target_entity is required")

        relationship_schema_name = str(cfg.get("relationship_schema_name", "")).strip()
        if not relationship_schema_name:
            relationship_schema_name = self._build_relationship_schema_name(
                field_schema_name=str(field["schema_name"]),
                entity_name=entity_name,
                target_entity=target_entity,
            )

        relationship_payload = {
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "SchemaName": relationship_schema_name,
            "ReferencedEntity": target_entity,
            "ReferencingEntity": entity_name,
            "Lookup": {
                "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
                "SchemaName": field["schema_name"],
                "DisplayName": {"LocalizedLabels": [{"Label": field["display_name"], "LanguageCode": 2052}]},
                "Description": {"LocalizedLabels": [{"Label": field.get("description", ""), "LanguageCode": 2052}]},
                "RequiredLevel": {"Value": field.get("required_level", "None")},
            },
        }

        url = f"{self.api_base}/RelationshipDefinitions?MSCRM.SolutionUniqueName={solution}"
        resp = self._post_with_customization_retry(url, relationship_payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                "Create lookup field failed: "
                f"{logical_name}, entity={entity_name}, target={target_entity}, "
                f"relationship={relationship_schema_name}, {resp.status_code}, {resp.text}"
            )

    def _build_relationship_schema_name(self, field_schema_name: str, entity_name: str, target_entity: str) -> str:
        raw_name = f"{entity_name}_{target_entity}_{field_schema_name}_rel"
        normalized = re.sub(r"[^A-Za-z0-9_]", "_", raw_name).strip("_")
        if not normalized:
            normalized = "D365LookupRel"
        if not normalized[0].isalpha():
            normalized = "R" + normalized
        return normalized[:79]

    def _post_with_customization_retry(self, url: str, payload: Dict[str, Any]) -> requests.Response:
        last_resp: Optional[requests.Response] = None
        for attempt in range(1, self.customization_retry_attempts + 1):
            resp = requests.post(url, headers=self.headers, data=json.dumps(payload), timeout=60)
            last_resp = resp
            if resp.status_code < 400:
                return resp
            if not self._is_customization_lock_error(resp):
                return resp
            if attempt < self.customization_retry_attempts:
                print(
                    "检测到 D365 正在执行系统级操作（LanguageProvision/EntityCustomization），"
                    f"第 {attempt} 次重试将在 {self.customization_retry_interval_seconds} 秒后进行..."
                )
                time.sleep(self.customization_retry_interval_seconds)
        return last_resp if last_resp is not None else requests.Response()

    def _is_customization_lock_error(self, resp: requests.Response) -> bool:
        if resp.status_code not in {409, 429}:
            return False
        body = resp.text.lower()
        return ("0x80071151" in body) or ("languageprovision" in body) or ("entitycustomization" in body)

    def get_entities_page(self, next_link: Optional[str] = None) -> Dict[str, Any]:
        # Avoid unsupported query parameters and rely on server paging.
        url = next_link or f"{self.api_base}/EntityDefinitions?$select=LogicalName"
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Load entities failed: {resp.status_code}, {resp.text}")
        body = resp.json()
        rows = body.get("value", [])
        entities: List[Dict[str, str]] = []
        for row in rows:
            logical_name = row.get("LogicalName", "")
            if logical_name:
                entities.append({"logical_name": logical_name, "display_name": ""})
        entities.sort(key=lambda x: x["logical_name"])
        return {"items": entities, "next_link": body.get("@odata.nextLink")}

    def list_all_entities(self) -> List[str]:
        names: List[str] = []
        next_link: Optional[str] = None
        while True:
            page = self.get_entities_page(next_link)
            for item in page.get("items", []):
                logical_name = str(item.get("logical_name", "")).strip()
                if logical_name:
                    names.append(logical_name)
            next_link = page.get("next_link")
            if not next_link:
                break
        return sorted(set(names))

    def search_entities(
        self,
        keyword: str,
        top: int = 50,
        progress_callback: Optional[Any] = None,
    ) -> List[str]:
        kw = keyword.strip().lower()
        if not kw:
            return []

        def _report(message: str) -> None:
            if progress_callback:
                progress_callback(message)

        # Metadata Entity 不支持 contains/startswith 等 OData 过滤，先尝试精确匹配。
        if LOGICAL_NAME_RE.match(kw):
            escaped = kw.replace("'", "''")
            url = f"{self.api_base}/EntityDefinitions(LogicalName='{escaped}')?$select=LogicalName"
            resp = requests.get(url, headers=self.headers, timeout=30)
            if resp.status_code == 200:
                logical_name = str(resp.json().get("LogicalName", "")).strip()
                if logical_name:
                    return [logical_name]

        _report("正在扫描环境表列表，请稍候...")
        matches: List[str] = []
        seen: set = set()
        next_link: Optional[str] = None
        page_count = 0
        max_pages = 200

        while len(matches) < top and page_count < max_pages:
            page_count += 1
            page = self.get_entities_page(next_link)
            for item in page.get("items", []):
                name = str(item.get("logical_name", "")).strip()
                if not name or name in seen:
                    continue
                if kw in name.lower():
                    seen.add(name)
                    matches.append(name)
                    if len(matches) >= top:
                        break
            if page_count % 3 == 0 or len(matches) >= top:
                _report(f"已扫描 {page_count} 页，找到 {len(matches)} 个匹配表...")
            next_link = page.get("next_link")
            if not next_link:
                break

        def _sort_key(name: str) -> Tuple[int, str]:
            name_lower = name.lower()
            if name_lower == kw:
                return (0, name)
            if name_lower.startswith(kw):
                return (1, name)
            return (2, name)

        matches.sort(key=_sort_key)
        return matches[:top]

    def _fetch_single_attribute_display_labels(
        self, entity_logical_name: str, attribute_logical_name: str
    ) -> Tuple[str, str]:
        escaped_entity = entity_logical_name.replace("'", "''")
        escaped_attr = attribute_logical_name.replace("'", "''")
        url = (
            f"{self.api_base}/EntityDefinitions(LogicalName='{escaped_entity}')"
            f"/Attributes(LogicalName='{escaped_attr}')?$select=DisplayName"
        )
        resp = requests.get(url, headers=self.headers, timeout=30)
        if resp.status_code >= 400:
            return "", ""
        return _parse_display_name_labels(resp.json().get("DisplayName"))

    def _fetch_attribute_bilingual_labels(
        self, entity_logical_name: str, attribute_names: List[str]
    ) -> Dict[str, Tuple[str, str]]:
        labels: Dict[str, Tuple[str, str]] = {}
        if not attribute_names:
            return labels
        unique_names = sorted({name for name in attribute_names if name})
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {
                executor.submit(self._fetch_single_attribute_display_labels, entity_logical_name, name): name
                for name in unique_names
            }
            for future in as_completed(futures):
                attr_name = futures[future]
                try:
                    labels[attr_name] = future.result()
                except Exception:
                    labels[attr_name] = ("", "")
        return labels

    def search_solutions(self, keyword: str, top: int = 30) -> List[Dict[str, str]]:
        return self.list_solutions(keyword=keyword, top=top)

    def list_solutions(self, keyword: str = "", top: int = 100) -> List[Dict[str, str]]:
        kw = keyword.strip()
        kw_safe = kw.replace("'", "''") if kw else ""
        base = (
            f"{self.api_base}/solutions?$select=solutionid,uniquename,friendlyname&"
            f"$top={int(top)}&$orderby=uniquename asc"
        )
        url = base
        if kw_safe:
            url = (
                f"{base}&$filter=contains(uniquename,'{kw_safe}') "
                f"or contains(friendlyname,'{kw_safe}')"
            )
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Load solutions failed: {resp.status_code}, {resp.text}")
        rows = resp.json().get("value", [])
        items: List[Dict[str, str]] = []
        for row in rows:
            unique_name = str(row.get("uniquename", "")).strip()
            if not unique_name:
                continue
            items.append(
                {
                    "solution_id": row.get("solutionid", ""),
                    "unique_name": unique_name,
                    "friendly_name": str(row.get("friendlyname", "")).strip(),
                }
            )
        return items

    def list_entities_by_solution(self, solution_id: str) -> List[Dict[str, str]]:
        sid = solution_id.strip().strip("{}")
        if not sid:
            return []
        sc_url = (
            f"{self.api_base}/solutioncomponents?"
            f"$select=objectid,componenttype&"
            f"$filter=_solutionid_value eq {sid} and componenttype eq 1"
        )
        resp = requests.get(sc_url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Load solution entities failed: {resp.status_code}, {resp.text}")
        rows = resp.json().get("value", [])
        entity_ids = [r.get("objectid", "").strip("{}") for r in rows if r.get("objectid")]
        entity_ids = sorted(set([x for x in entity_ids if x]))
        entities: List[Dict[str, str]] = []
        for meta_id in entity_ids:
            e_url = f"{self.api_base}/EntityDefinitions({meta_id})?$select=LogicalName"
            e_resp = requests.get(e_url, headers=self.headers, timeout=60)
            if e_resp.status_code >= 400:
                continue
            logical_name = e_resp.json().get("LogicalName", "")
            if logical_name:
                entities.append({"logical_name": logical_name, "display_name": ""})
        entities.sort(key=lambda x: x["logical_name"])
        return entities

    def _api_get(self, url: str, timeout: int = 60) -> requests.Response:
        resp = requests.get(url, headers=self.headers, timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"GET failed: HTTP {resp.status_code}, {resp.text}")
        return resp

    def _api_post(
        self, url: str, payload: Dict[str, Any], timeout: Optional[int] = 60
    ) -> requests.Response:
        resp = requests.post(url, headers=self.headers, data=json.dumps(payload), timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"POST failed: HTTP {resp.status_code}, {resp.text}")
        return resp

    def _api_delete(self, url: str, timeout: int = 60) -> requests.Response:
        resp = requests.delete(url, headers=self.headers, timeout=timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"DELETE failed: HTTP {resp.status_code}, {resp.text}")
        return resp

    def _extract_guid_field(self, body: Dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = body.get(key)
            if value:
                return str(value).strip().strip("{}")
        return ""

    def _get_parent_root_role_id(self, body: Dict[str, Any]) -> str:
        nested = body.get("parentrootroleid")
        if isinstance(nested, dict):
            nested_id = self._extract_guid_field(nested, "roleid", "RoleId")
            if nested_id:
                return nested_id
        return self._extract_guid_field(
            body,
            "parentrootroleid",
            "_parentrootroleid_value",
            "ParentRootRoleId",
        )

    def _get_parent_role_id(self, body: Dict[str, Any]) -> str:
        nested = body.get("parentroleid")
        if isinstance(nested, dict):
            nested_id = self._extract_guid_field(nested, "roleid", "RoleId")
            if nested_id:
                return nested_id
        return self._extract_guid_field(
            body,
            "parentroleid",
            "_parentroleid_value",
            "ParentRoleId",
        )

    def _is_modifiable_root_role(self, role_id: str, role_row: Dict[str, Any]) -> bool:
        rid = role_id.strip().strip("{}").lower()
        if not rid:
            return False
        parent_role = self._get_parent_role_id(role_row)
        if parent_role and parent_role.lower() != rid:
            return False
        parent_root = self._get_parent_root_role_id(role_row)
        if not parent_root:
            return not bool(parent_role)
        return rid == parent_root.lower()

    def _role_request_headers(self) -> Dict[str, str]:
        headers = dict(self.headers)
        headers["Prefer"] = 'odata.include-annotations="*"'
        return headers

    def _get_role_record(self, role_id: str) -> Dict[str, Any]:
        rid = role_id.strip().strip("{}")
        url = (
            f"{self.api_base}/roles({rid})?"
            f"$select=roleid,name,parentroleid,parentrootroleid,"
            f"_parentroleid_value,_parentrootroleid_value"
        )
        resp = requests.get(url, headers=self._role_request_headers(), timeout=60)
        if resp.status_code >= 400:
            url = f"{self.api_base}/roles({rid})"
            resp = requests.get(url, headers=self._role_request_headers(), timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"无法读取角色信息: {role_id}, {resp.status_code}, {resp.text}")
        return resp.json()

    def _find_root_role_id_by_name(self, role_name: str) -> str:
        name = role_name.strip()
        if not name:
            return ""
        name_safe = name.replace("'", "''")
        filters = [
            f"name eq '{name_safe}' and _parentroleid_value eq null",
            f"name eq '{name_safe}'",
        ]
        for role_filter in filters:
            url = (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid,"
                f"_parentroleid_value,_parentrootroleid_value&$top=5&$filter={role_filter}"
            )
            resp = requests.get(url, headers=self._role_request_headers(), timeout=60)
            if resp.status_code >= 400:
                continue
            for row in resp.json().get("value", []):
                role_id = str(row.get("roleid", "")).strip()
                if role_id and self._is_modifiable_root_role(role_id, row):
                    return role_id
        return ""

    def resolve_modifiable_role_id(self, role_id: str, cache: Optional[Dict[str, str]] = None) -> str:
        rid = role_id.strip().strip("{}")
        cache_key = rid.lower()
        if cache is not None and cache_key in cache:
            return cache[cache_key]
        body = self._get_role_record(role_id)
        parent_root = self._get_parent_root_role_id(body)
        parent_role = self._get_parent_role_id(body)
        role_self = self._extract_guid_field(body, "roleid", "RoleId") or rid
        role_name = str(body.get("name", "")).strip()
        if parent_role and parent_role.lower() != role_self.lower():
            if not parent_root:
                parent_root = self._find_root_role_id_by_name(role_name)
            if not parent_root:
                raise RuntimeError(
                    f"角色 [{role_name or role_self}] 为继承角色，无法定位可修改的根角色"
                )
            resolved = parent_root
        elif parent_root and parent_root.lower() != role_self.lower():
            resolved = parent_root
        elif parent_root:
            resolved = parent_root
        else:
            resolved = role_self
        if cache is not None:
            cache[cache_key] = resolved
        return resolved

    def normalize_roles_for_permissions(self, roles: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """按角色名去重；列表查询阶段已过滤为可修改根角色，此处不再逐条请求 API。"""
        by_name: Dict[str, Dict[str, str]] = {}
        for role in roles:
            name = str(role.get("name", "")).strip()
            role_id = str(role.get("role_id", "")).strip()
            if not name or not role_id:
                continue
            by_name[name.strip().lower()] = {"role_id": role_id, "name": name}
        result = list(by_name.values())
        result.sort(key=lambda x: x["name"].lower())
        return result

    def search_security_roles(self, keyword: str, top: int = 50) -> List[Dict[str, str]]:
        kw = keyword.strip()
        base = (
            f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid&"
            f"$top={int(top)}&$orderby=name asc"
        )
        if kw:
            kw_safe = kw.replace("'", "''")
            url = f"{base}&$filter=contains(name,'{kw_safe}')"
        else:
            url = base
        resp = self._api_get(url)
        rows = resp.json().get("value", [])
        items: List[Dict[str, str]] = []
        for row in rows:
            role_id = str(row.get("roleid", "")).strip()
            name = str(row.get("name", "")).strip()
            if role_id and name and self._is_modifiable_root_role(role_id, row):
                items.append({"role_id": role_id, "name": name})
        items.sort(key=lambda x: x["name"].lower())
        return items

    def list_all_security_roles(self) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        urls = [
            (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid,"
                f"_parentroleid_value,_parentrootroleid_value&"
                f"$orderby=name asc&$filter=componentstate eq 0 and _parentroleid_value eq null"
            ),
            (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid,"
                f"_parentroleid_value,_parentrootroleid_value&"
                f"$orderby=name asc&$filter=_parentroleid_value eq null"
            ),
            (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid,"
                f"_parentroleid_value,_parentrootroleid_value&"
                f"$orderby=name asc&$filter=componentstate eq 0"
            ),
            (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid,"
                f"_parentroleid_value,_parentrootroleid_value&$orderby=name asc"
            ),
        ]
        last_error: Optional[Exception] = None
        for start_url in urls:
            try:
                items = []
                url: Optional[str] = start_url
                while url:
                    resp = requests.get(url, headers=self._role_request_headers(), timeout=120)
                    if resp.status_code >= 400:
                        raise RuntimeError(f"GET failed: HTTP {resp.status_code}, {resp.text}")
                    body = resp.json()
                    for row in body.get("value", []):
                        role_id = str(row.get("roleid", "")).strip()
                        name = str(row.get("name", "")).strip()
                        if not role_id or not name:
                            continue
                        if not self._is_modifiable_root_role(role_id, row):
                            continue
                        items.append({"role_id": role_id, "name": name})
                    url = body.get("@odata.nextLink")
                break
            except Exception as exc:
                last_error = exc
                items = []
        if not items and last_error is not None:
            raise last_error
        dedup: Dict[str, Dict[str, str]] = {}
        for item in items:
            name_key = item["name"].strip().lower()
            if name_key not in dedup:
                dedup[name_key] = item
        result = list(dedup.values())
        result.sort(key=lambda x: x["name"].lower())
        return self.normalize_roles_for_permissions(result)

    def get_entity_schema_name(self, logical_name: str) -> str:
        ln = logical_name.strip()
        if not ln:
            raise RuntimeError("实体逻辑名不能为空")
        url = f"{self.api_base}/EntityDefinitions(LogicalName='{ln}')?$select=SchemaName"
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"无法获取实体 SchemaName: {logical_name}, {resp.status_code}, {resp.text}")
        schema_name = str(resp.json().get("SchemaName", "")).strip()
        if not schema_name:
            raise RuntimeError(f"无法获取实体 SchemaName: {logical_name}")
        return schema_name

    def get_entity_object_type_code(self, logical_name: str) -> Optional[int]:
        ln = logical_name.strip()
        if not ln:
            return None
        url = f"{self.api_base}/EntityDefinitions(LogicalName='{ln}')?$select=ObjectTypeCode"
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            return None
        value = resp.json().get("ObjectTypeCode")
        return int(value) if value is not None else None

    def _headers_with_language(self, language: str) -> Dict[str, str]:
        headers = dict(self.headers)
        headers["Accept-Language"] = language
        return headers

    def _fetch_entity_display_labels(self, logical_name: str) -> Tuple[str, str]:
        escaped = logical_name.replace("'", "''")
        url = f"{self.api_base}/EntityDefinitions(LogicalName='{escaped}')?$select=DisplayName"
        resp = requests.get(url, headers=self.headers, timeout=30)
        if resp.status_code >= 400:
            return "", ""
        return _parse_display_name_labels(resp.json().get("DisplayName"))

    def get_entity_info(self, logical_name: str) -> Dict[str, Any]:
        ln = logical_name.strip()
        if not ln:
            raise ValueError("实体逻辑名不能为空")
        escaped = ln.replace("'", "''")
        select = (
            "LogicalName,SchemaName,DisplayName,ObjectTypeCode,"
            "IsCustomEntity,IsActivity,PrimaryIdAttribute,PrimaryNameAttribute"
        )
        url = f"{self.api_base}/EntityDefinitions(LogicalName='{escaped}')?$select={select}"
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code == 404:
            raise RuntimeError(f"未找到数据表: {ln}")
        if resp.status_code >= 400:
            raise RuntimeError(f"查询数据表失败: HTTP {resp.status_code}, {resp.text}")
        row = resp.json()
        display_name_zh, display_name_en = _parse_display_name_labels(row.get("DisplayName"))
        if not display_name_zh or not display_name_en:
            extra_zh, extra_en = self._fetch_entity_display_labels(ln)
            display_name_zh = display_name_zh or extra_zh
            display_name_en = display_name_en or extra_en
        if not display_name_en:
            display_name_en = _format_localized_label(row.get("DisplayName"))
        if display_name_zh == display_name_en and not _has_chinese_char(display_name_zh):
            display_name_zh = ""
        return {
            "logical_name": str(row.get("LogicalName", ln)),
            "schema_name": str(row.get("SchemaName", "")),
            "display_name_zh": display_name_zh,
            "display_name_en": display_name_en,
            "display_name": _join_display_names(display_name_zh, display_name_en),
            "object_type_code": row.get("ObjectTypeCode"),
            "is_custom_entity": bool(row.get("IsCustomEntity")),
            "is_activity": bool(row.get("IsActivity")),
            "primary_id_attribute": str(row.get("PrimaryIdAttribute", "")),
            "primary_name_attribute": str(row.get("PrimaryNameAttribute", "")),
        }

    def list_entity_attributes(self, logical_name: str) -> List[Dict[str, Any]]:
        ln = logical_name.strip()
        if not ln:
            raise ValueError("实体逻辑名不能为空")
        escaped = ln.replace("'", "''")
        base_select_fields = (
            "LogicalName,SchemaName,AttributeType,"
            "RequiredLevel,IsCustomAttribute,"
            "IsValidForCreate,IsValidForUpdate,IsValidForRead"
        )
        select_attempts = [base_select_fields]
        last_error = ""
        for select_fields in select_attempts:
            url = (
                f"{self.api_base}/EntityDefinitions(LogicalName='{escaped}')/Attributes"
                f"?$select={select_fields}&$orderby=LogicalName asc"
            )
            attributes: List[Dict[str, Any]] = []
            attribute_names: List[str] = []
            try:
                while url:
                    resp = requests.get(url, headers=self.headers, timeout=120)
                    if resp.status_code == 404:
                        raise RuntimeError(f"未找到数据表: {ln}")
                    if resp.status_code >= 400:
                        raise RuntimeError(f"查询字段失败: HTTP {resp.status_code}, {resp.text}")
                    body = resp.json()
                    for row in body.get("value", []):
                        logical = str(row.get("LogicalName", ""))
                        attribute_names.append(logical)
                        attributes.append(
                            {
                                "entity_logical_name": ln,
                                "logical_name": logical,
                                "schema_name": str(row.get("SchemaName", "")),
                                "display_name_zh": "",
                                "display_name_en": "",
                                            "attribute_type": _format_attribute_type_label(row.get("AttributeType")),
                                "required_level": _format_required_level(row.get("RequiredLevel")),
                                "is_custom": bool(row.get("IsCustomAttribute")),
                                "valid_for_create": bool(row.get("IsValidForCreate")),
                                "valid_for_update": bool(row.get("IsValidForUpdate")),
                                "valid_for_read": bool(row.get("IsValidForRead")),
                            }
                        )
                    url = body.get("@odata.nextLink")
                break
            except RuntimeError as exc:
                last_error = str(exc)
                if select_fields != base_select_fields and "HTTP 400" in last_error:
                    continue
                raise
        else:
            raise RuntimeError(last_error or "查询字段失败")

        label_map = self._fetch_attribute_bilingual_labels(ln, attribute_names)
        for attr in attributes:
            logical = attr["logical_name"]
            display_name_zh, display_name_en = label_map.get(logical, ("", ""))
            if display_name_zh == display_name_en and not _has_chinese_char(display_name_zh):
                display_name_zh = ""
            attr["display_name_zh"] = display_name_zh
            attr["display_name_en"] = display_name_en
            attr["display_name"] = _join_display_names(display_name_zh, display_name_en)
        return attributes

    def get_entity_privilege_map(self, logical_name: str) -> Dict[str, str]:
        schema_name = self.get_entity_schema_name(logical_name)
        names = [f"prv{verb}{schema_name}" for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS]
        name_filters = " or ".join(
            [f"name eq '{name.replace(chr(39), chr(39) * 2)}'" for name in names]
        )
        url = f"{self.api_base}/privileges?$select=privilegeid,name&$filter={name_filters}"
        resp = self._api_get(url)
        result: Dict[str, str] = {}
        for row in resp.json().get("value", []):
            name = str(row.get("name", ""))
            pid = row.get("privilegeid", "")
            if not name or not pid:
                continue
            for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS:
                if name == f"prv{verb}{schema_name}":
                    result[verb] = pid
                    break
        if not result:
            raise RuntimeError(f"未找到实体 [{logical_name}] 的权限定义，请确认实体已发布。")
        return result

    def get_entity_privilege_ids(self, logical_name: str, access_rights: List[int]) -> List[str]:
        del access_rights
        privilege_map = self.get_entity_privilege_map(logical_name)
        return list(privilege_map.values())

    def _query_role_privileges_for_entity(
        self, privilege_map: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        privilege_ids = list(privilege_map.values())
        if not privilege_ids:
            return []
        pid_filter = " or ".join([f"privilegeid eq {pid}" for pid in privilege_ids])
        rows: List[Dict[str, Any]] = []
        url: Optional[str] = (
            f"{self.api_base}/roleprivilegescollection?$select=roleid,privilegeid,privilegedepthmask&"
            f"$filter={pid_filter}"
        )
        while url:
            resp = self._api_get(url, timeout=120)
            body = resp.json()
            rows.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
        return rows

    def get_entity_privilege_ids_by_verbs(self, logical_name: str, verbs: List[str]) -> List[str]:
        privilege_map = self.get_entity_privilege_map(logical_name)
        return [privilege_map[verb] for verb in verbs if verb in privilege_map]

    def retrieve_role_privileges(self, role_id: str) -> Dict[str, int]:
        rid = role_id.strip().strip("{}")
        url = f"{self.api_base}/roles({rid})/Microsoft.Dynamics.CRM.RetrieveRolePrivilegesRole"
        resp = self._api_post(url, {})
        result: Dict[str, int] = {}
        for row in resp.json().get("RolePrivileges", []):
            pid = str(row.get("PrivilegeId", "")).lower()
            depth = _parse_privilege_depth_value(row.get("Depth"))
            if pid and depth is not None:
                result[pid] = depth
        return result

    def get_role_privilege_ids(self, role_id: str) -> set:
        rid = role_id.strip().strip("{}")
        url = (
            f"{self.api_base}/roles({rid})/roleprivileges_association?"
            f"$select=privilegeid"
        )
        resp = self._api_get(url)
        return {str(r.get("privilegeid", "")).lower() for r in resp.json().get("value", []) if r.get("privilegeid")}

    def remove_role_privileges(self, role_id: str, privilege_ids: List[str]) -> None:
        if not privilege_ids:
            return
        rid = role_id.strip().strip("{}")
        action_url = f"{self.api_base}/roles({rid})/Microsoft.Dynamics.CRM.RemovePrivilegeRole"
        seen: set = set()
        for pid in privilege_ids:
            key = str(pid).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            pid_clean = str(pid).strip().strip("{}")
            payload = {
                "Privilege": {
                    "privilegeid": pid_clean,
                    "@odata.type": "Microsoft.Dynamics.CRM.privilege",
                }
            }
            try:
                self._api_post(action_url, payload)
            except RuntimeError as exc:
                disassoc_url = (
                    f"{self.api_base}/roles({rid})/roleprivileges_association({pid_clean})/$ref"
                )
                try:
                    self._api_delete(disassoc_url)
                except RuntimeError:
                    raise exc

    def _batch_get_role_names(self, role_ids: List[str]) -> Dict[str, str]:
        details = self._batch_get_role_details(role_ids)
        return {role_id: info["name"] for role_id, info in details.items()}

    def _batch_get_role_details(self, role_ids: List[str]) -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        unique_ids: List[str] = []
        seen: set = set()
        for role_id in role_ids:
            rid = role_id.strip().strip("{}")
            key = rid.lower()
            if rid and key not in seen:
                seen.add(key)
                unique_ids.append(rid)
        for start in range(0, len(unique_ids), 40):
            chunk = unique_ids[start : start + 40]
            role_filter = " or ".join([f"roleid eq {rid}" for rid in chunk])
            url = (
                f"{self.api_base}/roles?$select=roleid,name,parentroleid,parentrootroleid&$filter={role_filter}"
            )
            resp = self._api_get(url, timeout=120)
            for row in resp.json().get("value", []):
                rid = str(row.get("roleid", "")).strip()
                name = str(row.get("name", "")).strip()
                parent_root = self._get_parent_root_role_id(row) or rid
                if rid:
                    result[rid] = {
                        "name": name,
                        "parent_root_role_id": parent_root,
                    }
        return result

    def _get_role_name(self, role_id: str, cache: Optional[Dict[str, str]] = None) -> str:
        rid = role_id.strip().strip("{}")
        if cache is not None and rid in cache:
            return cache[rid]
        url = f"{self.api_base}/roles({rid})?$select=name"
        resp = requests.get(url, headers=self.headers, timeout=60)
        name = ""
        if resp.status_code < 400:
            name = str(resp.json().get("name", "")).strip()
        if cache is not None:
            cache[rid] = name
        return name

    def load_entity_permission_matrix(
        self,
        entity_logical_name: str,
        roles: Optional[List[Dict[str, str]]] = None,
        progress_callback: Optional[Any] = None,
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, Optional[int]]]]:
        privilege_map = self.get_entity_privilege_map(entity_logical_name)
        if roles is None:
            roles = self.list_all_security_roles()
        name_to_role_id = {role["name"].strip().lower(): role["role_id"] for role in roles}
        pid_to_verb = {pid.lower(): verb for verb, pid in privilege_map.items()}
        matrix: Dict[str, Dict[str, Optional[int]]] = {
            role["role_id"]: {verb: None for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS}
            for role in roles
        }
        if progress_callback:
            progress_callback(0, 1, "正在批量读取角色权限...")
        role_name_cache: Dict[str, str] = {role["role_id"]: role["name"] for role in roles}
        role_privilege_rows = self._query_role_privileges_for_entity(privilege_map)
        unknown_role_ids = [
            str(row.get("roleid", ""))
            for row in role_privilege_rows
            if str(row.get("roleid", "")) and str(row.get("roleid", "")) not in role_name_cache
        ]
        if unknown_role_ids:
            role_details = self._batch_get_role_details(unknown_role_ids)
            for rid, info in role_details.items():
                role_name_cache[rid] = info["name"]
        root_name_to_role_id = name_to_role_id
        for row in role_privilege_rows:
            source_role_id = str(row.get("roleid", ""))
            pid = str(row.get("privilegeid", "")).lower()
            verb = pid_to_verb.get(pid)
            if not source_role_id or not verb:
                continue
            role_name = role_name_cache.get(source_role_id, "")
            target_role_id = root_name_to_role_id.get(role_name.strip().lower())
            if not target_role_id:
                continue
            depth = _mask_to_privilege_depth(row.get("privilegedepthmask"))
            existing_depth = matrix[target_role_id].get(verb)
            if existing_depth is None or (depth is not None and depth > (existing_depth or -1)):
                matrix[target_role_id][verb] = depth
        if progress_callback:
            progress_callback(1, 1, "加载完成")
        return privilege_map, matrix

    def normalize_permission_matrix_role_ids(
        self,
        roles: List[Dict[str, str]],
        matrix: Dict[str, Dict[str, Optional[int]]],
    ) -> Dict[str, Dict[str, Optional[int]]]:
        """将矩阵键对齐到当前角色列表（仅内存映射，不请求 API）。"""
        valid_ids = {role["role_id"].lower(): role["role_id"] for role in roles}
        normalized: Dict[str, Dict[str, Optional[int]]] = {
            role["role_id"]: {verb: None for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS}
            for role in roles
        }
        for role_id, row in matrix.items():
            target_id = valid_ids.get(role_id.lower())
            if not target_id:
                continue
            for verb, depth in row.items():
                existing = normalized[target_id].get(verb)
                if existing is None or (depth is not None and depth > (existing or -1)):
                    normalized[target_id][verb] = depth
        return normalized

    def save_entity_permission_changes(
        self,
        privilege_map: Dict[str, str],
        original_matrix: Dict[str, Dict[str, Optional[int]]],
        current_matrix: Dict[str, Dict[str, Optional[int]]],
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, int]:
        summary = {"roles_updated": 0, "privileges_changed": 0, "roles_skipped": 0}
        modifiable_cache: Dict[str, str] = {}
        pending_changes: Dict[str, Dict[str, Any]] = {}

        for role_id, current_row in current_matrix.items():
            original_row = original_matrix.get(role_id, {})
            row_changes: Dict[str, Optional[int]] = {}
            for verb in privilege_map:
                old_depth = original_row.get(verb)
                new_depth = current_row.get(verb)
                if old_depth != new_depth:
                    row_changes[verb] = new_depth
            if not row_changes:
                continue
            try:
                modifiable_role_id = self.resolve_modifiable_role_id(role_id, modifiable_cache)
                verify_body = self._get_role_record(modifiable_role_id)
                if not self._is_modifiable_root_role(modifiable_role_id, verify_body):
                    parent_root = self._get_parent_root_role_id(verify_body)
                    role_name = str(verify_body.get("name", "")).strip()
                    fallback = parent_root if parent_root else self._find_root_role_id_by_name(role_name)
                    if fallback and fallback.lower() != modifiable_role_id.lower():
                        modifiable_role_id = fallback
                    else:
                        summary["roles_skipped"] += 1
                        continue
            except RuntimeError:
                summary["roles_skipped"] += 1
                continue
            mod_key = modifiable_role_id.strip().strip("{}").lower()
            bucket = pending_changes.setdefault(
                mod_key,
                {"role_id": modifiable_role_id, "verbs": {}, "original": {}},
            )
            for verb, new_depth in row_changes.items():
                bucket["verbs"][verb] = new_depth
                bucket["original"][verb] = original_row.get(verb)

        role_items = list(pending_changes.values())
        total = len(role_items)
        for index, item in enumerate(role_items, start=1):
            modifiable_role_id = item["role_id"]
            verb_changes: Dict[str, Optional[int]] = item["verbs"]
            original_row = item.get("original", {})
            to_remove: List[str] = []
            to_add: List[Dict[str, Any]] = []
            for verb, new_depth in verb_changes.items():
                pid = privilege_map.get(verb)
                if not pid:
                    continue
                old_depth = original_row.get(verb)
                if old_depth is not None:
                    to_remove.append(pid)
                if new_depth is not None:
                    to_add.append(
                        {
                            "@odata.type": "Microsoft.Dynamics.CRM.RolePrivilege",
                            "PrivilegeId": pid,
                            "Depth": _privilege_depth_name(new_depth),
                        }
                    )
            if not to_remove and not to_add:
                continue
            rid = modifiable_role_id.strip().strip("{}")
            if to_remove:
                self.remove_role_privileges(modifiable_role_id, to_remove)
            if to_add:
                url = f"{self.api_base}/roles({rid})/Microsoft.Dynamics.CRM.AddPrivilegesRole"
                self._api_post(url, {"Privileges": to_add})
            summary["roles_updated"] += 1
            summary["privileges_changed"] += len(to_remove) + len(to_add)
            if progress_callback:
                progress_callback(index, total)
        return summary

    def add_role_privileges(self, role_id: str, privilege_ids: List[str], depth: str = "Local") -> int:
        if not privilege_ids:
            return 0
        rid = role_id.strip().strip("{}")
        existing = self.get_role_privilege_ids(role_id)
        to_add = [pid for pid in privilege_ids if pid.lower() not in existing]
        if not to_add:
            return 0
        privileges = [
            {
                "@odata.type": "Microsoft.Dynamics.CRM.RolePrivilege",
                "PrivilegeId": pid,
                "Depth": depth,
            }
            for pid in to_add
        ]
        url = f"{self.api_base}/roles({rid})/Microsoft.Dynamics.CRM.AddPrivilegesRole"
        self._api_post(url, {"Privileges": privileges})
        return len(to_add)

    def assign_entity_permissions(
        self,
        role_id: str,
        entity_logical_names: List[str],
        permission_labels: List[str],
        depth: str = "Local",
    ) -> Dict[str, int]:
        verbs: List[str] = []
        for label in permission_labels:
            verb = PRIVILEGE_VERB_BY_LABEL.get(label)
            if verb:
                verbs.append(verb)
            elif label in PRIVILEGE_VERB_BY_LABEL.values():
                verbs.append(label)
        if "Append" in verbs and "AppendTo" not in verbs:
            verbs.append("AppendTo")
        summary = {"entities": 0, "privileges_added": 0, "skipped_entities": 0}
        for entity_name in entity_logical_names:
            try:
                privilege_ids = self.get_entity_privilege_ids_by_verbs(entity_name, verbs)
            except RuntimeError:
                summary["skipped_entities"] += 1
                continue
            if not privilege_ids:
                summary["skipped_entities"] += 1
                continue
            added = self.add_role_privileges(role_id=role_id, privilege_ids=privilege_ids, depth=depth)
            summary["entities"] += 1
            summary["privileges_added"] += added
        return summary

    def export_solution(self, solution_unique_name: str, managed: bool = False) -> bytes:
        payload = {
            "SolutionName": solution_unique_name,
            "Managed": bool(managed),
        }
        url = f"{self.api_base}/ExportSolution"
        resp = self._api_post(url, payload, timeout=None)
        body = resp.json()
        file_b64 = body.get("ExportSolutionFile", "")
        if not file_b64:
            raise RuntimeError("导出解决方案失败：响应中无 ExportSolutionFile")
        return base64.b64decode(file_b64)

    def import_solution(
        self,
        solution_bytes: bytes,
        overwrite_unmanaged: bool = True,
        publish_workflows: bool = True,
        progress_callback: Optional[Any] = None,
    ) -> str:
        import_job_id = str(uuid.uuid4())
        payload = {
            "ImportJobId": import_job_id,
            "CustomizationFile": base64.b64encode(solution_bytes).decode("ascii"),
            "OverwriteUnmanagedCustomizations": bool(overwrite_unmanaged),
            "PublishWorkflows": bool(publish_workflows),
        }
        url = f"{self.api_base}/ImportSolution"
        stop_poll = threading.Event()

        def poll_import_progress() -> None:
            job_id = import_job_id.strip().strip("{}")
            while not stop_poll.is_set():
                try:
                    poll_url = (
                        f"{self.api_base}/importjobs({job_id})?"
                        f"$select=progress,completedon,data"
                    )
                    resp = requests.get(poll_url, headers=self.headers, timeout=30)
                    if resp.status_code >= 400:
                        time.sleep(3)
                        continue
                    body = resp.json()
                    progress = body.get("progress")
                    if progress_callback is not None and progress is not None:
                        pct = max(0.0, min(100.0, float(progress)))
                        progress_callback(pct, f"正在导入解决方案... {int(pct)}%")
                    if body.get("completedon"):
                        data = str(body.get("data", ""))
                        if 'result="failure"' in data or "result='failure'" in data:
                            stop_poll.set()
                            return
                        if progress_callback is not None:
                            progress_callback(100.0, "导入完成")
                        stop_poll.set()
                        return
                except Exception:
                    pass
                time.sleep(3)

        if progress_callback:
            progress_callback(0.0, "正在导入解决方案（服务端处理中，可能需要较长时间）...")
        poll_thread = threading.Thread(target=poll_import_progress, daemon=True)
        poll_thread.start()
        try:
            # ImportSolution 为同步操作：此 POST 会阻塞直到服务端导入完成，不设客户端超时。
            self._api_post(url, payload, timeout=None)
        finally:
            stop_poll.set()
            poll_thread.join(timeout=5)
        if progress_callback:
            progress_callback(100.0, "导入完成")
        return import_job_id

    def publish_all_customizations(self) -> None:
        url = f"{self.api_base}/PublishAllXml"
        self._api_post(url, {}, timeout=None)

    def list_global_option_sets(self) -> List[str]:
        url = f"{self.api_base}/GlobalOptionSetDefinitions?$select=Name"
        resp = requests.get(url, headers=self.headers, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"Load global option sets failed: {resp.status_code}, {resp.text}")
        rows = resp.json().get("value", [])
        names = sorted({str(r.get("Name", "")).strip() for r in rows if r.get("Name")})
        return names

    def _exists(self, entity_name: str, logical_name: str) -> bool:
        url = (
            f"{self.api_base}/EntityDefinitions(LogicalName='{entity_name}')/Attributes"
            f"(LogicalName='{logical_name}')?$select=LogicalName"
        )
        resp = requests.get(url, headers=self.headers, timeout=30)
        return resp.status_code == 200

    def _validate_schema(self, schema: Dict[str, Any]) -> None:
        entity = schema.get("entity_logical_name", "")
        prefix = schema.get("publisher_prefix", "")
        fields = schema.get("fields", [])
        if not LOGICAL_NAME_RE.match(entity):
            raise ValueError("Invalid entity_logical_name")
        if not schema.get("solution_unique_name"):
            raise ValueError("solution_unique_name is required")
        if not fields:
            raise ValueError("fields cannot be empty")

        names = set()
        for f in fields:
            ln = f.get("logical_name", "")
            sn = f.get("schema_name", "")
            if not LOGICAL_NAME_RE.match(ln):
                raise ValueError(f"Invalid logical_name: {ln}")
            if prefix and not ln.startswith(prefix + "_"):
                raise ValueError(f"Field {ln} must start with prefix {prefix}_")
            if not SCHEMA_NAME_RE.match(sn):
                raise ValueError(f"Invalid schema_name: {sn}")
            if ln in names:
                raise ValueError(f"Duplicate logical_name: {ln}")
            names.add(ln)

    def _build_payload(self, field: Dict[str, Any]) -> Dict[str, Any]:
        ft = field["field_type"].lower()
        common = {
            "LogicalName": field["logical_name"],
            "SchemaName": field["schema_name"],
            "DisplayName": {"LocalizedLabels": [{"Label": field["display_name"], "LanguageCode": 2052}]},
            "Description": {"LocalizedLabels": [{"Label": field.get("description", ""), "LanguageCode": 2052}]},
            "RequiredLevel": {"Value": field.get("required_level", "None")},
            "IsAuditEnabled": {"Value": bool(field.get("is_audit_enabled", False))},
            "IsValidForAdvancedFind": {"Value": bool(field.get("searchable", True))},
        }

        if ft == "string":
            cfg = field.get("string", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
                    "MaxLength": int(cfg.get("max_length", 100)),
                    "FormatName": {"Value": cfg.get("format", "Text")},
                }
            )
        elif ft == "memo":
            cfg = field.get("memo", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.MemoAttributeMetadata",
                    "MaxLength": int(cfg.get("max_length", 2000)),
                }
            )
        elif ft == "integer":
            cfg = field.get("integer", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata",
                    "MinValue": int(cfg.get("min_value", -2147483648)),
                    "MaxValue": int(cfg.get("max_value", 2147483647)),
                }
            )
        elif ft == "decimal":
            cfg = field.get("decimal", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata",
                    "MinValue": float(cfg.get("min_value", -100000000000)),
                    "MaxValue": float(cfg.get("max_value", 100000000000)),
                    "Precision": int(cfg.get("precision", 2)),
                }
            )
        elif ft == "double":
            cfg = field.get("double", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.DoubleAttributeMetadata",
                    "MinValue": float(cfg.get("min_value", -100000000000)),
                    "MaxValue": float(cfg.get("max_value", 100000000000)),
                    "Precision": int(cfg.get("precision", 2)),
                }
            )
        elif ft == "money":
            cfg = field.get("money", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.MoneyAttributeMetadata",
                    "MinValue": float(cfg.get("min_value", -922337203685477)),
                    "MaxValue": float(cfg.get("max_value", 922337203685477)),
                    "Precision": int(cfg.get("precision", 2)),
                }
            )
        elif ft == "datetime":
            cfg = field.get("datetime", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
                    "Format": cfg.get("format", "DateOnly"),
                    "DateTimeBehavior": {"Value": cfg.get("behavior", "UserLocal")},
                    "ImeMode": cfg.get("ime_mode", "Auto"),
                }
            )
        elif ft == "boolean":
            cfg = field.get("boolean", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.BooleanAttributeMetadata",
                    "OptionSet": {
                        "FalseOption": {
                            "Value": 0,
                            "Label": {"LocalizedLabels": [{"Label": cfg.get("false_label", "No"), "LanguageCode": 2052}]},
                        },
                        "TrueOption": {
                            "Value": 1,
                            "Label": {"LocalizedLabels": [{"Label": cfg.get("true_label", "Yes"), "LanguageCode": 2052}]},
                        },
                    },
                    "DefaultValue": bool(cfg.get("default_value", False)),
                }
            )
        elif ft == "picklist":
            cfg = field.get("picklist", {})
            picklist_payload: Dict[str, Any] = {
                "@odata.type": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
            }
            if cfg.get("global_option_set_name"):
                global_name = str(cfg["global_option_set_name"]).strip()
                picklist_payload["OptionSet"] = {
                    "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
                    "IsGlobal": True,
                    "Name": global_name,
                }
            else:
                options = []
                for opt in cfg.get("options", []):
                    options.append(
                        {
                            "Value": int(opt["value"]),
                            "Label": {"LocalizedLabels": [{"Label": opt["label"], "LanguageCode": 2052}]},
                        }
                    )
                if not options:
                    raise ValueError("picklist requires local options or global option set name")
                picklist_payload["OptionSet"] = {
                    "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
                    "OptionSetType": "Picklist",
                    "IsGlobal": False,
                    "Options": options,
                }
            if cfg.get("default_value") is not None:
                picklist_payload["DefaultFormValue"] = cfg.get("default_value")
            common.update(picklist_payload)
        elif ft == "lookup":
            cfg = field.get("lookup", {})
            target_entity = cfg.get("target_entity", "").strip()
            if not target_entity:
                raise ValueError("lookup target_entity is required")
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
                    "Targets": [target_entity],
                }
            )
            relationship_schema_name = cfg.get("relationship_schema_name", "").strip()
            if relationship_schema_name:
                common["RelationshipSchemaName"] = relationship_schema_name
        elif ft == "file":
            cfg = field.get("file", {})
            common.update(
                {
                    "@odata.type": "Microsoft.Dynamics.CRM.FileAttributeMetadata",
                    "MaxSizeInKB": int(cfg.get("max_size_kb", 32768)),
                }
            )
        else:
            raise ValueError(f"Unsupported field_type: {ft}")

        return common


def load_config(config_path: str) -> Dict[str, Any]:
    cfg = dict(EMBEDDED_DEFAULT_CONFIG)
    path = Path(config_path)
    if not path.exists():
        return cfg
    with open(path, "r", encoding="utf-8") as f:
        file_cfg = json.load(f)
    cfg.update(file_cfg)
    return cfg


def load_activation_secret(config_path: str = "") -> str:
    env_secret = os.getenv("D365_ACTIVATION_SECRET", "").strip()
    if env_secret:
        return env_secret
    if config_path:
        return str(load_config(config_path).get("activation_secret", "")).strip()
    return ""


def load_environments(config_path: str) -> List[Dict[str, str]]:
    cfg = load_config(config_path)
    tenant_id = str(cfg.get("tenant_id", "")).strip()
    environments: List[Dict[str, str]] = []
    seen_urls: set = set()

    def add_env(name: str, org_url: str, client_id: str, client_secret: str) -> None:
        org = org_url.strip().rstrip("/")
        cid = client_id.strip()
        secret = client_secret.strip()
        if not name or not org or not cid or not secret:
            return
        key = org.lower()
        if key in seen_urls:
            return
        seen_urls.add(key)
        environments.append(
            {
                "name": name,
                "org_url": org,
                "client_id": cid,
                "client_secret": secret,
                "tenant_id": tenant_id,
            }
        )

    current_name = cfg.get("environment_name", "当前环境")
    add_env(
        name=str(current_name),
        org_url=str(cfg.get("org_url", "")),
        client_id=str(cfg.get("client_id", "")),
        client_secret=str(cfg.get("client_secret", "")),
    )
    for item in cfg.get("environments", []):
        if not isinstance(item, dict):
            continue
        add_env(
            name=str(item.get("name", "")),
            org_url=str(item.get("org_url", item.get("D365Url", ""))),
            client_id=str(item.get("client_id", item.get("D365AppID", ""))),
            client_secret=str(item.get("client_secret", item.get("D365AppSecret", ""))),
        )
    return environments


def create_creator_from_environment(env: Dict[str, str], fallback_tenant_id: str = "") -> "D365FieldCreator":
    tenant_id = str(env.get("tenant_id", fallback_tenant_id)).strip()
    if not tenant_id:
        raise ValueError("tenant_id 未配置")
    auth = D365Auth(
        tenant_id=tenant_id,
        client_id=str(env["client_id"]).strip(),
        client_secret=str(env["client_secret"]).strip(),
        org_url=str(env["org_url"]).strip(),
    )
    return D365FieldCreator(auth=auth, schema_file="")


def _parse_privilege_depth_value(depth: Any) -> Optional[int]:
    if depth is None:
        return None
    if isinstance(depth, int):
        return depth if 0 <= depth <= 3 else None
    if isinstance(depth, str):
        mapping = {"Basic": 0, "Local": 1, "Deep": 2, "Global": 3}
        return mapping.get(depth)
    return None


def _draw_privilege_depth_icon_at(
    canvas: tk.Canvas, x: int, y: int, size: int, depth: Optional[int]
) -> None:
    pad = 2
    x1, y1 = x + pad, y + pad
    x2, y2 = x + size - pad, y + size - pad
    canvas.create_oval(x1, y1, x2, y2, outline="#666666", width=1, fill="#ffffff")
    if depth is None:
        return
    fill_color = "#2f6fed"
    arcs = [(270, 90), (180, 90), (90, 90), (0, 90)]
    quarter_count = min(depth + 1, 4)
    for i in range(quarter_count):
        start, extent = arcs[i]
        canvas.create_arc(x1, y1, x2, y2, start=start, extent=extent, fill=fill_color, outline="")


def _draw_privilege_depth_icon(canvas: tk.Canvas, size: int, depth: Optional[int]) -> None:
    canvas.delete("all")
    _draw_privilege_depth_icon_at(canvas, 0, 0, size, depth)


def _paint_permission_row_canvas(
    row_canvas: tk.Canvas,
    row_data: Dict[str, Optional[int]],
    privilege_map: Dict[str, str],
    cell_size: int,
    cell_step: int,
) -> None:
    row_canvas.delete("all")
    for index, (_, verb) in enumerate(ENTITY_ACCESS_RIGHT_COLUMNS):
        x = index * cell_step + 6
        y = 2
        if verb not in privilege_map:
            x1, y1 = x + 2, y + 2
            x2, y2 = x + cell_size - 2, y + cell_size - 2
            row_canvas.create_oval(x1, y1, x2, y2, outline="#cccccc", width=1, fill="#f5f5f5")
            continue
        _draw_privilege_depth_icon_at(row_canvas, x, y, cell_size, row_data.get(verb))


def _mask_to_privilege_depth(mask: Any) -> Optional[int]:
    if mask is None:
        return None
    try:
        value = int(mask)
    except (TypeError, ValueError):
        return None
    if value & 8:
        return 3
    if value & 4:
        return 2
    if value & 2:
        return 1
    if value & 1:
        return 0
    return None


def _next_privilege_depth(depth: Optional[int]) -> Optional[int]:
    try:
        idx = PRIVILEGE_DEPTH_CYCLE.index(depth)
    except ValueError:
        idx = 0
    return PRIVILEGE_DEPTH_CYCLE[(idx + 1) % len(PRIVILEGE_DEPTH_CYCLE)]


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if isinstance(exc, requests.exceptions.RequestException):
        lower_message = message.lower()
        if (
            "unexpected_eof_while_reading" in lower_message
            or "connectionreseterror" in lower_message
            or "connection aborted" in lower_message
            or "max retries exceeded" in lower_message
        ):
            return (
                "D365 连接中断，接口还没返回结果时 HTTPS 连接被关闭。\n\n"
                "请先检查 VPN/代理/公司网络是否正常，确认当前环境地址可以在浏览器打开；"
                "如果网络刚切换过，稍等一会儿后重试。\n\n"
                f"原始错误: {message}"
            )
    if message and message.lower() != "none":
        return message
    if exc.args:
        return f"{type(exc).__name__}: {exc.args!r}"
    return type(exc).__name__


def _has_chinese_char(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _parse_display_name_labels(value: Any) -> Tuple[str, str]:
    zh, en = "", ""
    if not value:
        return zh, en
    if isinstance(value, str):
        return "", value
    if not isinstance(value, dict):
        return zh, en

    for item in value.get("LocalizedLabels") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("Label", "")).strip()
        if not label:
            continue
        lcid = item.get("LanguageCode")
        if lcid in CHINESE_LANGUAGE_CODES and not zh:
            zh = label
        elif lcid == ENGLISH_LANGUAGE_CODE and not en:
            en = label

    user_label = value.get("UserLocalizedLabel")
    if isinstance(user_label, dict):
        label = str(user_label.get("Label", "")).strip()
        if label:
            lcid = user_label.get("LanguageCode")
            if lcid in CHINESE_LANGUAGE_CODES and not zh:
                zh = label
            elif lcid == ENGLISH_LANGUAGE_CODE and not en:
                en = label
            elif not en and lcid not in CHINESE_LANGUAGE_CODES:
                en = label
    return zh, en


def _join_display_names(zh: str, en: str) -> str:
    zh = (zh or "").strip()
    en = (en or "").strip()
    if zh and en:
        return f"{zh} / {en}" if zh != en else zh
    return zh or en


def _format_localized_label(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        user_label = value.get("UserLocalizedLabel")
        if isinstance(user_label, dict):
            label = str(user_label.get("Label", "")).strip()
            if label:
                return label
        labels = value.get("LocalizedLabels")
        if isinstance(labels, list):
            for item in labels:
                if isinstance(item, dict):
                    label = str(item.get("Label", "")).strip()
                    if label:
                        return label
    return ""


def _format_required_level(value: Any) -> str:
    if not value:
        return "可选"
    if isinstance(value, dict):
        raw = value.get("Value", "")
    else:
        raw = str(value)
    mapping = {v: k for k, v in REQUIRED_LEVEL_LABEL_TO_VALUE.items()}
    return mapping.get(str(raw), str(raw) or "可选")


def _format_attribute_type_label(value: Any) -> str:
    if not value:
        return ""
    raw = str(value)
    label = FIELD_TYPE_VALUE_TO_LABEL.get(raw.lower())
    return f"{label} ({raw})" if label else raw


def _bool_label(value: bool) -> str:
    return "是" if value else "否"



def _privilege_depth_name(depth: Optional[int]) -> str:
    if depth is None or depth < 0 or depth >= len(PRIVILEGE_DEPTH_NAMES):
        raise ValueError(f"无效的权限深度: {depth}")
    return PRIVILEGE_DEPTH_NAMES[depth]


def _get_trial_state_paths() -> List[Path]:
    appdata_dir = Path(os.getenv("APPDATA", str(Path.home()))) / "D365Tool"
    return [
        Path.home() / ".d365tool-trial-state.json",
        appdata_dir / "trial-state.json",
    ]


def _write_json_with_parents(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_trial_signature(machine_code: str, trial_started_at_utc: str) -> str:
    if not ACTIVATION_SECRET:
        raise RuntimeError("activation_secret 未配置，请在 config.json 中设置或配置 D365_ACTIVATION_SECRET 环境变量。")
    msg = f"{machine_code}|{trial_started_at_utc}"
    return hmac.new(
        ACTIVATION_SECRET.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _load_trial_state(machine_code: str) -> Optional[Dict[str, Any]]:
    for path in _get_trial_state_paths():
        if not path.exists():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        state_machine_code = str(state.get("machine_code", "")).strip().upper()
        trial_started_at_utc = str(state.get("trial_started_at_utc", "")).strip()
        signature = str(state.get("signature", "")).strip().lower()
        expected = _build_trial_signature(machine_code, trial_started_at_utc)
        if state_machine_code == machine_code and signature == expected:
            return state
    return None


def _get_or_create_trial_state(machine_code: str) -> Dict[str, Any]:
    current_state = _load_trial_state(machine_code)
    if current_state:
        return current_state
    trial_started_at_utc = datetime.now(timezone.utc).isoformat()
    state = {
        "machine_code": machine_code,
        "trial_started_at_utc": trial_started_at_utc,
        "trial_days": TRIAL_DAYS,
        "signature": _build_trial_signature(machine_code, trial_started_at_utc),
    }
    for path in _get_trial_state_paths():
        _write_json_with_parents(path, state)
    return state


def _get_trial_remaining_days(machine_code: str) -> int:
    state = _get_or_create_trial_state(machine_code)
    started_raw = str(state.get("trial_started_at_utc", "")).strip()
    try:
        started_at = datetime.fromisoformat(started_raw)
    except ValueError:
        started_at = datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    expire_at = started_at + timedelta(days=TRIAL_DAYS)
    remaining_seconds = (expire_at - datetime.now(timezone.utc)).total_seconds()
    if remaining_seconds <= 0:
        return 0
    return int((remaining_seconds + 86399) // 86400)


def is_permanently_unlocked(machine_code: str) -> bool:
    if not PAYMENT_UNLOCK_FILE.exists():
        return False
    try:
        receipt = json.loads(PAYMENT_UNLOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    unlocked_machine_code = str(receipt.get("machine_code", "")).strip().upper()
    return bool(receipt.get("paid")) and bool(receipt.get("activated")) and unlocked_machine_code == machine_code


def save_permanent_unlock(machine_code: str) -> None:
    receipt = {
        "paid": True,
        "activated": True,
        "amount": PAYMENT_REQUIRED_AMOUNT,
        "machine_code": machine_code,
        "confirmed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    PAYMENT_UNLOCK_FILE.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")


def get_machine_code() -> str:
    raw = f"{platform.system()}|{platform.node()}|{platform.machine()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return digest[:24]


def generate_activation_code(machine_code: str) -> str:
    if not ACTIVATION_SECRET:
        raise RuntimeError("activation_secret 未配置，请在 config.json 中设置或配置 D365_ACTIVATION_SECRET 环境变量。")
    sig = hmac.new(
        ACTIVATION_SECRET.encode("utf-8"),
        machine_code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return f"D365-{sig[:8]}-{sig[8:16]}-{sig[16:24]}"


def verify_activation_code(machine_code: str, activation_code: str) -> bool:
    expected = generate_activation_code(machine_code)
    normalized_input = activation_code.strip().upper()
    return hmac.compare_digest(expected, normalized_input)


def ensure_payment_access(root: tk.Tk) -> bool:
    machine_code = get_machine_code()
    if is_permanently_unlocked(machine_code):
        return True
    if _get_trial_remaining_days(machine_code) > 0:
        return True

    dlg = tk.Toplevel(root)
    dlg.title("试用已到期 - 付费解锁")
    dlg.geometry("760x620")
    dlg.minsize(720, 580)
    dlg.resizable(True, True)
    dlg.transient(root)
    dlg.grab_set()
    dlg.protocol("WM_DELETE_WINDOW", lambda: None)

    ttk.Label(dlg, text="1天试用已到期，请付费激活后继续使用", font=("", 13, "bold")).pack(pady=(18, 8))
    ttk.Label(dlg, text=f"解锁金额：{PAYMENT_REQUIRED_AMOUNT} 元", font=("", 11)).pack(pady=2)
    ttk.Label(dlg, text="请扫码付款后，添加微信获取激活码。", font=("", 10)).pack(pady=(10, 4))
    ttk.Label(dlg, text="微信号：lilf0117", font=("", 11, "bold"), foreground="#0a7f2e").pack(pady=(0, 6))
    ttk.Label(
        dlg,
        text=(
            "激活步骤：\n"
            "1. 点击“打开收款码照片”并完成转账\n"
            "2. 添加微信 lilf0117，发送你的机器码\n"
            "3. 获取激活码后粘贴到下方输入框\n"
            "4. 点击“验证激活码并解锁”完成永久激活"
        ),
        justify="left",
        foreground="#444",
    ).pack(pady=(2, 4))

    def open_qr() -> None:
        webbrowser.open(PAYMENT_QR_IMAGE_URL, new=2)

    ttk.Label(dlg, text="你的机器码（发给微信获取激活码）：", font=("", 10)).pack(pady=(8, 2))
    machine_code_var = tk.StringVar(value=machine_code)
    machine_code_entry = ttk.Entry(dlg, textvariable=machine_code_var, width=50, state="readonly")
    machine_code_entry.pack(pady=(0, 8))

    ttk.Label(dlg, text="激活码：", font=("", 10)).pack(pady=(4, 2))
    activation_code_var = tk.StringVar()
    activation_code_entry = ttk.Entry(dlg, textvariable=activation_code_var, width=50)
    activation_code_entry.pack(pady=(0, 8))

    def copy_machine_code() -> None:
        root.clipboard_clear()
        root.clipboard_append(machine_code)
        messagebox.showinfo("已复制", "机器码已复制到剪贴板。", parent=dlg)

    def verify_and_unlock() -> None:
        activation_code = activation_code_var.get().strip()
        if not activation_code:
            messagebox.showwarning("缺少激活码", "请先输入激活码。", parent=dlg)
            return
        if not verify_activation_code(machine_code, activation_code):
            messagebox.showwarning("未通过", "激活码无效，请核对后重试。", parent=dlg)
            return
        save_permanent_unlock(machine_code)
        messagebox.showinfo("已解锁", "激活成功，已永久解锁。", parent=dlg)
        dlg.destroy()

    def quit_app() -> None:
        dlg.destroy()
        root.destroy()

    button_wrap = ttk.Frame(dlg)
    button_wrap.pack(pady=(8, 4))
    ttk.Button(button_wrap, text="打开收款码照片", command=open_qr).grid(row=0, column=0, padx=6)
    ttk.Button(button_wrap, text="复制机器码", command=copy_machine_code).grid(row=0, column=1, padx=6)
    ttk.Button(button_wrap, text="验证激活码并解锁", command=verify_and_unlock).grid(row=0, column=2, padx=6)
    ttk.Button(button_wrap, text="退出", command=quit_app).grid(row=0, column=3, padx=6)
    ttk.Label(dlg, text=PAYMENT_QR_IMAGE_URL, foreground="#666").pack(padx=12, pady=(6, 0))
    ttk.Label(
        dlg,
        text="提示：付款后加微信 lilf0117，发送机器码获取激活码。",
        foreground="#666",
    ).pack(pady=(4, 0))

    activation_code_entry.focus_set()
    root.wait_window(dlg)
    return is_permanently_unlocked(machine_code)


class FieldCreatorGUI:
    def __init__(self, default_config_path: str) -> None:
        self.default_config_path = default_config_path
        global ACTIVATION_SECRET
        ACTIVATION_SECRET = load_activation_secret(default_config_path)
        self.root = tk.Tk()
        if not ensure_payment_access(self.root):
            raise SystemExit(0)
        self.root.title("D365 开发工具")
        self.root.geometry("1120x820")
        self.root.minsize(960, 640)
        self.panels: Dict[str, ttk.Frame] = {}
        self._active_panel = ""
        self.vars: Dict[str, tk.StringVar] = {}
        self.entity_items: List[Dict[str, str]] = []
        self.entity_display_map: Dict[str, str] = {}
        self.solution_items: List[Dict[str, str]] = []
        self.solution_id_map: Dict[str, str] = {}
        self.current_solution_id = ""
        self.global_option_set_names: List[str] = []
        self.searching_entities = False
        self.searching_solutions = False
        self.dynamic_widgets: Dict[str, List[tk.Widget]] = {"lookup": [], "picklist": [], "file": []}
        self._build_form()
        db_path = default_db_path(self.default_config_path)
        self.op_logger = OperationLogger(db_path)
        self._session_id = self.op_logger.start_session(
            config_path=self.default_config_path,
            org_url="",
            tenant_id="",
            client_id="",
            launch_mode="gui",
            details={"db_path": db_path},
        )
        self._load_defaults()
        self.op_logger.log(
            session_id=self._session_id,
            category="connection",
            action="load_config",
            status="success",
            summary=f"启动时已加载配置: {self.default_config_path}",
            details=sanitize_details(self._ctx()),
            org_url=self.vars["org_url"].get().strip(),
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._log_op(
            "system",
            "app_start",
            "success",
            "D365 开发工具已启动",
            details={"db_path": db_path, "config_path": self.default_config_path},
        )

    def _add_entry(
        self, parent: ttk.Frame, row: int, label: str, key: str, show: Optional[str] = None
    ) -> Any:
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", padx=6, pady=4)
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=64, show=show)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        self.vars[key] = var
        return label_widget, entry

    def _on_top_frame_configure(self, _event: Any) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: Any) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event: Any) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _to_snake_case(self, name: str) -> str:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
        s3 = re.sub(r"[^A-Za-z0-9_]+", "_", s2)
        return s3.strip("_").lower()

    def _normalize_schema_name(self, name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_]", "", name)
        if not clean:
            return "Field001"
        if not clean[0].isalpha():
            clean = "F" + clean
        return clean

    def _map_csharp_type(self, cs_type: str) -> Optional[str]:
        t = cs_type.strip()
        t = t.replace("?", "")
        if t in {"string"}:
            return "string"
        if t in {"int", "long", "short", "byte"}:
            return "integer"
        if t in {"decimal"}:
            return "decimal"
        if t in {"double", "float"}:
            return "double"
        if t in {"bool"}:
            return "boolean"
        if t in {"DateTime", "DateOnly"}:
            return "datetime"
        return None

    def _split_attr_args(self, raw: str) -> List[str]:
        items: List[str] = []
        current = []
        in_quote = False
        escape = False
        for ch in raw:
            if escape:
                current.append(ch)
                escape = False
                continue
            if ch == "\\":
                current.append(ch)
                escape = True
                continue
            if ch == '"':
                in_quote = not in_quote
                current.append(ch)
                continue
            if ch == "," and not in_quote:
                items.append("".join(current).strip())
                current = []
                continue
            current.append(ch)
        if current:
            items.append("".join(current).strip())
        return [x for x in items if x]

    def _parse_d365_field_attribute(self, arg_text: str) -> Dict[str, str]:
        # Supported style:
        # [D365Field("picklist", RequiredLevel="ApplicationRequired", PicklistOptions="100:A|101:B")]
        meta: Dict[str, str] = {}
        args = self._split_attr_args(arg_text)
        for i, arg in enumerate(args):
            if "=" in arg:
                k, v = arg.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                meta[k] = v
            elif i == 0:
                meta["FieldType"] = arg.strip().strip('"')
        return meta

    def _parse_cs_properties(self, content: str) -> List[Dict[str, str]]:
        prop_pattern = re.compile(
            r"public\s+(?:virtual\s+)?([A-Za-z_][A-Za-z0-9_<>,\.\?]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get\s*;\s*set\s*;\s*\}"
        )
        attr_pattern = re.compile(r"\[D365Field\((.*)\)\]")
        class_pattern = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b")
        rows: List[Dict[str, str]] = []
        pending_meta: Dict[str, str] = {}
        current_class = ""
        brace_depth = 0
        in_target_class = False
        in_block_comment = False
        for line in content.splitlines():
            raw = line
            line = line.strip()
            if not line:
                continue
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                continue
            if line.startswith("/*"):
                if "*/" not in line:
                    in_block_comment = True
                continue
            if line.startswith("//"):
                continue
            class_match = class_pattern.search(line)
            if class_match:
                current_class = class_match.group(1)
                in_target_class = current_class == "EntityFieldTemplate"
                pending_meta = {}
            attr_match = attr_pattern.search(line)
            if attr_match:
                pending_meta = self._parse_d365_field_attribute(attr_match.group(1))
                continue
            m = prop_pattern.search(line)
            if not m:
                brace_depth += raw.count("{")
                brace_depth -= raw.count("}")
                if brace_depth <= 0 and current_class:
                    current_class = ""
                    in_target_class = False
                continue
            if not in_target_class:
                pending_meta = {}
                continue
            cs_type = m.group(1).strip()
            prop_name = m.group(2).strip()
            mapped = self._map_csharp_type(cs_type)
            field_type = (pending_meta.get("FieldType") or mapped or "").strip().lower()
            # Import from template class only when D365Field attribute exists.
            if field_type and "FieldType" in pending_meta:
                row = {"cs_type": cs_type, "name": prop_name, "field_type": field_type}
                for k, v in pending_meta.items():
                    row[f"meta_{k}"] = v
                rows.append(row)
            pending_meta = {}
            brace_depth += raw.count("{")
            brace_depth -= raw.count("}")
        return rows

    def _ctx(self) -> Dict[str, str]:
        entity = self.vars.get("entity_logical_name", tk.StringVar()).get().strip()
        if "(" in entity:
            entity = entity.split("(", 1)[0].strip()
        return {
            "org_url": self.vars.get("org_url", tk.StringVar()).get().strip(),
            "solution_name": self.vars.get("solution_unique_name", tk.StringVar()).get().strip(),
            "entity_name": entity,
            "tenant_id": self.vars.get("tenant_id", tk.StringVar()).get().strip(),
            "client_id": self.vars.get("client_id", tk.StringVar()).get().strip(),
            "publisher_prefix": self.vars.get("publisher_prefix", tk.StringVar()).get().strip(),
        }

    def _log_op(
        self,
        category: str,
        action: str,
        status: str,
        summary: str,
        *,
        details: Any = None,
        target_org_url: str = "",
        environment_name: str = "",
        duration_ms: Optional[int] = None,
        error_message: str = "",
        solution_name: str = "",
        entity_name: str = "",
    ) -> None:
        if not hasattr(self, "op_logger"):
            return
        ctx = self._ctx()
        self.op_logger.log(
            session_id=self._session_id,
            category=category,
            action=action,
            status=status,
            summary=summary,
            details=details,
            org_url=ctx["org_url"],
            target_org_url=target_org_url,
            environment_name=environment_name,
            solution_name=solution_name or ctx["solution_name"],
            entity_name=entity_name or ctx["entity_name"],
            duration_ms=duration_ms,
            error_message=error_message,
        )

    def _on_window_close(self) -> None:
        if hasattr(self, "js_capture_panel"):
            self.js_capture_panel.stop_capture(terminate_browser=True)
        self._log_op("system", "app_close", "success", "用户关闭 D365 开发工具")
        if hasattr(self, "op_logger"):
            self.op_logger.end_session(self._session_id, status="closed")
        self.root.destroy()

    def _show_panel(self, panel_id: str) -> None:
        frame = self.panels.get(panel_id)
        if frame is None:
            return
        previous_panel = self._active_panel
        if previous_panel and panel_id != previous_panel:
            self._log_op(
                "navigation",
                "switch_panel",
                "info",
                f"切换到功能页: {PANEL_LABELS.get(panel_id, panel_id)}",
                details={
                    "from_panel": previous_panel,
                    "from_label": PANEL_LABELS.get(previous_panel, previous_panel),
                    "to_panel": panel_id,
                    "to_label": PANEL_LABELS.get(panel_id, panel_id),
                },
            )
        if previous_panel == "publish_history" and panel_id != "publish_history":
            if hasattr(self, "publish_history_panel"):
                self.publish_history_panel.on_hide()
        if previous_panel == "local_table_store" and panel_id != "local_table_store":
            self._cancel_local_table_auto_refresh()
        frame.tkraise()
        self._active_panel = panel_id
        if panel_id == "deploy":
            self._refresh_deploy_panel()
        elif panel_id == "logs":
            self._refresh_logs_panel()
        elif panel_id == "translation_records":
            self._refresh_translation_records_panel()
        elif panel_id == "local_table_store":
            self._refresh_local_table_store_panel()
            if hasattr(self, "local_table_auto_refresh_var") and self.local_table_auto_refresh_var.get():
                self._schedule_local_table_auto_refresh()
        elif panel_id == "plugin" and hasattr(self, "plugin_panel"):
            self.plugin_panel.refresh()
        elif panel_id == "publish_history" and hasattr(self, "publish_history_panel"):
            self.publish_history_panel.on_show()

    def _on_nav_select(self, _event: Any = None) -> None:
        selection = self.nav_tree.selection()
        if not selection:
            return
        panel_id = selection[0]
        if panel_id == "root" or panel_id not in self.panels:
            return
        self._show_panel(panel_id)

    def _build_form(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        ttk.Label(
            self.root,
            text="D365 开发工具    软件著作人：lilf0117,如有问题请加微信联系",
            anchor="center",
            justify="center",
            font=("", 10, "bold"),
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))

        nav_wrap = ttk.Frame(main_pane, width=220)
        main_pane.add(nav_wrap, weight=0)
        nav_wrap.rowconfigure(1, weight=1)
        nav_wrap.columnconfigure(0, weight=1)
        ttk.Label(nav_wrap, text="功能导航", font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )
        nav_tree_frame = ttk.Frame(nav_wrap)
        nav_tree_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        nav_tree_frame.rowconfigure(0, weight=1)
        nav_tree_frame.columnconfigure(0, weight=1)
        self.nav_tree = ttk.Treeview(nav_tree_frame, show="tree", selectmode="browse")
        self.nav_tree.grid(row=0, column=0, sticky="nsew")
        nav_scroll = ttk.Scrollbar(nav_tree_frame, orient="vertical", command=self.nav_tree.yview)
        nav_scroll.grid(row=0, column=1, sticky="ns")
        self.nav_tree.configure(yscrollcommand=nav_scroll.set)

        content_host = ttk.Frame(main_pane)
        main_pane.add(content_host, weight=1)
        content_host.rowconfigure(0, weight=1)
        content_host.columnconfigure(0, weight=1)

        panel_defs = [
            ("connection", "连接配置", self._build_connection_panel),
            ("field", "字段创建", self._build_field_panel),
            ("field_changes", "字段变更记录", self._build_field_changes_panel),
            ("local_table_store", "本地表存储", self._build_local_table_store_panel),
            ("translation", "实体翻译", self._build_translation_panel),
            ("translation_records", "实体翻译记录", self._build_translation_records_panel),
            ("table_query", "数据表查询", self._build_table_query_panel),
            ("permission", "实体权限", self._build_permission_panel),
            ("access_inspector", "用户角色追溯", self._build_access_inspector_panel),
            ("plugin", "插件注册", self._build_plugin_panel),
            ("plugin_changes", "插件变更记录", self._build_plugin_changes_panel),
            ("js_capture", "脚本JS调试", self._build_js_capture_panel),
            ("deploy", "批量发版", self._build_deploy_panel),
            ("publish_history", "发布历史记录", self._build_publish_history_panel),
            ("logs", "操作日志", self._build_logs_panel),
        ]
        nav_groups = [
            ("connection", "连接配置", "", "connection"),
            ("field_group", "字段管理", "", None),
            ("field", "字段创建", "field_group", "field"),
            ("table_query", "数据表查询", "field_group", "table_query"),
            ("local_table_store", "本地表存储", "field_group", "local_table_store"),
            ("translation_group", "翻译管理", "", None),
            ("translation", "实体翻译", "translation_group", "translation"),
            ("translation_records", "实体翻译记录", "translation_group", "translation_records"),
            ("security_group", "安全管理", "", None),
            ("permission", "实体权限", "security_group", "permission"),
            ("access_inspector", "用户角色追溯", "security_group", "access_inspector"),
            ("plugin_group", "插件管理", "", None),
            ("plugin", "插件注册", "plugin_group", "plugin"),
            ("plugin_changes", "插件变更记录", "plugin_group", "plugin_changes"),
            ("script_group", "脚本管理", "", None),
            ("js_capture", "脚本JS调试", "script_group", "js_capture"),
            ("deploy_group", "发布管理", "", None),
            ("deploy", "批量发版", "deploy_group", "deploy"),
            ("publish_history", "发布历史记录", "deploy_group", "publish_history"),
            ("logs", "操作日志", "", "logs"),
        ]
        for panel_id, label, builder in panel_defs:
            panel = ttk.Frame(content_host)
            panel.grid(row=0, column=0, sticky="nsew")
            builder(panel)
            self.panels[panel_id] = panel
        for item_id, label, parent_iid, panel_id in nav_groups:
            tags = () if panel_id else ("nav_group",)
            self.nav_tree.insert(parent_iid, "end", iid=item_id, text=label, open=False, tags=tags)
        for group_id in ("field_group", "translation_group", "security_group", "plugin_group", "script_group", "deploy_group"):
            self.nav_tree.item(group_id, open=False)

        self.nav_tree.bind("<<TreeviewSelect>>", self._on_nav_select)
        self.nav_tree.selection_set("connection")
        self._show_panel("connection")

        log_frame = ttk.LabelFrame(self.root, text="操作日志", padding=4)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=0)
        self.log = tk.Text(log_frame, height=8, wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)

    def _build_connection_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="连接配置", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        self._add_entry(parent, 1, "配置文件路径", "config_path")
        self._add_entry(parent, 2, "租户 ID", "tenant_id")
        self._add_entry(parent, 3, "应用 ID", "client_id")
        self._add_entry(parent, 4, "应用密钥", "client_secret", show="*")
        self._add_entry(parent, 5, "组织 URL", "org_url")
        button_bar = ttk.Frame(parent)
        button_bar.grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(button_bar, text="保存配置", command=self._on_save_config).pack(side="left", padx=(0, 8))
        ttk.Button(button_bar, text="重新加载配置", command=self._load_defaults).pack(side="left")
        ttk.Label(
            parent,
            text="说明：此处配置为全局连接信息，字段创建、权限管理与批量发版共用。",
            foreground="#666666",
            wraplength=640,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=6, pady=(12, 0))

    def _build_field_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        scroll_host = ttk.Frame(parent)
        scroll_host.grid(row=0, column=0, sticky="nsew")
        scroll_host.rowconfigure(0, weight=1)
        scroll_host.columnconfigure(0, weight=1)

        self.field_canvas = tk.Canvas(scroll_host, highlightthickness=0)
        self.field_canvas.grid(row=0, column=0, sticky="nsew")
        field_yscroll = ttk.Scrollbar(scroll_host, orient="vertical", command=self.field_canvas.yview)
        field_yscroll.grid(row=0, column=1, sticky="ns")
        self.field_canvas.configure(yscrollcommand=field_yscroll.set)

        top = ttk.Frame(self.field_canvas, padding=10)
        self.field_canvas_window = self.field_canvas.create_window((0, 0), window=top, anchor="nw")
        top.bind("<Configure>", self._on_top_frame_configure)
        self.field_canvas.bind("<Configure>", self._on_canvas_configure)

        def _on_field_mousewheel(event: Any) -> None:
            if self._active_panel != "field":
                return
            self.field_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.field_canvas.bind("<MouseWheel>", _on_field_mousewheel)
        top.bind("<MouseWheel>", _on_field_mousewheel)
        top.columnconfigure(1, weight=1)
        self.canvas = self.field_canvas
        self.canvas_window = self.field_canvas_window

        ttk.Label(top, text="解决方案与实体", font=("", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(top, text="解决方案").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.vars["solution_search"] = tk.StringVar()
        solution_frame = ttk.Frame(top)
        solution_frame.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        solution_frame.columnconfigure(0, weight=1)
        self.solution_combo = ttk.Combobox(solution_frame, textvariable=self.vars["solution_search"])
        self.solution_combo.grid(row=0, column=0, sticky="ew")
        self.solution_combo.bind("<KeyRelease>", self._on_solution_input)
        self.solution_combo.bind("<<ComboboxSelected>>", self._on_solution_selected)
        ttk.Button(solution_frame, text="搜索解决方案", command=self._on_search_solutions).grid(
            row=0, column=1, padx=(8, 0)
        )
        self._add_entry(top, 2, "解决方案唯一名", "solution_unique_name")
        ttk.Label(top, text="实体逻辑名").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        self.vars["entity_logical_name"] = tk.StringVar()
        entity_frame = ttk.Frame(top)
        entity_frame.grid(row=3, column=1, sticky="ew", padx=6, pady=4)
        entity_frame.columnconfigure(0, weight=1)
        self.entity_combo = ttk.Combobox(entity_frame, textvariable=self.vars["entity_logical_name"])
        self.entity_combo.grid(row=0, column=0, sticky="ew")
        self.entity_combo.bind("<KeyRelease>", self._on_entity_input)
        self._add_entry(top, 4, "发布者前缀", "publisher_prefix")
        ttk.Label(
            top,
            text="说明：发布者前缀用于字段逻辑名校验，例如前缀为 mcs 时字段应为 mcs_xxx。",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

        ttk.Separator(top, orient="horizontal").grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(top, text="字段配置", font=("", 11, "bold")).grid(row=7, column=0, sticky="w", pady=(0, 6))
        self._add_entry(top, 8, "字段逻辑名", "logical_name")
        self._add_entry(top, 9, "字段架构名", "schema_name")
        self._add_entry(top, 10, "显示名称", "display_name")
        self._add_entry(top, 11, "描述", "description")
        ttk.Label(top, text="字段类型").grid(row=12, column=0, sticky="w", padx=6, pady=4)
        self.vars["field_type"] = tk.StringVar()
        self.field_type_combo = ttk.Combobox(
            top,
            textvariable=self.vars["field_type"],
            state="readonly",
            values=list(FIELD_TYPE_LABEL_TO_VALUE.keys()),
        )
        self.field_type_combo.grid(row=12, column=1, sticky="ew", padx=6, pady=4)
        self.field_type_combo.bind("<<ComboboxSelected>>", self._on_field_type_change)
        ttk.Label(top, text="是否必填").grid(row=13, column=0, sticky="w", padx=6, pady=4)
        self.vars["required_level"] = tk.StringVar()
        self.required_level_combo = ttk.Combobox(
            top, textvariable=self.vars["required_level"], state="readonly", values=list(REQUIRED_LEVEL_LABEL_TO_VALUE.keys())
        )
        self.required_level_combo.grid(row=13, column=1, sticky="ew", padx=6, pady=4)
        self._add_entry(top, 14, "最大长度 (string/memo)", "max_length")
        self.lookup_target_label = ttk.Label(top, text="查找目标实体 (lookup)")
        self.lookup_target_label.grid(row=15, column=0, sticky="w", padx=6, pady=4)
        self.vars["lookup_target_entity"] = tk.StringVar()
        self.lookup_target_combo = ttk.Combobox(top, textvariable=self.vars["lookup_target_entity"])
        self.lookup_target_combo.grid(row=15, column=1, sticky="ew", padx=6, pady=4)
        self.lookup_rel_label, self.lookup_rel_entry = self._add_entry(
            top, 16, "关系架构名 (lookup 可选)", "lookup_relationship_schema_name"
        )
        self.picklist_mode_label = ttk.Label(top, text="下拉模式 (local/global)")
        self.picklist_mode_label.grid(row=17, column=0, sticky="w", padx=6, pady=4)
        self.vars["picklist_mode"] = tk.StringVar()
        self.picklist_mode_combo = ttk.Combobox(
            top, textvariable=self.vars["picklist_mode"], state="readonly", values=["local", "global"]
        )
        self.picklist_mode_combo.grid(row=17, column=1, sticky="ew", padx=6, pady=4)
        self.picklist_mode_combo.bind("<<ComboboxSelected>>", self._on_picklist_mode_change)
        self.picklist_global_label = ttk.Label(top, text="全局选项集名称 (picklist)")
        self.picklist_global_label.grid(row=18, column=0, sticky="w", padx=6, pady=4)
        global_frame = ttk.Frame(top)
        global_frame.grid(row=18, column=1, sticky="ew", padx=6, pady=4)
        global_frame.columnconfigure(0, weight=1)
        self.vars["picklist_global_name"] = tk.StringVar()
        self.picklist_global_combo = ttk.Combobox(global_frame, textvariable=self.vars["picklist_global_name"])
        self.picklist_global_combo.grid(row=0, column=0, sticky="ew")
        self.picklist_global_combo.bind("<KeyRelease>", self._on_global_option_set_input)
        ttk.Button(global_frame, text="加载全局下拉", command=self._load_global_option_sets).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.picklist_default_label, self.picklist_default_entry = self._add_entry(
            top, 19, "默认值 (picklist，可选)", "picklist_default_value"
        )
        self.picklist_options_label = ttk.Label(top, text="本地下拉选项 (picklist，本地模式，每行: 值:标签)")
        self.picklist_options_label.grid(row=20, column=0, sticky="nw", padx=6, pady=4)
        self.picklist_options_text = tk.Text(top, height=5, wrap="word")
        self.picklist_options_text.grid(row=20, column=1, sticky="ew", padx=6, pady=4)
        self.file_size_label, self.file_size_entry = self._add_entry(
            top, 21, "文件大小上限KB (file)", "file_max_size_kb"
        )

        self.dynamic_widgets["lookup"] = [
            self.lookup_target_label,
            self.lookup_target_combo,
            self.lookup_rel_label,
            self.lookup_rel_entry,
        ]
        self.dynamic_widgets["picklist"] = [
            self.picklist_mode_label,
            self.picklist_mode_combo,
            self.picklist_global_label,
            global_frame,
            self.picklist_default_label,
            self.picklist_default_entry,
            self.picklist_options_label,
            self.picklist_options_text,
        ]
        self.dynamic_widgets["file"] = [
            self.file_size_label,
            self.file_size_entry,
        ]

        button_bar = ttk.Frame(top)
        button_bar.grid(row=23, column=0, columnspan=2, sticky="w", pady=10)
        ttk.Button(button_bar, text="创建字段", command=self._on_create).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_bar, text="导出CS模板", command=self._export_cs_template).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_bar, text="从CS导入字段", command=self._import_fields_from_cs).grid(row=0, column=2)

    def _load_defaults(self) -> None:
        self.vars["config_path"].set(self.default_config_path)
        cfg = load_config(self.default_config_path)
        self.vars["tenant_id"].set(cfg.get("tenant_id", ""))
        self.vars["client_id"].set(cfg.get("client_id", ""))
        self.vars["client_secret"].set(cfg.get("client_secret", ""))
        self.vars["org_url"].set(cfg.get("org_url", ""))
        self.vars["entity_logical_name"].set(cfg.get("entity_logical_name", ""))
        self.vars["solution_unique_name"].set(cfg.get("solution_unique_name", ""))
        self.vars["publisher_prefix"].set(cfg.get("publisher_prefix", "mcs"))
        self.vars["solution_search"].set(cfg.get("solution_unique_name", ""))
        self.vars["field_type"].set(FIELD_TYPE_VALUE_TO_LABEL.get("string", "单行文本"))
        self.vars["required_level"].set("可选")
        self.vars["max_length"].set("100")
        self.vars["lookup_target_entity"].set("")
        self.vars["lookup_relationship_schema_name"].set("")
        self.vars["picklist_mode"].set("local")
        self.vars["picklist_global_name"].set("")
        self.vars["picklist_default_value"].set("")
        self.vars["file_max_size_kb"].set("32768")
        if hasattr(self, "picklist_options_text"):
            self.picklist_options_text.delete("1.0", "end")
            self.picklist_options_text.insert("1.0", "100000000:选项1\n100000001:选项2")
        self._on_picklist_mode_change(None)
        self._on_field_type_change(None)
        if hasattr(self, "entity_combo"):
            self.entity_combo["values"] = []
        if hasattr(self, "solution_combo"):
            self.solution_combo["values"] = []
        if hasattr(self, "lookup_target_combo"):
            self.lookup_target_combo["values"] = []
        if hasattr(self, "picklist_global_combo"):
            self.picklist_global_combo["values"] = []
        self.entity_items = []
        self.solution_items = []
        self.solution_id_map = {}
        self.current_solution_id = ""
        self.global_option_set_names = []
        self._log_op(
            "connection",
            "reload_config",
            "success",
            f"已重新加载配置: {self.default_config_path}",
            details={"config_path": self.default_config_path},
        )

    def _on_save_config(self) -> None:
        config_path = self.vars["config_path"].get().strip() or self.default_config_path
        existing = load_config(config_path)
        payload = {
            "tenant_id": self.vars["tenant_id"].get().strip(),
            "client_id": self.vars["client_id"].get().strip(),
            "client_secret": self.vars["client_secret"].get().strip(),
            "org_url": self.vars["org_url"].get().strip(),
            "schema_file": "schema.json",
            "entity_logical_name": self.vars["entity_logical_name"].get().strip(),
            "solution_unique_name": self.vars["solution_unique_name"].get().strip(),
            "publisher_prefix": self.vars["publisher_prefix"].get().strip(),
        }
        if existing.get("environment_name"):
            payload["environment_name"] = existing["environment_name"]
        if existing.get("environments"):
            payload["environments"] = existing["environments"]
        # 保留翻译 API 配置
        for key in ("BaiduAppId", "BaiduSecretKey", "BaiduApiUrl",
                     "YoudaoAppKey", "YoudaoAppSecret", "YoudaoApiUrl"):
            if existing.get(key):
                payload[key] = existing[key]
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self._log_op(
            "connection",
            "save_config",
            "success",
            f"配置已保存: {config_path}",
            details={"config_path": config_path, "saved_fields": sanitize_details(payload)},
        )
        messagebox.showinfo("保存成功", f"配置已保存: {config_path}")

    def _create_creator(self) -> D365FieldCreator:
        auth = D365Auth(
            tenant_id=self.vars["tenant_id"].get().strip(),
            client_id=self.vars["client_id"].get().strip(),
            client_secret=self.vars["client_secret"].get().strip(),
            org_url=self.vars["org_url"].get().strip(),
        )
        return D365FieldCreator(auth=auth, schema_file="")

    def _on_search_solutions(self) -> None:
        keyword = self.vars["solution_search"].get().strip()
        if not keyword:
            self._log_op("field", "search_solutions", "failed", "搜索解决方案失败：未输入关键字")
            messagebox.showwarning("提示", "请先输入解决方案关键字。")
            return
        if self.searching_solutions:
            return
        try:
            self.searching_solutions = True
            creator = self._create_creator()
            self.solution_items = creator.search_solutions(keyword=keyword, top=50)
            options: List[str] = []
            self.solution_id_map = {}
            for item in self.solution_items:
                unique_name = item["unique_name"]
                friendly_name = item.get("friendly_name", "")
                display = f"{unique_name} ({friendly_name})" if friendly_name else unique_name
                options.append(display)
                self.solution_id_map[display] = item["solution_id"]
            self.solution_combo["values"] = options
            self.solution_combo.event_generate("<Down>")
            self._append_log(f"解决方案搜索完成，匹配数量: {len(options)}")
            self._log_op(
                "field",
                "search_solutions",
                "success",
                f"搜索解决方案完成，关键字={keyword}，匹配 {len(options)} 条",
                details={
                    "keyword": keyword,
                    "result_count": len(options),
                    "results": sanitize_details(self.solution_items[:20]),
                },
            )
            if not options:
                messagebox.showinfo("搜索结果", "未找到匹配的解决方案。")
        except Exception as e:
            err_msg = _format_exception(e)
            self._append_log(err_msg)
            self._log_op(
                "field",
                "search_solutions",
                "failed",
                f"搜索解决方案失败: {keyword}",
                details={"keyword": keyword},
                error_message=err_msg,
            )
            messagebox.showerror("错误", err_msg)
        finally:
            self.searching_solutions = False

    def _on_solution_input(self, _event: Any) -> None:
        keyword = self.vars["solution_search"].get().strip().lower()
        options: List[str] = []
        for item in self.solution_items:
            unique_name = item["unique_name"]
            friendly_name = item.get("friendly_name", "")
            display = f"{unique_name} ({friendly_name})" if friendly_name else unique_name
            if not keyword or keyword in display.lower():
                options.append(display)
        self.solution_combo["values"] = options

    def _on_solution_selected(self, _event: Any) -> None:
        text = self.vars["solution_search"].get().strip()
        solution_id = self.solution_id_map.get(text, "")
        unique_name = text.split("(", 1)[0].strip() if text else ""
        if unique_name:
            self.vars["solution_unique_name"].set(unique_name)
        if not solution_id:
            return
        self.current_solution_id = solution_id
        self._log_op(
            "field",
            "select_solution",
            "success",
            f"已选择解决方案: {unique_name}",
            details={"solution_unique_name": unique_name, "solution_id": solution_id},
            solution_name=unique_name,
        )
        self._load_entities_for_solution(solution_id)

    def _load_entities_for_solution(self, solution_id: str) -> None:
        if self.searching_entities:
            return
        try:
            self.searching_entities = True
            creator = self._create_creator()
            self.entity_items = creator.list_entities_by_solution(solution_id)
            self._filter_entity_values("")
            self.lookup_target_combo["values"] = [x["logical_name"] for x in self.entity_items]
            self.entity_combo.event_generate("<Down>")
            self._append_log(f"已加载解决方案实体数量: {len(self.entity_items)}")
            self._log_op(
                "field",
                "load_solution_entities",
                "success",
                f"已加载解决方案实体 {len(self.entity_items)} 个",
                details={
                    "solution_id": solution_id,
                    "entity_count": len(self.entity_items),
                    "entities": sanitize_details(self.entity_items[:50]),
                },
            )
            if not self.entity_items:
                messagebox.showinfo("提示", "该解决方案下未找到实体组件。")
        except Exception as e:
            self._append_log(str(e))
            self._log_op(
                "field",
                "load_solution_entities",
                "failed",
                f"加载解决方案实体失败: {solution_id}",
                details={"solution_id": solution_id},
                error_message=str(e),
            )
            messagebox.showerror("错误", str(e))
        finally:
            self.searching_entities = False

    def _filter_entity_values(self, keyword: str) -> None:
        if not hasattr(self, "entity_combo"):
            return
        kw = keyword.lower()
        options: List[str] = []
        for item in self.entity_items:
            ln = item["logical_name"]
            dn = item.get("display_name", "")
            search_text = f"{ln} {dn}".lower()
            if not kw or kw in search_text:
                if dn:
                    options.append(f"{ln} ({dn})")
                else:
                    options.append(ln)
            if len(options) >= 100:
                break
        self.entity_combo["values"] = options

    def _on_entity_input(self, _event: Any) -> None:
        text = self.vars["entity_logical_name"].get().strip()
        if "(" in text and text.endswith(")"):
            text = text.split("(", 1)[0].strip()
            self.vars["entity_logical_name"].set(text)
        self._filter_entity_values(text)

    def _load_global_option_sets(self) -> None:
        try:
            creator = self._create_creator()
            self.global_option_set_names = creator.list_global_option_sets()
            self.picklist_global_combo["values"] = self.global_option_set_names
            self._append_log(f"已加载全局选项集数量: {len(self.global_option_set_names)}")
            self._log_op(
                "field",
                "load_global_option_sets",
                "success",
                f"已加载全局选项集 {len(self.global_option_set_names)} 个",
                details={
                    "count": len(self.global_option_set_names),
                    "names": self.global_option_set_names[:50],
                },
            )
            if self.global_option_set_names:
                self.picklist_global_combo.event_generate("<Down>")
            else:
                messagebox.showinfo("提示", "未找到全局选项集。")
        except Exception as e:
            self._append_log(str(e))
            self._log_op(
                "field",
                "load_global_option_sets",
                "failed",
                "加载全局选项集失败",
                error_message=str(e),
            )
            messagebox.showerror("错误", str(e))

    def _on_global_option_set_input(self, _event: Any) -> None:
        keyword = self.vars["picklist_global_name"].get().strip().lower()
        if not self.global_option_set_names:
            return
        self.picklist_global_combo["values"] = [x for x in self.global_option_set_names if keyword in x.lower()]

    def _on_picklist_mode_change(self, _event: Any) -> None:
        mode = self.vars["picklist_mode"].get().strip().lower()
        if mode == "global":
            self.picklist_options_text.config(state="disabled")
            self.picklist_global_combo.config(state="normal")
        else:
            self.picklist_options_text.config(state="normal")
            self.picklist_global_combo.config(state="normal")

    def _on_field_type_change(self, _event: Any) -> None:
        field_type = self._get_field_type_value()
        for w in self.dynamic_widgets["lookup"]:
            w.grid_remove()
        for w in self.dynamic_widgets["picklist"]:
            w.grid_remove()
        for w in self.dynamic_widgets["file"]:
            w.grid_remove()
        if field_type == "lookup":
            for w in self.dynamic_widgets["lookup"]:
                w.grid()
        elif field_type == "picklist":
            for w in self.dynamic_widgets["picklist"]:
                w.grid()
        elif field_type == "file":
            for w in self.dynamic_widgets["file"]:
                w.grid()

    def _parse_picklist_options(self, text: str) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if ":" not in line:
                raise ValueError(f"下拉选项格式错误: {line}，请使用 值:标签")
            value_part, label_part = line.split(":", 1)
            value = int(value_part.strip())
            label = label_part.strip()
            if not label:
                raise ValueError(f"下拉选项标签不能为空: {line}")
            options.append({"value": value, "label": label})
        return options

    def _get_field_type_value(self) -> str:
        label = self.vars["field_type"].get().strip()
        return FIELD_TYPE_LABEL_TO_VALUE.get(label, label.lower() or "string")

    def _get_required_level_value(self) -> str:
        label = self.vars["required_level"].get().strip()
        return REQUIRED_LEVEL_LABEL_TO_VALUE.get(label, "None")

    def _normalize_required_level_text(self, value: str) -> Optional[str]:
        raw = value.strip()
        if not raw:
            return None
        lowered = raw.lower()
        mapping = {
            "none": "None",
            "recommended": "Recommended",
            "applicationrequired": "ApplicationRequired",
        }
        if lowered in mapping:
            return mapping[lowered]
        if raw in {"None", "Recommended", "ApplicationRequired"}:
            return raw
        return None

    def _clear_created_field_inputs(self) -> None:
        self.vars["logical_name"].set("")
        self.vars["schema_name"].set("")
        self.vars["display_name"].set("")

    def _confirm_import_preview(self, preview_lines: List[str]) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title("导入预览确认")
        dialog.geometry("760x520")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="以下字段将被导入到当前实体，确认无误后点击“确认导入”",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 6))

        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        txt = tk.Text(frame, wrap="none")
        txt.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        txt.configure(yscrollcommand=yscroll.set)
        txt.insert("1.0", "\n".join(preview_lines))
        txt.configure(state="disabled")

        result = {"confirmed": False}

        btns = ttk.Frame(dialog)
        btns.pack(fill="x", padx=10, pady=(6, 10))

        def on_confirm() -> None:
            result["confirmed"] = True
            dialog.destroy()

        def on_cancel() -> None:
            result["confirmed"] = False
            dialog.destroy()

        ttk.Button(btns, text="确认导入", command=on_confirm).pack(side="left")
        ttk.Button(btns, text="取消", command=on_cancel).pack(side="left", padx=(8, 0))

        dialog.wait_window()
        return result["confirmed"]

    def _export_cs_template(self) -> None:
        default_name = "D365FieldTemplate.cs"
        path = filedialog.asksaveasfilename(
            title="导出 CS 模板",
            defaultextension=".cs",
            initialfile=default_name,
            filetypes=[("C# File", "*.cs"), ("All Files", "*.*")],
        )
        if not path:
            self._log_op("field", "export_cs_template", "cancelled", "用户取消导出 CS 模板")
            return
        template = """using System;

namespace D365ModelTemplate
{
    [AttributeUsage(AttributeTargets.Property)]
    public sealed class D365FieldAttribute : Attribute
    {
        // string/memo/integer/decimal/double/money/datetime/boolean/picklist/lookup/file
        public string FieldType { get; }
        public string RequiredLevel { get; set; } = "None"; // None/Recommended/ApplicationRequired
        public int MaxLength { get; set; } = 100; // string/memo
        public int FileMaxSizeKB { get; set; } = 32768; // file
        public string LookupTarget { get; set; } = ""; // lookup target entity
        public string PicklistMode { get; set; } = "local"; // local/global
        public string PicklistOptions { get; set; } = ""; // 100000000:选项1|100000001:选项2
        public string GlobalOptionSetName { get; set; } = ""; // picklist global name
        public int DefaultValue { get; set; } = int.MinValue; // picklist default
        public string DisplayName { get; set; } = ""; // 字段显示名称，留空则默认取属性名

        public D365FieldAttribute(string fieldType)
        {
            FieldType = fieldType;
        }
    }
    // 只需修改下面这部分的内容即可，上面定义的不需要更改会自动忽略，通过模板导入的时候，字段不需要填写：mcs_
    public class EntityFieldTemplate
    {
        [D365Field("string", MaxLength = 200, RequiredLevel = "None", DisplayName = "测试字段1")]
        public string test1 { get; set; } //单行文本

        [D365Field("memo", MaxLength = 2000, RequiredLevel = "Recommended", DisplayName = "测试字段2")]
        public string test2 { get; set; } //多行文本

        [D365Field("integer", RequiredLevel = "None", DisplayName = "测试字段3")]
        public int test3 { get; set; } //整数

        [D365Field("decimal", RequiredLevel = "None", DisplayName = "测试字段4")]
        public decimal test4 { get; set; } //小数

        [D365Field("double", RequiredLevel = "None", DisplayName = "测试字段5")]
        public double test5 { get; set; }  //浮点数

        [D365Field("money", RequiredLevel = "None", DisplayName = "测试字段6")]
        public decimal test6 { get; set; } //货币

        [D365Field("datetime", RequiredLevel = "Recommended", DisplayName = "测试字段7")]
        public DateTime test7 { get; set; } //日期时间

        [D365Field("boolean", RequiredLevel = "None", DisplayName = "测试字段8")]
        public bool test6 { get; set; } //是/否

        [D365Field("picklist", PicklistMode = "local", PicklistOptions = "100000000:待签|100000001:已签", DefaultValue = 100000000, RequiredLevel = "None", DisplayName = "测试字段9")]
        public int test9 { get; set; } //下拉选项

        [D365Field("picklist", PicklistMode = "global", GlobalOptionSetName = "mcs_GlobalStatus", RequiredLevel = "None", DisplayName = "测试字段10")]
        public int test10 { get; set; } //全局下拉选项

        [D365Field("lookup", LookupTarget = "account", RequiredLevel = "Recommended", DisplayName = "测试字段11")]
        public Guid test11 { get; set; } //查找类型

        [D365Field("file", FileMaxSizeKB = 32768, RequiredLevel = "None", DisplayName = "测试字段12")]
        public string test12 { get; set; } //文件
    }
}
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
        self._append_log(f"CS 模板已导出: {path}")
        self._log_op(
            "field",
            "export_cs_template",
            "success",
            f"CS 模板已导出: {path}",
            details={"output_path": path},
        )
        messagebox.showinfo("导出成功", f"CS 模板已导出:\n{path}")

    def _import_fields_from_cs(self) -> None:
        cs_path = filedialog.askopenfilename(
            title="选择要导入的 CS 文件",
            filetypes=[("C# File", "*.cs"), ("All Files", "*.*")],
        )
        if not cs_path:
            self._log_op("field", "import_cs", "cancelled", "用户取消选择 CS 文件")
            return

        entity_value = self.vars["entity_logical_name"].get().strip()
        if "(" in entity_value:
            entity_value = entity_value.split("(", 1)[0].strip()
        solution = self.vars["solution_unique_name"].get().strip()
        prefix = self.vars["publisher_prefix"].get().strip() or "mcs"
        required_level = self._get_required_level_value()
        if not entity_value or not solution:
            self._log_op(
                "field",
                "import_cs",
                "failed",
                "CS 导入失败：未选择实体或解决方案",
                details={"cs_path": cs_path},
            )
            messagebox.showerror("错误", "请先选择实体并填写解决方案唯一名。")
            return

        with open(cs_path, "r", encoding="utf-8") as f:
            content = f.read()
        props = self._parse_cs_properties(content)
        if not props:
            self._log_op(
                "field",
                "import_cs",
                "failed",
                "CS 导入失败：未识别到可导入属性",
                details={"cs_path": cs_path},
            )
            messagebox.showerror("错误", "未识别到可导入属性（需 public ... { get; set; }）。")
            return

        self._log_op(
            "field",
            "import_cs",
            "started",
            f"准备从 CS 导入 {len(props)} 个字段",
            details={
                "cs_path": cs_path,
                "entity": entity_value,
                "solution": solution,
                "publisher_prefix": prefix,
                "property_count": len(props),
                "properties": sanitize_details(props),
            },
            entity_name=entity_value,
            solution_name=solution,
        )
        preview_lines = [
            f"目标解决方案: {solution}",
            f"目标实体: {entity_value}",
            f"发布者前缀: {prefix}",
            "",
            "即将导入字段清单:",
        ]
        for i, p in enumerate(props, start=1):
            snake_name = self._to_snake_case(p["name"])
            logical_name = f"{prefix}_{snake_name}"
            field_type_label = FIELD_TYPE_VALUE_TO_LABEL.get(p["field_type"], p["field_type"])
            preview_lines.append(
                f"{i:>3}. 逻辑名={logical_name} | 类型={field_type_label} | 来源属性={p['name']}"
            )
        preview_lines.append("")
        preview_lines.append(f"总计: {len(props)} 个字段")
        if not self._confirm_import_preview(preview_lines):
            self._append_log("用户取消了 CS 字段导入。")
            self._log_op(
                "field",
                "import_cs",
                "cancelled",
                "用户在预览确认阶段取消 CS 字段导入",
                details={"cs_path": cs_path, "property_count": len(props)},
                entity_name=entity_value,
                solution_name=solution,
            )
            return

        try:
            creator = self._create_creator()
            created = 0
            skipped = 0
            for p in props:
                snake_name = self._to_snake_case(p["name"])
                logical_name = f"{prefix}_{snake_name}"
                schema_name = self._normalize_schema_name(f"{prefix}_{p['name']}")
                field_type = p["field_type"]
                required_level_raw = p.get("meta_RequiredLevel", "").strip()
                required_level_value = required_level
                parsed_required_level = self._normalize_required_level_text(required_level_raw)
                if parsed_required_level:
                    required_level_value = parsed_required_level
                display_name = p.get("meta_DisplayName", "").strip() or p["name"]
                field: Dict[str, Any] = {
                    "logical_name": logical_name,
                    "schema_name": schema_name,
                    "display_name": display_name,
                    "description": f"Imported from CS property: {p['name']}",
                    "field_type": field_type,
                    "required_level": required_level_value,
                    "is_audit_enabled": False,
                    "searchable": True,
                }
                if field_type == "string":
                    max_length = int(p.get("meta_MaxLength", "200"))
                    field["string"] = {"max_length": max_length, "format": "Text"}
                elif field_type == "memo":
                    max_length = int(p.get("meta_MaxLength", "2000"))
                    field["memo"] = {"max_length": max_length}
                elif field_type == "integer":
                    field["integer"] = {"min_value": -2147483648, "max_value": 2147483647}
                elif field_type == "decimal":
                    field["decimal"] = {"min_value": -100000000000, "max_value": 100000000000, "precision": 2}
                elif field_type == "double":
                    field["double"] = {"min_value": -100000000000, "max_value": 100000000000, "precision": 2}
                elif field_type == "boolean":
                    field["boolean"] = {"true_label": "是", "false_label": "否", "default_value": False}
                elif field_type == "datetime":
                    field["datetime"] = {"format": "DateOnly", "behavior": "UserLocal"}
                elif field_type == "money":
                    field["money"] = {"min_value": -922337203685477, "max_value": 922337203685477, "precision": 2}
                elif field_type == "picklist":
                    picklist_mode = p.get("meta_PicklistMode", "local").strip().lower()
                    picklist_cfg: Dict[str, Any] = {}
                    if p.get("meta_DefaultValue", "").strip():
                        v = p.get("meta_DefaultValue", "").strip()
                        if v != str(-(2**31)):
                            picklist_cfg["default_value"] = int(v)
                    if picklist_mode == "global":
                        global_name = p.get("meta_GlobalOptionSetName", "").strip()
                        if not global_name:
                            raise ValueError(f"属性 {p['name']} 未填写 GlobalOptionSetName")
                        picklist_cfg["global_option_set_name"] = global_name
                    else:
                        raw_options = p.get("meta_PicklistOptions", "").strip()
                        if not raw_options:
                            raise ValueError(f"属性 {p['name']} 未填写 PicklistOptions")
                        options = self._parse_picklist_options(raw_options.replace("|", "\n"))
                        picklist_cfg["options"] = options
                    field["picklist"] = picklist_cfg
                elif field_type == "lookup":
                    lookup_target = p.get("meta_LookupTarget", "").strip()
                    if not lookup_target:
                        raise ValueError(f"属性 {p['name']} 未填写 LookupTarget")
                    field["lookup"] = {"target_entity": lookup_target, "relationship_schema_name": ""}
                elif field_type == "file":
                    max_size_kb = int(p.get("meta_FileMaxSizeKB", "32768"))
                    field["file"] = {"max_size_kb": max_size_kb}

                result = creator.create_field(entity_name=entity_value, solution=solution, field=field)
                if result.startswith("Skip existing field:"):
                    skipped += 1
                else:
                    created += 1
                field_type_label = FIELD_TYPE_VALUE_TO_LABEL.get(field_type, field_type)
                self._append_log(f"{result}，字段类型: {field_type_label}，来源属性: {p['name']}")

            messagebox.showinfo(
                "导入完成",
                f"CS 导入完成。\n创建: {created}\n跳过已存在: {skipped}\n总属性: {len(props)}",
            )
            self._log_op(
                "field",
                "import_cs",
                "success",
                f"CS 导入完成：创建 {created}，跳过 {skipped}，共 {len(props)} 个属性",
                details={
                    "cs_path": cs_path,
                    "created": created,
                    "skipped": skipped,
                    "total": len(props),
                    "entity": entity_value,
                    "solution": solution,
                },
                entity_name=entity_value,
                solution_name=solution,
            )
        except Exception as e:
            self._append_log(str(e))
            self._log_op(
                "field",
                "import_cs",
                "failed",
                "CS 字段导入失败",
                details={"cs_path": cs_path, "entity": entity_value, "solution": solution},
                error_message=str(e),
                entity_name=entity_value,
                solution_name=solution,
            )
            messagebox.showerror("错误", str(e))

    def _on_create(self) -> None:
        try:
            creator = self._create_creator()
            field_type = self._get_field_type_value()
            field_type_label = FIELD_TYPE_VALUE_TO_LABEL.get(field_type, field_type)
            entity_value = self.vars["entity_logical_name"].get().strip()
            if "(" in entity_value:
                entity_value = entity_value.split("(", 1)[0].strip()
            field: Dict[str, Any] = {
                "logical_name": self.vars["logical_name"].get().strip(),
                "schema_name": self.vars["schema_name"].get().strip(),
                "display_name": self.vars["display_name"].get().strip(),
                "description": self.vars["description"].get().strip(),
                "field_type": field_type,
                "required_level": self._get_required_level_value(),
                "is_audit_enabled": False,
                "searchable": True,
            }
            if field_type in {"string", "memo"}:
                field[field_type] = {"max_length": int(self.vars["max_length"].get().strip() or "100")}
            elif field_type == "lookup":
                lookup_target_entity = self.vars["lookup_target_entity"].get().strip()
                if not lookup_target_entity:
                    raise ValueError("lookup 类型必须填写 查找目标实体")
                field["lookup"] = {
                    "target_entity": lookup_target_entity,
                    "relationship_schema_name": self.vars["lookup_relationship_schema_name"].get().strip(),
                }
            elif field_type == "file":
                file_max_size_kb = int(self.vars["file_max_size_kb"].get().strip() or "32768")
                field["file"] = {"max_size_kb": file_max_size_kb}
            elif field_type == "picklist":
                picklist_mode = self.vars["picklist_mode"].get().strip().lower() or "local"
                default_value_text = self.vars["picklist_default_value"].get().strip()
                picklist_cfg: Dict[str, Any] = {}
                if default_value_text:
                    picklist_cfg["default_value"] = int(default_value_text)
                if picklist_mode == "global":
                    global_name = self.vars["picklist_global_name"].get().strip()
                    if not global_name:
                        raise ValueError("picklist 全局模式必须填写 全局选项集名称")
                    picklist_cfg["global_option_set_name"] = global_name
                else:
                    raw = self.picklist_options_text.get("1.0", "end").strip()
                    options = self._parse_picklist_options(raw)
                    if not options:
                        raise ValueError("picklist 本地模式至少需要一个选项")
                    picklist_cfg["options"] = options
                field["picklist"] = picklist_cfg

            schema = {
                "entity_logical_name": entity_value,
                "solution_unique_name": self.vars["solution_unique_name"].get().strip(),
                "publisher_prefix": self.vars["publisher_prefix"].get().strip(),
                "fields": [field],
            }
            creator._validate_schema(schema)
            result = creator.create_field(
                entity_name=schema["entity_logical_name"],
                solution=schema["solution_unique_name"],
                field=field,
            )
            detail_result = f"{result}，字段类型: {field_type_label}"
            self._append_log(detail_result)
            self._clear_created_field_inputs()
            self._log_op(
                "field",
                "create_field",
                "success",
                detail_result,
                details={
                    "entity": entity_value,
                    "solution": schema["solution_unique_name"],
                    "field": sanitize_details(field),
                    "api_result": result,
                },
                entity_name=entity_value,
                solution_name=schema["solution_unique_name"],
            )
            messagebox.showinfo("创建成功", detail_result)
        except Exception as e:
            self._append_log(str(e))
            self._log_op(
                "field",
                "create_field",
                "failed",
                "创建字段失败",
                details={
                    "logical_name": self.vars.get("logical_name", tk.StringVar()).get().strip(),
                    "field_type": self.vars.get("field_type", tk.StringVar()).get().strip(),
                },
                error_message=str(e),
            )
            messagebox.showerror("错误", str(e))

    def _get_config_path(self) -> str:
        return self.vars["config_path"].get().strip() or self.default_config_path

    def _get_tenant_id(self) -> str:
        return self.vars["tenant_id"].get().strip()

    def _get_current_environment(self) -> Dict[str, str]:
        return {
            "name": "当前连接",
            "org_url": self.vars["org_url"].get().strip(),
            "client_id": self.vars["client_id"].get().strip(),
            "client_secret": self.vars["client_secret"].get().strip(),
            "tenant_id": self._get_tenant_id(),
        }

    def _create_creator_for_environment(self, env: Dict[str, str]) -> D365FieldCreator:
        tenant_id = str(env.get("tenant_id", self._get_tenant_id())).strip()
        return create_creator_from_environment(env, fallback_tenant_id=tenant_id)

    def _get_table_query_environment(self) -> Dict[str, str]:
        env_name = self.table_query_env_var.get().strip()
        env = self.table_query_env_map.get(env_name)
        if env is None:
            raise RuntimeError(f"未找到环境配置: {env_name}")
        return env

    def _build_local_table_store_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(2, weight=1)
        parent.rowconfigure(4, weight=2)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(2, weight=1)

        ttk.Label(toolbar, text="本地表存储", font=("", 11, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Button(toolbar, text="刷新本地列表", command=self._refresh_local_table_store_panel).grid(row=0, column=1, padx=(0, 8))

        self.local_table_keyword_var = tk.StringVar()
        keyword_entry = ttk.Entry(toolbar, textvariable=self.local_table_keyword_var)
        keyword_entry.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        keyword_entry.bind("<Return>", lambda _e: self._refresh_local_table_store_panel())
        ttk.Button(toolbar, text="筛选", command=self._refresh_local_table_store_panel).grid(row=0, column=3, padx=(0, 8))

        ttk.Button(toolbar, text="立即更新全部", command=self._refresh_all_local_tables_from_crm).grid(row=0, column=4, padx=(0, 8))
        ttk.Button(toolbar, text="更新选中表", command=self._refresh_selected_local_table_from_crm).grid(row=0, column=5, padx=(0, 8))

        self.local_table_auto_refresh_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            toolbar,
            text="自动刷新(30秒)",
            variable=self.local_table_auto_refresh_var,
            command=self._on_local_table_auto_refresh_toggle,
        ).grid(row=0, column=6, sticky="w")

        self.local_table_status_var = tk.StringVar(value="数据表查询成功后会自动保存到这里。")
        ttk.Label(parent, textvariable=self.local_table_status_var, foreground="#666").grid(
            row=1, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        table_frame = ttk.LabelFrame(parent, text="本地已保存表", padding=4)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 6))
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.local_table_tree = ttk.Treeview(
            table_frame,
            columns=("environment_name", "logical_name", "display_name", "schema_name", "field_count", "last_refreshed_at"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        for column, title, width in (
            ("environment_name", "环境", 120),
            ("logical_name", "逻辑名", 180),
            ("display_name", "显示名", 240),
            ("schema_name", "Schema名", 180),
            ("field_count", "字段数", 70),
            ("last_refreshed_at", "最后刷新", 190),
        ):
            self.local_table_tree.heading(column, text=title)
            self.local_table_tree.column(column, width=width, anchor="w", stretch=(column in {"display_name", "schema_name"}))
        self.local_table_tree.grid(row=0, column=0, sticky="nsew")
        table_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.local_table_tree.yview)
        table_y.grid(row=0, column=1, sticky="ns")
        self.local_table_tree.configure(yscrollcommand=table_y.set)
        self.local_table_tree.bind("<<TreeviewSelect>>", self._on_local_table_selected)

        self.local_table_info_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.local_table_info_var, foreground="#333").grid(
            row=3, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        field_frame = ttk.LabelFrame(parent, text="本地字段缓存", padding=4)
        field_frame.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 8))
        field_frame.rowconfigure(0, weight=1)
        field_frame.columnconfigure(0, weight=1)
        self.local_table_field_tree = self._create_table_query_field_tree(field_frame)

        self.local_table_rows: List[Dict[str, Any]] = []
        self.local_table_row_map: Dict[str, Dict[str, Any]] = {}
        self.local_table_refreshing = False
        self.local_table_auto_refresh_job: Optional[str] = None
        self._refresh_local_table_store_panel()

    def _refresh_local_table_store_panel(self) -> None:
        if not hasattr(self, "local_table_tree") or not hasattr(self, "op_logger"):
            return
        keyword = self.local_table_keyword_var.get().strip() if hasattr(self, "local_table_keyword_var") else ""
        rows = self.op_logger.list_local_crm_tables(keyword)
        self.local_table_rows = rows
        self.local_table_row_map = {}
        for item in self.local_table_tree.get_children():
            self.local_table_tree.delete(item)
        for row in rows:
            item_id = str(row.get("id"))
            display = _join_display_names(str(row.get("display_name_zh") or ""), str(row.get("display_name_en") or ""))
            self.local_table_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row.get("environment_name", ""),
                    row.get("logical_name", ""),
                    display,
                    row.get("schema_name", ""),
                    row.get("field_count", 0),
                    row.get("last_refreshed_at", ""),
                ),
            )
            self.local_table_row_map[item_id] = row
        self.local_table_status_var.set(f"本地共 {len(rows)} 张表。")
        self._populate_table_query_tree(self.local_table_field_tree, [])
        self.local_table_info_var.set("")

    def _on_local_table_selected(self, _event: Any = None) -> None:
        selection = self.local_table_tree.selection()
        if not selection:
            return
        row = self.local_table_row_map.get(selection[0])
        if not row:
            return
        fields = self.op_logger.list_local_crm_table_fields(int(row["id"]))
        self._populate_table_query_tree(self.local_table_field_tree, fields)
        display = _join_display_names(str(row.get("display_name_zh") or ""), str(row.get("display_name_en") or "")) or "-"
        self.local_table_info_var.set(
            f"环境: {row.get('environment_name', '')} | 表: {row.get('logical_name', '')} | 显示名: {display} | 字段数: {len(fields)}"
        )

    def _local_table_env_map(self) -> Dict[str, Dict[str, str]]:
        envs = load_environments(self._get_config_path())
        env_map: Dict[str, Dict[str, str]] = {}
        for env in envs:
            env_map[str(env.get("name", "")).strip().lower()] = env
            env_map[str(env.get("org_url", "")).strip().rstrip("/").lower()] = env
        return env_map

    def _resolve_local_table_environment(self, row: Dict[str, Any]) -> Optional[Dict[str, str]]:
        env_map = self._local_table_env_map()
        env = env_map.get(str(row.get("environment_name", "")).strip().lower())
        if env:
            return env
        return env_map.get(str(row.get("org_url", "")).strip().rstrip("/").lower())

    def _refresh_selected_local_table_from_crm(self) -> None:
        selection = self.local_table_tree.selection() if hasattr(self, "local_table_tree") else ()
        if not selection:
            messagebox.showwarning("提示", "请先选择一张本地表。", parent=self.root)
            return
        row = self.local_table_row_map.get(selection[0])
        if row:
            self._refresh_local_tables_from_crm([row])

    def _refresh_all_local_tables_from_crm(self) -> None:
        rows = list(getattr(self, "local_table_rows", []))
        if not rows:
            self.local_table_status_var.set("本地还没有保存表，请先在数据表查询中查询一次。")
            return
        self._refresh_local_tables_from_crm(rows)

    def _refresh_local_tables_from_crm(self, rows: List[Dict[str, Any]]) -> None:
        if getattr(self, "local_table_refreshing", False):
            return
        self.local_table_refreshing = True
        self.local_table_status_var.set(f"正在从 CRM 更新 {len(rows)} 张本地表...")

        def worker() -> None:
            updated = 0
            failed: List[str] = []
            for row in rows:
                logical_name = str(row.get("logical_name", "")).strip()
                try:
                    env = self._resolve_local_table_environment(row)
                    if not env:
                        failed.append(f"{logical_name}: 未找到环境配置")
                        continue
                    creator = self._create_creator_for_environment(env)
                    entity_info = creator.get_entity_info(logical_name)
                    attributes = creator.list_entity_attributes(logical_name)
                    self.op_logger.upsert_local_crm_table(
                        environment_name=str(env.get("name", row.get("environment_name", ""))),
                        org_url=str(env.get("org_url", row.get("org_url", ""))),
                        entity_info=entity_info,
                        fields=attributes,
                    )
                    updated += 1
                except Exception as exc:
                    failed.append(f"{logical_name}: {_format_exception(exc)}")

            def on_done() -> None:
                self.local_table_refreshing = False
                self._refresh_local_table_store_panel()
                msg = f"CRM 更新完成：成功 {updated} 张，失败 {len(failed)} 张。"
                if failed:
                    msg += " " + "；".join(failed[:3])
                self.local_table_status_var.set(msg)
                self._append_log(msg)

            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_local_table_auto_refresh_toggle(self) -> None:
        if self.local_table_auto_refresh_var.get():
            self._schedule_local_table_auto_refresh()
        else:
            self._cancel_local_table_auto_refresh()

    def _schedule_local_table_auto_refresh(self) -> None:
        self._cancel_local_table_auto_refresh()
        if not hasattr(self, "local_table_auto_refresh_var") or not self.local_table_auto_refresh_var.get():
            return
        if self._active_panel != "local_table_store":
            return
        self.local_table_auto_refresh_job = self.root.after(30000, self._local_table_auto_refresh_tick)

    def _cancel_local_table_auto_refresh(self) -> None:
        job = getattr(self, "local_table_auto_refresh_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except ValueError:
                pass
        self.local_table_auto_refresh_job = None

    def _local_table_auto_refresh_tick(self) -> None:
        self.local_table_auto_refresh_job = None
        if self._active_panel == "local_table_store" and self.local_table_auto_refresh_var.get():
            self._refresh_all_local_tables_from_crm()
            self._schedule_local_table_auto_refresh()

    def _build_translation_panel(self, parent: ttk.Frame) -> None:
        """构建实体翻译面板。"""
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.translation_panel = TranslationPanel(self, parent)

    def _build_table_query_panel(self, parent: ttk.Frame) -> None:
        environments = load_environments(self._get_config_path())
        self.table_query_env_map: Dict[str, Dict[str, str]] = {}
        env_names: List[str] = []
        for env in environments:
            name = env["name"]
            if name not in self.table_query_env_map:
                self.table_query_env_map[name] = env
                env_names.append(name)
        current_env = self._get_current_environment()
        cfg = load_config(self._get_config_path())
        default_env_name = str(cfg.get("environment_name", "")).strip()
        if default_env_name not in self.table_query_env_map and env_names:
            for env in environments:
                if env["org_url"].rstrip("/").lower() == current_env["org_url"].rstrip("/").lower():
                    default_env_name = env["name"]
                    break
            else:
                default_env_name = env_names[0]

        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="数据表查询", font=("", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        query_frame = ttk.LabelFrame(parent, text="查询条件", padding=8)
        query_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        query_frame.columnconfigure(1, weight=1)
        query_frame.columnconfigure(3, weight=1)

        ttk.Label(query_frame, text="目标环境").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        self.table_query_env_var = tk.StringVar(value=default_env_name)
        self.table_query_env_url_var = tk.StringVar()
        ttk.Combobox(
            query_frame,
            textvariable=self.table_query_env_var,
            state="readonly",
            values=env_names,
            width=18,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=2)
        ttk.Label(query_frame, textvariable=self.table_query_env_url_var, foreground="#666").grid(
            row=0, column=2, columnspan=2, sticky="w", pady=2
        )

        ttk.Label(query_frame, text="表逻辑名").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        self.table_query_entity_var = tk.StringVar()
        entity_entry = ttk.Entry(query_frame, textvariable=self.table_query_entity_var)
        entity_entry.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=2)
        entity_entry.bind("<Return>", lambda _e: self._on_query_table_fields())

        ttk.Label(query_frame, text="字段筛选").grid(row=1, column=2, sticky="w", padx=(0, 6), pady=2)
        self.table_query_filter_var = tk.StringVar()
        filter_entry = ttk.Entry(query_frame, textvariable=self.table_query_filter_var)
        filter_entry.grid(row=1, column=3, sticky="ew", padx=(0, 12), pady=2)
        filter_entry.bind("<KeyRelease>", lambda _e: self._apply_table_query_filter())

        ttk.Button(query_frame, text="查询", command=self._on_query_table_fields, width=10).grid(
            row=1, column=4, sticky="e", pady=2
        )

        ttk.Label(
            query_frame,
            text="输入表逻辑名精确查询（如 mcs_contract），不依赖解决方案。",
            foreground="#888",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

        def _update_table_query_env_label(_event: Any = None) -> None:
            env = self.table_query_env_map.get(self.table_query_env_var.get().strip(), current_env)
            self.table_query_env_url_var.set(env.get("org_url", ""))

        def _on_table_query_env_changed(_event: Any = None) -> None:
            _update_table_query_env_label()
            self._clear_table_query_results()
            self.table_query_status_var.set("环境已切换，请重新输入表逻辑名并查询。")

        self.table_query_env_var.trace_add("write", lambda *_args: _on_table_query_env_changed())
        _update_table_query_env_label()

        self.table_query_info_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.table_query_info_var, foreground="#333").grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        body = ttk.LabelFrame(parent, text="字段列表", padding=4)
        body.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 4))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        tree_host = ttk.Frame(body)
        tree_host.grid(row=0, column=0, sticky="nsew")
        tree_host.rowconfigure(0, weight=1)
        tree_host.columnconfigure(0, weight=1)
        self.table_query_tree = self._create_table_query_field_tree(tree_host)

        self.table_query_pager = PaginationBar(
            body,
            page_size_options=(50, 100, 200),
            default_page_size=TABLE_QUERY_DEFAULT_PAGE_SIZE,
            on_change=self._render_table_query_page,
        )
        self.table_query_pager.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.table_query_status_var = tk.StringVar(value="请选择环境，输入表逻辑名后点击查询。")
        ttk.Label(parent, textvariable=self.table_query_status_var, foreground="#666").grid(
            row=4, column=0, sticky="w", padx=8, pady=(0, 8)
        )

        self._table_query_rows: List[Dict[str, Any]] = []
        self._table_query_sort_column = "logical_name"
        self._table_query_sort_reverse = False
        self._table_query_loading = False

    def _clear_table_query_results(self) -> None:
        if hasattr(self, "table_query_tree"):
            for item in self.table_query_tree.get_children():
                self.table_query_tree.delete(item)
        self._table_query_rows = []
        if hasattr(self, "table_query_pager"):
            self.table_query_pager.page = 1
            self.table_query_pager.set_total(0)
        if hasattr(self, "table_query_info_var"):
            self.table_query_info_var.set("")

    def _create_table_query_field_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        tree = ttk.Treeview(
            parent,
            columns=TABLE_QUERY_FIELD_COLUMNS,
            show="headings",
            selectmode="browse",
            height=18,
        )
        for col in TABLE_QUERY_FIELD_COLUMNS:
            tree.heading(
                col,
                text=TABLE_QUERY_FIELD_HEADINGS[col],
                command=lambda c=col: self._on_table_query_heading_click(c),
            )
            tree.column(
                col,
                width=TABLE_QUERY_FIELD_WIDTHS[col],
                stretch=(col in {"logical_name", "schema_name", "display_name_zh", "display_name_en"}),
            )
        tree.grid(row=0, column=0, sticky="nsew")
        tree_y = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree_x.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        return tree

    def _populate_table_query_tree(self, tree: ttk.Treeview, rows: List[Dict[str, Any]]) -> None:
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert(
                "",
                "end",
                values=(
                    row.get("logical_name", ""),
                    row.get("schema_name", ""),
                    row.get("display_name_zh", ""),
                    row.get("display_name_en", ""),
                    row.get("attribute_type", ""),
                    row.get("required_level", ""),
                    _bool_label(bool(row.get("is_custom"))),
                    _bool_label(bool(row.get("valid_for_create"))),
                    _bool_label(bool(row.get("valid_for_update"))),
                    _bool_label(bool(row.get("valid_for_read"))),
                ),
            )

    def _sort_table_query_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        column = getattr(self, "_table_query_sort_column", "logical_name")
        reverse = bool(getattr(self, "_table_query_sort_reverse", False))

        def key(row: Dict[str, Any]) -> Any:
            value = row.get(column, "")
            if isinstance(value, bool):
                return int(value)
            return str(value or "").lower()

        return sorted(rows, key=key, reverse=reverse)

    def _on_table_query_heading_click(self, column: str) -> None:
        if self._table_query_sort_column == column:
            self._table_query_sort_reverse = not self._table_query_sort_reverse
        else:
            self._table_query_sort_column = column
            self._table_query_sort_reverse = False
        if hasattr(self, "table_query_pager"):
            self.table_query_pager.page = 1
        self._render_table_query_page()

    def _get_table_query_filtered_rows(self) -> List[Dict[str, Any]]:
        keyword = self.table_query_filter_var.get().strip().lower()
        if not keyword:
            return self._sort_table_query_rows(list(self._table_query_rows))
        rows = [
            row
            for row in self._table_query_rows
            if keyword in row.get("logical_name", "").lower()
            or keyword in row.get("schema_name", "").lower()
            or keyword in row.get("display_name_zh", "").lower()
            or keyword in row.get("display_name_en", "").lower()
            or keyword in row.get("attribute_type", "").lower()
        ]
        return self._sort_table_query_rows(rows)

    def _render_table_query_page(self, page: Optional[int] = None) -> None:
        if not hasattr(self, "table_query_tree"):
            return
        if page is not None and hasattr(self, "table_query_pager"):
            self.table_query_pager.page = page

        filtered = self._get_table_query_filtered_rows()
        total = len(filtered)
        page_size = self.table_query_pager.page_size() if hasattr(self, "table_query_pager") else TABLE_QUERY_DEFAULT_PAGE_SIZE
        current_page = self.table_query_pager.page if hasattr(self, "table_query_pager") else 1
        if total <= 0:
            self._populate_table_query_tree(self.table_query_tree, [])
            if hasattr(self, "table_query_pager"):
                self.table_query_pager.set_total(0)
            return

        if hasattr(self, "table_query_pager"):
            self.table_query_pager.set_total(total)
            current_page = self.table_query_pager.page
        start = (max(1, current_page) - 1) * page_size
        page_rows = filtered[start : start + page_size]
        self._populate_table_query_tree(self.table_query_tree, page_rows)

        if hasattr(self, "table_query_status_var"):
            if self.table_query_filter_var.get().strip():
                self.table_query_status_var.set(
                    f"共 {len(self._table_query_rows)} 个字段，筛选后 {total} 个，当前第 {current_page}/{self.table_query_pager.page_count()} 页"
                )
            else:
                self.table_query_status_var.set(
                    f"共 {total} 个字段，当前第 {current_page}/{self.table_query_pager.page_count()} 页"
                )

    def _apply_table_query_filter(self) -> None:
        if not self._table_query_rows:
            return
        self._render_table_query_page(1)

    def _on_query_table_fields(self) -> None:
        entity_name = self.table_query_entity_var.get().strip().lower()
        if not entity_name:
            messagebox.showwarning("提示", "请先输入表逻辑名。", parent=self.root)
            return
        if not LOGICAL_NAME_RE.match(entity_name):
            messagebox.showwarning(
                "提示",
                "表逻辑名格式不正确，请使用精确的逻辑名（小写字母开头，仅含字母、数字、下划线）。",
                parent=self.root,
            )
            return
        if self._table_query_loading:
            return

        env_name = self.table_query_env_var.get().strip()
        env_url = self.table_query_env_map.get(env_name, {}).get("org_url", "")

        self._table_query_loading = True
        self.table_query_status_var.set(f"正在查询 [{env_name}] 表 [{entity_name}] 的字段及中英文显示名...")
        self.table_query_info_var.set("")
        self.root.update_idletasks()
        self._log_op(
            "table_query",
            "query_entity_fields",
            "started",
            f"开始查询表 [{entity_name}] 的字段",
            details={"entity": entity_name, "environment_name": env_name},
            environment_name=env_name,
            target_org_url=env_url,
            entity_name=entity_name,
        )

        def worker() -> None:
            try:
                creator = self._create_creator_for_environment(self._get_table_query_environment())
                entity_info = creator.get_entity_info(entity_name)
                attributes = creator.list_entity_attributes(entity_name)

                def on_done() -> None:
                    self._table_query_loading = False
                    self.op_logger.upsert_local_crm_table(
                        environment_name=env_name,
                        org_url=env_url,
                        entity_info=entity_info,
                        fields=attributes,
                    )
                    self._table_query_rows = attributes
                    self.table_query_filter_var.set("")
                    if hasattr(self, "table_query_pager"):
                        self.table_query_pager.page = 1
                    self._render_table_query_page()
                    info_parts = [
                        f"环境: {env_name}",
                        f"表: {entity_info['logical_name']}",
                        f"显示名: {_join_display_names(entity_info.get('display_name_zh', ''), entity_info.get('display_name_en', '')) or '-'}",
                        f"Schema: {entity_info['schema_name'] or '-'}",
                    ]
                    if entity_info.get("object_type_code") is not None:
                        info_parts.append(f"类型码: {entity_info['object_type_code']}")
                    info_parts.append("自定义表" if entity_info.get("is_custom_entity") else "系统表")
                    if entity_info.get("primary_id_attribute"):
                        info_parts.append(f"主键: {entity_info['primary_id_attribute']}")
                    if entity_info.get("primary_name_attribute"):
                        info_parts.append(f"主名称: {entity_info['primary_name_attribute']}")
                    self.table_query_info_var.set(" | ".join(info_parts))
                    self._append_log(f"已加载 [{env_name}] 表 [{entity_name}] 字段数量: {len(attributes)}")
                    self._log_op(
                        "table_query",
                        "query_entity_fields",
                        "success",
                        f"已查询表 [{entity_name}] 的字段，共 {len(attributes)} 个",
                        details={
                            "entity": entity_name,
                            "environment_name": env_name,
                            "entity_info": sanitize_details(entity_info),
                            "field_count": len(attributes),
                            "fields_preview": sanitize_details(attributes[:30]),
                        },
                        environment_name=env_name,
                        target_org_url=env_url,
                        entity_name=entity_name,
                    )

                self.root.after(0, on_done)
            except Exception as exc:
                err_msg = _format_exception(exc)

                def on_error(msg: str = err_msg) -> None:
                    self._table_query_loading = False
                    self._clear_table_query_results()
                    self.table_query_status_var.set(f"查询失败: {msg}")
                    self._append_log(f"查询 [{env_name}] 表 [{entity_name}] 字段失败: {msg}")
                    self._log_op(
                        "table_query",
                        "query_entity_fields",
                        "failed",
                        f"查询表 [{entity_name}] 字段失败",
                        details={"entity": entity_name, "environment_name": env_name},
                        environment_name=env_name,
                        target_org_url=env_url,
                        entity_name=entity_name,
                        error_message=msg,
                    )
                    messagebox.showerror("错误", msg, parent=self.root)

                self.root.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def _build_access_inspector_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self.access_panel = UserAccessPanel(self, parent)

    def _build_permission_panel(self, parent: ttk.Frame) -> None:
        environments = load_environments(self._get_config_path())
        env_map: Dict[str, Dict[str, str]] = {}
        env_names: List[str] = []
        for env in environments:
            name = env["name"]
            if name not in env_map:
                env_map[name] = env
                env_names.append(name)
        self.perm_env_map = env_map
        current_env = self._get_current_environment()

        cfg = load_config(self._get_config_path())
        default_env_name = str(cfg.get("environment_name", "")).strip()
        if default_env_name not in env_map and env_names:
            for env in environments:
                if env["org_url"].rstrip("/").lower() == current_env["org_url"].rstrip("/").lower():
                    default_env_name = env["name"]
                    break
            else:
                default_env_name = env_names[0]

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        header = ttk.Frame(parent, padding=(4, 4, 4, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="目标环境").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.perm_env_var = tk.StringVar(value=default_env_name)
        perm_env_url_var = tk.StringVar()
        perm_env_combo = ttk.Combobox(
            header,
            textvariable=self.perm_env_var,
            state="readonly",
            values=env_names,
            width=24,
        )
        perm_env_combo.grid(row=0, column=1, sticky="w")
        ttk.Label(header, textvariable=perm_env_url_var, foreground="#666").grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(16, 0)
        )
        ttk.Label(header, text="数据表（逻辑名）").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.perm_entity_var = tk.StringVar()
        perm_entity_entry = ttk.Entry(header, textvariable=self.perm_entity_var, width=40)
        perm_entity_entry.grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(header, text="复制来源表（逻辑名）").grid(row=1, column=2, sticky="w", padx=(16, 8), pady=(8, 0))
        self.perm_copy_entity_var = tk.StringVar(value="")
        perm_copy_entity_entry = ttk.Entry(header, textvariable=self.perm_copy_entity_var, width=40)
        perm_copy_entity_entry.grid(row=1, column=3, sticky="w", pady=(8, 0))

        def _update_perm_env_label(_event: Any = None) -> None:
            env = env_map.get(self.perm_env_var.get().strip(), current_env)
            perm_env_url_var.set(env.get("org_url", ""))

        def _permission_creator() -> D365FieldCreator:
            env = env_map.get(self.perm_env_var.get().strip(), current_env)
            return self._create_creator_for_environment(env)

        def _on_perm_env_changed(_event: Any = None) -> None:
            _update_perm_env_label()
            matrix_state["all_roles"] = None
            matrix_state["loaded_entity"] = ""
            matrix_state["roles"] = []
            matrix_state["original_matrix"] = {}
            matrix_state["current_matrix"] = {}
            matrix_state["privilege_map"] = {}
            _clear_grid()
            env_name = self.perm_env_var.get().strip()
            env_url = env_map.get(env_name, current_env).get("org_url", "")
            self._log_op(
                "permission",
                "switch_environment",
                "info",
                f"权限管理切换目标环境: {env_name}",
                details={"environment_name": env_name, "org_url": env_url},
                environment_name=env_name,
                target_org_url=env_url,
            )
            status_var.set(f"已切换至 [{env_name}]，请输入表逻辑名后点击「加载权限」")

        perm_env_combo.bind("<<ComboboxSelected>>", _on_perm_env_changed)
        _update_perm_env_label()

        toolbar = ttk.Frame(parent, padding=(4, 0, 4, 6))
        toolbar.grid(row=1, column=0, sticky="ew")
        status_var = tk.StringVar(value="请选择环境，输入表逻辑名后点击「加载权限」")
        role_filter_var = tk.StringVar()

        grid_wrap = ttk.Frame(parent, padding=(4, 0, 4, 4))
        grid_wrap.grid(row=2, column=0, sticky="nsew")
        grid_wrap.rowconfigure(1, weight=1)
        grid_wrap.columnconfigure(0, weight=1)

        perm_cell_size = 26
        perm_cell_step = 46
        perm_row_height = 32
        perm_role_col_width = 360
        perm_icons_width = len(ENTITY_ACCESS_RIGHT_COLUMNS) * perm_cell_step + 16

        header_bar = ttk.Frame(grid_wrap)
        header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(
            header_bar,
            text="安全角色",
            font=("", 9, "bold"),
            width=50,
        ).grid(row=0, column=0, sticky="w", padx=4)
        for col_index, (label, _) in enumerate(ENTITY_ACCESS_RIGHT_COLUMNS, start=1):
            ttk.Label(
                header_bar,
                text=label,
                font=("", 9, "bold"),
                width=6,
                anchor="center",
            ).grid(row=0, column=col_index, padx=2)

        body_wrap = ttk.Frame(grid_wrap)
        body_wrap.grid(row=1, column=0, sticky="nsew")
        body_wrap.rowconfigure(0, weight=1)
        body_wrap.columnconfigure(0, weight=1)

        body_canvas = tk.Canvas(body_wrap, highlightthickness=0)
        body_canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(body_wrap, orient="vertical", command=body_canvas.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        body_canvas.configure(yscrollcommand=y_scroll.set)

        body_host = ttk.Frame(body_canvas)
        body_window = body_canvas.create_window((0, 0), window=body_host, anchor="nw")

        def on_body_configure(_event: Any) -> None:
            body_canvas.configure(scrollregion=body_canvas.bbox("all"))

        # 让 body_canvas 始终铺满容器，减少右侧空白
        def on_body_canvas_configure(event: Any) -> None:
            body_canvas.itemconfigure(body_window, width=event.width)

        body_host.bind("<Configure>", on_body_configure)
        body_canvas.bind("<Configure>", on_body_canvas_configure)

        def on_perm_mousewheel(event: Any) -> None:
            if self._active_panel != "permission":
                return
            body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        body_canvas.bind("<MouseWheel>", on_perm_mousewheel)
        body_host.bind("<MouseWheel>", on_perm_mousewheel)

        matrix_state: Dict[str, Any] = {
            "privilege_map": {},
            "original_matrix": {},
            "current_matrix": {},
            "roles": [],
            "row_canvases": {},
            "loaded_entity": "",
            "all_roles": None,
            "render_token": 0,
            "loading": False,
        }

        legend = ttk.Frame(toolbar)
        legend.pack(side="left")
        ttk.Label(legend, text="图例:").pack(side="left", padx=(0, 6))
        for depth in PRIVILEGE_DEPTH_CYCLE[1:]:
            sample = tk.Canvas(legend, width=18, height=18, highlightthickness=0)
            _draw_privilege_depth_icon(sample, 18, depth)
            sample.pack(side="left", padx=(0, 2))
            ttk.Label(legend, text=PRIVILEGE_DEPTH_LABELS_CN[depth]).pack(side="left", padx=(0, 10))

        ttk.Label(toolbar, text="筛选角色").pack(side="left", padx=(12, 6))
        role_filter_entry = ttk.Entry(toolbar, textvariable=role_filter_var, width=18)
        role_filter_entry.pack(side="left")

        def _get_active_entity() -> str:
            return self.perm_entity_var.get().strip().lower()

        def _matrix_changed() -> bool:
            return matrix_state["current_matrix"] != matrix_state["original_matrix"]

        def _refresh_status() -> None:
            selected = _get_active_entity()
            loaded = matrix_state.get("loaded_entity", "")
            if not matrix_state["roles"]:
                status_var.set("尚未加载权限")
                return
            changed = _matrix_changed()
            entity_label = selected or loaded or "未选择"
            if selected and loaded and selected != loaded:
                status_var.set(
                    f"当前选择: {selected} | 矩阵来源: {loaded} | 角色数: {len(matrix_state['roles'])} | "
                    f"{'有未保存修改' if changed else '无修改'} | 保存将写入 [{selected}]"
                )
            else:
                status_var.set(
                    f"当前表: {entity_label} | 角色数: {len(matrix_state['roles'])} | "
                    f"{'有未保存修改' if changed else '无修改'}"
                )

        def _clear_grid() -> None:
            matrix_state["render_token"] += 1
            for child in body_host.winfo_children():
                child.destroy()
            matrix_state["row_canvases"] = {}

        def _render_grid() -> None:
            _clear_grid()
            roles = matrix_state["roles"]
            keyword = role_filter_var.get().strip().lower()
            filtered_roles = [
                role for role in roles if not keyword or keyword in role["name"].lower()
            ]
            privilege_map = matrix_state["privilege_map"]
            current_matrix = matrix_state["current_matrix"]
            render_token = matrix_state["render_token"]

            if not filtered_roles:
                if roles and keyword:
                    ttk.Label(body_host, text="没有匹配的角色").grid(row=0, column=0, sticky="w", padx=6)
                else:
                    ttk.Label(body_host, text="尚未加载权限").grid(row=0, column=0, sticky="w", padx=6)
                return

            def render_chunk(start_index: int) -> None:
                if render_token != matrix_state["render_token"]:
                    return
                chunk_size = 30
                end_index = min(start_index + chunk_size, len(filtered_roles))
                for row_index in range(start_index, end_index):
                    role = filtered_roles[row_index]
                    role_id = role["role_id"]
                    row_frame = ttk.Frame(body_host)
                    row_frame.grid(row=row_index, column=0, sticky="ew", pady=1)
                    ttk.Label(
                        row_frame,
                        text=role["name"],
                        width=50,
                    ).pack(side="left", padx=(4, 8))
                    row_canvas = tk.Canvas(
                        row_frame,
                        width=perm_icons_width,
                        height=perm_row_height,
                        highlightthickness=0,
                        cursor="hand2",
                    )
                    row_canvas.pack(side="left")
                    row_data = current_matrix.get(role_id, {})
                    _paint_permission_row_canvas(
                        row_canvas,
                        row_data,
                        privilege_map,
                        perm_cell_size,
                        perm_cell_step,
                    )

                    def make_row_click_handler(
                        bound_role_id: str, bound_canvas: tk.Canvas
                    ) -> Any:
                        def on_row_click(event: Any) -> None:
                            col = int((event.x - 6) // perm_cell_step)
                            if col < 0 or col >= len(ENTITY_ACCESS_RIGHT_COLUMNS):
                                return
                            verb = ENTITY_ACCESS_RIGHT_COLUMNS[col][1]
                            if verb not in privilege_map:
                                return
                            row_values = current_matrix.setdefault(bound_role_id, {})
                            row_values[verb] = _next_privilege_depth(row_values.get(verb))
                            _paint_permission_row_canvas(
                                bound_canvas,
                                row_values,
                                privilege_map,
                                perm_cell_size,
                                perm_cell_step,
                            )
                            _refresh_status()

                        return on_row_click

                    row_canvas.bind("<Button-1>", make_row_click_handler(role_id, row_canvas))
                    matrix_state["row_canvases"][role_id] = row_canvas

                if end_index < len(filtered_roles):
                    status_var.set(f"正在渲染界面 ({end_index}/{len(filtered_roles)})...")
                    self.root.after(1, lambda: render_chunk(end_index))
                else:
                    _refresh_status()

            status_var.set(f"正在渲染界面 (0/{len(filtered_roles)})...")
            render_chunk(0)

        def _apply_loaded_matrix(
            target_entity: str,
            roles: List[Dict[str, str]],
            privilege_map: Dict[str, str],
            matrix: Dict[str, Dict[str, Optional[int]]],
        ) -> None:
            matrix_state["privilege_map"] = privilege_map
            matrix_state["original_matrix"] = {
                role_id: dict(row) for role_id, row in matrix.items()
            }
            matrix_state["current_matrix"] = {
                role_id: dict(row) for role_id, row in matrix.items()
            }
            matrix_state["roles"] = roles
            matrix_state["loaded_entity"] = target_entity
            matrix_state["loading"] = False
            status_var.set(f"数据已加载，正在渲染 {len(roles)} 个角色...")
            parent.update_idletasks()
            _render_grid()
            _refresh_status()
            self._append_log(f"已加载表 [{target_entity}] 的权限矩阵，角色数: {len(roles)}")
            env_name = self.perm_env_var.get().strip()
            env_url = env_map.get(env_name, current_env).get("org_url", "")
            self._log_op(
                "permission",
                "load_permissions",
                "success",
                f"已加载表 [{target_entity}] 权限矩阵，角色数: {len(roles)}",
                details={
                    "entity": target_entity,
                    "role_count": len(roles),
                    "environment_name": env_name,
                    "privilege_verbs": list(privilege_map.keys()),
                },
                environment_name=env_name,
                target_org_url=env_url,
                entity_name=target_entity,
            )

        def load_permissions(entity_name: Optional[str] = None, force: bool = False) -> None:
            target_entity = (entity_name or _get_active_entity()).strip().lower()
            if not target_entity:
                self._append_log("权限加载失败：请输入数据表逻辑名。")
                return
            self.perm_entity_var.set(target_entity)
            if matrix_state["loading"]:
                return
            if (
                not force
                and _matrix_changed()
                and not messagebox.askyesno(
                    "未保存的修改",
                    "当前权限矩阵有未保存的修改，重新加载将丢失这些修改。是否继续？",
                    parent=self.root,
                )
            ):
                return

            matrix_state["loading"] = True
            status_var.set("正在后台加载权限...")
            parent.update_idletasks()
            env_name = self.perm_env_var.get().strip()
            env_url = env_map.get(env_name, current_env).get("org_url", "")
            self._log_op(
                "permission",
                "load_permissions",
                "started",
                f"开始加载表 [{target_entity}] 的权限",
                details={"entity": target_entity, "force_reload": force, "environment_name": env_name},
                environment_name=env_name,
                target_org_url=env_url,
                entity_name=target_entity,
            )

            def worker() -> None:
                try:
                    creator = _permission_creator()
                    creator.get_entity_info(target_entity)
                    if matrix_state["all_roles"] is None:
                        self.root.after(0, lambda: status_var.set("正在加载安全角色列表..."))
                        matrix_state["all_roles"] = creator.list_all_security_roles()
                    roles = list(matrix_state["all_roles"])
                    self.root.after(
                        0,
                        lambda: status_var.set(
                            f"正在加载表 [{target_entity}] 权限（{len(roles)} 个角色）..."
                        ),
                    )
                    privilege_map, matrix = creator.load_entity_permission_matrix(
                        entity_logical_name=target_entity,
                        roles=roles,
                    )

                    def on_done() -> None:
                        _apply_loaded_matrix(target_entity, roles, privilege_map, matrix)

                    self.root.after(0, on_done)
                except Exception as exc:
                    err_msg = _format_exception(exc)

                    def on_error(msg: str = err_msg) -> None:
                        matrix_state["loading"] = False
                        self._append_log(f"权限加载失败: {msg}")
                        self._log_op(
                            "permission",
                            "load_permissions",
                            "failed",
                            f"加载表 [{target_entity}] 权限失败",
                            details={"entity": target_entity, "force_reload": force},
                            environment_name=env_name,
                            target_org_url=env_url,
                            entity_name=target_entity,
                            error_message=msg,
                        )
                        messagebox.showerror("错误", msg, parent=self.root)

                    self.root.after(0, on_error)

            threading.Thread(target=worker, daemon=True).start()

        def copy_from_entity() -> None:
            source_entity = self.perm_copy_entity_var.get().strip().lower()
            target_entity = _get_active_entity()
            if not target_entity:
                self._append_log("请先输入要配置的数据表逻辑名。")
                return
            if not source_entity:
                self._append_log("请先输入复制来源表逻辑名。")
                return
            if not matrix_state["roles"]:
                self._append_log("请先点击“加载权限”。")
                return
            if source_entity == target_entity:
                self._append_log("复制来源表与当前表相同，无需复制。")
                return
            if not messagebox.askyesno(
                "确认复制",
                f"将把 [{source_entity}] 的权限复制到当前表 [{target_entity}] 的矩阵中。\n"
                "复制后需点击“保存修改”才会写入环境。\n\n是否继续？",
                parent=self.root,
            ):
                return
            env_name = self.perm_env_var.get().strip()
            env_url = env_map.get(env_name, current_env).get("org_url", "")
            self._log_op(
                "permission",
                "copy_permissions",
                "started",
                f"开始从 [{source_entity}] 复制权限到 [{target_entity}]",
                details={"source_entity": source_entity, "target_entity": target_entity},
                environment_name=env_name,
                target_org_url=env_url,
                entity_name=target_entity,
            )
            try:
                status_var.set(f"正在从 [{source_entity}] 复制权限到 [{target_entity}]...")
                parent.update_idletasks()

                def worker() -> None:
                    try:
                        creator = _permission_creator()
                        creator.get_entity_info(source_entity)
                        creator.get_entity_info(target_entity)
                        source_privilege_map, source_matrix = creator.load_entity_permission_matrix(
                            entity_logical_name=source_entity,
                            roles=matrix_state["roles"],
                        )
                        target_privilege_map = creator.get_entity_privilege_map(target_entity)

                        def on_done() -> None:
                            matrix_state["privilege_map"] = target_privilege_map
                            matrix_state["loaded_entity"] = target_entity
                            for role in matrix_state["roles"]:
                                role_id = role["role_id"]
                                source_row = source_matrix.get(role_id, {})
                                target_row = matrix_state["current_matrix"].setdefault(role_id, {})
                                for _, verb in ENTITY_ACCESS_RIGHT_COLUMNS:
                                    if verb in source_privilege_map and verb in target_privilege_map:
                                        target_row[verb] = source_row.get(verb)
                            _render_grid()
                            _refresh_status()
                            status_var.set(
                                f"已从 [{source_entity}] 复制到 [{target_entity}]，请点击“保存修改”写入环境。"
                            )
                            self._log_op(
                                "permission",
                                "copy_permissions",
                                "success",
                                f"已从 [{source_entity}] 复制权限到 [{target_entity}]",
                                details={"source_entity": source_entity, "target_entity": target_entity},
                                environment_name=env_name,
                                target_org_url=env_url,
                                entity_name=target_entity,
                            )

                        self.root.after(0, on_done)
                    except Exception as exc:
                        err_msg = _format_exception(exc)
                        self.root.after(
                            0,
                            lambda msg=err_msg: (
                                self._append_log(f"复制权限失败: {msg}"),
                                self._log_op(
                                    "permission",
                                    "copy_permissions",
                                    "failed",
                                    f"复制权限失败: {source_entity} -> {target_entity}",
                                    details={"source_entity": source_entity, "target_entity": target_entity},
                                    environment_name=env_name,
                                    target_org_url=env_url,
                                    entity_name=target_entity,
                                    error_message=msg,
                                ),
                            ),
                        )

                threading.Thread(target=worker, daemon=True).start()
            except Exception as e:
                self._append_log(f"复制权限失败: {_format_exception(e)}")

        def save_changes() -> None:
            target_entity = _get_active_entity()
            if not target_entity:
                self._append_log("请先输入要保存的数据表逻辑名。")
                return
            if not matrix_state["roles"]:
                self._append_log("请先加载权限。")
                return
            if not _matrix_changed():
                self._append_log("没有需要保存的修改。")
                return
            env_name = self.perm_env_var.get().strip()
            env_url = env_map.get(env_name, current_env).get("org_url", "")
            if not messagebox.askyesno(
                "确认保存",
                f"将把对表 [{target_entity}] 的权限修改保存到以下环境：\n"
                f"  {env_name}  ({env_url})\n\n是否继续？",
                parent=self.root,
            ):
                return

            self._log_op(
                "permission",
                "save_permissions",
                "started",
                f"开始保存表 [{target_entity}] 的权限到 {env_name}",
                details={"entity": target_entity, "environment_name": env_name, "org_url": env_url},
                environment_name=env_name,
                target_org_url=env_url,
                entity_name=target_entity,
            )
            status_var.set(f"正在准备保存到 [{target_entity}]（{env_name}）...")
            parent.update_idletasks()

            def worker() -> None:
                try:
                    creator = _permission_creator()
                    privilege_map = creator.get_entity_privilege_map(target_entity)
                    roles = matrix_state["roles"]
                    current_matrix = {
                        role_id: dict(row)
                        for role_id, row in matrix_state["current_matrix"].items()
                    }
                    current_matrix = creator.normalize_permission_matrix_role_ids(
                        roles, current_matrix
                    )
                    if matrix_state.get("loaded_entity") == target_entity:
                        original_matrix = {
                            role_id: dict(row)
                            for role_id, row in matrix_state["original_matrix"].items()
                        }
                        original_matrix = creator.normalize_permission_matrix_role_ids(
                            roles, original_matrix
                        )
                    else:
                        _, original_matrix = creator.load_entity_permission_matrix(
                            entity_logical_name=target_entity,
                            roles=roles,
                        )
                        original_matrix = creator.normalize_permission_matrix_role_ids(
                            roles, original_matrix
                        )

                    def on_save_progress(index: int, total: int) -> None:
                        text = f"正在保存到 [{target_entity}] ({index}/{total})..."
                        self.root.after(0, lambda t=text: status_var.set(t))

                    summary = creator.save_entity_permission_changes(
                        privilege_map=privilege_map,
                        original_matrix=original_matrix,
                        current_matrix=current_matrix,
                        progress_callback=on_save_progress,
                    )

                    def on_done() -> None:
                        matrix_state["privilege_map"] = privilege_map
                        matrix_state["original_matrix"] = {
                            role_id: dict(row) for role_id, row in current_matrix.items()
                        }
                        matrix_state["loaded_entity"] = target_entity
                        _refresh_status()
                        msg = (
                            f"保存完成。环境: {env_name} ({env_url}) | "
                            f"目标表: {target_entity} | "
                            f"更新角色数: {summary['roles_updated']} | "
                            f"变更权限项: {summary['privileges_changed']}"
                        )
                        if summary.get("roles_skipped"):
                            msg += f" | 跳过不可修改角色: {summary['roles_skipped']}"
                        self._append_log(msg)
                        status_var.set("权限保存成功")
                        self._log_op(
                            "permission",
                            "save_permissions",
                            "success",
                            msg,
                            details=sanitize_details(summary),
                            environment_name=env_name,
                            target_org_url=env_url,
                            entity_name=target_entity,
                        )

                    self.root.after(0, on_done)
                except Exception as exc:
                    err_msg = _format_exception(exc)
                    self.root.after(
                        0,
                        lambda msg=err_msg: (
                            self._append_log(f"权限保存失败: {msg}"),
                            self._log_op(
                                "permission",
                                "save_permissions",
                                "failed",
                                f"保存表 [{target_entity}] 权限失败",
                                details={"entity": target_entity, "environment_name": env_name},
                                environment_name=env_name,
                                target_org_url=env_url,
                                entity_name=target_entity,
                                error_message=msg,
                            ),
                        ),
                    )

            threading.Thread(target=worker, daemon=True).start()

        def on_entity_changed(_event: Any = None) -> None:
            selected = _get_active_entity()
            if not selected:
                return
            if selected == matrix_state.get("loaded_entity", ""):
                return
            if _matrix_changed():
                if not messagebox.askyesno(
                    "未保存的修改",
                    "切换数据表将丢失未保存的修改，是否继续？",
                    parent=self.root,
                ):
                    self.perm_entity_var.set(matrix_state["loaded_entity"] or "")
                    return
            matrix_state["loaded_entity"] = ""
            matrix_state["roles"] = []
            matrix_state["original_matrix"] = {}
            matrix_state["current_matrix"] = {}
            matrix_state["privilege_map"] = {}
            _clear_grid()
            status_var.set(f"已输入 [{selected}]，请点击「加载权限」")
            self._log_op(
                "permission",
                "select_entity",
                "info",
                f"权限管理切换数据表: {selected}",
                details={"entity": selected},
                entity_name=selected,
            )

        def on_filter_changed(_event: Any = None) -> None:
            _render_grid()

        def reload_permissions() -> None:
            self._log_op("permission", "reload_permissions", "started", "重新加载权限矩阵")
            matrix_state["all_roles"] = None
            load_permissions(force=True)

        ttk.Button(toolbar, text="加载权限", command=lambda: load_permissions()).pack(side="left", padx=(12, 6))
        ttk.Button(toolbar, text="从其他表复制", command=copy_from_entity).pack(side="left", padx=6)
        ttk.Button(toolbar, text="保存修改", command=save_changes).pack(side="left", padx=6)
        ttk.Button(toolbar, text="重新加载", command=reload_permissions).pack(side="left", padx=6)

        footer = ttk.Frame(parent, padding=(4, 0, 4, 4))
        footer.grid(row=3, column=0, sticky="ew")
        ttk.Label(footer, textvariable=status_var, foreground="#666").pack(side="left")
        ttk.Label(
            footer,
            text="提示：点击圆点可在 无 → 用户 → 部门 → 子部门 → 组织 之间切换",
            foreground="#666",
        ).pack(side="right")

        perm_entity_entry.bind("<Return>", lambda _e: load_permissions())
        perm_entity_entry.bind("<FocusOut>", on_entity_changed)
        role_filter_entry.bind("<KeyRelease>", on_filter_changed)

    def _refresh_deploy_panel(self) -> None:
        if not hasattr(self, "deploy_listbox"):
            return
        current_name = self.vars["solution_unique_name"].get().strip()
        if current_name and current_name not in self.deploy_solution_names:
            self.deploy_solution_names.insert(0, current_name)
            self._deploy_refresh_listbox()

    def _deploy_refresh_listbox(self) -> None:
        if not hasattr(self, "deploy_listbox"):
            return
        self.deploy_listbox.delete(0, tk.END)
        for index, name in enumerate(self.deploy_solution_names, start=1):
            self.deploy_listbox.insert(tk.END, f"{index}. {name}")

    def _build_deploy_panel(self, parent: ttk.Frame) -> None:
        environments = load_environments(self._get_config_path())
        current_env = self._get_current_environment()
        env_options = [current_env["name"]]
        env_map: Dict[str, Dict[str, str]] = {current_env["name"]: current_env}
        for env in environments:
            name = env["name"]
            if name not in env_map:
                env_options.append(name)
                env_map[name] = env

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        intro = ttk.Frame(parent, padding=(4, 4, 4, 4))
        intro.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            intro,
            text=f"源环境（当前连接）: {current_env['org_url']}",
            wraplength=720,
        ).pack(anchor="w")
        ttk.Label(
            intro,
            text="按列表顺序依次导出 → 导入 → 发布。可用于替代 n8n 发版流程，建议优先使用本工具完成 UAT 发版。",
            wraplength=720,
            foreground="#666",
        ).pack(anchor="w", pady=(4, 0))

        target_frame = ttk.Frame(parent, padding=(4, 0, 4, 4))
        target_frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(target_frame, text="目标环境").pack(side="left")
        target_var = tk.StringVar()
        target_names = [n for n in env_options if n != current_env["name"]]
        if not target_names:
            target_names = env_options
        if target_names:
            target_var.set(target_names[0])
        ttk.Combobox(
            target_frame,
            textvariable=target_var,
            state="readonly",
            values=target_names,
            width=40,
        ).pack(side="left", padx=(8, 0))

        solutions_frame = ttk.LabelFrame(parent, text="待发布解决方案（按顺序执行）", padding=4)
        solutions_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        solutions_frame.columnconfigure(0, weight=1)
        solutions_frame.rowconfigure(0, weight=1)

        self.deploy_solution_names: List[str] = []
        initial_name = self.vars["solution_unique_name"].get().strip()
        if initial_name:
            self.deploy_solution_names.append(initial_name)

        self.deploy_listbox = tk.Listbox(solutions_frame, height=10, exportselection=False)
        self.deploy_listbox.grid(row=0, column=0, rowspan=5, sticky="nsew", padx=(6, 4), pady=6)
        list_scroll = ttk.Scrollbar(solutions_frame, orient="vertical", command=self.deploy_listbox.yview)
        list_scroll.grid(row=0, column=1, rowspan=5, sticky="ns", pady=6)
        self.deploy_listbox.configure(yscrollcommand=list_scroll.set)

        btn_col = ttk.Frame(solutions_frame)
        btn_col.grid(row=0, column=2, sticky="n", padx=(4, 6), pady=6)

        add_entry_var = tk.StringVar()
        add_entry = ttk.Entry(btn_col, textvariable=add_entry_var, width=28)
        add_entry.pack(fill="x", pady=(0, 4))

        def add_solution_name(name: str) -> None:
            unique = name.strip()
            if not unique:
                return
            if unique in self.deploy_solution_names:
                self._append_log(f"解决方案 [{unique}] 已在发版列表中。")
                return
            self.deploy_solution_names.append(unique)
            self._deploy_refresh_listbox()
            self._log_op(
                "deploy",
                "add_solution",
                "success",
                f"已添加到发版列表: {unique}",
                details={"solution_name": unique, "list_size": len(self.deploy_solution_names)},
                solution_name=unique,
            )
            self.deploy_listbox.selection_clear(0, tk.END)
            self.deploy_listbox.selection_set(tk.END)
            self.deploy_listbox.see(tk.END)

        def add_from_entry() -> None:
            add_solution_name(add_entry_var.get())
            add_entry_var.set("")

        def add_current_solution() -> None:
            add_solution_name(self.vars["solution_unique_name"].get())

        def remove_selected() -> None:
            selection = self.deploy_listbox.curselection()
            if not selection:
                return
            del self.deploy_solution_names[selection[0]]
            self._deploy_refresh_listbox()

        def move_selected(delta: int) -> None:
            selection = self.deploy_listbox.curselection()
            if not selection:
                return
            index = selection[0]
            new_index = index + delta
            if new_index < 0 or new_index >= len(self.deploy_solution_names):
                return
            self.deploy_solution_names[index], self.deploy_solution_names[new_index] = (
                self.deploy_solution_names[new_index],
                self.deploy_solution_names[index],
            )
            self._deploy_refresh_listbox()
            self.deploy_listbox.selection_clear(0, tk.END)
            self.deploy_listbox.selection_set(new_index)
            self.deploy_listbox.see(new_index)

        ttk.Button(btn_col, text="添加", command=add_from_entry).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="添加当前解决方案", command=add_current_solution).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="删除选中", command=remove_selected).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="上移", command=lambda: move_selected(-1)).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="下移", command=lambda: move_selected(1)).pack(fill="x", pady=2)

        search_frame = ttk.Frame(solutions_frame)
        search_frame.grid(row=5, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 6))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="搜索源环境解决方案").grid(row=0, column=0, sticky="w")
        search_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=search_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        search_result_var = tk.StringVar()
        search_combo = ttk.Combobox(
            search_frame,
            textvariable=search_result_var,
            state="readonly",
            width=42,
        )
        search_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        search_items: List[Dict[str, str]] = []

        def search_source_solutions() -> None:
            keyword = search_var.get().strip()
            try:
                creator = self._create_creator()
                search_items[:] = creator.list_solutions(keyword=keyword, top=80)
                options = [
                    f"{item['unique_name']} | {item.get('friendly_name', '')}".strip(" |")
                    for item in search_items
                ]
                search_combo["values"] = options
                if options:
                    search_combo.current(0)
                else:
                    search_result_var.set("")
                    self._append_log("未找到匹配的解决方案。")
                self._log_op(
                    "deploy",
                    "search_solutions",
                    "success" if options else "info",
                    f"发版页搜索解决方案，关键字={keyword}，匹配 {len(options)} 条",
                    details={"keyword": keyword, "result_count": len(options)},
                )
            except Exception as exc:
                self._append_log(f"搜索解决方案失败: {_format_exception(exc)}")
                self._log_op(
                    "deploy",
                    "search_solutions",
                    "failed",
                    f"发版页搜索解决方案失败: {keyword}",
                    details={"keyword": keyword},
                    error_message=_format_exception(exc),
                )

        def add_from_search() -> None:
            index = search_combo.current()
            if index < 0 or index >= len(search_items):
                self._append_log("请先搜索并选择要添加的解决方案。")
                return
            add_solution_name(search_items[index]["unique_name"])

        ttk.Button(search_frame, text="搜索", command=search_source_solutions).grid(row=0, column=2)
        ttk.Button(search_frame, text="添加选中", command=add_from_search).grid(row=1, column=2, pady=(6, 0))

        self._deploy_refresh_listbox()

        option_frame = ttk.Frame(parent, padding=(4, 0, 4, 4))
        option_frame.grid(row=3, column=0, sticky="ew")
        managed_var = tk.BooleanVar(value=False)
        overwrite_var = tk.BooleanVar(value=True)
        save_local_var = tk.BooleanVar(value=False)
        publish_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(option_frame, text="导出为托管解决方案", variable=managed_var).pack(anchor="w")
        ttk.Checkbutton(option_frame, text="覆盖目标环境非托管自定义项", variable=overwrite_var).pack(anchor="w")
        ttk.Checkbutton(option_frame, text="每个解决方案导入后在目标环境发布（PublishAllXml）", variable=publish_var).pack(anchor="w")
        ttk.Checkbutton(option_frame, text="同时保存 zip 到本地目录（每个解决方案一个文件）", variable=save_local_var).pack(anchor="w")

        status_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=status_var, foreground="#666").grid(row=4, column=0, sticky="w", padx=8, pady=(4, 2))

        progress_frame = ttk.Frame(parent, padding=(4, 0, 4, 4))
        progress_frame.grid(row=5, column=0, sticky="ew")
        progress_frame.columnconfigure(0, weight=1)
        progress_bar = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        progress_bar.grid(row=0, column=0, sticky="ew")
        percent_var = tk.StringVar(value="0%")
        ttk.Label(progress_frame, textvariable=percent_var, width=6).grid(row=0, column=1, padx=(8, 0))

        deploy_state = {"running": False}

        def _set_determinate_progress(percent: float, message: str) -> None:
            status_var.set(message)
            value = max(0.0, min(100.0, percent))
            progress_bar.stop()
            progress_bar.configure(mode="determinate", maximum=100)
            progress_bar["value"] = value
            percent_var.set(f"{int(value)}%")

        def _finish_progress(success: bool) -> None:
            progress_bar.stop()
            if success:
                progress_bar.configure(mode="determinate", maximum=100)
                progress_bar["value"] = 100
                percent_var.set("100%")
            else:
                progress_bar["value"] = 0
                percent_var.set("0%")

        def _overall_progress(solution_index: int, total: int, import_pct: float) -> float:
            if total <= 0:
                return 0.0
            return max(0.0, min(100.0, ((solution_index + import_pct / 100.0) / total) * 100.0))

        def deploy() -> None:
            if deploy_state["running"]:
                return
            if not self.deploy_solution_names:
                self._append_log("请至少添加一个要发布的解决方案。")
                return
            target_name = target_var.get().strip()
            target_env = env_map.get(target_name)
            if not target_env:
                self._append_log("请选择目标环境。")
                return
            if target_env["org_url"].rstrip("/").lower() == current_env["org_url"].rstrip("/").lower():
                self._append_log("目标环境不能与当前源环境相同。")
                return

            preview = "\n".join([f"{idx + 1}. {name}" for idx, name in enumerate(self.deploy_solution_names)])
            if not messagebox.askyesno(
                "确认发版",
                f"将从\n{current_env['org_url']}\n按顺序发版以下 {len(self.deploy_solution_names)} 个解决方案到\n"
                f"{target_env['org_url']}\n\n{preview}\n\n是否继续？",
                parent=self.root,
            ):
                return

            deploy_options = {
                "managed": managed_var.get(),
                "overwrite_unmanaged": overwrite_var.get(),
                "publish_after_import": publish_var.get(),
                "save_local_zip": save_local_var.get(),
            }
            self._log_op(
                "deploy",
                "batch_deploy",
                "started",
                f"开始批量发版 {len(self.deploy_solution_names)} 个解决方案到 {target_name}",
                details={
                    "source_org_url": current_env["org_url"],
                    "target_environment": target_name,
                    "target_org_url": target_env["org_url"],
                    "solutions": list(self.deploy_solution_names),
                    "options": deploy_options,
                },
                target_org_url=target_env["org_url"],
                environment_name=target_name,
            )

            deploy_state["running"] = True
            deploy_btn.configure(state="disabled")
            _set_determinate_progress(0.0, "准备发版...")

            def worker() -> None:
                try:
                    source_creator = self._create_creator()
                    target_creator = self._create_creator_for_environment(target_env)
                    total = len(self.deploy_solution_names)
                    save_dir: Optional[str] = None

                    if save_local_var.get():
                        save_result: Dict[str, Optional[str]] = {"path": None}
                        save_event = threading.Event()

                        def ask_save_dir() -> None:
                            save_result["path"] = filedialog.askdirectory(
                                title="选择保存 zip 的本地目录",
                                parent=self.root,
                            )
                            save_event.set()

                        self.root.after(0, ask_save_dir)
                        save_event.wait(timeout=300)
                        save_dir = save_result["path"]
                        if not save_dir:
                            raise RuntimeError("未选择本地保存目录，已取消发版。")

                    completed: List[str] = []
                    for index, solution_name in enumerate(self.deploy_solution_names):
                        step_label = f"[{index + 1}/{total}] {solution_name}"

                        self.root.after(
                            0,
                            lambda i=index, label=step_label: _set_determinate_progress(
                                _overall_progress(i, total, 0.0),
                                f"{label} 正在从源环境导出...",
                            ),
                        )
                        solution_bytes = source_creator.export_solution(
                            solution_unique_name=solution_name,
                            managed=managed_var.get(),
                        )

                        if save_dir:
                            save_path = os.path.join(save_dir, f"{solution_name}.zip")
                            with open(save_path, "wb") as f:
                                f.write(solution_bytes)
                            self._append_log(f"解决方案已保存到本地: {save_path}")

                        def on_import_progress(
                            percent: float,
                            message: str,
                            i: int = index,
                            label: str = step_label,
                        ) -> None:
                            self.root.after(
                                0,
                                lambda p=percent, m=message, idx=i, lbl=label: _set_determinate_progress(
                                    _overall_progress(idx, total, p),
                                    f"{lbl} {m}",
                                ),
                            )

                        self.root.after(
                            0,
                            lambda i=index, label=step_label: _set_determinate_progress(
                                _overall_progress(i, total, 0.0),
                                f"{label} 正在导入到 {target_name}...",
                            ),
                        )
                        target_creator.import_solution(
                            solution_bytes=solution_bytes,
                            overwrite_unmanaged=overwrite_var.get(),
                            publish_workflows=True,
                            progress_callback=on_import_progress,
                        )

                        if publish_var.get():
                            self.root.after(
                                0,
                                lambda i=index, label=step_label: _set_determinate_progress(
                                    _overall_progress(i + 1, total, 0.0),
                                    f"{label} 正在发布自定义项...",
                                ),
                            )
                            target_creator.publish_all_customizations()

                        completed.append(solution_name)
                        self.root.after(
                            0,
                            lambda i=index, label=step_label: _set_determinate_progress(
                                _overall_progress(i + 1, total, 0.0),
                                f"{label} 已完成",
                            ),
                        )

                    summary_lines = "\n".join(
                        [f"{idx + 1}. {name}" for idx, name in enumerate(completed)]
                    )
                    msg = (
                        f"已成功发版 {len(completed)} 个解决方案到 "
                        f"{target_name} ({target_env['org_url']}): "
                        + summary_lines.replace("\n", " | ")
                    )

                    def on_done() -> None:
                        deploy_state["running"] = False
                        deploy_btn.configure(state="normal")
                        _finish_progress(True)
                        status_var.set("发版完成")
                        self._append_log(msg)
                        self._log_op(
                            "deploy",
                            "batch_deploy",
                            "success",
                            msg,
                            details={
                                "target_environment": target_name,
                                "target_org_url": target_env["org_url"],
                                "completed_solutions": completed,
                                "options": deploy_options,
                            },
                            target_org_url=target_env["org_url"],
                            environment_name=target_name,
                        )

                    self.root.after(0, on_done)
                except Exception as exc:
                    err_msg = _format_exception(exc)
                    if "AADSTS7000222" in err_msg or (
                        "invalid_client" in err_msg and "expired" in err_msg.lower()
                    ):
                        err_msg = (
                            f"连接目标环境 [{target_name}] 失败：应用密钥 (Client Secret) 已过期。\n\n"
                            f"请更新 config.json 中 environments 里 [{target_name}] 的 client_secret。\n\n"
                            f"原始错误：{err_msg}"
                        )

                    def on_error(msg: str = err_msg) -> None:
                        deploy_state["running"] = False
                        deploy_btn.configure(state="normal")
                        _finish_progress(False)
                        status_var.set("发版失败")
                        self._append_log(f"发版失败: {msg}")
                        self._log_op(
                            "deploy",
                            "batch_deploy",
                            "failed",
                            f"批量发版到 {target_name} 失败",
                            details={
                                "target_environment": target_name,
                                "target_org_url": target_env.get("org_url", ""),
                                "solutions": list(self.deploy_solution_names),
                                "options": deploy_options,
                            },
                            target_org_url=target_env.get("org_url", ""),
                            environment_name=target_name,
                            error_message=msg,
                        )

                    self.root.after(0, on_error)

            threading.Thread(target=worker, daemon=True).start()

        btn_bar = ttk.Frame(parent, padding=(4, 4, 4, 4))
        btn_bar.grid(row=6, column=0, sticky="w")
        deploy_btn = ttk.Button(btn_bar, text="开始发版", command=deploy)
        deploy_btn.pack(side="left")

    def _build_publish_history_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self.publish_history_panel = PublishHistoryPanel(self, parent)

    def _build_plugin_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.plugin_panel = PluginRegistrationPanel(self, parent)

    def _build_field_changes_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.field_changes_panel = D365ChangeHistoryPanel(self, parent, "field")

    def _build_plugin_changes_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.plugin_changes_panel = D365ChangeHistoryPanel(self, parent, "plugin")

    def _build_js_capture_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.js_capture_panel = JsCapturePanel(self, parent)

    def _build_translation_records_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent, padding=(4, 4, 4, 0))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(3, weight=1)

        ttk.Label(toolbar, text="语言").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.translation_record_lang_var = tk.StringVar(value="全部")
        ttk.Combobox(
            toolbar,
            textvariable=self.translation_record_lang_var,
            state="readonly",
            values=["全部"] + D365TranslationManager.AVAILABLE_LANGUAGES,
            width=14,
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))

        ttk.Label(toolbar, text="搜索").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.translation_record_keyword_var = tk.StringVar()
        keyword_entry = ttk.Entry(toolbar, textvariable=self.translation_record_keyword_var)
        keyword_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        keyword_entry.bind("<Return>", lambda _event: self._refresh_translation_records_panel())

        ttk.Label(toolbar, text="条数").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self.translation_record_limit_var = tk.StringVar(value="300")
        ttk.Entry(toolbar, textvariable=self.translation_record_limit_var, width=7).grid(
            row=0, column=5, sticky="w", padx=(0, 8)
        )
        ttk.Button(toolbar, text="刷新", command=self._refresh_translation_records_panel).grid(
            row=0, column=6, sticky="w"
        )

        self.translation_record_stats_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.translation_record_stats_var, foreground="#666").grid(
            row=1, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        body = ttk.LabelFrame(parent, text="翻译记录列表", padding=4)
        body.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        columns = ("updated_at", "source_text", "lang_name", "translated_text", "hit_count")
        self.translation_records_tree = ttk.Treeview(
            body,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=18,
        )
        self.translation_records_tree.heading("updated_at", text="更新时间", command=lambda: self._sort_treeview_column(self.translation_records_tree, "updated_at", False))
        self.translation_records_tree.heading("source_text", text="源文本", command=lambda: self._sort_treeview_column(self.translation_records_tree, "source_text", False))
        self.translation_records_tree.heading("lang_name", text="语言", command=lambda: self._sort_treeview_column(self.translation_records_tree, "lang_name", False))
        self.translation_records_tree.heading("translated_text", text="翻译结果", command=lambda: self._sort_treeview_column(self.translation_records_tree, "translated_text", False))
        self.translation_records_tree.heading("hit_count", text="命中", command=lambda: self._sort_treeview_column(self.translation_records_tree, "hit_count", False, numeric=True))
        self.translation_records_tree.column("updated_at", width=150, stretch=False)
        self.translation_records_tree.column("source_text", width=180, stretch=False)
        self.translation_records_tree.column("lang_name", width=120, stretch=False)
        self.translation_records_tree.column("translated_text", width=520, stretch=True)
        self.translation_records_tree.column("hit_count", width=60, stretch=False, anchor="center")
        self.translation_records_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(body, orient="vertical", command=self.translation_records_tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(body, orient="horizontal", command=self.translation_records_tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.translation_records_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.translation_records_pager = PaginationBar(parent, default_page_size=2000, on_change=self._refresh_translation_records_panel)
        self.translation_records_pager.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))

    def _refresh_translation_records_panel(self) -> None:
        if not hasattr(self, "translation_records_tree") or not hasattr(self, "op_logger"):
            return
        if hasattr(self, "translation_records_pager"):
            limit = self.translation_records_pager.page_size()
            offset = self.translation_records_pager.offset()
        else:
            try:
                limit = max(1, min(5000, int(self.translation_record_limit_var.get().strip() or "300")))
            except ValueError:
                limit = 300
                self.translation_record_limit_var.set("300")
            offset = 0
        keyword = self.translation_record_keyword_var.get().strip()
        lang = self.translation_record_lang_var.get().strip()
        lang_filter = lang if lang and lang != "全部" else None
        rows = self.op_logger.query_translation_cache(
            limit=limit,
            offset=offset,
            keyword=keyword or None,
            lang_name=lang_filter,
        )

        for item in self.translation_records_tree.get_children():
            self.translation_records_tree.delete(item)
        for row in rows:
            source_text = str(row.get("source_text", ""))
            translated_text = str(row.get("translated_text", ""))
            if len(source_text) > 80:
                source_text = source_text[:77] + "..."
            if len(translated_text) > 180:
                translated_text = translated_text[:177] + "..."
            self.translation_records_tree.insert(
                "",
                "end",
                values=(
                    self._format_log_time(str(row.get("updated_at", ""))),
                    source_text,
                    str(row.get("lang_label", "") or row.get("lang_name", "")),
                    translated_text,
                    str(row.get("hit_count", 0)),
                ),
            )
        total = self.op_logger.count_translation_cache(keyword=keyword or None, lang_name=lang_filter)
        if hasattr(self, "translation_records_pager"):
            self.translation_records_pager.set_total(total)
        hint_parts = []
        if lang_filter:
            hint_parts.append(f"语言={lang}")
        if keyword:
            hint_parts.append(f"搜索={keyword}")
        hint = f" | {' '.join(hint_parts)}" if hint_parts else ""
        self.translation_record_stats_var.set(
            f"显示 {len(rows)} 条 / 筛选共 {total} 条{hint} | {self.op_logger.db_path}"
        )

    def _sort_treeview_column(self, tree: ttk.Treeview, column: str, reverse: bool, numeric: bool = False) -> None:
        def sort_key(item_id: str) -> Any:
            value = tree.set(item_id, column)
            if numeric:
                try:
                    return float(value)
                except ValueError:
                    return 0
            return str(value or "").lower()

        items = list(tree.get_children(""))
        items.sort(key=sort_key, reverse=reverse)
        for index, item_id in enumerate(items):
            tree.move(item_id, "", index)
        tree.heading(
            column,
            command=lambda: self._sort_treeview_column(tree, column, not reverse, numeric=numeric),
        )

    def _format_log_time(self, iso_text: str) -> str:
        text = (iso_text or "").strip()
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
            dt = dt + timedelta(hours=8)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            raw = iso_text[:19].replace("T", " ")
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S") + timedelta(hours=8)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                return raw

    def _format_log_detail(self, row: Dict[str, Any]) -> str:
        lines = [
            f"编号: {row.get('id', '')}",
            f"时间: {self._format_log_time(str(row.get('created_at', '')))}  ({row.get('created_at', '')})",
            f"分类: {LOG_CATEGORY_LABELS.get(str(row.get('category', '')), row.get('category', ''))} / {row.get('action', '')}",
            f"状态: {LOG_STATUS_LABELS.get(str(row.get('status', '')), row.get('status', ''))}",
            f"摘要: {row.get('summary', '')}",
        ]
        if row.get("org_url"):
            lines.append(f"源环境: {row['org_url']}")
        if row.get("target_org_url"):
            lines.append(f"目标环境: {row['target_org_url']}")
        if row.get("environment_name"):
            lines.append(f"环境名称: {row['environment_name']}")
        if row.get("solution_name"):
            lines.append(f"解决方案: {row['solution_name']}")
        if row.get("entity_name"):
            lines.append(f"实体: {row['entity_name']}")
        if row.get("duration_ms") is not None:
            lines.append(f"耗时: {row['duration_ms']} ms")
        if row.get("error_message"):
            lines.append(f"错误: {row['error_message']}")
        if row.get("session_id"):
            lines.append(f"会话 ID: {row['session_id']}")
        details_json = row.get("details_json") or ""
        if details_json:
            lines.append("")
            lines.append("详细参数:")
            try:
                details_obj = json.loads(details_json)
                lines.append(json.dumps(details_obj, ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                lines.append(str(details_json))
        return "\n".join(lines)

    def _refresh_logs_panel(self) -> None:
        if not hasattr(self, "logs_tree") or not hasattr(self, "op_logger"):
            return
        category_raw = self.log_filter_category.get().strip()
        status_raw = self.log_filter_status.get().strip()
        keyword = self.log_filter_keyword.get().strip()
        if hasattr(self, "logs_pager"):
            limit = self.logs_pager.page_size()
            offset = self.logs_pager.offset()
        else:
            try:
                limit = max(1, min(2000, int(self.log_filter_limit.get().strip() or "200")))
            except ValueError:
                limit = 200
                self.log_filter_limit.set("200")
            offset = 0

        category = None
        for key, label in LOG_CATEGORY_LABELS.items():
            if category_raw == label:
                category = key
                break
        if category_raw and category_raw != "全部" and category is None and category_raw in LOG_CATEGORY_LABELS:
            category = category_raw

        status = None
        for key, label in LOG_STATUS_LABELS.items():
            if status_raw == label:
                status = key
                break
        if status_raw and status_raw != "全部" and status is None and status_raw in LOG_STATUS_LABELS:
            status = status_raw

        category_filter = category if category_raw != "全部" else None
        status_filter = status if status_raw != "全部" else None
        rows = self.op_logger.query_operations(
            limit=limit,
            offset=offset,
            category=category_filter,
            status=status_filter,
            keyword=keyword or None,
        )

        selected_id = ""
        selection = self.logs_tree.selection()
        if selection:
            selected_id = selection[0]

        for item in self.logs_tree.get_children():
            self.logs_tree.delete(item)
        self._logs_data_map.clear()

        for row in rows:
            op_id = str(row["id"])
            cat_label = LOG_CATEGORY_LABELS.get(str(row.get("category", "")), str(row.get("category", "")))
            status_label = LOG_STATUS_LABELS.get(str(row.get("status", "")), str(row.get("status", "")))
            summary = str(row.get("summary", ""))
            if len(summary) > 120:
                summary = summary[:117] + "..."
            self.logs_tree.insert(
                "",
                "end",
                iid=op_id,
                values=(
                    self._format_log_time(str(row.get("created_at", ""))),
                    cat_label,
                    str(row.get("action", "")),
                    status_label,
                    summary,
                ),
                tags=(str(row.get("status", "")),),
            )
            self._logs_data_map[op_id] = row

        filtered_total = self.op_logger.count_operations(
            category=category_filter,
            status=status_filter,
            keyword=keyword or None,
        )
        if hasattr(self, "logs_pager"):
            self.logs_pager.set_total(filtered_total)
        stats = self.op_logger.get_statistics()
        filter_hint = []
        if category_raw != "全部":
            filter_hint.append(f"分类={category_raw}")
        if status_raw != "全部":
            filter_hint.append(f"状态={status_raw}")
        if keyword:
            filter_hint.append(f"关键字={keyword}")
        hint = f" | {' '.join(filter_hint)}" if filter_hint else ""
        self.log_stats_var.set(
            f"显示 {len(rows)} 条 / 筛选共 {filtered_total} 条 / 数据库共 {stats.get('total_operations', 0)} 条{hint} | {self.op_logger.db_path}"
        )

        if selected_id in self._logs_data_map:
            self.logs_tree.selection_set(selected_id)
            self.logs_tree.see(selected_id)
            self._show_log_detail(selected_id)
        else:
            self._show_log_detail("")

    def _show_log_detail(self, op_id: str) -> None:
        if not hasattr(self, "log_detail_text"):
            return
        self.log_detail_text.configure(state="normal")
        self.log_detail_text.delete("1.0", "end")
        if op_id and op_id in self._logs_data_map:
            self.log_detail_text.insert("1.0", self._format_log_detail(self._logs_data_map[op_id]))
        else:
            self.log_detail_text.insert("1.0", "请选择一条日志查看详细信息。")
        self.log_detail_text.configure(state="disabled")

    def _on_log_row_selected(self, _event: Any = None) -> None:
        selection = self.logs_tree.selection()
        if not selection:
            self._show_log_detail("")
            return
        self._show_log_detail(selection[0])

    def _build_logs_panel(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent, padding=(4, 4, 4, 0))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(7, weight=1)

        ttk.Label(toolbar, text="分类").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.log_filter_category = tk.StringVar(value="全部")
        category_values = ["全部"] + list(LOG_CATEGORY_LABELS.values())
        ttk.Combobox(
            toolbar,
            textvariable=self.log_filter_category,
            state="readonly",
            values=category_values,
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))

        ttk.Label(toolbar, text="状态").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.log_filter_status = tk.StringVar(value="全部")
        status_values = ["全部"] + list(LOG_STATUS_LABELS.values())
        ttk.Combobox(
            toolbar,
            textvariable=self.log_filter_status,
            state="readonly",
            values=status_values,
            width=8,
        ).grid(row=0, column=3, sticky="w", padx=(0, 12))

        ttk.Label(toolbar, text="条数").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self.log_filter_limit = tk.StringVar(value="200")
        ttk.Entry(toolbar, textvariable=self.log_filter_limit, width=6).grid(
            row=0, column=5, sticky="w", padx=(0, 12)
        )

        ttk.Label(toolbar, text="搜索").grid(row=0, column=6, sticky="w", padx=(0, 4))
        self.log_filter_keyword = tk.StringVar()
        keyword_entry = ttk.Entry(toolbar, textvariable=self.log_filter_keyword)
        keyword_entry.grid(row=0, column=7, sticky="ew", padx=(0, 8))
        keyword_entry.bind("<Return>", lambda _e: self._refresh_logs_panel())

        ttk.Button(toolbar, text="刷新", command=self._refresh_logs_panel).grid(row=0, column=8, padx=(0, 8))
        ttk.Button(
            toolbar,
            text="当前会话",
            command=lambda: self._refresh_logs_panel_for_session(self._session_id),
        ).grid(row=0, column=9)

        self.log_stats_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.log_stats_var, foreground="#666").grid(
            row=1, column=0, sticky="w", padx=8, pady=(0, 4)
        )

        body = ttk.Frame(parent)
        body.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)
        body.columnconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(body, text="日志列表", padding=4)
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        columns = ("time", "category", "action", "status", "summary")
        self.logs_tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=16,
        )
        self.logs_tree.heading("time", text="时间")
        self.logs_tree.heading("category", text="分类")
        self.logs_tree.heading("action", text="动作")
        self.logs_tree.heading("status", text="状态")
        self.logs_tree.heading("summary", text="摘要")
        self.logs_tree.column("time", width=140, stretch=False)
        self.logs_tree.column("category", width=90, stretch=False)
        self.logs_tree.column("action", width=120, stretch=False)
        self.logs_tree.column("status", width=60, stretch=False)
        self.logs_tree.column("summary", width=420, stretch=True)
        self.logs_tree.grid(row=0, column=0, sticky="nsew")
        logs_yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.logs_tree.yview)
        logs_yscroll.grid(row=0, column=1, sticky="ns")
        logs_xscroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.logs_tree.xview)
        logs_xscroll.grid(row=1, column=0, sticky="ew")
        self.logs_tree.configure(yscrollcommand=logs_yscroll.set, xscrollcommand=logs_xscroll.set)
        self.logs_tree.bind("<<TreeviewSelect>>", self._on_log_row_selected)

        self.logs_tree.tag_configure("success", foreground="#1a7f37")
        self.logs_tree.tag_configure("failed", foreground="#cf222e")
        self.logs_tree.tag_configure("started", foreground="#0969da")
        self.logs_tree.tag_configure("cancelled", foreground="#9a6700")
        self.logs_tree.tag_configure("info", foreground="#57606a")

        detail_frame = ttk.LabelFrame(body, text="详细信息", padding=4)
        detail_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)

        self.log_detail_text = tk.Text(detail_frame, height=10, wrap="word")
        self.log_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.log_detail_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.log_detail_text.configure(yscrollcommand=detail_scroll.set, state="disabled")

        self._logs_data_map: Dict[str, Dict[str, Any]] = {}

        self.logs_pager = PaginationBar(parent, default_page_size=2000, on_change=self._refresh_logs_panel)
        self.logs_pager.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))

    def _refresh_logs_panel_for_session(self, session_id: str) -> None:
        if not hasattr(self, "logs_tree") or not hasattr(self, "op_logger"):
            return
        rows = self.op_logger.query_operations(limit=500, session_id=session_id)
        for item in self.logs_tree.get_children():
            self.logs_tree.delete(item)
        self._logs_data_map.clear()
        for row in rows:
            op_id = str(row["id"])
            cat_label = LOG_CATEGORY_LABELS.get(str(row.get("category", "")), str(row.get("category", "")))
            status_label = LOG_STATUS_LABELS.get(str(row.get("status", "")), str(row.get("status", "")))
            summary = str(row.get("summary", ""))
            if len(summary) > 120:
                summary = summary[:117] + "..."
            self.logs_tree.insert(
                "",
                "end",
                iid=op_id,
                values=(
                    self._format_log_time(str(row.get("created_at", ""))),
                    cat_label,
                    str(row.get("action", "")),
                    status_label,
                    summary,
                ),
                tags=(str(row.get("status", "")),),
            )
            self._logs_data_map[op_id] = row
        self.log_stats_var.set(f"当前会话 {len(rows)} 条 | 会话 ID: {session_id}")

    def _append_log(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")
        if hasattr(self, "op_logger"):
            self._log_op(
                "ui",
                "message",
                "info",
                text[:500],
                details={"message": text, "active_panel": self._active_panel},
            )

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if getattr(sys, "frozen", False):
        default_config_path = str(Path(sys.executable).with_name("config.json"))
    else:
        default_config_path = str(Path(__file__).with_name("config.json"))

    parser = argparse.ArgumentParser(description="Create D365 fields from a schema JSON file")
    parser.add_argument("--tenant-id")
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument("--org-url", help="Example: https://orgname.crm.dynamics.com")
    parser.add_argument("--schema-file", help="Path to schema json")
    parser.add_argument("--config", default=default_config_path, help="Path to config json")
    parser.add_argument("--gui", action="store_true", help="Launch GUI form")
    args = parser.parse_args()

    if args.gui or len(__import__("sys").argv) == 1:
        FieldCreatorGUI(default_config_path=args.config).run()
        return

    cfg: Dict[str, Any] = load_config(args.config)

    tenant_id = args.tenant_id or cfg.get("tenant_id")
    client_id = args.client_id or cfg.get("client_id")
    client_secret = args.client_secret or cfg.get("client_secret")
    org_url = args.org_url or cfg.get("org_url")
    schema_file = args.schema_file or cfg.get("schema_file")

    missing = []
    if not tenant_id:
        missing.append("tenant_id")
    if not client_id:
        missing.append("client_id")
    if not client_secret:
        missing.append("client_secret")
    if not org_url:
        missing.append("org_url")
    if not schema_file:
        missing.append("schema_file")
    if missing:
        raise ValueError(
            "Missing required settings: "
            + ", ".join(missing)
            + ". Provide command args or fill config.json."
        )

    auth = D365Auth(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        org_url=org_url,
    )
    creator = D365FieldCreator(auth=auth, schema_file=schema_file)
    creator.run()


if __name__ == "__main__":
    main()
