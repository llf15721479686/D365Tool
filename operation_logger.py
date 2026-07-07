"""SQLite 操作审计日志 — 记录 D365 工具每次使用的详细操作。"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

APP_VERSION = "1.0.0"
DEFAULT_DB_NAME = "d365_tool_operations.db"
SCHEMA_VERSION = 1

_SENSITIVE_KEYS = frozenset(
    {
        "client_secret",
        "secret",
        "password",
        "token",
        "access_token",
        "authorization",
        "api_key",
        "credential",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _mask_secret_value(value: str) -> str:
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"{text[:2]}****{text[-2:]}"


def sanitize_details(data: Any) -> Any:
    """递归脱敏，避免密钥/token 写入数据库。"""
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if key_lower in _SENSITIVE_KEYS or "secret" in key_lower or "password" in key_lower:
                cleaned[key] = _mask_secret_value(str(value)) if value else ""
            else:
                cleaned[key] = sanitize_details(value)
        return cleaned
    if isinstance(data, list):
        return [sanitize_details(item) for item in data]
    if isinstance(data, tuple):
        return [sanitize_details(item) for item in data]
    return data


def dumps_details(data: Any) -> str:
    if data is None:
        return ""
    return json.dumps(sanitize_details(data), ensure_ascii=False, default=str)


class OperationLogger:
    """线程安全的 SQLite 操作日志记录器。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self.db_path = Path(db_path)
        else:
            self.db_path = Path.cwd() / DEFAULT_DB_NAME
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        ended_at TEXT,
                        host_name TEXT,
                        os_info TEXT,
                        app_version TEXT,
                        config_path TEXT,
                        org_url TEXT,
                        tenant_id TEXT,
                        client_id TEXT,
                        launch_mode TEXT,
                        details_json TEXT
                    );

                    CREATE TABLE IF NOT EXISTS operations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        category TEXT NOT NULL,
                        action TEXT NOT NULL,
                        status TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        details_json TEXT,
                        org_url TEXT,
                        target_org_url TEXT,
                        environment_name TEXT,
                        solution_name TEXT,
                        entity_name TEXT,
                        duration_ms INTEGER,
                        error_message TEXT,
                        FOREIGN KEY (session_id) REFERENCES sessions(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_operations_created_at
                        ON operations(created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_operations_session
                        ON operations(session_id);
                    CREATE INDEX IF NOT EXISTS idx_operations_category_action
                        ON operations(category, action);

                    CREATE TABLE IF NOT EXISTS translation_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_text TEXT NOT NULL,
                        lang_name TEXT NOT NULL,
                        lang_label TEXT NOT NULL,
                        translated_text TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        hit_count INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(source_text, lang_name)
                    );

                    CREATE INDEX IF NOT EXISTS idx_translation_cache_updated_at
                        ON translation_cache(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_translation_cache_source_lang
                        ON translation_cache(source_text, lang_name);

                    CREATE TABLE IF NOT EXISTS js_debug_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_text TEXT NOT NULL,
                        local_file TEXT NOT NULL,
                        mime TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(match_text, local_file)
                    );

                    CREATE INDEX IF NOT EXISTS idx_js_debug_rules_sort
                        ON js_debug_rules(enabled DESC, sort_order ASC, id ASC);

                    CREATE TABLE IF NOT EXISTS local_crm_tables (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        environment_name TEXT NOT NULL,
                        org_url TEXT NOT NULL,
                        logical_name TEXT NOT NULL,
                        schema_name TEXT,
                        display_name_zh TEXT,
                        display_name_en TEXT,
                        object_type_code INTEGER,
                        is_custom_entity INTEGER NOT NULL DEFAULT 0,
                        primary_id_attribute TEXT,
                        primary_name_attribute TEXT,
                        field_count INTEGER NOT NULL DEFAULT 0,
                        last_refreshed_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(org_url, logical_name)
                    );

                    CREATE INDEX IF NOT EXISTS idx_local_crm_tables_updated_at
                        ON local_crm_tables(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_local_crm_tables_env_name
                        ON local_crm_tables(environment_name, logical_name);

                    CREATE TABLE IF NOT EXISTS local_crm_table_fields (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        table_id INTEGER NOT NULL,
                        logical_name TEXT NOT NULL,
                        schema_name TEXT,
                        display_name_zh TEXT,
                        display_name_en TEXT,
                        attribute_type TEXT,
                        required_level TEXT,
                        is_custom INTEGER NOT NULL DEFAULT 0,
                        valid_for_create INTEGER NOT NULL DEFAULT 0,
                        valid_for_update INTEGER NOT NULL DEFAULT 0,
                        valid_for_read INTEGER NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        UNIQUE(table_id, logical_name),
                        FOREIGN KEY (table_id) REFERENCES local_crm_tables(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_local_crm_table_fields_table
                        ON local_crm_table_fields(table_id, sort_order ASC, id ASC);
                    """
                )
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
                conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key = ?",
                    (key,),
                ).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO schema_meta(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
                conn.commit()

    def start_session(
        self,
        *,
        config_path: str = "",
        org_url: str = "",
        tenant_id: str = "",
        client_id: str = "",
        launch_mode: str = "gui",
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        session_id = str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "started_at": _utc_now_iso(),
            "host_name": platform.node(),
            "os_info": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "app_version": APP_VERSION,
            "config_path": config_path,
            "org_url": org_url,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "launch_mode": launch_mode,
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
            "db_path": str(self.db_path),
        }
        if details:
            payload.update(sanitize_details(details))

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, started_at, host_name, os_info, app_version,
                        config_path, org_url, tenant_id, client_id, launch_mode, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        payload["started_at"],
                        payload["host_name"],
                        payload["os_info"],
                        APP_VERSION,
                        config_path,
                        org_url,
                        tenant_id,
                        client_id,
                        launch_mode,
                        dumps_details(payload),
                    ),
                )
                conn.commit()
        return session_id

    def end_session(
        self,
        session_id: str,
        *,
        status: str = "closed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        ended_at = _utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT details_json FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                merged: Dict[str, Any] = {}
                if row and row["details_json"]:
                    try:
                        merged = json.loads(row["details_json"])
                    except json.JSONDecodeError:
                        merged = {}
                merged["ended_at"] = ended_at
                merged["end_status"] = status
                if details:
                    merged.update(sanitize_details(details))
                conn.execute(
                    """
                    UPDATE sessions
                    SET ended_at = ?, details_json = ?
                    WHERE id = ?
                    """,
                    (ended_at, dumps_details(merged), session_id),
                )
                conn.commit()

    def log(
        self,
        *,
        session_id: str,
        category: str,
        action: str,
        status: str,
        summary: str,
        details: Any = None,
        org_url: str = "",
        target_org_url: str = "",
        environment_name: str = "",
        solution_name: str = "",
        entity_name: str = "",
        duration_ms: Optional[int] = None,
        error_message: str = "",
    ) -> int:
        created_at = _utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO operations (
                        session_id, created_at, category, action, status, summary,
                        details_json, org_url, target_org_url, environment_name,
                        solution_name, entity_name, duration_ms, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        created_at,
                        category,
                        action,
                        status,
                        summary[:2000],
                        dumps_details(details),
                        org_url,
                        target_org_url,
                        environment_name,
                        solution_name,
                        entity_name,
                        duration_ms,
                        error_message[:4000] if error_message else "",
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)

    @contextmanager
    def track(
        self,
        *,
        session_id: str,
        category: str,
        action: str,
        summary: str,
        details: Any = None,
        org_url: str = "",
        target_org_url: str = "",
        environment_name: str = "",
        solution_name: str = "",
        entity_name: str = "",
    ) -> Iterator[Dict[str, Any]]:
        """上下文管理器：自动记录开始/成功/失败及耗时。"""
        started = time.perf_counter()
        context: Dict[str, Any] = {"operation_id": None}
        base_details = details if isinstance(details, dict) else {"payload": details}
        self.log(
            session_id=session_id,
            category=category,
            action=action,
            status="started",
            summary=f"开始: {summary}",
            details=base_details,
            org_url=org_url,
            target_org_url=target_org_url,
            environment_name=environment_name,
            solution_name=solution_name,
            entity_name=entity_name,
        )
        try:
            yield context
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            context["operation_id"] = self.log(
                session_id=session_id,
                category=category,
                action=action,
                status="failed",
                summary=f"失败: {summary}",
                details={**(base_details or {}), "exception_type": type(exc).__name__},
                org_url=org_url,
                target_org_url=target_org_url,
                environment_name=environment_name,
                solution_name=solution_name,
                entity_name=entity_name,
                duration_ms=duration_ms,
                error_message=str(exc),
            )
            raise
        else:
            duration_ms = int((time.perf_counter() - started) * 1000)
            context["operation_id"] = self.log(
                session_id=session_id,
                category=category,
                action=action,
                status="success",
                summary=f"成功: {summary}",
                details=base_details,
                org_url=org_url,
                target_org_url=target_org_url,
                environment_name=environment_name,
                solution_name=solution_name,
                entity_name=entity_name,
                duration_ms=duration_ms,
            )

    def get_cached_translation(self, source_text: str, lang_name: str) -> Optional[str]:
        source = str(source_text).strip()
        lang = str(lang_name).strip()
        if not source or not lang:
            return None
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT translated_text
                    FROM translation_cache
                    WHERE source_text = ? AND lang_name = ?
                    """,
                    (source, lang),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    """
                    UPDATE translation_cache
                    SET hit_count = hit_count + 1, updated_at = ?
                    WHERE source_text = ? AND lang_name = ?
                    """,
                    (_utc_now_iso(), source, lang),
                )
                conn.commit()
                return str(row["translated_text"] or "")

    def upsert_cached_translation(
        self,
        source_text: str,
        lang_name: str,
        lang_label: str,
        translated_text: str,
    ) -> None:
        source = str(source_text).strip()
        lang = str(lang_name).strip()
        translated = str(translated_text).strip()
        if not source or not lang or not translated:
            return
        now = _utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO translation_cache (
                        source_text, lang_name, lang_label, translated_text,
                        created_at, updated_at, hit_count
                    ) VALUES (?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(source_text, lang_name) DO UPDATE SET
                        lang_label = excluded.lang_label,
                        translated_text = excluded.translated_text,
                        updated_at = excluded.updated_at
                    """,
                    (source, lang, str(lang_label).strip(), translated, now, now),
                )
                conn.commit()

    def list_js_debug_rules(self, *, include_disabled: bool = False) -> List[Dict[str, Any]]:
        where_sql = "" if include_disabled else "WHERE enabled = 1"
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id, match_text, local_file, mime, enabled, sort_order, created_at, updated_at
                    FROM js_debug_rules
                    {where_sql}
                    ORDER BY sort_order ASC, id ASC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    def replace_js_debug_rules(self, rules: List[Dict[str, str]]) -> None:
        now = _utc_now_iso()
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM js_debug_rules")
                for index, rule in enumerate(rules):
                    match = str(rule.get("match") or rule.get("match_text") or "").strip()
                    local_file = str(rule.get("file") or rule.get("local_file") or "").strip()
                    mime = str(rule.get("mime") or "").strip()
                    if not match or not local_file:
                        continue
                    conn.execute(
                        """
                        INSERT INTO js_debug_rules (
                            match_text, local_file, mime, enabled, sort_order, created_at, updated_at
                        ) VALUES (?, ?, ?, 1, ?, ?, ?)
                        ON CONFLICT(match_text, local_file) DO UPDATE SET
                            mime = excluded.mime,
                            enabled = 1,
                            sort_order = excluded.sort_order,
                            updated_at = excluded.updated_at
                        """,
                        (match, local_file, mime, index, now, now),
                    )
                conn.commit()

    def upsert_local_crm_table(
        self,
        *,
        environment_name: str,
        org_url: str,
        entity_info: Dict[str, Any],
        fields: List[Dict[str, Any]],
    ) -> int:
        now = _utc_now_iso()
        logical_name = str(entity_info.get("logical_name", "")).strip()
        clean_org_url = str(org_url).strip().rstrip("/")
        if not logical_name or not clean_org_url:
            raise ValueError("local table requires org_url and logical_name")

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO local_crm_tables (
                        environment_name, org_url, logical_name, schema_name,
                        display_name_zh, display_name_en, object_type_code, is_custom_entity,
                        primary_id_attribute, primary_name_attribute, field_count,
                        last_refreshed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(org_url, logical_name) DO UPDATE SET
                        environment_name = excluded.environment_name,
                        schema_name = excluded.schema_name,
                        display_name_zh = excluded.display_name_zh,
                        display_name_en = excluded.display_name_en,
                        object_type_code = excluded.object_type_code,
                        is_custom_entity = excluded.is_custom_entity,
                        primary_id_attribute = excluded.primary_id_attribute,
                        primary_name_attribute = excluded.primary_name_attribute,
                        field_count = excluded.field_count,
                        last_refreshed_at = excluded.last_refreshed_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(environment_name).strip(),
                        clean_org_url,
                        logical_name,
                        str(entity_info.get("schema_name", "") or ""),
                        str(entity_info.get("display_name_zh", "") or ""),
                        str(entity_info.get("display_name_en", "") or ""),
                        entity_info.get("object_type_code"),
                        1 if entity_info.get("is_custom_entity") else 0,
                        str(entity_info.get("primary_id_attribute", "") or ""),
                        str(entity_info.get("primary_name_attribute", "") or ""),
                        len(fields),
                        now,
                        now,
                        now,
                    ),
                )
                table_id = int(
                    conn.execute(
                        "SELECT id FROM local_crm_tables WHERE org_url = ? AND logical_name = ?",
                        (clean_org_url, logical_name),
                    ).fetchone()["id"]
                )
                conn.execute("DELETE FROM local_crm_table_fields WHERE table_id = ?", (table_id,))
                for index, field in enumerate(fields):
                    field_name = str(field.get("logical_name", "") or "").strip()
                    if not field_name:
                        continue
                    conn.execute(
                        """
                        INSERT INTO local_crm_table_fields (
                            table_id, logical_name, schema_name, display_name_zh, display_name_en,
                            attribute_type, required_level, is_custom, valid_for_create,
                            valid_for_update, valid_for_read, sort_order, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            table_id,
                            field_name,
                            str(field.get("schema_name", "") or ""),
                            str(field.get("display_name_zh", "") or ""),
                            str(field.get("display_name_en", "") or ""),
                            str(field.get("attribute_type", "") or ""),
                            str(field.get("required_level", "") or ""),
                            1 if field.get("is_custom") else 0,
                            1 if field.get("valid_for_create") else 0,
                            1 if field.get("valid_for_update") else 0,
                            1 if field.get("valid_for_read") else 0,
                            index,
                            now,
                        ),
                    )
                conn.commit()
                return table_id

    def list_local_crm_tables(self, keyword: str = "") -> List[Dict[str, Any]]:
        params: List[Any] = []
        where_sql = ""
        clean_keyword = str(keyword or "").strip()
        if clean_keyword:
            like = f"%{clean_keyword}%"
            where_sql = """
            WHERE logical_name LIKE ? OR schema_name LIKE ? OR display_name_zh LIKE ?
                OR display_name_en LIKE ? OR environment_name LIKE ?
            """
            params.extend([like, like, like, like, like])
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM local_crm_tables
                    {where_sql}
                    ORDER BY last_refreshed_at DESC, updated_at DESC, logical_name ASC
                    """,
                    params,
                ).fetchall()
        return [dict(row) for row in rows]

    def list_local_crm_table_fields(self, table_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM local_crm_table_fields
                    WHERE table_id = ?
                    ORDER BY sort_order ASC, id ASC
                    """,
                    (int(table_id),),
                ).fetchall()
        return [dict(row) for row in rows]

    def _translation_cache_filter_sql(
        self,
        *,
        keyword: Optional[str] = None,
        lang_name: Optional[str] = None,
    ) -> tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if keyword:
            clauses.append("(source_text LIKE ? OR translated_text LIKE ? OR lang_name LIKE ? OR lang_label LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like, like])
        if lang_name and lang_name != "全部":
            clauses.append("lang_name = ?")
            params.append(lang_name)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def query_translation_cache(
        self,
        *,
        limit: int = 300,
        offset: int = 0,
        keyword: Optional[str] = None,
        lang_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._translation_cache_filter_sql(keyword=keyword, lang_name=lang_name)
        params.append(max(1, min(5000, int(limit))))
        params.append(max(0, int(offset)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM translation_cache
                    {where_sql}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    params,
                ).fetchall()
        return [dict(row) for row in rows]

    def count_translation_cache(
        self,
        *,
        keyword: Optional[str] = None,
        lang_name: Optional[str] = None,
    ) -> int:
        where_sql, params = self._translation_cache_filter_sql(keyword=keyword, lang_name=lang_name)
        with self._lock:
            with self._connect() as conn:
                return int(conn.execute(f"SELECT COUNT(*) FROM translation_cache {where_sql}", params).fetchone()[0])

    def query_recent(
        self,
        *,
        limit: int = 100,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.query_operations(
            limit=limit,
            session_id=session_id,
            category=category,
        )

    def _operations_filter_sql(
        self,
        *,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if keyword:
            clauses.append(
                "(summary LIKE ? OR action LIKE ? OR entity_name LIKE ? OR solution_name LIKE ? OR error_message LIKE ?)"
            )
            like = f"%{keyword}%"
            params.extend([like, like, like, like, like])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def query_operations(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._operations_filter_sql(
            session_id=session_id, category=category, status=status, keyword=keyword
        )
        params.append(limit)
        params.append(max(0, int(offset)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM operations
                    {where_sql}
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    params,
                ).fetchall()
        return [dict(row) for row in rows]

    def count_operations(
        self,
        *,
        session_id: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> int:
        where_sql, params = self._operations_filter_sql(
            session_id=session_id, category=category, status=status, keyword=keyword
        )
        with self._lock:
            with self._connect() as conn:
                return int(conn.execute(f"SELECT COUNT(*) FROM operations {where_sql}", params).fetchone()[0])

    def get_statistics(self) -> Dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                total_ops = conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
                total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                by_category = conn.execute(
                    """
                    SELECT category, COUNT(*) AS cnt
                    FROM operations
                    GROUP BY category
                    ORDER BY cnt DESC
                    """
                ).fetchall()
                by_status = conn.execute(
                    """
                    SELECT status, COUNT(*) AS cnt
                    FROM operations
                    GROUP BY status
                    ORDER BY cnt DESC
                    """
                ).fetchall()
                last_op = conn.execute(
                    "SELECT created_at, summary FROM operations ORDER BY id DESC LIMIT 1"
                ).fetchone()
        return {
            "db_path": str(self.db_path),
            "total_operations": total_ops,
            "total_sessions": total_sessions,
            "by_category": {row["category"]: row["cnt"] for row in by_category},
            "by_status": {row["status"]: row["cnt"] for row in by_status},
            "last_operation": dict(last_op) if last_op else None,
        }


def default_db_path(config_path: str) -> str:
    base_dir = Path(config_path).resolve().parent
    return str(base_dir / DEFAULT_DB_NAME)
