"""Dominio del modulo models: limiti e invarianti (NewRay.md §§6.1, 8.2, 9.1; B-03).

I tipi sono il contratto condiviso tra profili (binding), adapter
Ollama e worker dei run (B-04): qui si verificano le invarianti di
costruzione, senza rete e senza framework.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from newray.bootstrap.wiring import build_chat_model
from newray.kernel.errors import ModelUnavailable
from newray.modules.models import (
    MAX_CHAT_MESSAGE_LENGTH,
    MAX_MODEL_NAME_LENGTH,
    RUNTIME_OLLAMA,
    ChatMessage,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    ModelInfo,
    ModelStatus,
)
from newray.modules.models.adapters.echo import EchoChatModel


def _model_info(name: str = "llama3.1") -> ModelInfo:
    return ModelInfo(
        name=name,
        runtime=RUNTIME_OLLAMA,
        digest="sha256:abc123",
        status=ModelStatus.QUALIFIED,
        capabilities=("chat",),
    )


# --- ModelInfo ------------------------------------------------------------


def test_model_info_valido() -> None:
    info = _model_info()
    assert info.name == "llama3.1"
    assert info.status is ModelStatus.QUALIFIED
    assert info.digest == "sha256:abc123"


def test_model_info_nome_fuori_limite() -> None:
    with pytest.raises(ValueError, match="nome del modello"):
        _model_info(name="x" * (MAX_MODEL_NAME_LENGTH + 1))
    with pytest.raises(ValueError, match="nome del modello"):
        _model_info(name="")


def test_model_info_runtime_estraneo() -> None:
    with pytest.raises(ValueError, match="runtime non supportato"):
        ModelInfo(
            name="gpt-x",
            runtime="openai",
            digest=None,
            status=ModelStatus.INSTALLED,
            capabilities=(),
        )


def test_model_info_è_immutable() -> None:
    info = _model_info()
    with pytest.raises(FrozenInstanceError):
        info.name = "altro"  # type: ignore[misc]


# --- ChatMessage e ChatRole ----------------------------------------------


def test_chat_message_valido() -> None:
    message = ChatMessage(role=ChatRole.USER, content="Ciao")
    assert message.role is ChatRole.USER
    assert message.content == "Ciao"


def test_chat_message_contenuto_fuori_limite() -> None:
    with pytest.raises(ValueError, match="contenuto fuori dai limiti"):
        ChatMessage(role=ChatRole.USER, content="x" * (MAX_CHAT_MESSAGE_LENGTH + 1))
    with pytest.raises(ValueError, match="contenuto fuori dai limiti"):
        ChatMessage(role=ChatRole.SYSTEM, content="")


def test_ruoli_hanno_valori_stabili() -> None:
    assert [role.value for role in ChatRole] == ["system", "user", "assistant"]


# --- ChatRequest ----------------------------------------------------------


def _request(**overrides: object) -> ChatRequest:
    base: dict[str, object] = {
        "model": "llama3.1",
        "runtime": RUNTIME_OLLAMA,
        "messages": (ChatMessage(role=ChatRole.USER, content="Ciao"),),
    }
    base.update(overrides)
    return ChatRequest(**base)  # type: ignore[arg-type]


def test_richiesta_valida_con_limiti() -> None:
    request = _request(
        parameters={"temperature": 0.2},
        max_tokens=64,
        inactivity_timeout=timedelta(seconds=30),
    )
    assert request.max_tokens == 64
    assert request.inactivity_timeout == timedelta(seconds=30)
    assert request.parameters == {"temperature": 0.2}


def test_richiesta_senza_messaggi_è_riuscita() -> None:
    with pytest.raises(ValueError, match="almeno un messaggio"):
        _request(messages=())


def test_richiesta_modello_fuori_limite() -> None:
    with pytest.raises(ValueError, match="modello fuori dai limiti"):
        _request(model="x" * (MAX_MODEL_NAME_LENGTH + 1))


def test_richiesta_runtime_estraneo() -> None:
    with pytest.raises(ValueError, match="runtime non supportato"):
        _request(runtime="openai")


def test_richiesta_max_tokens_minimo() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        _request(max_tokens=0)
    request = _request(max_tokens=1)
    assert request.max_tokens == 1


def test_richiesta_timeout_di_inattivita_deve_essere_positivo() -> None:
    for timeout in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError, match="inactivity_timeout"):
            _request(inactivity_timeout=timeout)


# --- Eventi di stream -----------------------------------------------------


def test_content_delta_testo_non_vuoto() -> None:
    delta = ContentDelta(text="frammento")
    assert delta.text == "frammento"
    with pytest.raises(ValueError, match="non può essere vuoto"):
        ContentDelta(text="")


def test_completion_valida_e_limiti() -> None:
    completion = Completion(finish_reason="stop", prompt_tokens=1, completion_tokens=2)
    assert completion.finish_reason == "stop"
    with pytest.raises(ValueError, match="non può essere vuoto"):
        Completion(finish_reason="", prompt_tokens=None, completion_tokens=None)
    with pytest.raises(ValueError, match="negativi"):
        Completion(finish_reason="stop", prompt_tokens=-1, completion_tokens=None)


def test_completion_eval_duration_negativo() -> None:
    with pytest.raises(ValueError, match="eval_duration_ns"):
        Completion(
            finish_reason="stop",
            prompt_tokens=None,
            completion_tokens=None,
            eval_duration_ns=-1,
        )


def test_completion_tokens_per_second_valido() -> None:
    c = Completion(
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
        eval_duration_ns=2_000_000_000,
    )
    assert c.tokens_per_second == 10.0


def test_completion_tokens_per_second_arrotondamento() -> None:
    c = Completion(
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=7,
        eval_duration_ns=3_000_000_000,
    )
    assert c.tokens_per_second == 2.33


def test_completion_tokens_per_second_nullo_senza_dati() -> None:
    c = Completion(finish_reason="stop", prompt_tokens=10, completion_tokens=None)
    assert c.tokens_per_second is None


def test_completion_tokens_per_second_nullo_senza_durata() -> None:
    c = Completion(
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
        eval_duration_ns=None,
    )
    assert c.tokens_per_second is None


def test_completion_tokens_per_second_nullo_con_zero_token() -> None:
    c = Completion(
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=0,
        eval_duration_ns=1_000_000_000,
    )
    assert c.tokens_per_second is None


def test_completion_tokens_per_second_nullo_con_zero_durata() -> None:
    c = Completion(
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
        eval_duration_ns=0,
    )
    assert c.tokens_per_second is None


def test_produzione_senza_runtime_non_usa_echo_fallback() -> None:
    model = build_chat_model(None)
    assert not isinstance(model, EchoChatModel)

    async def scenario() -> None:
        with pytest.raises(ModelUnavailable, match="non configurato"):
            _ = [event async for event in model.stream(_request())]

    asyncio.run(scenario())
