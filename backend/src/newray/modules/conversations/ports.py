"""Porte di persistenza di conversazioni e messaggi (NewRay.md §§5.1, 7.3).

Ogni operazione su dati privati richiede scope obbligatorio: non esistono
letture senza scope. Gli adapter implementano le porte; nei test i fake
(``tests/fakes``) verificano il contratto, l'adapter PostgreSQL lo
verifica su PostgreSQL reale con RLS. Dominio e applicazione non
conoscono il trasporto.
"""

from __future__ import annotations

import builtins
import uuid
from datetime import datetime
from typing import Protocol

from newray.kernel.identity import Scope

from .domain import Conversation, Message, MessageRole


class ConversationRepository(Protocol):
    """Conversazioni: scritture e letture sempre in scope (§7.3)."""

    def add_conversation(self, conversation: Conversation) -> None: ...

    def get_conversation(self, scope: Scope, conversation_id: uuid.UUID) -> Conversation | None:
        """Lettura per ID: fuori scope (o assente) → ``None``."""
        ...

    def list_conversations(
        self,
        scope: Scope,
        *,
        before: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> list[Conversation]:
        """Dalle più recenti (``updated_at``, ``id``); ``before`` è il
        cursore keyset della pagina precedente."""
        ...

    def rename_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        title: str,
        now: datetime,
        *,
        expected_updated_at: datetime | None = None,
    ) -> Conversation | None:
        """Aggiorna titolo e ``updated_at``; fuori scope (o assente) → ``None``.

        Con ``expected_updated_at`` la mutazione è a versione attesa: se la
        conversazione è cambiata dall'ultima lettura del chiamante si solleva
        ``Conflict`` (scrittura obsoleta, mai sovrascritta silenziosamente).
        """
        ...

    def delete_conversation(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        """Cancellazione fisica in scope: i messaggi seguono per cascata.

        Con ``expected_updated_at`` versione attesa: stato mutato dall'ultima
        lettura → ``Conflict``. Se una cancellazione concorre con un append,
        il lock della riga padre decide l'ordine: chi arriva primo vince,
        l'altro vede lo stato successivo (append → la cascata rimuove il
        messaggio; cancellazione → l'append trova la conversazione assente).
        """
        ...


class MessageStore(Protocol):
    """Messaggi: la sequenza la assegna la persistenza in modo atomico."""

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
        """Aggiunge un messaggio con la prossima sequenza strettamente crescente.

        La sequenza viene dal contatore atomico della conversazione
        (lock della riga padre): append concorrenti ottengono sequenze
        distinte senza gare sulle letture. L'``updated_at`` della
        conversazione è aggiornato nella stessa transazione corta.

        ``idempotency_key``: ricevuta scoped per (scope, conversazione,
        chiave) sull'hash del payload. Stessa chiave e stesso payload
        restituisce lo stesso messaggio (nessun duplicato); stessa chiave
        con payload diverso solleva ``Conflict``. Conversazione assente
        (o fuori scope) → ``NotFound``.

        Il chiamante ha già verificato che la conversazione è nel proprio
        scope (casi d'uso); lo schema blocca comunque per vincolo i
        messaggi su conversazioni non di proprietà del principal.
        """
        ...

    def list(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int,
    ) -> builtins.list[Message]:
        """Messaggi in ordine di sequenza, da ``after_sequence`` escluso."""
        ...

    def list_recent(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        *,
        limit: int,
    ) -> builtins.list[Message]:
        """Ultimi messaggi in ordine cronologico.

        Il limite è applicato ai messaggi più recenti, non ai primi della
        conversazione: serve alla composizione esplicita del contesto.
        """
        ...

    def find_exchange(
        self,
        scope: Scope,
        conversation_id: uuid.UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Message, Message] | None:
        """Esito già persistito di una generazione inline idempotente.

        Stessa chiave e hash restituisce la coppia user/assistant; stessa
        chiave con richiesta diversa solleva ``Conflict``.
        """
        ...

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
        """Persistenza atomica di prompt e risposta contigui.

        Nessun prompt diventa visibile senza la relativa risposta. Con una
        chiave, la ricevuta punta alla risposta e permette il replay della
        coppia senza ripetere l'inference.
        """
        ...
