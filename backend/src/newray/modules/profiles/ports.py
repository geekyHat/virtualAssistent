"""Porte di persistenza di profili e binding (NewRay.md §§5.1, 7.3; B-02).

Ogni operazione su dati privati richiede scope obbligatorio: non esistono
letture senza scope. Il catalogo dei modelli è una porta del modulo
``models`` (B-03): è lo stato di un runtime di inference locale, non un
dato privato dell'utente (NewRay.md §9.1).

Gli adapter implementano le porte; nei test i fake (``tests/fakes``)
verificano il contratto, l'adapter PostgreSQL lo verifica su PostgreSQL
reale con RLS. Dominio e applicazione non conoscono il trasporto.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from newray.kernel.identity import Scope

from .domain import ModelBinding, Profile, ProfileVersion


class ProfileRepository(Protocol):
    """Profili e versioni: scritture e letture sempre in scope (§7.3).

    Le versioni sono immutabili: la porta espone solo inserimento e
    lettura, nessun aggiornamento (NewRay.md §7.2).
    """

    def add_profile(self, profile: Profile) -> None: ...

    def add_version(self, version: ProfileVersion) -> None: ...

    def list_profiles(self, scope: Scope) -> list[tuple[Profile, ProfileVersion]]:
        """Profilo con la versione corrente (l'ultima per data); ordine
        deterministico per tipo di profilo."""
        ...

    def get_profile(
        self, scope: Scope, profile_id: uuid.UUID
    ) -> tuple[Profile, ProfileVersion] | None:
        """Lettura per ID: fuori scope (o assente) → ``None``."""
        ...


class ModelBindingStore(Protocol):
    """Binding dei modelli: immutabili, sempre in scope (§7.2, §7.3)."""

    def add_binding(self, binding: ModelBinding) -> None: ...

    def get_binding(self, scope: Scope, binding_id: uuid.UUID) -> ModelBinding | None:
        """Lettura per ID: fuori scope (o assente) → ``None``."""
        ...


class ProfileVersionWriter(Protocol):
    """Scrittura atomica di un binding e una versione nuovi (B-02.1).

    Mai una modifica in-place: ogni cambio di modello inserisce un
    ``ModelBinding`` e una ``ProfileVersion`` nuovi, mai un aggiornamento
    delle righe esistenti (§7.2). ``expected_profile_version`` è
    l'optimistic lock: se la versione corrente del profilo non coincide,
    l'esito è ``Conflict`` (stato cambiato dall'ultima lettura del
    client). ``idempotency_key``/``request_hash`` rendono sicuro il
    retry della stessa richiesta: stessa chiave e stesso hash restituisce
    lo stesso esito senza duplicarlo, stessa chiave con hash diverso è un
    conflitto esplicito.
    """

    def find_switch_receipt(
        self, scope: Scope, profile_id: uuid.UUID, idempotency_key: str, request_hash: str
    ) -> tuple[ProfileVersion, ModelBinding] | None:
        """Replay della richiesta originale, indipendente dal catalogo corrente.

        Stessa chiave con payload diverso -> ``Conflict``.
        """
        ...

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
        """Profilo assente/fuori scope -> ``NotFound``. Nessuna riga
        vincitrice più recente/idempotenza in conflitto -> ``Conflict``."""
        ...


class ProfileDefaultsSeeder(Protocol):
    """Provisioning del default: passo atomico e idempotente (R03/P-02).

    Binding, profili e versioni corrono in un'unica transazione: un guasto
    non lascia stati parziali. Sotto concorrenza sullo stesso scope un solo
    scrittore completa l'inizializzazione; gli stati parziali già esistenti
    (es. binding senza profili) vengono riconosciuti e completati, mai
    riscritti: le righe esistenti conservano ID e contenuto.
    """

    def execute(
        self,
        scope: Scope,
        binding: ModelBinding,
        profiles: list[tuple[Profile, ProfileVersion]],
    ) -> None: ...
