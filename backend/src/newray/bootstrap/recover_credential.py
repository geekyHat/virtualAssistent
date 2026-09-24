"""Recupero locale della credenziale del proprietario (B-03.2-14; NewRay.md §20.3).

Comando riservato a chi ha già accesso locale al processo/DB
dell'installazione: non esiste un percorso web equivalente ("password
dimenticata" via browser). Il nome del proprietario è solo la chiave di
lookup, non un'autenticazione — l'autorità è dimostrata dall'accesso
locale stesso. Non ricrea l'owner, non azzera il database, non accetta
una password condivisa: preserva user/org ID e ogni altro dato, cambia
solo l'hash della credenziale. Tutte le sessioni esistenti vengono
revocate: dopo il reset nessun accesso aperto in precedenza resta valido.

Avvio::

    uv run python -m newray.bootstrap.recover_credential --name "Ada"

La nuova password viene richiesta interattivamente (mai come argomento):
un argomento in chiaro finirebbe nella cronologia della shell e
nell'elenco dei processi del sistema.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from newray.infrastructure.database import create_engine
from newray.kernel.errors import DomainError

from .settings import Settings
from .wiring import build_identity_service


def _prompt_credential() -> str:
    first = getpass.getpass("Nuova password locale: ")
    second = getpass.getpass("Ripeti la password: ")
    if first != second:
        raise SystemExit("le due password inserite non coincidono")
    return first


def run(name: str, credential_provider: Callable[[], str] = _prompt_credential) -> str:
    """Esegue il recupero e restituisce il messaggio di esito.

    Separato da ``main`` per essere testabile senza un terminale
    interattivo reale: i test iniettano un ``credential_provider`` finto.
    """
    settings = Settings()  # type: ignore[call-arg]
    engine = create_engine(settings.database_dsn)
    try:
        identity = build_identity_service(engine)
        credential = credential_provider()
        revoked = identity.recover_owner_credential(name, credential)
    finally:
        engine.dispose()
    plural = "e" if revoked != 1 else ""
    return (
        f"Credenziale aggiornata per «{name}». {revoked} sessione{plural} precedente{plural} "
        f"revocata{plural}: rientrare con la nuova password."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reimposta la credenziale locale del proprietario (B-03.2-14)."
    )
    parser.add_argument(
        "--name", required=True, help="nome visualizzato del proprietario da recuperare"
    )
    args = parser.parse_args()

    try:
        message = run(args.name)
    except DomainError as exc:
        print(f"Recupero non riuscito: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(message)


if __name__ == "__main__":
    main()
