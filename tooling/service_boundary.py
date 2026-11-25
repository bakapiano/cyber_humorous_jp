"""Short, atomic task-claim boundary for an explicit Windows service migration."""

import argparse

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from youmo_workflow.config import get_settings

NAME = "workflow_service_migration_boundary"
SQL = f"""CREATE TRIGGER {NAME}
BEFORE UPDATE OF status ON tasks
WHEN NEW.status = 'running' AND OLD.status IN ('pending', 'retry_wait')
BEGIN SELECT RAISE(ABORT, 'service_migration_boundary'); END"""


def boundary(connection: Connection, action: str) -> None:
    existing = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (NAME,)
    ).scalar()
    if existing is not None and existing.strip() != SQL.strip():
        raise RuntimeError("Unexpected boundary definition; not changed")
    if action == "release":
        if existing:
            connection.exec_driver_sql(f"DROP TRIGGER {NAME}")
        return
    active = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM tasks WHERE status='running'"
    ).scalar()
    submitting = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM publications WHERE status='submitting'"
    ).scalar()
    if active or submitting:
        raise RuntimeError("Tasks or uploads are in flight; no process may be stopped")
    if not existing:
        connection.exec_driver_sql(SQL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["acquire", "release"])
    action = parser.parse_args().action
    with create_engine(get_settings().database_url).begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        boundary(connection, action)
    print(f"Service migration boundary: {action}")


if __name__ == "__main__":
    main()
