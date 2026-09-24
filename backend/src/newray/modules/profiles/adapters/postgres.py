"""Adapter PostgreSQL del modulo profiles (NewRay.md §7.3; ADR 0002).

Il ruolo applicativo (``newray_app``) non è proprietario delle tabelle e
non ha superuser né BYPASSRLS: la RLS (FORCE, migration 0003) filtra ogni
riga. Il contesto di scope è impostato per transazione
(``set_config(..., is_local=true)``): il proprietario è il principal, mai
derivato dal payload.

Versioni e binding sono immutabili: l'adapter espone solo inserimento e
lettura, e i grant del ruolo applicativo riflettono lo stesso invariante
(nessun UPDATE/DELETE, migration 0003).

Il SQL resta in questo file: dominio e applicazione non conoscono né
SQLAlchemy né PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.exc import IntegrityError

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Scope, new_id

from ..domain import AProfile, ModelBinding, Profile, ProfileVersion

_PROFILE_COLUMNS = (
    "p.id AS profile_id, p.organization_id, p.owner_id, p.kind, p.display_name, "
    "p.created_at AS profile_created, p.updated_at"
)
_VERSION_COLUMNS = (
    "v.id AS version_id, v.version, v.model_binding_id, v.instructions, "
    "v.created_at AS version_created"
)


def _set_scope_context(conn: Connection, scope: Scope) -> None:
    """Contesto di scope per la transazione corrente (locale, auto-pulito)."""
    conn.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(scope.user_id)})
    conn.execute(
        text("SELECT set_config('app.organization_id', :v, true)"),
        {"v": str(scope.organization_id)},
    )


def _profile(row: Row[Any]) -> Profile:
    return Profile(
        id=row.profile_id,
        organization_id=row.organization_id,
        owner_id=row.owner_id,
        kind=AProfile(row.kind),
        display_name=row.display_name,
        created_at=row.profile_created,
        updated_at=row.updated_at,
    )


def _version(row: Row[Any]) -> ProfileVersion:
    return ProfileVersion(
        id=row.version_id,
        profile_id=row.profile_id,
        organization_id=row.organization_id,
        owner_id=row.owner_id,
        version=row.version,
        model_binding_id=row.model_binding_id,
        instructions=row.instructions,
        created_at=row.version_created,
    )


class PostgresProfileRepository:
    """``ProfileRepository`` su PostgreSQL: scope obbligatorio, RLS di barriera."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_profile(self, profile: Profile) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(profile.organization_id, profile.owner_id))
            conn.execute(
                text(
                    "INSERT INTO profiles "
                    "(id, organization_id, owner_id, kind, display_name, created_at, updated_at) "
                    "VALUES (:id, :org, :owner, :kind, :name, :created, :updated)"
                ),
                {
                    "id": profile.id,
                    "org": profile.organization_id,
                    "owner": profile.owner_id,
                    "kind": profile.kind.value,
                    "name": profile.display_name,
                    "created": profile.created_at,
                    "updated": profile.updated_at,
                },
            )

    def add_version(self, version: ProfileVersion) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(version.organization_id, version.owner_id))
            conn.execute(
                text(
                    "INSERT INTO profile_versions "
                    "(id, profile_id, organization_id, owner_id, version, "
                    " model_binding_id, instructions, created_at) "
                    "VALUES (:id, :profile, :org, :owner, :version, "
                    " :binding, :instructions, :created)"
                ),
                {
                    "id": version.id,
                    "profile": version.profile_id,
                    "org": version.organization_id,
                    "owner": version.owner_id,
                    "version": version.version,
                    "binding": version.model_binding_id,
                    "instructions": version.instructions,
                    "created": version.created_at,
                },
            )

    def list_profiles(self, scope: Scope) -> list[tuple[Profile, ProfileVersion]]:
        rows = self._select_latest(scope)
        return [(_profile(row), _version(row)) for row in rows]

    def get_profile(
        self, scope: Scope, profile_id: uuid.UUID
    ) -> tuple[Profile, ProfileVersion] | None:
        rows = self._select_latest(scope, profile_id)
        if not rows:
            return None
        return _profile(rows[0]), _version(rows[0])

    def _select_latest(self, scope: Scope, profile_id: uuid.UUID | None = None) -> list[Row[Any]]:
        """Ultima versione per profilo, selezionata in SQL (B-03.2-07).

        ``DISTINCT ON (p.id)`` con l'ORDER BY appropriato restituisce una
        sola riga per profilo, quella con la coppia ``(created_at, id)``
        massima. Lista e lettura singola condividono la stessa regola:
        non possono divergere per pareggio temporale né per ordine di
        insert. Il read non carica tutte le versioni in memoria.
        """
        sql = (
            f"SELECT DISTINCT ON (p.id) {_PROFILE_COLUMNS}, {_VERSION_COLUMNS} "
            "FROM profiles p "
            "JOIN profile_versions v "
            "ON v.profile_id = p.id AND v.organization_id = p.organization_id "
            "AND v.owner_id = p.owner_id "
        )
        params: dict[str, object] = {}
        if profile_id is not None:
            sql += "WHERE p.id = :id "
            params["id"] = profile_id
        # ``DISTINCT ON (p.id)`` richiede che ``p.id`` sia la prima chiave
        # dell'ORDER BY. La più recente vince per ``created_at`` DESC, con
        # ``v.id`` DESC come tiebreaker deterministico.
        sql += "ORDER BY p.id, v.created_at DESC, v.id DESC"
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            return list(conn.execute(text(sql), params).all())


