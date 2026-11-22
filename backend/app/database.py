from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import BACKEND_DIR
from .document_models import Base


class Database:
    def __init__(self, database_url: str):
        url = make_url(database_url)
        if (
            url.drivername not in {"sqlite", "sqlite+pysqlite"}
            or not url.database
            or url.database == ":memory:"
            or url.host
            or url.username
            or url.query
        ):
            raise ValueError("DATABASE_URL must point to a local SQLite file.")
        self.path = (BACKEND_DIR / url.database).resolve()
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=self.path.as_posix()),
            hide_parameters=True,
        )
        self.sessions = sessionmaker(self.engine)
        event.listen(self.engine, "connect", configure_sqlite)
        event.listen(self.engine, "begin", begin_sqlite_transaction)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.engine.execution_options(sqlite_immediate=True).begin() as connection:
            Base.metadata.create_all(connection)

    def dispose(self) -> None:
        self.engine.dispose()


def configure_sqlite(connection, _record) -> None:
    connection.isolation_level = None
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


def begin_sqlite_transaction(connection) -> None:
    immediate = connection.get_execution_options().get("sqlite_immediate")
    connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.sessions() as session:
        yield session
