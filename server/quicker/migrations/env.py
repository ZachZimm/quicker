from alembic import context
from quicker.db import Base
from sqlalchemy import engine_from_config, pool

config = context.config
if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"), target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
