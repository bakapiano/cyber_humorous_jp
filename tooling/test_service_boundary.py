import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from tooling.service_boundary import NAME, boundary


@pytest.mark.parametrize("status", ["running", "submitting"])
def test_service_migration_refuses_inflight_work(status: str) -> None:
    with create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tasks (status TEXT)")
        connection.exec_driver_sql("CREATE TABLE publications (status TEXT)")
        table = "tasks" if status == "running" else "publications"
        connection.exec_driver_sql(f"INSERT INTO {table} VALUES (?)", (status,))
        with pytest.raises(RuntimeError, match="in flight"):
            boundary(connection, "acquire")
        assert not connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).scalar()


def test_gate_blocks_claims_and_preserves_task_history() -> None:
    with create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tasks (status TEXT, attempts INTEGER)")
        connection.exec_driver_sql("CREATE TABLE publications (status TEXT)")
        connection.exec_driver_sql("INSERT INTO tasks VALUES ('pending', 2)")
        boundary(connection, "acquire")
        boundary(connection, "acquire")
        with pytest.raises(IntegrityError) as error:
            connection.exec_driver_sql("UPDATE tasks SET status='running'")
        assert isinstance(error.value.orig, sqlite3.IntegrityError)
        assert tuple(connection.exec_driver_sql("SELECT * FROM tasks").one()) == (
            "pending",
            2,
        )
        boundary(connection, "release")
        boundary(connection, "release")
        connection.exec_driver_sql("UPDATE tasks SET status='running'")
        assert tuple(connection.exec_driver_sql("SELECT * FROM tasks").one()) == (
            "running",
            2,
        )


def test_service_migration_never_overwrites_an_unrecognized_trigger() -> None:
    with create_engine("sqlite://").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tasks (status TEXT)")
        connection.exec_driver_sql(
            f"CREATE TRIGGER {NAME} AFTER INSERT ON tasks BEGIN SELECT 1; END"
        )
        for action in ("acquire", "release"):
            with pytest.raises(RuntimeError, match="Unexpected boundary"):
                boundary(connection, action)
