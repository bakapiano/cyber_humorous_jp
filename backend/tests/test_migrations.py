from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from youmo_workflow.storage.database import create_database, migrate


def test_frozen_migration_is_repeatable(tmp_path: Path) -> None:
    engine = create_database(f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}")
    migrate(engine)
    migrate(engine)
    assert set(inspect(engine).get_table_names()) >= {
        "sources",
        "videos",
        "production_batches",
        "tasks",
        "artifacts",
        "publications",
        "publish_slots",
        "alembic_version",
        "workflow_settings",
    }


def test_upgrade_keeps_populated_batch_snapshots_and_allows_same_day(tmp_path: Path) -> None:
    engine = create_database(f"sqlite:///{(tmp_path / 'old.sqlite3').as_posix()}")
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        metadata = MetaData()
        metadata.reflect(conn)
        batch = metadata.tables["production_batches"]
        conn.execute(
            batch.insert().values(
                id="old",
                local_date="2026-11-06",
                snapshot_at="old-time",
                status="failed",
                policy_json='{"old":true}',
                candidate_count=1,
            )
        )
        conn.execute(
            metadata.tables["sources"]
            .insert()
            .values(id="src", name="test", url="test", evidence_count=0, enabled=False)
        )
        conn.execute(
            metadata.tables["videos"]
            .insert()
            .values(
                id="vid",
                source_id="src",
                title="old-title",
                origin="channel",
                discovered_at="old-time",
                revision=1,
                screened=False,
            )
        )
        conn.execute(
            metadata.tables["batch_items"]
            .insert()
            .values(batch_id="old", video_id="vid", snapshot_json='{"frozen":true}')
        )
    migrate(engine)
    migrate(engine)
    with engine.begin() as conn:
        new = Table("production_batches", MetaData(), autoload_with=conn)
        assert conn.scalar(select(new.c.policy_json).where(new.c.id == "old")) == '{"old":true}'
        assert (
            conn.exec_driver_sql("SELECT snapshot_json FROM batch_items").scalar()
            == '{"frozen":true}'
        )
        conn.execute(
            new.insert().values(
                id="new",
                local_date="2026-11-06",
                snapshot_at="new-time",
                status="queued",
                policy_json="{}",
                candidate_count=0,
                run_key="manual:new",
            )
        )
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
