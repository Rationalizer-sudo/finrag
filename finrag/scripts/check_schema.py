"""校验 ``sql/01_schema.sql`` 与 SQLAlchemy ORM 模型是否一致。

为什么需要这个脚本
------------------
本项目的建表有两条路径：

1. 生产 / Docker：执行 ``sql/01_schema.sql``（带字符集、注释、索引、全文索引、视图）；
2. 测试 / 快速初始化：``Base.metadata.create_all()``（按 ORM 模型建表）。

两条路径一旦漂移，就会出现「本地测试全绿、上线插入报 Unknown column」这类
最难排查的问题。本脚本通过解析 SQL 文件并与 ORM 元数据逐表比对来提前发现漂移，
无需连接数据库即可运行（因此 CI 与离线开发机都能用）。

用法::

    python scripts/check_schema.py
    python scripts/check_schema.py --verbose   # 打印每张表的列清单
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

from _common import PROJECT_ROOT, banner, error, info, kv, success, warn

SCHEMA_FILE = PROJECT_ROOT / "sql" / "01_schema.sql"

# CREATE TABLE `name` ( ... ) ENGINE=...;
RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`(?P<name>\w+)`\s*\((?P<body>.*?)\n\)\s*"
    r"ENGINE",
    re.DOTALL | re.IGNORECASE,
)
# 列定义行：行首为反引号包裹的列名
RE_COLUMN_LINE = re.compile(r"^\s*`(?P<col>\w+)`\s+[A-Za-z]", re.MULTILINE)
# 约束/索引行（不是列）
CONSTRAINT_KEYWORDS = (
    "PRIMARY KEY", "UNIQUE KEY", "KEY ", "CONSTRAINT", "INDEX ", "FULLTEXT",
    "FOREIGN KEY", "CHECK ",
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="校验建表脚本与 ORM 模型一致性")
    parser.add_argument("--verbose", "-v", action="store_true", help="打印每张表的列清单")
    parser.add_argument("--schema", default=str(SCHEMA_FILE), help="建表脚本路径")
    return parser.parse_args()


def parse_schema(path: Path) -> Dict[str, Set[str]]:
    """解析 SQL 文件，返回 ``{表名: {列名, ...}}``。"""
    text = path.read_text(encoding="utf-8")
    tables: Dict[str, Set[str]] = {}
    for match in RE_CREATE_TABLE.finditer(text):
        name = match.group("name")
        body = match.group("body")
        columns: Set[str] = set()
        for line_match in RE_COLUMN_LINE.finditer(body):
            line = line_match.group(0).strip()
            upper = line.upper()
            if any(upper.startswith(keyword) for keyword in CONSTRAINT_KEYWORDS):
                continue
            columns.add(line_match.group("col"))
        tables[name] = columns
    return tables


def parse_models() -> Dict[str, Set[str]]:
    """从 ORM 元数据中提取 ``{表名: {列名, ...}}``。"""
    from app.db.models import Base

    result: Dict[str, Set[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        result[table_name] = {column.name for column in table.columns}
    return result


def main() -> int:
    """主流程。"""
    args = parse_args()
    schema_path = Path(args.schema)

    banner("建表脚本 ↔ ORM 模型一致性校验")
    kv("SQL 脚本", schema_path)
    if not schema_path.is_file():
        error(f"未找到建表脚本：{schema_path}")
        return 1

    try:
        schema_tables = parse_schema(schema_path)
        model_tables = parse_models()
    except Exception as exc:
        error(f"解析失败：{exc}")
        return 1

    kv("SQL 中的表数量", len(schema_tables))
    kv("ORM 中的表数量", len(model_tables))
    print()

    problems = 0

    only_in_schema = sorted(set(schema_tables) - set(model_tables))
    only_in_models = sorted(set(model_tables) - set(schema_tables))
    if only_in_schema:
        warn(f"仅存在于 SQL 脚本中的表（ORM 中未定义）：{only_in_schema}")
        problems += len(only_in_schema)
    if only_in_models:
        error(f"仅存在于 ORM 中的表（SQL 脚本缺失，生产建表会漏表）：{only_in_models}")
        problems += len(only_in_models)

    for table in sorted(set(schema_tables) & set(model_tables)):
        sql_columns = schema_tables[table]
        orm_columns = model_tables[table]
        missing_in_sql = sorted(orm_columns - sql_columns)
        extra_in_sql = sorted(sql_columns - orm_columns)

        if missing_in_sql or extra_in_sql:
            error(f"表 {table} 列不一致：")
            if missing_in_sql:
                print(f"      SQL 脚本缺少列（ORM 有）：{missing_in_sql}")
            if extra_in_sql:
                print(f"      SQL 脚本多出列（ORM 无）：{extra_in_sql}")
            problems += len(missing_in_sql) + len(extra_in_sql)
        else:
            success(f"表 {table}：{len(sql_columns)} 列，完全一致")
        if args.verbose:
            print(f"      列：{', '.join(sorted(sql_columns))}")

    print()
    if problems:
        error(f"共发现 {problems} 处不一致，请同步 sql/01_schema.sql 与 backend/app/db/models.py")
        info("提示：生产与 Docker 部署以 sql/01_schema.sql 为准，测试以 ORM 为准，两者必须一致。")
        return 1

    success("建表脚本与 ORM 模型完全一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
