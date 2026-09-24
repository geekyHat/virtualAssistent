"""Adapter PostgreSQL del modulo conversations (NewRay.md §7.3; ADR 0002).

Il ruolo applicativo (``newray_app``) non è proprietario delle tabelle e
non ha superuser né BYPASSRLS: la RLS (FORCE, migration 0002) filtra ogni
riga. Il contesto di scope è impostato per transazione
(``set_config(..., is_local=true)``): il proprietario è il principal, mai
derivato dal payload.

La sequenza dei messaggi viene dal contatore atomico
``conversations.next_sequence`` (migration 0004): l'append aggiorna la
riga padre con ``UPDATE ... SET next_sequence = next_sequence + 1``, il
lock di riga serializza gli append concorrenti e l'``updated_at`` cambia
nella stessa transazione corta. Il vincolo ``UNIQUE (conversation_id,
sequence)`` resta la barriera finale.

L'idempotenza dell'append è una ricevuta scoped per (scope, conversazione,
chiave) su ``message_appends`` (migration 0004): l'hash del payload
normalizzato distingue «stesso input» (stesso esito) da «input diverso»
(``Conflict``); il vincolo UNIQUE è la barriera, non il controllo in lettura.

Il SQL resta in questo file: dominio e applicazione non conoscono né
SQLAlchemy né PostgreSQL.
"""

from __future__ import annotations

import builtins
import hashlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Scope, new_id

from ..domain import Conversation, Message, MessageRole


def _set_scope_context(conn: Connection, scope: Scope) -> None:
    """Contesto di scope per la transazione corrente (locale, auto-pulito)."""
    conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(scope.user_id)})
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(scope.organization_id)},
    )


