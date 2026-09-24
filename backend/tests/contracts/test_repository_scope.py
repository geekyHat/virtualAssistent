"""Contratto di scope dei repository (NewRay.md §§7.3, 22.2 #3).

A non accede ai dati di B anche indovinando l'ID: lo scope obbligatorio è
applicato nell'adapter, non convenuto dal chiamante.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fakes import InMemoryUserRepository
from newray.kernel.identity import Role, Scope, new_id
from newray.modules.identity.domain import User

ORA = datetime(2026, 9, 16, tzinfo=UTC)


def _membro(organization_id: uuid.UUID, member_id: uuid.UUID | None = None) -> User:
    return User(
        id=member_id or new_id(),
        organization_id=organization_id,
        display_name="Membro",
        role=Role.MEMBER,
        created_at=ORA,
    )


def test_scope_di_a_non_legge_il_record_di_b() -> None:
    org = new_id()
    repo = InMemoryUserRepository()
    a, b = _membro(org), _membro(org)
    repo.add_user(a)
    repo.add_user(b)
    assert repo.get_user(Scope(org, a.id), a.id) is a
    # B chiede il record di A con il proprio scope: nessun dato.
    assert repo.get_user(Scope(org, b.id), a.id) is None


def test_scope_di_altra_organizzazione_non_legge() -> None:
    """FK/vincoli compositi: niente riferimenti fra organizzazioni (§7.2)."""
    org_a, org_b = new_id(), new_id()
    repo = InMemoryUserRepository()
    a = _membro(org_a)
    repo.add_user(a)
    assert repo.get_user(Scope(org_b, a.id), a.id) is None