class PostgresProfileDefaultsSeeder:
    """``ProfileDefaultsSeeder`` su PostgreSQL: un'unica transazione (R03).

    Binding, profili e versioni corrono nello stesso contesto di
    transazione (``engine.begin``). Ogni riga usa ``ON CONFLICT DO
    NOTHING`` sui vincoli unici di migrazione 0003 e un read-back per
    recuperare l'ID reale: il concorrente che perde la gara inserisce il
    binding/profilo in modo no-op e riusa gli ID del vincente, così le
    versioni referenziano sempre righe esistenti. Niente 500, niente
    stato parziale, niente organizzazione orfana.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(
        self,
        scope: Scope,
        binding: ModelBinding,
        profiles: list[tuple[Profile, ProfileVersion]],
    ) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            binding_id = self._ensure_binding(conn, binding)
            for profile, version in profiles:
                profile_id = self._ensure_profile(conn, profile)
                self._ensure_version(conn, profile_id, binding_id, version)

    def _ensure_binding(self, conn: Connection, binding: ModelBinding) -> uuid.UUID:
        conn.execute(
            text(
                "INSERT INTO model_bindings "
                "(id, organization_id, owner_id, name, runtime, model_name, "
                " parameters, created_at) "
                "VALUES (:id, :org, :owner, :name, :runtime, :model, "
                " :parameters, :created) "
                "ON CONFLICT (organization_id, owner_id, name) DO NOTHING"
            ),
            {
                "id": binding.id,
                "org": binding.organization_id,
                "owner": binding.owner_id,
                "name": binding.name,
                "runtime": binding.runtime,
                "model": binding.model_name,
                "parameters": json.dumps(binding.parameters),
                "created": binding.created_at,
            },
        )
        return self._read_back(
            conn,
            "SELECT id FROM model_bindings "
            "WHERE organization_id = :org AND owner_id = :owner AND name = :name",
            {"org": binding.organization_id, "owner": binding.owner_id, "name": binding.name},
            "binding del seeding",
        )

    def _ensure_profile(self, conn: Connection, profile: Profile) -> uuid.UUID:
        conn.execute(
            text(
                "INSERT INTO profiles "
                "(id, organization_id, owner_id, kind, display_name, created_at, updated_at) "
                "VALUES (:id, :org, :owner, :kind, :name, :created, :updated) "
                "ON CONFLICT (organization_id, owner_id, kind) DO NOTHING"
            ),
            {
                "id": profile.id,
                "org": profile.organization_id,
                "owner": profile.owner_id,
                "kind": profile.kind.value,
                "name": profile.display_name,
                "created": profile.created_at,
                "updated": profile.updated_at,
            },
        )
        return self._read_back(
            conn,
            "SELECT id FROM profiles "
            "WHERE organization_id = :org AND owner_id = :owner AND kind = :kind",
            {
                "org": profile.organization_id,
                "owner": profile.owner_id,
                "kind": profile.kind.value,
            },
            "profilo del seeding",
        )

    def _ensure_version(
        self,
        conn: Connection,
        profile_id: uuid.UUID,
        binding_id: uuid.UUID,
        version: ProfileVersion,
    ) -> None:
        # Le chiavi referenziano le righe reali lette sopra, non gli ID
        # pre-generati del chiamante: se il concorrente ha già creato il
        # profilo, la versione resta coerente con il vincente.
        conn.execute(
            text(
                "INSERT INTO profile_versions "
                "(id, profile_id, organization_id, owner_id, version, "
                " model_binding_id, instructions, created_at) "
                "VALUES (:id, :profile, :org, :owner, :version, "
                " :binding, :instructions, :created) "
                "ON CONFLICT (profile_id, version) DO NOTHING"
            ),
            {
                "id": version.id,
                "profile": profile_id,
                "org": version.organization_id,
                "owner": version.owner_id,
                "version": version.version,
                "binding": binding_id,
                "instructions": version.instructions,
                "created": version.created_at,
            },
        )

    @staticmethod
    def _read_back(conn: Connection, sql: str, params: dict[str, object], cosa: str) -> uuid.UUID:
        row = conn.execute(text(sql), params).first()
        if row is None:
            # Invariante dello schema: dopo l'insert (o del concorrente)
            # la riga deve essere visibile. Assente → difetto del server.
            raise RuntimeError(f"{cosa} mancante dal read-back del seeding")
        # ``row.id`` è tipizzato ``Any`` da SQLAlchemy: il tipo di dominio è
        # ``UUID`` per costruzione dello schema (migration 0003 e affini).
        identifier: uuid.UUID = row.id
        return identifier


class PostgresModelBindingStore:
    """``ModelBindingStore`` su PostgreSQL: scope obbligatorio, immutabile."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_binding(self, binding: ModelBinding) -> None:
        with self._engine.begin() as conn:
            _set_scope_context(conn, Scope(binding.organization_id, binding.owner_id))
            conn.execute(
                text(
                    "INSERT INTO model_bindings "
                    "(id, organization_id, owner_id, name, runtime, model_name, "
                    " parameters, created_at) "
                    "VALUES (:id, :org, :owner, :name, :runtime, :model, "
                    " :parameters, :created)"
                ),
                {
                    "id": binding.id,
                    "org": binding.organization_id,
                    "owner": binding.owner_id,
                    "name": binding.name,
                    "runtime": binding.runtime,
                    "model": binding.model_name,
                    # JSONB: psycopg accetta la serializzazione esplicita;
                    # il cast lo applica il tipo della colonna.
                    "parameters": json.dumps(binding.parameters),
                    "created": binding.created_at,
                },
            )

    def get_binding(self, scope: Scope, binding_id: uuid.UUID) -> ModelBinding | None:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            row = conn.execute(
                text(
                    "SELECT id, organization_id, owner_id, name, runtime, model_name, "
                    "parameters, created_at "
                    "FROM model_bindings WHERE id = :id"
                ),
                {"id": binding_id},
            ).first()
        if row is None:
            return None
        return ModelBinding(
            id=row.id,
            organization_id=row.organization_id,
            owner_id=row.owner_id,
            name=row.name,
            runtime=row.runtime,
            model_name=row.model_name,
            parameters=row.parameters,
            created_at=row.created_at,
        )