def _conversation(row: Row[Any]) -> Conversation:
    return Conversation(
        id=row.id,
        organization_id=row.organization_id,
        owner_id=row.owner_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresConversationRepository:
    """``ConversationRepository`` su PostgreSQL: scope obbligatorio, RLS di barriera."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_conversation(self, conversation: Conversation) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(conversation.organization_id, conversation.owner_id))
            conn.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, organization_id, owner_id, title, created_at, updated_at) "
                    "VALUES (:id, :org, :owner, :title, :created, :updated)"
                ),
                {
                    "id": conversation.id,
                    "org": conversation.organization_id,
                    "owner": conversation.owner_id,
                    "title": conversation.title,
                    "created": conversation.created_at,
                    "updated": conversation.updated_at,
                },
            )

    def get_conversation(self, scope: Scope, conversation_id: uuid.UUID) -> Conversation | None:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            row = conn.execute(
                text(
                    "SELECT id, organization_id, owner_id, title, created_at, updated_at "
                    "FROM conversations WHERE id = :id"
                ),
                {"id": conversation_id},
            ).first()
        return None if row is None else _conversation(row)

    def list_conversations(
        self,
        scope: Scope,
        *,
        before: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> list[Conversation]:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            if before is None:
                sql = (
                    "SELECT id, organization_id, owner_id, title, created_at, updated_at "
                    "FROM conversations "
                    "ORDER BY updated_at DESC, id DESC LIMIT :limit"
                )
                params: dict[str, object] = {"limit": limit}
            else:
                sql = (
                    "SELECT id, organization_id, owner_id, title, created_at, updated_at "
                    "FROM conversations "
                    "WHERE (updated_at, id) < (:before_ts, :before_id) "
                    "ORDER BY updated_at DESC, id DESC LIMIT :limit"
                )
                params = {"before_ts": before[0], "before_id": before[1], "limit": limit}
            rows = conn.execute(text(sql), params).all()
        return [_conversation(row) for row in rows]

    def rename_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        title: str,
        now: datetime,
        *,
        expected_updated_at: datetime | None = None,
    ) -> Conversation | None:
        """Rinomina con controllo di versione nella stessa istruzione.

        ``updated_at`` è anche il token di concorrenza esposto dal contratto:
        il predicato nell'UPDATE evita la gara SELECT→UPDATE. Il valore nuovo
        avanza almeno di un microsecondo, anche con clock applicativo fermo o
        writer che attende il lock della riga.
        """
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            predicate = "AND updated_at = :expected" if expected_updated_at is not None else ""
            row = conn.execute(
                text(
                    "UPDATE conversations SET title = :title, "
                    "updated_at = GREATEST(updated_at + INTERVAL '1 microsecond', "
                    ":updated + INTERVAL '1 microsecond') "
                    f"WHERE id = :id {predicate} "
                    "RETURNING id, organization_id, owner_id, title, created_at, updated_at"
                ),
                {
                    "id": conversation_id,
                    "title": title,
                    "updated": now,
                    "expected": expected_updated_at,
                },
            ).first()
            if row is None and expected_updated_at is not None:
                exists = conn.execute(
                    text("SELECT 1 FROM conversations WHERE id = :id"), {"id": conversation_id}
                ).first()
                if exists is not None:
                    raise Conflict("la conversazione è cambiata dopo l'ultima lettura")
        return None if row is None else _conversation(row)

    def delete_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        """Cancellazione con versione attesa atomica (``Conflict`` se obsoleta)."""
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            predicate = "AND updated_at = :expected" if expected_updated_at is not None else ""
            result = conn.execute(
                text(f"DELETE FROM conversations WHERE id = :id {predicate}"),
                {"id": conversation_id, "expected": expected_updated_at},
            )
            if result.rowcount == 0 and expected_updated_at is not None:
                exists = conn.execute(
                    text("SELECT 1 FROM conversations WHERE id = :id"), {"id": conversation_id}
                ).first()
                if exists is not None:
                    raise Conflict("la conversazione è cambiata dopo l'ultima lettura")
        return result.rowcount > 0


class PostgresMessageStore:
    """``MessageStore`` su PostgreSQL: sequenza assegnata atomicamente."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _payload_hash(role: MessageRole, content: str) -> str:
        """Hash del payload normalizzato: ruolo deciso dal server + contenuto.

        Il ruolo fa parte dell'identità dell'operazione: due richieste con
        la stessa chiave ma ruoli diversi sono input diversi.
        """
        return hashlib.sha256(f"{role.value}\x00{content}".encode()).hexdigest()

    def _load_message(self, conn: Connection, message_id: uuid.UUID) -> Message:
        row = conn.execute(
            text(
                "SELECT conversation_id, role, content, sequence, created_at "
                "FROM messages WHERE id = :id"
            ),
            {"id": message_id},
        ).one()
        return Message(
            id=message_id,
            conversation_id=row.conversation_id,
            role=MessageRole(row.role),
            content=row.content,
            sequence=row.sequence,
            created_at=row.created_at,
        )

    def append(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content: str,
        now: datetime,
        *,
        idempotency_key: str | None = None,
    ) -> Message:
        """Append serializzato dal lock della riga padre (migration 0004).

        Una sola transazione corta: il contatore atomico assegna la
        sequenza, l'``updated_at`` della conversazione cambia nello stesso
        passo e la ricevuta di idempotenza (se presente) è atomica col
        messaggio. Conversazione assente (o fuori scope, via RLS) →
        ``NotFound``: niente sequenza allocata su una riga che non c'è.
        """
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            # Il lock serializza sia l'allocazione sia la stessa chiave
            # idempotente concorrente. Il replay viene risolto prima di ogni
            # mutazione della conversazione.
            conversation = conn.execute(
                text("SELECT id FROM conversations WHERE id = :cid FOR UPDATE"),
                {"cid": conversation_id},
            ).first()
            if conversation is None:
                raise NotFound("conversazione non trovata")

            if idempotency_key is not None:
                digest = self._payload_hash(role, content)
                receipt = conn.execute(
                    text(
                        "SELECT payload_hash, message_id FROM message_appends "
                        "WHERE conversation_id = :cid AND idempotency_key = :key "
                        "AND organization_id = :org AND owner_id = :owner"
                    ),
                    {
                        "cid": conversation_id,
                        "key": idempotency_key,
                        "org": scope.organization_id,
                        "owner": scope.user_id,
                    },
                ).first()
                if receipt is not None:
                    if receipt.payload_hash != digest:
                        raise Conflict("chiave di idempotenza già usata con un contenuto diverso")
                    return self._load_message(conn, receipt.message_id)

            message_id = new_id()
            # next_sequence è il prossimo valore libero: RETURNING restituisce
            # quello allocato prima dell'incremento. GREATEST impedisce che un
            # writer in attesa del lock faccia retrocedere updated_at.
            counter = conn.execute(
                text(
                    "UPDATE conversations "
                    "SET next_sequence = next_sequence + 1, "
                    "updated_at = GREATEST(updated_at + INTERVAL '1 microsecond', "
                    ":updated + INTERVAL '1 microsecond') "
                    "WHERE id = :cid "
                    "RETURNING next_sequence - 1 AS sequence"
                ),
                {"cid": conversation_id, "updated": now},
            ).one()
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, conversation_id, organization_id, owner_id, "
                    " role, content, sequence, created_at) "
                    "VALUES (:id, :cid, :org, :owner, :role, :content, :seq, :created)"
                ),
                {
                    "id": message_id,
                    "cid": conversation_id,
                    "org": scope.organization_id,
                    "owner": scope.user_id,
                    "role": role.value,
                    "content": content,
                    "seq": counter.sequence,
                    "created": now,
                },
            )
            if idempotency_key is not None:
                # La ricevuta è dentro la stessa transazione: o esistono
                # insieme, o non esistono. Il vincolo UNIQUE è la barriera
                # contro una corsa di ricevute duplicate.
                conn.execute(
                    text(
                        "INSERT INTO message_appends "
                        "(id, conversation_id, organization_id, owner_id, "
                        " idempotency_key, payload_hash, message_id, created_at) "
                        "VALUES (:id, :cid, :org, :owner, :key, :hash, :mid, :created)"
                    ),
                    {
                        "id": new_id(),
                        "cid": conversation_id,
                        "org": scope.organization_id,
                        "owner": scope.user_id,
                        "key": idempotency_key,
                        "hash": digest,
                        "mid": message_id,
                        "created": now,
                    },
                )
        return Message(
            id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=counter.sequence,
            created_at=now,
        )

    def list(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int,
    ) -> builtins.list[Message]:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            rows = conn.execute(
                text(
                    "SELECT id, role, content, sequence, created_at "
                    "FROM messages "
                    "WHERE conversation_id = :cid AND sequence > :after "
                    "ORDER BY sequence "
                    "LIMIT :limit"
                ),
                {"cid": conversation_id, "after": after_sequence, "limit": limit},
            ).all()
        return [
            Message(
                id=row.id,
                conversation_id=conversation_id,
                role=MessageRole(row.role),
                content=row.content,
                sequence=row.sequence,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def list_recent(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        limit: int,
    ) -> builtins.list[Message]:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            rows = conn.execute(
                text(
                    "SELECT id, role, content, sequence, created_at FROM ("
                    "SELECT id, role, content, sequence, created_at FROM messages "
                    "WHERE conversation_id = :cid ORDER BY sequence DESC LIMIT :limit"
                    ") recent ORDER BY sequence"
                ),
                {"cid": conversation_id, "limit": limit},
            ).all()
        return [
            Message(
                id=row.id,
                conversation_id=conversation_id,
                role=MessageRole(row.role),
                content=row.content,
                sequence=row.sequence,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def _find_exchange(
        self,
        conn: Connection,
        conversation_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Message, Message] | None:
        receipt = conn.execute(
            text(
                "SELECT payload_hash, message_id FROM message_appends "
                "WHERE conversation_id = :cid AND idempotency_key = :key"
            ),
            {"cid": conversation_id, "key": f"inline:{idempotency_key}"},
        ).first()
        if receipt is None:
            return None
        if receipt.payload_hash != request_hash:
            raise Conflict("chiave di idempotenza già usata con una richiesta diversa")
        assistant = self._load_message(conn, receipt.message_id)
        user_row = conn.execute(
            text(
                "SELECT id FROM messages WHERE conversation_id = :cid "
                "AND sequence = :sequence AND role = 'user'"
            ),
            {"cid": conversation_id, "sequence": assistant.sequence - 1},
        ).one()
        return self._load_message(conn, user_row.id), assistant

    def find_exchange(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Message, Message] | None:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            return self._find_exchange(conn, conversation_id, idempotency_key, request_hash)

    def append_exchange(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        user_content: str,
        assistant_content: str,
        now: datetime,
        *,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[Message, Message]:
        """Prompt e risposta atomici, con ricevuta inline compatibile.

        La ricevuta riusa ``message_appends`` con namespace ``inline:`` e
        punta alla risposta; il messaggio utente è quello immediatamente
        precedente, garantito dallo stesso lock e dalla stessa transazione.
        """
        if idempotency_key is not None and request_hash is None:
            raise ValueError("request_hash obbligatorio con idempotency_key")
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            conversation = conn.execute(
                text("SELECT id FROM conversations WHERE id = :cid FOR UPDATE"),
                {"cid": conversation_id},
            ).first()
            if conversation is None:
                raise NotFound("conversazione non trovata")
            if idempotency_key is not None:
                assert request_hash is not None
                existing = self._find_exchange(conn, conversation_id, idempotency_key, request_hash)
                if existing is not None:
                    return existing

            counter = conn.execute(
                text(
                    "UPDATE conversations SET next_sequence = next_sequence + 2, "
                    "updated_at = GREATEST(updated_at + INTERVAL '1 microsecond', "
                    ":updated + INTERVAL '1 microsecond') WHERE id = :cid "
                    "RETURNING next_sequence - 2 AS user_sequence"
                ),
                {"cid": conversation_id, "updated": now},
            ).one()
            user_id, assistant_id = new_id(), new_id()
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, conversation_id, organization_id, owner_id, role, content, "
                    "sequence, created_at) "
                    "VALUES (:uid, :cid, :org, :owner, 'user', :user_content, :useq, :created), "
                    "(:aid, :cid, :org, :owner, 'assistant', :assistant_content, :aseq, :created)"
                ),
                {
                    "uid": user_id,
                    "aid": assistant_id,
                    "cid": conversation_id,
                    "org": scope.organization_id,
                    "owner": scope.user_id,
                    "user_content": user_content,
                    "assistant_content": assistant_content,
                    "useq": counter.user_sequence,
                    "aseq": counter.user_sequence + 1,
                    "created": now,
                },
            )
            if idempotency_key is not None:
                assert request_hash is not None
                conn.execute(
                    text(
                        "INSERT INTO message_appends "
                        "(id, conversation_id, organization_id, owner_id, "
                        "idempotency_key, payload_hash, message_id, created_at) "
                        "VALUES (:id, :cid, :org, :owner, :key, :hash, :mid, :created)"
                    ),
                    {
                        "id": new_id(),
                        "cid": conversation_id,
                        "org": scope.organization_id,
                        "owner": scope.user_id,
                        "key": f"inline:{idempotency_key}",
                        "hash": request_hash,
                        "mid": assistant_id,
                        "created": now,
                    },
                )
        return (
            Message(
                id=user_id,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=user_content,
                sequence=counter.user_sequence,
                created_at=now,
            ),
            Message(
                id=assistant_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=assistant_content,
                sequence=counter.user_sequence + 1,
                created_at=now,
            ),
        )
