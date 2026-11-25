from alembic import context

from youmo_workflow.storage.database import migration_connection
from youmo_workflow.storage.models import Base

connection = migration_connection(context.config.attributes["connection"])
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
