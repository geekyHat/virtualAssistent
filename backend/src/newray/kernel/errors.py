"""Errori di dominio con codici stabili (NewRay.md §19.4).

I moduli sollevano questi tipi; il trasporto HTTP li mapperà su risposte
strutturate (``interfaces/http/errors.py`` nasce con A-04/A-05). Il codice
è stabile e non dipende dalla localizzazione del messaggio; nei payload
pubblici non compaiono segreti né stack trace.
"""

from __future__ import annotations


class DomainError(Exception):
    """Errore di dominio con codice stabile e messaggio localizzabile."""

    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)


class SessionInvalid(DomainError):
    """Sessione assente, scaduta o revocata: nessun principal risolvibile."""

    code = "SESSION_INVALID"


class AccessDenied(DomainError):
    """Azione negata dalla policy server-side (principal/risorsa/azione)."""

    code = "ACCESS_DENIED"


class InvalidCredentials(DomainError):
    """Credenziali di login errate (utente o password): nessun principal
    risolto e nessuna distinzione verso l'esterno fra "utente sconosciuto"
    e "credenziale sbagliata" (NewRay.md §19.4)."""

    code = "INVALID_CREDENTIALS"


class InvalidName(DomainError):
    """Nome del proprietario vuoto dopo normalizzazione (B-03.2-30).

    Il nome è normalizzato (margini di whitespace rimossi) nei casi d'uso
    di bootstrap e login; se il risultato è vuoto l'input è malformato,
    non un tentativo di credenziale: errore di validazione distinto da
    ``INVALID_CREDENTIALS`` (che collassa owner assente/credenziale
    sbagliata per non fare da oracolo, §19.4).
    """

    code = "INVALID_NAME"


class CredentialNotSet(DomainError):
    """Il proprietario esiste ma non ha ancora una credenziale locale.

    Caso migratorio: un'installazione bootstrappata prima dell'aggiunta
    del login (B-03.2-14). Il recupero è esplicito (comando locale che
    imposta la credenziale), non ricreazione silenziosa dell'owner.
    """

    code = "CREDENTIAL_NOT_SET"


class Conflict(DomainError):
    """Operazione in conflitto con lo stato esistente (es. bootstrap ripetuto)."""

    code = "CONFLICT"


class NotFound(DomainError):
    """Risorsa assente o fuori scope del principal.

    Per i dati privati non si distingue "non esiste" da "non è tua":
    l'esistenza di risorse altrui non deve essere rivelata.
    """

    code = "NOT_FOUND"


class ModelUnavailable(DomainError):
    """Il modello del binding non è disponibile nel runtime (NewRay.md §19.4,
    ADR 0003): errore recuperabile, mai un fallback invisibile.

    Il client può recuperarsi con un'azione esplicita (installare il
    modello, scegliere un altro profilo), non con un retry cieco.
    """

    code = "MODEL_UNAVAILABLE"


class InferenceFailed(DomainError):
    """Il runtime di inference ha rifiutato la generazione (NewRay.md §19.4).

    L'errore vendor è mappato su un codice stabile: i dettagli del vendor
    non arrivano mai al payload pubblico. Non è recuperabile con un retry
    cieco: la causa sta nella richiesta (modello, parametri, payload).
    """

    code = "INFERENCE_FAILED"


class InferenceTimeout(DomainError):
    """La generazione ha superato il limite di tempo esplicito (NewRay.md §19.4).

    Guasto transitorio: un nuovo tentativo con lo stesso payload può avere
    esito diverso. Per lo streaming senza effetti esterni il recupero è
    affidato al worker (B-04/B-05), non al client.
    """

    code = "INFERENCE_TIMEOUT"
