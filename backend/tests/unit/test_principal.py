"""Contratti dei tipi di identità (NewRay.md §7.1).

Verifica gli invariati di A-02: profilo AI e ruolo account sono assi
distinti, lo scope è obbligatorio, il principal è immutabile e derivato
solo dal server.
"""

from __future__ import annotations

import dataclasses

import pytest

from newray.kernel.identity import Principal, Role, Scope, new_id
from newray.modules.profiles import AProfile


def test_ruolo_account_e_profilo_ai_sono_assi_distinti() -> None:
    """Criterio di uscita A-02: il profilo AI è distinto dal ruolo.

    I due tipi non si sovrappongono e il principal non contiene alcun campo
    profilo: scegliere un profilo non altera ruoli né permessi.
    """
    assert set(Role) == {Role.OWNER, Role.MEMBER, Role.ADMINISTRATOR}
    assert set(AProfile) == {AProfile.ASSISTANT, AProfile.RESEARCHER, AProfile.CODER}
    assert set(Role).isdisjoint(AProfile)
    campi = {campo.name for campo in dataclasses.fields(Principal)}
    assert "role" in campi
    assert not any("profile" in nome for nome in campi)


def test_scope_richiede_organizzazione_e_utente() -> None:
    """Niente filtro facoltabile: user_id=None non è accettabile (§7.3)."""
    org, user = new_id(), new_id()
    scope = Scope(org, user)
    assert scope.organization_id is org
    assert scope.user_id is user
    with pytest.raises(TypeError):
        Scope(None, user)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Scope(org, None)  # type: ignore[arg-type]


def test_principal_immutable_e_scope_derivato() -> None:
    principal = Principal(new_id(), new_id(), new_id(), Role.OWNER)
    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.role = Role.MEMBER  # type: ignore[misc]
    assert principal.scope == Scope(principal.organization_id, principal.user_id)


def test_id_opachi_sono_uuid_v4_distinti() -> None:
    assert new_id() != new_id()
