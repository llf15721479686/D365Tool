"""解决方案 / 发布历史 — Web API 封装。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

HISTORY_LIMIT = 100
USER_MATCH_TOLERANCE_SECONDS = 180

OPERATION_LABELS: Dict[int, str] = {
    0: "导入",
    1: "卸载",
    2: "导出",
    3: "发布",
}

SUBOPERATION_LABELS: Dict[int, str] = {
    0: "无",
    1: "新建",
    2: "升级",
    3: "更新",
    4: "删除",
}

STATUS_LABELS: Dict[int, str] = {
    0: "开始",
    1: "结束",
}

ASYNC_NAME_BY_OPERATION: Dict[int, Tuple[str, ...]] = {
    0: ("ImportSolution",),
    2: ("ExportSolution",),
    3: ("PublishAllXml", "Publish"),
}


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_datetime(value: Any) -> str:
    dt = _parse_iso_datetime(value)
    if dt is None:
        raw = str(value or "")[:16].replace("T", " ")
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M") + timedelta(hours=8)
            return parsed.strftime("%Y/%m/%d %H:%M")
        except ValueError:
            return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt + timedelta(hours=8)
    return dt.strftime("%Y/%m/%d %H:%M")


def _format_error_code(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).strip()
    if not text or text == "0":
        return ""
    try:
        code = int(text)
        if code == 0:
            return ""
        if code < 0:
            code = code & 0xFFFFFFFF
        return format(code, "X")
    except (TypeError, ValueError):
        return text


def _format_result(result: Any, status: Any, endtime: Any) -> str:
    if endtime in (None, "") and status == 0:
        return "进行中"
    if isinstance(result, bool):
        return "成功" if result else "失败"
    if result in (1, "1", True):
        return "成功"
    if result in (0, "0", False):
        return "失败"
    return str(result or "")


def _extract_user_account(user: Any) -> str:
    if not isinstance(user, dict):
        return ""
    for field in ("domainname", "internalemailaddress"):
        value = str(user.get(field, "") or "").strip()
        if not value or value.upper() == "SYSTEM":
            continue
        if "@" in value:
            prefix = value.split("@", 1)[0].strip()
            if prefix and not _is_service_account(prefix):
                return prefix
        elif not _is_service_account(value):
            return value
    fullname = str(user.get("fullname", "") or "").strip()
    if fullname and fullname.upper() != "SYSTEM" and not fullname.startswith("#"):
        if not _is_service_account(fullname):
            return fullname
    return ""


def _is_service_account(account: str) -> bool:
    text = str(account or "").strip()
    if not text:
        return True
    upper = text.upper()
    if upper == "SYSTEM":
        return True
    service_prefixes = (
        "D365APPUSER",
        "D365ADMIN",
        "APPLICATIONUSER",
        "NT AUTHORITY",
    )
    return any(upper.startswith(prefix) for prefix in service_prefixes)


def _seconds_apart(left: Optional[datetime], right: Optional[datetime]) -> float:
    if left is None or right is None:
        return float("inf")
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return abs((left - right).total_seconds())


class D365PublishHistoryManager:
    def __init__(self, creator: Any) -> None:
        self.creator = creator

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

    def _load_import_job_index(self, limit: int = 300) -> List[Dict[str, Any]]:
        url = (
            f"{self.api_base}/importjobs?"
            f"$select=importjobid,solutionname,name,startedon,completedon&"
            f"$expand=createdby($select=domainname,fullname,internalemailaddress)&"
            f"$orderby=startedon desc&$top={int(limit)}"
        )
        try:
            rows = self._get_json_pages(url)
        except RuntimeError:
            return []
        index: List[Dict[str, Any]] = []
        for row in rows:
            solution_name = str(row.get("solutionname", "") or row.get("name", "")).strip()
            started = _parse_iso_datetime(row.get("startedon"))
            completed = _parse_iso_datetime(row.get("completedon"))
            account = _extract_user_account(row.get("createdby"))
            if not solution_name or started is None:
                continue
            index.append(
                {
                    "solution_name": solution_name.lower(),
                    "started": started,
                    "completed": completed,
                    "user_account": account,
                }
            )
        return index

    def _load_async_job_index(self, limit: int = 500) -> List[Dict[str, Any]]:
        url = (
            f"{self.api_base}/asyncoperations?"
            f"$select=asyncoperationid,name,createdon,completedon,operationtype&"
            f"$expand=createdby($select=domainname,fullname,internalemailaddress)&"
            f"$orderby=createdon desc&$top={int(limit)}"
        )
        try:
            rows = self._get_json_pages(url)
        except RuntimeError:
            return []
        index: List[Dict[str, Any]] = []
        allowed_names = set()
        for names in ASYNC_NAME_BY_OPERATION.values():
            allowed_names.update(names)
        for row in rows:
            name = str(row.get("name", "") or "").strip()
            if name not in allowed_names:
                continue
            created = _parse_iso_datetime(row.get("createdon"))
            completed = _parse_iso_datetime(row.get("completedon"))
            account = _extract_user_account(row.get("createdby"))
            if created is None:
                continue
            index.append(
                {
                    "asyncoperationid": str(row.get("asyncoperationid", "")).strip().lower(),
                    "async_name": name,
                    "started": created,
                    "completed": completed,
                    "user_account": account,
                }
            )
        return index

    def _resolve_user_account(
        self,
        raw_row: Dict[str, Any],
        import_jobs: List[Dict[str, Any]],
        async_jobs: List[Dict[str, Any]],
    ) -> str:
        operation = raw_row.get("msdyn_operation")
        try:
            operation_value = int(operation)
        except (TypeError, ValueError):
            operation_value = -1

        solution_name = str(raw_row.get("msdyn_name", "") or "").strip().lower()
        start_dt = _parse_iso_datetime(raw_row.get("msdyn_starttime"))
        end_dt = _parse_iso_datetime(raw_row.get("msdyn_endtime"))
        activity_id = str(raw_row.get("msdyn_activityid", "") or "").strip().lower()

        best_account = ""
        best_delta = float("inf")

        def consider(account: str, delta: float) -> None:
            nonlocal best_account, best_delta
            if not account or delta > USER_MATCH_TOLERANCE_SECONDS:
                return
            if delta < best_delta:
                best_delta = delta
                best_account = account
                return
            if delta == best_delta and best_account and _is_service_account(best_account) and not _is_service_account(account):
                best_account = account

        if operation_value == 0 and solution_name:
            for job in import_jobs:
                if job["solution_name"] != solution_name:
                    continue
                delta = _seconds_apart(start_dt, job["started"])
                if end_dt and job["completed"]:
                    delta = min(delta, _seconds_apart(end_dt, job["completed"]))
                consider(job["user_account"], delta)

        async_names = ASYNC_NAME_BY_OPERATION.get(operation_value, ())
        if async_names:
            for job in async_jobs:
                if job["async_name"] not in async_names:
                    continue
                delta = _seconds_apart(start_dt, job["started"])
                if end_dt and job["completed"]:
                    delta = min(delta, _seconds_apart(end_dt, job["completed"]))
                consider(job["user_account"], delta)

        if not best_account and activity_id:
            for job in async_jobs:
                async_id = str(job.get("asyncoperationid", "") or "").strip().lower()
                if async_id and async_id == activity_id:
                    account = job["user_account"]
                    if account and not _is_service_account(account):
                        return account

        return best_account

    def list_recent_history(self, limit: int = HISTORY_LIMIT) -> List[Dict[str, Any]]:
        select_fields = (
            "msdyn_solutionhistoryid,msdyn_name,msdyn_starttime,msdyn_endtime,msdyn_solutionversion,"
            "msdyn_publishername,msdyn_operation,msdyn_suboperation,msdyn_result,msdyn_errorcode,"
            "msdyn_status,msdyn_exceptionmessage,msdyn_exceptionstack,msdyn_solutionhistorydescription,"
            "msdyn_packagename,msdyn_packageversion,msdyn_activityid"
        )
        url = (
            f"{self.api_base}/msdyn_solutionhistories?"
            f"$select={select_fields}&$orderby=msdyn_starttime desc&$top={int(limit)}"
        )
        resp = requests.get(url, headers=self.headers, timeout=120)
        if resp.status_code == 404:
            raise RuntimeError("当前环境不支持 msdyn_solutionhistories 接口。")
        if resp.status_code >= 400:
            raise RuntimeError(f"加载发布历史失败: HTTP {resp.status_code}, {resp.text}")

        raw_rows = resp.json().get("value", [])
        import_jobs = self._load_import_job_index()
        async_jobs = self._load_async_job_index()

        rows: List[Dict[str, Any]] = []
        for row in raw_rows:
            normalized = self._normalize_row(row)
            normalized["publisher"] = self._resolve_user_account(row, import_jobs, async_jobs)
            normalized["solution_publisher"] = str(row.get("msdyn_publishername", "") or "").strip()
            rows.append(normalized)
        return rows

    def _normalize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        operation = row.get("msdyn_operation")
        suboperation = row.get("msdyn_suboperation")
        status = row.get("msdyn_status")
        endtime = row.get("msdyn_endtime")
        error_code = _format_error_code(row.get("msdyn_errorcode"))
        result_label = _format_result(row.get("msdyn_result"), status, endtime)
        name = str(row.get("msdyn_name", "") or row.get("msdyn_packagename", "")).strip()
        version = str(
            row.get("msdyn_solutionversion", "") or row.get("msdyn_packageversion", "")
        ).strip()
        return {
            "id": str(row.get("msdyn_solutionhistoryid", "")).strip(),
            "name": name,
            "start_time": _format_datetime(row.get("msdyn_starttime")),
            "end_time": _format_datetime(endtime),
            "version": version,
            "publisher": "",
            "solution_publisher": str(row.get("msdyn_publishername", "") or "").strip(),
            "operation": OPERATION_LABELS.get(int(operation), str(operation or ""))
            if operation is not None
            else "",
            "suboperation": SUBOPERATION_LABELS.get(int(suboperation), str(suboperation or ""))
            if suboperation is not None
            else "",
            "result": result_label,
            "error_code": error_code,
            "status": STATUS_LABELS.get(int(status), str(status or "")) if status is not None else "",
            "exception_message": str(row.get("msdyn_exceptionmessage", "") or "").strip(),
            "exception_stack": str(row.get("msdyn_exceptionstack", "") or "").strip(),
            "description": str(row.get("msdyn_solutionhistorydescription", "") or "").strip(),
            "raw_error_code": row.get("msdyn_errorcode"),
        }

    @staticmethod
    def format_error_detail(row: Dict[str, Any], environment_name: str) -> str:
        lines = [
            f"环境: {environment_name}",
            f"名称: {row.get('name', '')}",
            f"开始时间: {row.get('start_time', '')}",
            f"结束时间: {row.get('end_time', '')}",
            f"结果: {row.get('result', '')}",
            f"错误代码: {row.get('error_code', '') or row.get('raw_error_code', '')}",
        ]
        if row.get("publisher"):
            lines.append(f"发布者: {row['publisher']}")
        if row.get("solution_publisher"):
            lines.append(f"解决方案发布者: {row['solution_publisher']}")
        if row.get("operation"):
            lines.append(f"操作: {row['operation']}")
        if row.get("suboperation"):
            lines.append(f"子操作: {row['suboperation']}")
        if row.get("version"):
            lines.append(f"版本: {row['version']}")
        if row.get("description"):
            lines.append("")
            lines.append("描述:")
            lines.append(row["description"])
        if row.get("exception_message"):
            lines.append("")
            lines.append("异常信息:")
            lines.append(row["exception_message"])
        if row.get("exception_stack"):
            lines.append("")
            lines.append("异常堆栈:")
            lines.append(row["exception_stack"])
        return "\n".join(lines)
