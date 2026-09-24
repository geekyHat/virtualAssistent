"""Fake dei contratti di persistenza e clock (NewRay.md §22.1).

I fake verificano i contratti (schemi, scope, esiti), non la qualità live:
la prova RLS con ruolo applicativo reale sta nei test di integrazione su
PostgreSQL (``tests/integration``).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta

from newray.kernel.errors import Conflict, NotFound
from newray.kernel.identity import Role, Scope, new_id
from newray.modules.conversations import Conversation, Message, MessageRole
from newray.modules.identity import Organization, Session, User
from newray.modules.models import ModelInfo, ModelReadiness, ReadinessState
from newray.modules.profiles import AProfile, ModelBinding, Profile, ProfileVersion


class FakeClock:
    """Clock controllato: il tempo avanza solo quando il test lo decide."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class InMemoryUserRepository:
    """Implementazione di test di ``UserRepository`` con scope obbligatorio."""

    def __init__(self) -> None:
        self._organizations: dict[uuid.UUID, Organization] = {}
        self._users: dict[uuid.UUID, User] = {}

    def add_organization(self, scope: Scope, organization: Organization) -> None:
        if organization.id != scope.organization_id:
            return
        self._organizations[organization.id] = organization

    def get_organization(self, scope: Scope, organization_id: uuid.UUID) -> Organization | None:
        if organization_id != scope.organization_id:
            return None
        return self._organizations.get(organization_id)

    def add_user(self, user: User) -> bool:
        # Stesso vincolo dell'adapter PostgreSQL: un solo owner per
        # installazione, applicato atomicamente (NewRay.md §20.3).
        if user.role is Role.OWNER and any(
            existing.role is Role.OWNER for existing in self._users.values()
        ):
            return False
        self._users[user.id] = user
        return True

    def get_user(self, scope: Scope, user_id: uuid.UUID) -> User | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        # Isolamento server-side (NewRay.md §7.3): i dati identificativi
        # sono privati del loro proprietario; scope errato → nessun dato.
        if user.id != scope.user_id or user.organization_id != scope.organization_id:
            return None
        return user

    def find_owner_by_display_name(self, display_name: str) -> User | None:
        # Stesso contratto del port: match esatto per nome, ristretto
        # all'owner; nessuna esposizione di altri utenti.
        for user in self._users.values():
            if user.role is Role.OWNER and user.display_name == display_name:
                return user
        return None

    def update_credential_hash(self, scope: Scope, new_hash: str) -> None:
        # Stesso invariante dell'adapter Postgres: lo scope deve
        # corrispondere alla riga, il solo lookup pre-auth non basta.
        existing = self._users.get(scope.user_id)
        if existing is None or existing.organization_id != scope.organization_id:
            raise RuntimeError(
                "update_credential_hash: nessuna riga aggiornata per lo scope indicato"
            )
        self._users[scope.user_id] = User(
            id=existing.id,
            organization_id=existing.organization_id,
            display_name=existing.display_name,
            role=existing.role,
            created_at=existing.created_at,
            credential_hash=new_hash,
        )

    def has_owner(self) -> bool:
        return any(user.role is Role.OWNER for user in self._users.values())

    def organization_count(self) -> int:
        return len(self._organizations)

    def user_count(self) -> int:
        return len(self._users)

    def set_credential_hash(self, user_id: uuid.UUID, credential_hash: str | None) -> None:
        """Utility di test: simula sia la migrazione del legacy owner
        (hash None → hash impostato) sia la corruzione voluta del
        record (hash valido → None) per il caso ``CREDENTIAL_NOT_SET``.
        Non fa parte del contratto del port."""
        existing = self._users[user_id]
        self._users[user_id] = User(
            id=existing.id,
            organization_id=existing.organization_id,
            display_name=existing.display_name,
            role=existing.role,
            created_at=existing.created_at,
            credential_hash=credential_hash,
        )