def _is_unique_violation(exc: IntegrityError, constraint: str) -> bool:
    """Riconosce la violazione di un vincolo UNIQUE specifico (sqlstate
    23505) senza confondersi con altri ``IntegrityError`` (es. FK)."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505":
        return False
    diag = getattr(orig, "diag", None)
    return diag is not None and getattr(diag, "constraint_name", None) == constraint


class PostgresProfileVersionWriter:
    """``ProfileVersionWriter`` su PostgreSQL (B-02.1).

    ``profiles``/``model_bindings``/``profile_versions`` non hanno grant
    UPDATE (0003, per costruzione: versioni e binding immutabili anche a
    livello di privilegio) — niente ``SELECT ... FOR UPDATE`` per
    serializzare lo switch. La barriera di concorrenza è il vincolo
    UNIQUE ``profile_versions_profile_version``: due scritture con la
    stessa versione attesa calcolano la stessa prossima versione, la
    prima vince l'INSERT e la seconda riceve ``Conflict`` dalla
    violazione del vincolo, mai una riga silenziosamente sovrascritta.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def find_switch_receipt(
        self, scope: Scope, profile_id: uuid.UUID, idempotency_key: str, request_hash: str
    ) -> tuple[ProfileVersion, ModelBinding] | None:
        with self._engine.connect() as conn:
            _set_scope_context(conn, scope)
            receipt = conn.execute(
                text(
                    "SELECT payload_hash, profile_version_id FROM profile_version_switches "
                    "WHERE profile_id = :pid AND idempotency_key = :key "
                    "AND organization_id = :org AND owner_id = :owner"
                ),
                {
                    "pid": profile_id,
                    "key": idempotency_key,
                    "org": scope.organization_id,
                    "owner": scope.user_id,
                },
            ).first()
            if receipt is None:
                return None
            if receipt.payload_hash != request_hash:
                raise Conflict("chiave di idempotenza già usata con una richiesta diversa")
            return self._load_version_and_binding(conn, receipt.profile_version_id)

    def switch_model(
        self,
        scope: Scope,
        profile_id: uuid.UUID,
        binding: ModelBinding,
        version: ProfileVersion,
        *,
        expected_profile_version: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> tuple[ProfileVersion, ModelBinding]:
        with self._engine.begin() as conn:
            _set_scope_context(conn, scope)
            exists = conn.execute(
                text("SELECT 1 FROM profiles WHERE id = :id"), {"id": profile_id}
            ).first()
            if exists is None:
                raise NotFound("profilo non trovato")

            if idempotency_key is not None:
                # Richieste concorrenti con la stessa chiave aspettano la
                # ricevuta vincente prima del replay check.
                conn.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {
                        "key": (
                            f"{scope.organization_id}:{scope.user_id}:"
                            f"{profile_id}:{idempotency_key}"
                        )
                    },
                )
                receipt = conn.execute(
                    text(
                        "SELECT payload_hash, profile_version_id FROM profile_version_switches "
                        "WHERE profile_id = :pid AND idempotency_key = :key "
                        "AND organization_id = :org AND owner_id = :owner"
                    ),
                    {
                        "pid": profile_id,
                        "key": idempotency_key,
                        "org": scope.organization_id,
                        "owner": scope.user_id,
                    },
                ).first()
                if receipt is not None:
                    if receipt.payload_hash != request_hash:
                        raise Conflict("chiave di idempotenza già usata con una richiesta diversa")
                    return self._load_version_and_binding(conn, receipt.profile_version_id)

            if expected_profile_version is not None:
                current = conn.execute(
                    text(
                        "SELECT version FROM profile_versions "
                        "WHERE profile_id = :pid "
                        "ORDER BY created_at DESC, id DESC LIMIT 1"
                    ),
                    {"pid": profile_id},
                ).first()
                if current is None or current.version != expected_profile_version:
                    raise Conflict("la versione del profilo è cambiata dopo l'ultima lettura")

            conn.execute(
                text(
                    "INSERT INTO model_bindings "
                    "(id, organization_id, owner_id, name, runtime, model_name, "
                    " parameters, created_at) "
                    "VALUES (:id, :org, :owner, :name, :runtime, :model, :parameters, :created)"
                ),
                {
                    "id": binding.id,
                    "org": binding.organization_id,
                    "owner": binding.owner_id,
                    "name": binding.name,
                    "runtime": binding.runtime,
                    "model": binding.model_name,
                    "parameters": json.dumps(binding.parameters),
                    "created": binding.created_at,
                },
            )

            # Il vincolo UNIQUE (profile_id, version) è la barriera reale
            # contro la corsa fra due switch concorrenti con la stessa
            # versione attesa: qui si converte la violazione in Conflict.
            try:
                conn.execute(
                    text(
                        "INSERT INTO profile_versions "
                        "(id, profile_id, organization_id, owner_id, version, "
                        " model_binding_id, instructions, created_at) "
                        "VALUES (:id, :profile, :org, :owner, :version, "
                        " :binding, :instructions, :created)"
                    ),
                    {
                        "id": version.id,
                        "profile": profile_id,
                        "org": version.organization_id,
                        "owner": version.owner_id,
                        "version": version.version,
                        "binding": binding.id,
                        "instructions": version.instructions,
                        "created": version.created_at,
                    },
                )
            except IntegrityError as exc:
                if _is_unique_violation(exc, "profile_versions_profile_version"):
                    raise Conflict(
                        "la versione del profilo è cambiata dopo l'ultima lettura"
                    ) from exc
                raise

            if idempotency_key is not None:
                assert request_hash is not None
                conn.execute(
                    text(
                        "INSERT INTO profile_version_switches "
                        "(id, profile_id, organization_id, owner_id, idempotency_key, "
                        " payload_hash, profile_version_id, created_at) "
                        "VALUES (:id, :pid, :org, :owner, :key, :hash, :vid, :created)"
                    ),
                    {
                        "id": new_id(),
                        "pid": profile_id,
                        "org": scope.organization_id,
                        "owner": scope.user_id,
                        "key": idempotency_key,
                        "hash": request_hash,
                        "vid": version.id,
                        "created": version.created_at,
                    },
                )
        return version, binding

    @staticmethod
    def _load_version_and_binding(
        conn: Connection, version_id: uuid.UUID
    ) -> tuple[ProfileVersion, ModelBinding]:
        row = conn.execute(
            text(
                "SELECT v.id AS version_id, v.profile_id, v.organization_id, v.owner_id, "
                "v.version, v.model_binding_id, v.instructions, "
                "v.created_at AS version_created, "
                "b.name AS binding_name, b.runtime, b.model_name, b.parameters, "
                "b.created_at AS binding_created "
                "FROM profile_versions v "
                "JOIN model_bindings b ON b.id = v.model_binding_id "
                "WHERE v.id = :id"
            ),
            {"id": version_id},
        ).one()
        persisted_version = ProfileVersion(
            id=row.version_id,
            profile_id=row.profile_id,
            organization_id=row.organization_id,
            owner_id=row.owner_id,
            version=row.version,
            model_binding_id=row.model_binding_id,
            instructions=row.instructions,
            created_at=row.version_created,
        )
        persisted_binding = ModelBinding(
            id=row.model_binding_id,
            organization_id=row.organization_id,
            owner_id=row.owner_id,
            name=row.binding_name,
            runtime=row.runtime,
            model_name=row.model_name,
            parameters=row.parameters,
            created_at=row.binding_created,
        )
        return persisted_version, persisted_binding
