"""用户 → 角色 → 实体权限追溯 — Web API 封装。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

_PRIVILEGE_VERBS: Tuple[str, ...] = (
    "AppendTo",
    "Append",
    "Create",
    "Read",
    "Write",
    "Delete",
    "Assign",
    "Share",
)

ENTITY_SCOPE_OPTIONS: Tuple[str, ...] = (
    "显示所有表",
    "仅显示分配的表",
    "仅显示未分配的表",
)

_PRIVILEGE_NAME_RE = re.compile(
    r"^prv(AppendTo|Append|Create|Read|Write|Delete|Assign|Share)(.+)$"
)


def _parse_privilege_name(name: str) -> Optional[Tuple[str, str]]:
    match = _PRIVILEGE_NAME_RE.match(str(name or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2)


class D365AccessManager:
    def __init__(self, creator: Any) -> None:
        self.creator = creator
        self._entity_by_schema: Optional[Dict[str, Dict[str, str]]] = None
        self._privilege_by_id: Optional[Dict[str, Tuple[str, str]]] = None

    @property
    def api_base(self) -> str:
        return self.creator.api_base

    @property
    def headers(self) -> Dict[str, str]:
        return self.creator.headers

    def _get_json_pages(self, start_url: str, timeout: int = 120) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        url: Optional[str] = start_url
        while url:
            resp = requests.get(url, headers=self.headers, timeout=timeout)
            if resp.status_code >= 400:
                raise RuntimeError(f"GET failed: HTTP {resp.status_code}, {resp.text}")
            body = resp.json()
            rows.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
        return rows

    def list_users(
        self,
        keyword: str = "",
        only_enabled: bool = True,
        max_users: int = 3000,
    ) -> List[Dict[str, str]]:
        filters: List[str] = []
        if only_enabled:
            filters.append("isdisabled eq false")
        kw = keyword.strip()
        if kw:
            kw_safe = kw.replace("'", "''")
            filters.append(
                "("
                f"contains(fullname,'{kw_safe}') or "
                f"contains(domainname,'{kw_safe}') or "
                f"contains(internalemailaddress,'{kw_safe}')"
                ")"
            )
        filter_expr = " and ".join(filters)
        select = (
            "systemuserid,fullname,domainname,internalemailaddress,jobtitle,isdisabled,"
            "_businessunitid_value"
        )
        url = (
            f"{self.api_base}/systemusers?$select={select}"
            f"&$expand=businessunitid($select=name)"
            f"&$orderby=fullname asc"
        )
        if filter_expr:
            url += f"&$filter={filter_expr}"
        users: List[Dict[str, str]] = []
        next_url: Optional[str] = url
        while next_url and len(users) < max_users:
            resp = requests.get(next_url, headers=self.headers, timeout=120)
            if resp.status_code >= 400:
                raise RuntimeError(f"加载用户失败: HTTP {resp.status_code}, {resp.text}")
            body = resp.json()
            for row in body.get("value", []):
                user_id = str(row.get("systemuserid", "")).strip()
                fullname = str(row.get("fullname", "")).strip()
                if not user_id or not fullname:
                    continue
                business_unit = ""
                bu = row.get("businessunitid")
                if isinstance(bu, dict):
                    business_unit = str(bu.get("name", "")).strip()
                users.append(
                    {
                        "user_id": user_id,
                        "fullname": fullname,
                        "domainname": str(row.get("domainname", "")).strip(),
                        "email": str(row.get("internalemailaddress", "")).strip(),
                        "jobtitle": str(row.get("jobtitle", "")).strip(),
                        "business_unit": business_unit,
                        "isdisabled": str(row.get("isdisabled", False)).lower(),
                    }
                )
                if len(users) >= max_users:
                    break
            next_url = body.get("@odata.nextLink")
        return users

    def get_user_roles(self, user_id: str) -> List[Dict[str, str]]:
        uid = user_id.strip().strip("{}")
        url = (
            f"{self.api_base}/systemusers({uid})?"
            f"$select=systemuserid,fullname&"
            f"$expand=systemuserroles_association($select=roleid,name)"
        )
        resp = requests.get(url, headers=self.headers, timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f"加载用户角色失败: HTTP {resp.status_code}, {resp.text}")
        roles_raw = resp.json().get("systemuserroles_association", []) or []
        roles: List[Dict[str, str]] = []
        seen: set = set()
        for row in roles_raw:
            role_id = str(row.get("roleid", "")).strip()
            name = str(row.get("name", "")).strip()
            if not role_id or not name:
                continue
            key = role_id.lower()
            if key in seen:
                continue
            seen.add(key)
            roles.append({"role_id": role_id, "name": name})
        roles.sort(key=lambda item: item["name"].lower())
        return roles

    def _ensure_entity_index(self) -> Dict[str, Dict[str, str]]:
        from d365_field_creator import _has_chinese_char, _parse_display_name_labels

        if self._entity_by_schema is not None:
            return self._entity_by_schema
        index: Dict[str, Dict[str, str]] = {}
        headers = dict(self.headers)
        headers["Accept-Language"] = "zh-CN,en-US"
        url: Optional[str] = (
            f"{self.api_base}/EntityDefinitions?$select=LogicalName,SchemaName,DisplayName"
        )
        while url:
            resp = requests.get(url, headers=headers, timeout=120)
            if resp.status_code >= 400:
                raise RuntimeError(f"加载实体元数据失败: HTTP {resp.status_code}, {resp.text}")
            body = resp.json()
            for row in body.get("value", []):
                logical_name = str(row.get("LogicalName", "")).strip()
                schema_name = str(row.get("SchemaName", "")).strip()
                if not logical_name or not schema_name:
                    continue
                display_name_zh, display_name_en = _parse_display_name_labels(row.get("DisplayName"))
                if not display_name_zh and display_name_en and _has_chinese_char(display_name_en):
                    display_name_zh = display_name_en
                display_name = display_name_zh or display_name_en or logical_name
                index[schema_name.lower()] = {
                    "logical_name": logical_name,
                    "schema_name": schema_name,
                    "display_name": display_name,
                    "display_name_zh": display_name_zh,
                    "display_name_en": display_name_en,
                }
            url = body.get("@odata.nextLink")
        self._entity_by_schema = index
        return index

    def _ensure_privilege_index(self) -> Dict[str, Tuple[str, str]]:
        if self._privilege_by_id is not None:
            return self._privilege_by_id
        index: Dict[str, Tuple[str, str]] = {}
        url: Optional[str] = f"{self.api_base}/privileges?$select=privilegeid,name"
        while url:
            resp = requests.get(url, headers=self.headers, timeout=120)
            if resp.status_code >= 400:
                raise RuntimeError(f"加载权限定义失败: HTTP {resp.status_code}, {resp.text}")
            body = resp.json()
            for row in body.get("value", []):
                pid = str(row.get("privilegeid", "")).strip().lower()
                name = str(row.get("name", "")).strip()
                if not pid or not name:
                    continue
                parsed = _parse_privilege_name(name)
                if parsed:
                    index[pid] = parsed
            url = body.get("@odata.nextLink")
        self._privilege_by_id = index
        return index

    def clear_caches(self) -> None:
        self._entity_by_schema = None
        self._privilege_by_id = None

    def _get_role_privileges(self, role_id: str) -> Dict[str, int]:
        from d365_field_creator import _mask_to_privilege_depth

        rid = role_id.strip().strip("{}")
        role_ids_to_try: List[str] = []
        for candidate in (rid,):
            if candidate and candidate.lower() not in {x.lower() for x in role_ids_to_try}:
                role_ids_to_try.append(candidate)
        try:
            resolved = self.creator.resolve_modifiable_role_id(role_id).strip().strip("{}")
            if resolved and resolved.lower() not in {x.lower() for x in role_ids_to_try}:
                role_ids_to_try.append(resolved)
        except RuntimeError:
            pass

        result: Dict[str, int] = {}
        for try_rid in role_ids_to_try:
            url: Optional[str] = (
                f"{self.api_base}/roleprivilegescollection?"
                f"$select=roleid,privilegeid,privilegedepthmask&"
                f"$filter=roleid eq {try_rid}"
            )
            while url:
                resp = requests.get(url, headers=self.headers, timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(f"加载角色权限失败: HTTP {resp.status_code}, {resp.text}")
                body = resp.json()
                for row in body.get("value", []):
                    pid = str(row.get("privilegeid", "")).strip().lower()
                    depth = _mask_to_privilege_depth(row.get("privilegedepthmask"))
                    if not pid or depth is None:
                        continue
                    existing = result.get(pid)
                    if existing is None or depth > existing:
                        result[pid] = depth
                url = body.get("@odata.nextLink")
            if result:
                break
        return result

    def _build_assigned_entities(self, role_privileges: Dict[str, int]) -> Dict[str, Dict[str, Any]]:
        from d365_field_creator import ENTITY_ACCESS_RIGHT_COLUMNS

        privilege_index = self._ensure_privilege_index()
        entity_index = self._ensure_entity_index()
        grouped: Dict[str, Dict[str, Any]] = {}

        for pid, depth in role_privileges.items():
            parsed = privilege_index.get(str(pid).lower())
            if not parsed:
                continue
            verb, schema_name = parsed
            entity_meta = entity_index.get(schema_name.lower())
            if not entity_meta:
                continue
            logical_name = entity_meta["logical_name"]
            bucket = grouped.setdefault(
                logical_name,
                {
                    "logical_name": logical_name,
                    "schema_name": entity_meta["schema_name"],
                    "display_name": entity_meta["display_name"],
                    "permissions": {col[1]: None for col in ENTITY_ACCESS_RIGHT_COLUMNS},
                },
            )
            if depth is None:
                continue
            existing = bucket["permissions"].get(verb)
            if existing is None or depth > existing:
                bucket["permissions"][verb] = depth
        return grouped

    @staticmethod
    def entity_has_assignment(entity: Dict[str, Any]) -> bool:
        permissions = entity.get("permissions", {})
        return any(value is not None for value in permissions.values())

    def get_role_entities(self, role_id: str) -> List[Dict[str, Any]]:
        role_privileges = self._get_role_privileges(role_id)
        grouped = self._build_assigned_entities(role_privileges)
        result = [item for item in grouped.values() if self.entity_has_assignment(item)]
        result.sort(key=lambda row: row["logical_name"].lower())
        return result

    def get_role_entity_matrix(self, role_id: str) -> List[Dict[str, Any]]:
        from d365_field_creator import ENTITY_ACCESS_RIGHT_COLUMNS

        role_privileges = self._get_role_privileges(role_id)
        assigned = self._build_assigned_entities(role_privileges)
        entity_index = self._ensure_entity_index()
        result: List[Dict[str, Any]] = []
        seen: set = set()
        for meta in sorted(entity_index.values(), key=lambda item: item["logical_name"].lower()):
            logical_name = meta["logical_name"]
            if logical_name in seen:
                continue
            seen.add(logical_name)
            if logical_name in assigned:
                result.append(assigned[logical_name])
            else:
                result.append(
                    {
                        "logical_name": logical_name,
                        "schema_name": meta["schema_name"],
                        "display_name": meta["display_name"],
                        "permissions": {col[1]: None for col in ENTITY_ACCESS_RIGHT_COLUMNS},
                    }
                )
        return result