class InMemoryOwnerBootstrap:
    """Implementazione di test di ``OwnerBootstrap``: passo atomico (R02).

    Condivide i store dei test: il vincolo "un solo owner" è verificato
    prima di persistere qualsiasi record, così un conflitto non lascia
    residui, come nella transazione dell'adapter.
    """

    def __init__(self, users: InMemoryUserRepository, sessions: InMemorySessionStore) -> None:
        self._users = users
        self._sessions = sessions

    def execute(self, organization: Organization, user: User, session: Session) -> None:
        if not self._users.add_user(user):
            # Conflict di dominio: niente organizzazione, niente sessione.
            raise Conflict("il proprietario è già stato creato")
        self._users.add_organization(Scope(organization.id, user.id), organization)
        self._sessions.save(session)


class InMemorySessionStore:
    """Implementazione di test di ``SessionStore``."""

    def __init__(self) -> None:
        self._sessions: dict[uuid.UUID, Session] = {}

    def save(self, session: Session) -> None:
        self._sessions[session.id] = session

    def find_for_resolution(self, session_id: uuid.UUID) -> Session | None:
        # L'ID opaco è il riferimento presentato: il fake lo restituisce e
        # il caso d'uso valuta revoca e scadenza (NewRay.md §7.1).
        return self._sessions.get(session_id)

    def session_count(self) -> int:
        return len(self._sessions)

    def revoke(self, scope: Scope, session_id: uuid.UUID) -> bool:
        session = self._sessions.get(session_id)
        if (
            session is None
            or session.user_id != scope.user_id
            or session.organization_id != scope.organization_id
        ):
            return False
        session.revoked = True
        return True

    def revoke_all_for_user(self, scope: Scope) -> int:
        revoked = 0
        for session in self._sessions.values():
            if (
                session.user_id == scope.user_id
                and session.organization_id == scope.organization_id
                and not session.revoked
            ):
                session.revoked = True
                revoked += 1
        return revoked


class InMemoryConversationRepository:
    """Implementazione di test di ``ConversationRepository`` con scope obbligatorio."""

    def __init__(self) -> None:
        self._conversations: dict[uuid.UUID, Conversation] = {}

    def add_conversation(self, conversation: Conversation) -> None:
        self._conversations[conversation.id] = conversation

    def get_conversation(self, scope: Scope, conversation_id: uuid.UUID) -> Conversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            return None
        # Stesso isolamento server-side dell'adapter (NewRay.md §7.3):
        # fuori scope → nessun dato, nessuna rivelazione di esistenza.
        if (
            conversation.owner_id != scope.user_id
            or conversation.organization_id != scope.organization_id
        ):
            return None
        return conversation

    def list_conversations(
        self,
        scope: Scope,
        *,
        before: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> list[Conversation]:
        items = [
            c
            for c in self._conversations.values()
            if c.owner_id == scope.user_id and c.organization_id == scope.organization_id
        ]
        items.sort(key=lambda c: (c.updated_at, c.id), reverse=True)
        if before is not None:
            items = [c for c in items if (c.updated_at, c.id) < before]
        return items[:limit]

    def rename_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        title: str,
        now: datetime,
        *,
        expected_updated_at: datetime | None = None,
    ) -> Conversation | None:
        """Stesso contratto dell'adapter: versione attesa → ``Conflict``."""
        conversation = self.get_conversation(scope, conversation_id)
        if conversation is None:
            return None
        if expected_updated_at is not None and conversation.updated_at != expected_updated_at:
            raise Conflict("la conversazione è cambiata dopo l'ultima lettura")
        updated_at = max(now, conversation.updated_at + timedelta(microseconds=1))
        renamed = Conversation(
            id=conversation.id,
            organization_id=conversation.organization_id,
            owner_id=conversation.owner_id,
            title=title,
            created_at=conversation.created_at,
            updated_at=updated_at,
        )
        self._conversations[conversation_id] = renamed
        return renamed

    def delete_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        conversation = self.get_conversation(scope, conversation_id)
        if conversation is None:
            return False
        if expected_updated_at is not None and conversation.updated_at != expected_updated_at:
            raise Conflict("la conversazione è cambiata dopo l'ultima lettura")
        del self._conversations[conversation_id]
        return True


