"""初始化 MySQL：创建数据库、（可选）创建应用账号、执行建表脚本。

用法::

    python scripts/init_mysql.py                    # 建库 + 建表
    python scripts/init_mysql.py --create-user      # 额外创建 .env 中配置的账号并授权
    python scripts/init_mysql.py --schema-only      # 只执行 sql/01_schema.sql
    python scripts/init_mysql.py --dry-run          # 只打印将要执行的语句

脚本使用 ``.env`` 中 ``MYSQL_*`` 配置；建库阶段需要具备 CREATE 权限的账号
（通常为 root，可用 ``--admin-user`` / ``--admin-password`` 单独指定）。
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from _common import PROJECT_ROOT, banner, error, info, kv, read_sql_file, success, warn

SCHEMA_FILE = PROJECT_ROOT / "sql" / "01_schema.sql"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="初始化 FinRAG 的 MySQL 数据库")
    parser.add_argument("--host", default=None, help="MySQL 主机（默认取 .env）")
    parser.add_argument("--port", default=None, type=int, help="MySQL 端口（默认取 .env）")
    parser.add_argument("--admin-user", default=None, help="建库用管理员账号（默认 root）")
    parser.add_argument("--admin-password", default=None, help="建库用管理员口令")
    parser.add_argument("--database", default=None, help="目标数据库名（默认取 .env MYSQL_DB）")
    parser.add_argument("--create-user", action="store_true", help="创建 .env 中配置的应用账号并授权")
    parser.add_argument("--schema-only", action="store_true", help="只执行建表脚本，不建库")
    parser.add_argument("--drop", action="store_true", help="执行前先删除同名数据库（危险）")
    parser.add_argument("--dry-run", action="store_true", help="只打印语句，不实际执行")
    return parser.parse_args()


def connect(host: str, port: int, user: str, password: str, database: str | None):
    """建立 pymysql 连接。"""
    import pymysql

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )


def execute_statements(connection, statements: List[str]) -> int:
    """逐条执行 SQL，返回成功条数。"""
    executed = 0
    with connection.cursor() as cursor:
        for index, statement in enumerate(statements, start=1):
            summary = " ".join(statement.split())[:80]
            try:
                cursor.execute(statement)
                executed += 1
                info(f"[{index}/{len(statements)}] 已执行：{summary} ...")
            except Exception as exc:
                error(f"[{index}/{len(statements)}] 执行失败：{summary} ...")
                error(f"        原因：{exc}")
                raise
    return executed


def main() -> int:
    """主流程。"""
    args = parse_args()

    banner("FinRAG 数据库初始化")
    try:
        from app.core.config import get_settings
    except Exception as exc:
        error(f"导入配置失败：{exc}")
        return 1

    settings = get_settings()
    host = args.host or settings.mysql_host
    port = args.port or settings.mysql_port
    database = args.database or settings.mysql_db
    admin_user = args.admin_user or "root"
    admin_password = args.admin_password if args.admin_password is not None else settings.mysql_password

    kv("MySQL 地址", f"{host}:{port}")
    kv("目标数据库", database)
    kv("建库账号", admin_user)
    kv("建表脚本", SCHEMA_FILE)

    if not SCHEMA_FILE.is_file():
        error(f"未找到建表脚本：{SCHEMA_FILE}")
        return 1

    statements = read_sql_file(SCHEMA_FILE)
    info(f"建表脚本共解析出 {len(statements)} 条 SQL 语句")

    if args.dry_run:
        banner("Dry-run：将执行的语句")
        for index, statement in enumerate(statements, start=1):
            print(f"  {index:>3}. {' '.join(statement.split())[:110]}")
        success("Dry-run 结束，未做任何修改。")
        return 0

    try:
        import pymysql  # noqa: F401
    except ImportError:
        error("未安装 PyMySQL，请先执行：pip install PyMySQL")
        return 1

    # ---- 1. 建库 ----
    try:
        if args.schema_only:
            connection = connect(host, port, admin_user, admin_password, database)
            info("已跳过建库，直接连接目标数据库")
        else:
            root_connection = connect(host, port, admin_user, admin_password, None)
            with root_connection.cursor() as cursor:
                if args.drop:
                    warn(f"正在删除数据库 {database} ...")
                    cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
                )
            success(f"数据库 {database} 已就绪")
            root_connection.close()
            connection = connect(host, port, admin_user, admin_password, database)
    except Exception as exc:
        error(f"连接 MySQL 或建库失败：{exc}")
        info("请确认 MySQL 服务已启动，且账号密码正确：")
        info(f"  mysql -h {host} -P {port} -u {admin_user} -p")
        return 1

    # ---- 2. 建表 ----
    try:
        executed = execute_statements(connection, statements)
        success(f"建表完成，共执行 {executed} 条语句")
    except Exception:
        connection.close()
        return 1

    # ---- 3. 创建应用账号 ----
    if args.create_user:
        user = settings.mysql_user
        password = settings.mysql_password
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE USER IF NOT EXISTS '{user}'@'%' IDENTIFIED BY '{password}'"
                )
                cursor.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{user}'@'%'")
                cursor.execute("FLUSH PRIVILEGES")
            success(f"应用账号 {user} 已创建并授权访问 {database}")
        except Exception as exc:
            warn(f"创建应用账号失败（可忽略，若你直接用 root 连接）：{exc}")

    # ---- 4. 校验 ----
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (database,),
            )
            tables = [row[0] for row in cursor.fetchall()]
        banner("初始化结果")
        kv("数据表数量", len(tables), len(tables) > 0)
        for name in tables:
            print(f"    - {name}")
        if len(tables) == 0:
            error("未创建任何数据表，请检查脚本内容与账号权限。")
            return 1
        success("MySQL 初始化完成。下一步：python scripts/init_milvus.py")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
