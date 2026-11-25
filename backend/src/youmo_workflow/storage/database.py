from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.storage.models import Base


def create_database(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def configure(connection: object, _record: object) -> None:
        import sqlite3

        sqlite = cast(sqlite3.Connection, connection)
        sqlite.execute("PRAGMA foreign_keys=ON")
        sqlite.execute("PRAGMA journal_mode=WAL")

    return engine


def migrate(engine: Engine) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "migrations"))
    with engine.connect() as conn:
        # SQLite parent-table rebuilds require FK enforcement off on this one
        # connection. Lock the migration and validate all references before commit.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Migration would break foreign-key integrity")
            conn.commit()
        finally:
            conn.rollback()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def test_database(url: str) -> Engine:
    engine = create_database(url)
    Base.metadata.create_all(engine)
    return engine


def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def migration_connection(value: object) -> Connection:
    if not isinstance(value, Connection):
        raise TypeError("Migration requires an explicit connection")
    return value