class InMemoryMessageStore:
    """Implementazione di test di ``MessageStore``: sequenza strettamente crescente.

    Il contratto del port è qui: ordine, sequenza, cursore. Lo scope è
    verificato dal caso d'uso (la conversazione) e dall'adapter con la RLS
    reale su PostgreSQL (``tests/integration``).
    """

    def __init__(self, conversations: InMemoryConversationRepository | None = None) -> None:
        self._messages: list[Message] = []
        self._conversations = conversations
        # Ricevute di idempotenza: (scope, conversazione, chiave) →
        # (hash del payload, messaggio). Stesso contratto dell'adapter:
        # stesso input → stesso esito, input diverso → ``Conflict``.
        self._receipts: dict[tuple[Scope, uuid.UUID, str], tuple[str, Message]] = {}
        self._exchange_receipts: dict[
            tuple[Scope, uuid.UUID, str], tuple[str, Message, Message]
        ] = {}

    @staticmethod
    def _payload_hash(role: MessageRole, content: str) -> str:
        return hashlib.sha256(f"{role.value}\x00{content}".encode()).hexdigest()

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
        if idempotency_key is not None:
            receipt = self._receipts.get((scope, conversation_id, idempotency_key))
            if receipt is not None:
                stored_hash, stored_message = receipt
                if stored_hash != self._payload_hash(role, content):
                    raise Conflict("chiave di idempotenza già usata con un contenuto diverso")
                return stored_message
        next_sequence = 1 + max(
            (m.sequence for m in self._messages if m.conversation_id == conversation_id),
            default=0,
        )
        message = Message(
            id=new_id(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=next_sequence,
            created_at=now,
        )
        self._messages.append(message)
        if self._conversations is not None:
            conversation = self._conversations.get_conversation(scope, conversation_id)
            if conversation is not None:
                self._conversations._conversations[conversation_id] = Conversation(
                    id=conversation.id,
                    organization_id=conversation.organization_id,
                    owner_id=conversation.owner_id,
                    title=conversation.title,
                    created_at=conversation.created_at,
                    updated_at=max(
                        now + timedelta(microseconds=1),
                        conversation.updated_at + timedelta(microseconds=1),
                    ),
                )
        if idempotency_key is not None:
            self._receipts[(scope, conversation_id, idempotency_key)] = (
                self._payload_hash(role, content),
                message,
            )
        return message

    def list(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int,
    ) -> list[Message]:
        items = sorted(
            (
                m
                for m in self._messages
                if m.conversation_id == conversation_id and m.sequence > after_sequence
            ),
            key=lambda m: m.sequence,
        )
        return items[:limit]

    def list_recent(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        limit: int,
    ) -> list[Message]:
        items = sorted(
            (m for m in self._messages if m.conversation_id == conversation_id),
            key=lambda m: m.sequence,
        )
        return items[-limit:]

    def find_exchange(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Message, Message] | None:
        receipt = self._exchange_receipts.get((scope, conversation_id, idempotency_key))
        if receipt is None:
            return None
        stored_hash, user_message, assistant_message = receipt
        if stored_hash != request_hash:
            raise Conflict("chiave di idempotenza già usata con una richiesta diversa")
        return user_message, assistant_message

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
        if idempotency_key is not None:
            assert request_hash is not None
            existing = self.find_exchange(scope, conversation_id, idempotency_key, request_hash)
            if existing is not None:
                return existing
        user_message = self.append(
            scope,
            conversation_id,
            MessageRole.USER,
            user_content,
            now,
        )
        assistant_message = self.append(
            scope,
            conversation_id,
            MessageRole.ASSISTANT,
            assistant_content,
            now,
        )
        if idempotency_key is not None:
            assert request_hash is not None
            self._exchange_receipts[(scope, conversation_id, idempotency_key)] = (
                request_hash,
                user_message,
                assistant_message,
            )
        return user_message, assistant_message


class InMemoryModelCatalog:
    """Implementazione di test di ``ModelCatalog``: lista configurabile.

    Il catalogo è lo stato del runtime locale, non un dato privato:
    nessun scope (NewRay.md §9.1). Asincrona come la porta: il runtime
    è un servizio di rete locale (B-03).
    """

    def __init__(self, models: tuple[ModelInfo, ...] = ()) -> None:
        self._models = list(models)

    async def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        if not model_name:
            return ModelReadiness(ReadinessState.NOT_CONFIGURED, None)
        if not self._models:
            return ModelReadiness(ReadinessState.CATALOG_EMPTY, model_name)
        selected = next((model for model in self._models if model.name == model_name), None)
        if selected is None:
            return ModelReadiness(ReadinessState.MODEL_MISSING, model_name)
        return ModelReadiness(
            ReadinessState.INSTALLED_UNVERIFIED,
            model_name,
            selected.digest,
            selected.capabilities,
        )


class InMemoryModelBindingStore:
    """Implementazione di test di ``ModelBindingStore`` con scope obbligatorio."""

    def __init__(self) -> None:
        self._bindings: dict[uuid.UUID, ModelBinding] = {}

    def add_binding(self, binding: ModelBinding) -> None:
        self._bindings[binding.id] = binding

    def get_binding(self, scope: Scope, binding_id: uuid.UUID) -> ModelBinding | None:
        binding = self._bindings.get(binding_id)
        if binding is None:
            return None
        if binding.organization_id != scope.organization_id or binding.owner_id != scope.user_id:
            return None
        return binding

    def find_binding_by_name(self, scope: Scope, name: str) -> ModelBinding | None:
        for binding in self._bindings.values():
            if (
                binding.organization_id == scope.organization_id
                and binding.owner_id == scope.user_id
                and binding.name == name
            ):
                return binding
        return None


class InMemoryProfileRepository:
    """Implementazione di test di ``ProfileRepository`` con scope obbligatorio.

    Ogni profilo restituisce la versione corrente (l'ultima per data);
    l'ordine è deterministico per tipo di profilo, come l'adapter.
    """

    def __init__(self) -> None:
        self._profiles: dict[uuid.UUID, Profile] = {}
        self._versions: dict[uuid.UUID, ProfileVersion] = {}

    def add_profile(self, profile: Profile) -> None:
        self._profiles[profile.id] = profile

    def add_version(self, version: ProfileVersion) -> None:
        self._versions[version.id] = version

    def list_profiles(self, scope: Scope) -> list[tuple[Profile, ProfileVersion]]:
        return [
            item
            for profile in sorted(self._profiles.values(), key=lambda p: (p.kind.value, p.id))
            if (item := self.get_profile(scope, profile.id)) is not None
        ]

    def get_profile(
        self, scope: Scope, profile_id: uuid.UUID
    ) -> tuple[Profile, ProfileVersion] | None:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return None
        if profile.organization_id != scope.organization_id or profile.owner_id != scope.user_id:
            return None
        version = self._current(scope, profile)
        if version is None:
            return None
        return profile, version

    def _current(self, scope: Scope, profile: Profile) -> ProfileVersion | None:
        versions = [
            v
            for v in self._versions.values()
            if v.profile_id == profile.id
            and v.organization_id == scope.organization_id
            and v.owner_id == scope.user_id
        ]
        if not versions:
            return None
        return max(versions, key=lambda v: (v.created_at, v.id))

    def find_profile_by_kind(self, scope: Scope, kind: AProfile) -> Profile | None:
        # Indipendente dalla presenza di versioni: serve al seeding per
        # riconoscere i profili parzialmente creati (R03).
        for profile in self._profiles.values():
            if (
                profile.organization_id == scope.organization_id
                and profile.owner_id == scope.user_id
                and profile.kind is kind
            ):
                return profile
        return None

    def has_version(self, scope: Scope, profile_id: uuid.UUID, version: str) -> bool:
        return any(
            v.profile_id == profile_id
            and v.organization_id == scope.organization_id
            and v.owner_id == scope.user_id
            and v.version == version
            for v in self._versions.values()
        )


class InMemoryProfileDefaultsSeeder:
    """Implementazione di test di ``ProfileDefaultsSeeder`` (R03).

    Condivide i store dei test e riflette il contratto dell'adapter: le
    righe esistenti (binding per nome, profilo per tipo, versione per
    profilo) sono riusate senza riscrittura, le chiavi referenziano gli
    ID reali e il passo è idempotente: una seconda chiamata non duplica
    nulla, come la transazione ``ON CONFLICT DO NOTHING`` + read-back.
    """

    def __init__(
        self,
        profiles: InMemoryProfileRepository,
        bindings: InMemoryModelBindingStore,
    ) -> None:
        self._profiles = profiles
        self._bindings = bindings

    def execute(
        self,
        scope: Scope,
        binding: ModelBinding,
        profiles: list[tuple[Profile, ProfileVersion]],
    ) -> None:
        existing_binding = self._bindings.find_binding_by_name(scope, binding.name)
        binding_id = existing_binding.id if existing_binding is not None else binding.id
        if existing_binding is None:
            self._bindings.add_binding(binding)
        for profile, version in profiles:
            existing_profile = self._profiles.find_profile_by_kind(scope, profile.kind)
            profile_id = existing_profile.id if existing_profile is not None else profile.id
            if existing_profile is None:
                self._profiles.add_profile(profile)
            if not self._profiles.has_version(scope, profile_id, version.version):
                self._profiles.add_version(
                    ProfileVersion(
                        id=version.id,
                        profile_id=profile_id,
                        organization_id=scope.organization_id,
                        owner_id=scope.user_id,
                        version=version.version,
                        model_binding_id=binding_id,
                        instructions=version.instructions,
                        created_at=version.created_at,
                    )
                )


class InMemoryProfileVersionWriter:
    """Implementazione di test di ``ProfileVersionWriter`` (B-02.1).

    Stessa semantica dell'adapter Postgres: nessun lock di riga, "una
    versione per (profile_id, version)" è la barriera di concorrenza,
    verificata qui prima dell'inserimento — non un lock preso in anticipo.
    """

    def __init__(
        self,
        profiles: InMemoryProfileRepository,
        bindings: InMemoryModelBindingStore,
    ) -> None:
        self._profiles = profiles
        self._bindings = bindings
        # (org, owner, profile_id, idempotency_key) -> (payload_hash, profile_version_id)
        self._switches: dict[
            tuple[uuid.UUID, uuid.UUID, uuid.UUID, str], tuple[str, uuid.UUID]
        ] = {}

    def find_switch_receipt(
        self, scope: Scope, profile_id: uuid.UUID, idempotency_key: str, request_hash: str
    ) -> tuple[ProfileVersion, ModelBinding] | None:
        key = (scope.organization_id, scope.user_id, profile_id, idempotency_key)
        receipt = self._switches.get(key)
        if receipt is None:
            return None
        receipt_hash, version_id = receipt
        if receipt_hash != request_hash:
            raise Conflict("chiave di idempotenza già usata con una richiesta diversa")
        version = self._profiles._versions[version_id]  # noqa: SLF001
        binding = self._bindings.get_binding(scope, version.model_binding_id)
        assert binding is not None
        return version, binding

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
        profile = self._profiles._profiles.get(profile_id)  # noqa: SLF001
        if (
            profile is None
            or profile.organization_id != scope.organization_id
            or profile.owner_id != scope.user_id
        ):
            raise NotFound("profilo non trovato")

        if idempotency_key is not None:
            assert request_hash is not None
            receipt = self.find_switch_receipt(scope, profile_id, idempotency_key, request_hash)
            if receipt is not None:
                return receipt

        current = self._profiles._current(scope, profile)  # noqa: SLF001
        if expected_profile_version is not None and (
            current is None or current.version != expected_profile_version
        ):
            raise Conflict("la versione del profilo è cambiata dopo l'ultima lettura")

        # Barriera di concorrenza: stessa versione già presente per il profilo
        # (stesso ruolo del vincolo UNIQUE dell'adapter Postgres).
        if self._profiles.has_version(scope, profile_id, version.version):
            raise Conflict("la versione del profilo è cambiata dopo l'ultima lettura")

        self._bindings.add_binding(binding)
        self._profiles.add_version(version)
        if idempotency_key is not None:
            assert request_hash is not None
            key = (scope.organization_id, scope.user_id, profile_id, idempotency_key)
            self._switches[key] = (request_hash, version.id)
        return version, binding
