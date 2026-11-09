from sqlalchemy import (
    Column, Integer, MetaData, String, Table, Text, create_engine,
)

metadata = MetaData()
payments = Table(
    "paypilot_payments", metadata,
    Column("id", String(36), primary_key=True),
    Column("owner", String(128), nullable=False, index=True),
    Column("amount", String(24), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("description", String(127), nullable=False),
    Column("fingerprint", String(64), nullable=False, index=True),
    Column("status", String(24), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("order_id", String(64), unique=True),
    Column("approval_url", Text),
    Column("capture_id", String(64)),
    Column("completed_at", String(40)),
)
model_calls = Table(
    "paypilot_model_calls", metadata,
    Column("id", Integer, primary_key=True),
    Column("owner", String(128), nullable=False, index=True),
    Column("day", String(10), nullable=False, index=True),
)


def make_engine(url):
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, **kwargs)
