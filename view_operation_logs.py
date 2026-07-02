"""查看 D365 工具 SQLite 操作日志（命令行）。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from operation_logger import DEFAULT_DB_NAME, OperationLogger, default_db_path


def _print_rows(rows: list) -> None:
    if not rows:
        print("（无记录）")
        return
    for row in rows:
        print("-" * 80)
        print(f"#{row['id']}  {row['created_at']}  [{row['category']}/{row['action']}]  {row['status']}")
        print(f"摘要: {row['summary']}")
        if row.get("org_url"):
            print(f"源环境: {row['org_url']}")
        if row.get("target_org_url"):
            print(f"目标环境: {row['target_org_url']}")
        if row.get("solution_name"):
            print(f"解决方案: {row['solution_name']}")
        if row.get("entity_name"):
            print(f"实体: {row['entity_name']}")
        if row.get("duration_ms") is not None:
            print(f"耗时: {row['duration_ms']} ms")
        if row.get("error_message"):
            print(f"错误: {row['error_message']}")
        if row.get("details_json"):
            try:
                details = json.loads(row["details_json"])
                print("详情:")
                print(json.dumps(details, ensure_ascii=False, indent=2))
            except json.JSONDecodeError:
                print(f"详情: {row['details_json']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="查看 D365 工具操作日志")
    parser.add_argument("--config", default="config.json", help="配置文件路径，用于定位数据库")
    parser.add_argument("--db", default="", help="直接指定 SQLite 数据库路径")
    parser.add_argument("--limit", type=int, default=30, help="显示最近 N 条操作")
    parser.add_argument("--category", default="", help="按 category 过滤")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    args = parser.parse_args()

    db_path = args.db or default_db_path(args.config)
    if not Path(db_path).exists():
        print(f"数据库不存在: {db_path}")
        print("请先运行 D365 工具并完成至少一次操作。")
        return

    logger = OperationLogger(db_path)
    print(f"数据库: {db_path}")

    if args.stats:
        stats = logger.get_statistics()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    rows = logger.query_recent(
        limit=args.limit,
        category=args.category or None,
    )
    _print_rows(rows)


if __name__ == "__main__":
    main()
