"""P-05: worker che consuma la coda dei run durevoli (NewRay.md §8.4).

Claim/heartbeat/finalize passano dalle funzioni SQL SECURITY DEFINER (ADR
0007): nessuna logica di dominio qui attraversa lo scope fra organizzazioni,
solo transizioni di stato già autorizzate. Lo snapshot del run è l'unica
fonte del ``ChatRequest``: nessuna ri-risoluzione di profilo/binding nel
worker (già congelati da ``DurableRunService.create``).

Una generazione attiva per risorsa: più istanze di ``RunWorker`` sullo
stesso ``resource_id`` sono serializzate dal resource lease
(``run_resource_leases``), non da un lock di processo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Mapping

from newray.kernel.identity import Principal, Role, Scope, new_id
from newray.modules.conversations import ConversationService
from newray.modules.models import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
)

from .durable import (
    FINISH_REASON_DEADLINE_EXCEEDED,
    FINISH_REASON_EMPTY_OUTPUT,
    FINISH_REASON_MODEL_ERROR,
    DurableRun,
    RunState,
    RunStore,
)

logger = logging.getLogger(__name__)

#: Risorsa generativa unica del pilot (una GPU): NewRay.md §8.4 non fissa
#: un identificatore, questo è il valore iniziale dichiarato per B-05.
DEFAULT_RESOURCE_ID = "gpu:0"
#: Durata del lease claim/heartbeat. Valore iniziale non misurato (NewRay.md
#: §8.4 non fissa numeri): rinnovato dall'heartbeat a lease/3.
DEFAULT_LEASE_SECONDS = 15
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
#: Deadline complessiva di una generazione. Valore iniziale dichiarato,
#: da tarare in P-19/P-20.
DEFAULT_MAX_DURATION_SECONDS = 300
#: Grace oltre la quale un worker senza heartbeat proprio è considerato
#: verificato morto (non il solo lease del run scaduto, NewRay.md §8.4).
DEFAULT_RECLAIM_GRACE_SECONDS = 45


def _chat_request_from_snapshot(snapshot: Mapping[str, object]) -> ChatRequest:
    """Ricostruisce la richiesta congelata a creazione: nessuna
    ri-risoluzione di profilo/binding (già fatta da ``DurableRunService``)."""
    raw_messages = snapshot["messages"]
    assert isinstance(raw_messages, tuple | list)
    messages = tuple(
        ChatMessage(ChatRole(str(item["role"])), str(item["content"])) for item in raw_messages
    )
    raw_parameters = snapshot["parameters"]
    assert isinstance(raw_parameters, Mapping)
    parameters = dict(raw_parameters)
    max_tokens = snapshot.get("max_output_tokens")
    return ChatRequest(
        model=str(snapshot["model_name"]),
        runtime=str(snapshot["runtime"]),
        messages=messages,
        parameters=parameters,
        max_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
    )


class _Progress:
    """Contenitore mutabile condiviso fra stream e heartbeat (asyncio
    cooperativo: nessuna vera concorrenza, nessun lock necessario)."""

    __slots__ = ("text", "lost_fence")

    def __init__(self) -> None:
        self.text = ""
        self.lost_fence = False


class RunWorker:
    """Reclama, esegue e finalizza run durevoli uno alla volta."""

    def __init__(
        self,
        store: RunStore,
        conversations: ConversationService,
        chat_model: ChatModel,
        *,
        worker_id: uuid.UUID | None = None,
        resource_id: str = DEFAULT_RESOURCE_ID,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
        reclaim_grace_seconds: int = DEFAULT_RECLAIM_GRACE_SECONDS,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._conversations = conversations
        self._chat_model = chat_model
        self.worker_id = worker_id or new_id()
        self.resource_id = resource_id
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_duration_seconds = max_duration_seconds
        self._reclaim_grace_seconds = reclaim_grace_seconds
        #: Cadenza del rinnovo lease/checkpoint, disaccoppiata dalla durata
        #: (intera, per il parametro SQL) del lease stesso: di norma
        #: ``lease_seconds / 3``, esplicito nei test per restare veloci.
        self._heartbeat_interval_seconds = heartbeat_interval_seconds or max(1.0, lease_seconds / 3)

    async def register(self, *, pid: int, hostname: str) -> None:
        await asyncio.to_thread(self._store.register_worker, self.worker_id, pid, hostname)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Ciclo del worker: claim → esecuzione, altrimenti reclaim+poll."""
        while not stop.is_set():
            claimed = await self._claim_and_execute()
            if not claimed:
                await asyncio.to_thread(self._store.touch_worker, self.worker_id)
                recovered = await asyncio.to_thread(
                    self._store.reclaim_stale, self._reclaim_grace_seconds
                )
                for run in recovered:
                    logger.warning("run %s recuperato come interrupted (worker morto)", run.id)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_seconds)

    async def _claim_and_execute(self) -> bool:
        run = await asyncio.to_thread(
            self._store.claim, self.worker_id, self._lease_seconds, self.resource_id
        )
        if run is None:
            return False
        await self._execute(run)
        return True

    async def _heartbeat_loop(
        self, run_id: uuid.UUID, fence: int, progress: _Progress, stop: asyncio.Event
    ) -> None:
        interval = self._heartbeat_interval_seconds
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
            if stop.is_set():
                return
            updated = await asyncio.to_thread(
                self._store.heartbeat,
                run_id,
                self.worker_id,
                fence,
                self._lease_seconds,
                progress.text,
                self.resource_id,
            )
            if updated is None:
                progress.lost_fence = True
                return

    async def _execute(self, run: DurableRun) -> None:
        request = _chat_request_from_snapshot(run.snapshot)
        progress = _Progress()
        fence = run.fence
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(run.id, fence, progress, stop_heartbeat)
        )
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._max_duration_seconds

        completion: Completion | None = None
        deadline_hit = False
        model_error = False
        stream = self._chat_model.stream(request)
        try:
            async for event in stream:
                if progress.lost_fence:
                    break
                if isinstance(event, ContentDelta):
                    progress.text += event.text
                elif isinstance(event, Completion):
                    completion = event
                    break
                if loop.time() >= deadline_at:
                    deadline_hit = True
                    break
        except Exception:
            logger.exception("run %s: errore di generazione", run.id)
            model_error = True
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()
            stop_heartbeat.set()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        if progress.lost_fence:
            # Un worker più recente possiede già il run: nessuna scrittura,
            # né di stato né di messaggi (fencing).
            return

        if deadline_hit:
            await self._finalize_only(
                run,
                fence,
                RunState.FAILED,
                FINISH_REASON_DEADLINE_EXCEEDED,
                None,
                None,
                None,
                progress.text,
            )
            return
        if model_error:
            await self._finalize_only(
                run,
                fence,
                RunState.FAILED,
                FINISH_REASON_MODEL_ERROR,
                None,
                None,
                None,
                progress.text,
            )
            return
        if completion is None or not progress.text:
            await self._finalize_only(
                run,
                fence,
                RunState.FAILED,
                FINISH_REASON_EMPTY_OUTPUT,
                None,
                None,
                None,
                progress.text,
            )
            return

        # Successo: finalizza (fencing-checked) PRIMA di persistere lo
        # scambio in conversations. Un fencing perso fra l'ultimo heartbeat
        # e qui interrompe senza scrivere alcun messaggio a nome del run.
        finalized = await asyncio.to_thread(
            self._store.finalize,
            run.id,
            self.worker_id,
            fence,
            RunState.COMPLETED,
            completion.finish_reason,
            completion.prompt_tokens,
            completion.completion_tokens,
            completion.eval_duration_ns,
            progress.text,
            self.resource_id,
        )
        if finalized is None:
            return

        principal = _principal_for_run(finalized)
        await asyncio.to_thread(
            self._conversations.add_exchange,
            principal,
            finalized.conversation_id,
            str(finalized.snapshot["prompt"]),
            progress.text,
            idempotency_key=finalized.idempotency_key,
            request_hash=finalized.payload_hash,
        )

    async def _finalize_only(
        self,
        run: DurableRun,
        fence: int,
        state: RunState,
        finish_reason: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        eval_duration_ns: int | None,
        partial_text: str,
    ) -> None:
        await asyncio.to_thread(
            self._store.finalize,
            run.id,
            self.worker_id,
            fence,
            state,
            finish_reason,
            prompt_tokens,
            completion_tokens,
            eval_duration_ns,
            partial_text,
            self.resource_id,
        )


def _principal_for_run(run: DurableRun) -> Principal:
    """Principal sintetico per ``ConversationService``: solo ``scope`` è
    consultato in ``get_conversation``/``add_exchange``. ``session_id`` e
    ``role`` non esistono per un job in background e non partecipano ad
    alcuna decisione di autorizzazione lungo questo percorso: lo scope
    (organizzazione/proprietario) resta l'unica fonte, presa dal run
    congelato al momento della creazione dal principal reale (P-05)."""
    scope: Scope = Scope(run.organization_id, run.owner_id)
    return Principal(
        user_id=scope.user_id,
        organization_id=scope.organization_id,
        session_id=run.id,
        role=Role.OWNER,
    )
