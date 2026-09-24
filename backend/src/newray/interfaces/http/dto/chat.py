"""DTO della generazione chat (B-09.2).

Il client invia il contenuto del messaggio utente e il profilo; il server
salva il messaggio, risolve il binding, genera e salva la risposta
dell'assistente. Il risultato è uno stream SSE.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from newray.modules.conversations import MAX_MESSAGE_LENGTH


class RunRequest(BaseModel):
    """Richiesta di generazione: messaggio utente e profilo selezionato."""

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    profile_id: uuid.UUID
