"""P-17: isolamento cross-scope per conversazioni e messaggi."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from adversarial.conftest import (  # type: ignore[import-not-found]
    bootstrap_owner,
    create_conversation,
)


def test_conversation_cross_scope_404(hostile_client: TestClient) -> None:
    """Ada crea una conversazione. Un id fabbricato (scope estraneo) → 404
    uniforme; l'esistenza della conversazione non trapela.

    E l'accesso a un id inesistente ritorna anch'esso 404: il client non
    può discriminare "non esiste" da "non tuo"."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    conversation_id = create_conversation(hostile_client, "adv-cross")

    # Id fabbricato → 404 (nessuna informazione trapela).
    r = hostile_client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert r.status_code == 404
    r = hostile_client.patch(
        f"/api/v1/conversations/{uuid.uuid4()}",
        json={"title": "furto"},
    )
    assert r.status_code == 404
    r = hostile_client.delete(f"/api/v1/conversations/{uuid.uuid4()}")
    assert r.status_code == 404

    # La conversazione di Ada resta invariata.
    r = hostile_client.get(f"/api/v1/conversations/{conversation_id}")
    assert r.status_code == 200


def test_messages_di_conversazione_ignota_404(hostile_client: TestClient) -> None:
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")
    assert r.status_code == 404
    r = hostile_client.post(
        f"/api/v1/conversations/{uuid.uuid4()}/messages",
        json={"content": "ciao"},
    )
    assert r.status_code == 404


def test_post_message_dopo_revoca_e_401(hostile_client: TestClient) -> None:
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    conversation_id = create_conversation(hostile_client, "adv-revoke")
    hostile_client.post("/api/v1/session/revoke")
    hostile_client.cookies.clear()
    r = hostile_client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "furto post-revoca"},
    )
    assert r.status_code == 401


def test_delete_conversation_altrui_id_fabbricato_404(hostile_client: TestClient) -> None:
    """Un DELETE su id fabbricato deve tornare 404, non 204 (che
    indicherebbe cancellazione riuscita e possibile leak di scope)."""
    bootstrap_owner(hostile_client, "Ada", "test-passphrase-1234")
    r = hostile_client.delete(f"/api/v1/conversations/{uuid.uuid4()}")
    assert r.status_code == 404
