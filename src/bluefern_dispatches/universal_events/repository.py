from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .orm import (
    Base,
    CandidateEventRow,
    EventAttributeRow,
    EventEntityLinkRow,
    EventEvidenceRow,
    EventRelationshipRow,
    EventRow,
    EntityResolutionDecisionRow,
    LocationRow,
    OrganizationRow,
    SQLITE_INVARIANT_TRIGGERS,
    SourceItemRow,
    SourceRow,
)


class UniversalEventRepository(Protocol):
    def initialize_schema(self) -> None: ...
    def session_scope(self) -> Iterator[Session]: ...
    def close(self) -> None: ...
    def get_source(self, source_id: str) -> SourceRow | None: ...
    def get_source_item(self, source_item_id: str) -> SourceItemRow | None: ...
    def get_candidate(self, candidate_id: str) -> CandidateEventRow | None: ...
    def get_event(self, event_id: str) -> EventRow | None: ...
    def list_candidates(self, *, candidate_status: str | None = None, domain: str | None = None) -> list[CandidateEventRow]: ...
    def list_events(
        self,
        *,
        verification_status: str | None = None,
        domain: str | None = None,
    ) -> list[EventRow]: ...


class SQLiteUniversalEventRepository:
    def __init__(self, database_path: Path | str):
        db_path = Path(database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = db_path
        self.engine: Engine = create_engine(
            f"sqlite:///{db_path.resolve().as_posix()}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self._session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def initialize_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for trigger_sql in SQLITE_INVARIANT_TRIGGERS:
                connection.execute(text(trigger_sql))

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()

    def get_source(self, source_id: str) -> SourceRow | None:
        with self.session_scope() as session:
            return session.get(SourceRow, source_id)

    def get_source_item(self, source_item_id: str) -> SourceItemRow | None:
        with self.session_scope() as session:
            return session.get(SourceItemRow, source_item_id)

    def get_candidate(self, candidate_id: str) -> CandidateEventRow | None:
        with self.session_scope() as session:
            stmt = (
                select(CandidateEventRow)
                .where(CandidateEventRow.candidate_id == candidate_id)
                .options(
                    selectinload(CandidateEventRow.source_item),
                    selectinload(CandidateEventRow.location),
                    selectinload(CandidateEventRow.organization),
                    selectinload(CandidateEventRow.reviews),
                )
            )
            return session.execute(stmt).scalar_one_or_none()

    def get_event(self, event_id: str) -> EventRow | None:
        with self.session_scope() as session:
            stmt = (
                select(EventRow)
                .where(EventRow.event_id == event_id)
                .options(
                    selectinload(EventRow.candidate).selectinload(CandidateEventRow.source_item),
                    selectinload(EventRow.location),
                    selectinload(EventRow.organization),
                    selectinload(EventRow.evidence).selectinload(EventEvidenceRow.source_item).selectinload(SourceItemRow.source),
                    selectinload(EventRow.attributes).selectinload(EventAttributeRow.source_item),
                    selectinload(EventRow.outgoing_relationships).selectinload(EventRelationshipRow.to_event),
                    selectinload(EventRow.incoming_relationships).selectinload(EventRelationshipRow.from_event),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.mention),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.resolution_decision),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.organization),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.location),
                )
            )
            return session.execute(stmt).scalar_one_or_none()

    def list_candidates(self, *, candidate_status: str | None = None, domain: str | None = None) -> list[CandidateEventRow]:
        with self.session_scope() as session:
            stmt = select(CandidateEventRow)
            if candidate_status is not None:
                stmt = stmt.where(CandidateEventRow.candidate_status == candidate_status)
            if domain is not None:
                stmt = stmt.where(CandidateEventRow.domain == domain)
            stmt = stmt.options(
                selectinload(CandidateEventRow.source_item),
                selectinload(CandidateEventRow.location),
                selectinload(CandidateEventRow.organization),
                selectinload(CandidateEventRow.reviews),
            )
            return list(session.execute(stmt).scalars().all())

    def list_events(self, *, verification_status: str | None = None, domain: str | None = None) -> list[EventRow]:
        with self.session_scope() as session:
            stmt = select(EventRow)
            if verification_status is not None:
                stmt = stmt.where(EventRow.verification_status == verification_status)
            if domain is not None:
                stmt = stmt.where(EventRow.domain == domain)
            stmt = stmt.options(
                selectinload(EventRow.candidate).selectinload(CandidateEventRow.source_item),
                selectinload(EventRow.location),
                selectinload(EventRow.organization),
                selectinload(EventRow.evidence).selectinload(EventEvidenceRow.source_item).selectinload(SourceItemRow.source),
                selectinload(EventRow.attributes).selectinload(EventAttributeRow.source_item),
                selectinload(EventRow.outgoing_relationships).selectinload(EventRelationshipRow.to_event),
                selectinload(EventRow.incoming_relationships).selectinload(EventRelationshipRow.from_event),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.mention),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.resolution_decision),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.organization),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.location),
            )
            return list(session.execute(stmt).scalars().all())
