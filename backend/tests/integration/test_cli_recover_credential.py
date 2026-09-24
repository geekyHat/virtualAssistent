"""CLI di recupero della credenziale del proprietario, end-to-end (B-03.2-14).

Prova l'intero percorso ``newray.bootstrap.recover_credential`` su
PostgreSQL reale: lettura dei settings dall'ambiente, apertura
dell'engine, chiamata al caso d'uso. Senza i DSN ``NEWRAY_TEST_*_URL`` si
salta, come il resto della suite di integrazione.
"""

from __future__ import annotations

import pytest
from conftest import DatabaseHandles

from newray.bootstrap import recover_credential
from newray.infrastructure.database import create_engine as create_db_engine
from newray.kernel.clock import SystemClock
from newray.kernel.errors import InvalidCredentials, NotFound
from newray.modules.identity import IdentityService
from newray.modules.identity.adapters.postgres import (
    PostgresOwnerBootstrap,
    PostgresSessionStore,
    PostgresUserRepository,
)


def test_recover_credential_cli_aggiorna_la_credenziale_reale(
    test_databases: DatabaseHandles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_DATABASE_DSN", test_databases.app)

    engine = create_db_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        primo = identity.bootstrap_owner("Ada", "test-passphrase-1234")
    finally:
        engine.dispose()

    message = recover_credential.run("Ada", credential_provider=lambda: "nuova-passphrase-5678")

    assert "Ada" in message
    assert "1 sessione" in message  # solo la sessione del bootstrap

    engine = create_db_engine(test_databases.app)
    try:
        identity = IdentityService(
            PostgresUserRepository(engine),
            PostgresSessionStore(engine),
            SystemClock(),
            PostgresOwnerBootstrap(engine),
        )
        with pytest.raises(InvalidCredentials):
            identity.login("Ada", "test-passphrase-1234")
        recuperato = identity.login("Ada", "nuova-passphrase-5678")
        assert recuperato.user_id == primo.user_id
        assert recuperato.organization_id == primo.organization_id
    finally:
        engine.dispose()


def test_recover_credential_cli_owner_sconosciuto_solleva_errore_di_dominio(
    test_databases: DatabaseHandles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWRAY_DATABASE_DSN", test_databases.app)

    with pytest.raises(NotFound):
        recover_credential.run("Nessuno", credential_provider=lambda: "nuova-passphrase-5678")
