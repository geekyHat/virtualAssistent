"""Porte di persistenza dell'identità (NewRay.md §§5.1, 6.1, 7.3).

Gli adapter implementano queste porte: nei test le implementazioni fake
(``tests/fakes``) verificano il contratto; l'adapter PostgreSQL
(``adapters/postgres.py``, A-03) lo verifica su PostgreSQL reale con RLS.
Dominio e applicazione non conoscono il trasporto.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from newray.kernel.identity import Scope

from .domain import Organization, Session, User


class UserRepository(Protocol):
    """Accesso a organizzazioni e utenti.

    Ogni lettura su dati privati richiede scope obbligatorio (NewRay.md
    §7.3): non esistono letture senza scope. Il vincolo "un owner per
    organizzazione" è applicato in ``add_user``; il bootstrap monouso è
    atomico tramite ``OwnerBootstrap``: niente letture preliminari fuori
    scope.
    """

    def add_organization(self, scope: Scope, organization: Organization) -> None: ...

    def get_organization(self, scope: Scope, organization_id: uuid.UUID) -> Organization | None: ...

    def add_user(self, user: User) -> bool:
        """Inserimento atomico.

        Restituisce False se il proprietario esiste già (bootstrap locale
        monouso per installazione, NewRay.md §20.3), True altrimenti.
        """
        ...

    def get_user(self, scope: Scope, user_id: uuid.UUID) -> User | None:
        """Lettura privata: scope obbligatorio, mai facoltativo."""
        ...

    def find_owner_by_display_name(self, display_name: str) -> User | None:
        """Lookup pre-autenticazione ai soli fini del login (B-03.2-14).

        La lettura è ristretta al ``role = 'owner'`` — l'edizione personale
        ha un unico owner per installazione (NewRay.md §20.3) e questa è
        l'unica identità presentabile senza cookie. La policy RLS
        dedicata (migrazione 0005) autorizza la SELECT solo con il flag
        di lookup attivo per la transazione corrente: nessuna
        esposizione di altri utenti né trasformazione del canale in un
        oracolo per enumerare i membri.

        Restituisce ``None`` senza rivelare la causa (utente sconosciuto
        contro credenziale sbagliata): il caso d'uso mappa in
        ``INVALID_CREDENTIALS`` uniforme.
        """
        ...

    def update_credential_hash(self, scope: Scope, new_hash: str) -> None:
        """Aggiorna l'hash della credenziale (rehash PBKDF2 → Argon2id,
        o recupero locale B-03.2-14).

        Lo scope (non il solo ID) è necessario: la policy generale
        ``users_isolation`` governa anche l'UPDATE e richiede i GUC di
        scope della transazione, non il solo flag di lookup pre-auth
        (quello autorizza esclusivamente la SELECT). L'aggiornamento è
        atomico e non altera altri campi dell'utente. L'utente deve
        esistere — il caso d'uso chiama questo metodo solo dopo una
        verifica riuscita o un lookup riuscito.
        """
        ...

    def has_owner(self) -> bool:
        """Stato pubblico dell'installazione: esiste già un owner?

        Usato dal contratto ``GET /session/status`` per distinguere prima
        installazione (bootstrap) da rientro autenticato (login) senza
        rivelare il nome. Usa la stessa policy di ``find_owner_*``.
        """
        ...


class OwnerBootstrap(Protocol):
    """Bootstrap del proprietario: organizzazione, owner e sessione in una
    sola transazione atomica (R02, B-03.2-02).

    Se il proprietario esiste già, l'intera transazione è annullata
    (organizzazione inclusa) e si solleva ``Conflict`` di dominio: niente
    stato parziale, niente organizzazione orfana; due bootstrap simultanei
    producono un solo vincente e un conflitto gestito.
    """

    def execute(self, organization: Organization, user: User, session: Session) -> None: ...


class SessionStore(Protocol):
    """Sessioni opache: lookup solo per ID emesso dal server."""

    def save(self, session: Session) -> None: ...

    def find_for_resolution(self, session_id: uuid.UUID) -> Session | None:
        """Lettura in risoluzione: l'ID opaco presentato viene verificato,
        non fidato (NewRay.md §7.1)."""
        ...

    def revoke(self, scope: Scope, session_id: uuid.UUID) -> bool:
        """Revoca idempotente nello scope autenticato del principal."""
        ...

    def revoke_all_for_user(self, scope: Scope) -> int:
        """Revoca tutte le sessioni attive dell'utente (recupero
        credenziale, B-03.2-14): dopo un reset locale nessuna sessione
        aperta in precedenza resta valida. Restituisce il numero di
        sessioni revocate."""
        ...
