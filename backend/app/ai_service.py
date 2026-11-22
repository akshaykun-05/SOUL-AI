import hashlib
import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.dialects.sqlite import insert

from .ai_adapter import Engine, PROMPT
from .ai_schemas import AIProcessingResult
from .database import Database
from .document_models import AIResultCache, Document
from .document_repository import get_document
from .document_schemas import DocumentResponse

CACHE_SCHEMA = 1


def load_document(database: Database, document_id: UUID) -> DocumentResponse:
    with database.sessions() as session:
        return get_document(session, document_id)


def cache_key(document: DocumentResponse, engine: Engine, revision: str) -> str:
    inputs = [CACHE_SCHEMA, revision, PROMPT, engine.model_identity, document.model_dump(mode="json")]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def read_cache(database: Database, document: DocumentResponse, key: str) -> AIProcessingResult | None:
    with database.sessions() as session:
        cached = session.get(AIResultCache, document.document_id)
        if cached is None or cached.cache_key != key:
            return None
        try:
            result = AIProcessingResult.model_validate_json(cached.payload)
        except ValidationError:
            return None
        if result.document_id == document.document_id and result.page_count == document.page_count:
            return result
        return None


def write_cache(
    database: Database, key: str, result: AIProcessingResult
) -> None:
    with database.sessions.begin() as session:
        session.connection(execution_options={"sqlite_immediate": True})
        if session.get(Document, result.document_id) is None:
            return
        statement = insert(AIResultCache).values(
            document_id=result.document_id, cache_key=key, payload=result.model_dump_json(),
        )
        session.execute(statement.on_conflict_do_update(
            index_elements=[AIResultCache.document_id],
            set_={"cache_key": statement.excluded.cache_key, "payload": statement.excluded.payload},
        ))
