"""Worker P-05: consuma la coda dei run durevoli.

Ownership: modulo ``runs``. Il worker è un processo separato dal servizio
HTTP (avviato dal launcher, `bootstrap/worker_main.py`). L'invariante
"un'inference generativa attiva per risorsa" è tenuta da un ``ComputeLease``
(default: ``FsComputeLease`` con ``fcntl.flock`` non-bloccante su
``$NEWRAY_STATE_DIR/compute.lock``): due processi worker sullo stesso host
si serializzano, e se il processo detentore muore il kernel rilascia il
lock. Fencing e claim atomico proteggono in aggiunta contro un vecchio
processo sopravvissuto che tenti di scrivere. La serializzazione multi-host
resta ambito di P-18 (deployment).

Nessuna transazione DB resta aperta durante l'inference; heartbeat e
checkpoint sono unità autonome. Alla terminazione onesta il worker chiama
``ConversationService.add_exchange`` per persistere prompt + risposta in
un'unica transazione: nessun prompt orfano, nessun doppio messaggio.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from newray.kernel.clock import Clock, SystemClock
from newray.kernel.errors import DomainError, InferenceFailed, InferenceTimeout, NotFound
from newray.kernel.identity import Scope, new_id
from newray.modules.conversations import ConversationService
from newray.modules.models import (
    ChatMessage,
    ChatModel,
    ChatRequest,
    ChatRole,
    Completion,
    ContentDelta,
    StreamEvent,
)

from .durable import (
    ComputeLease,
    DurableRun,
    LeaseLost,
    NoopComputeLease,
    RunAlreadyTerminal,
    RunStore,
)

logger = logging.getLogger("newray.runs.worker")


class WorkerConfig:
    """Parametri del worker.

    ``token_safety_chars_per_token`` è una safety net contro un runtime che
    ignora ``num_predict`` (=``max_output_tokens``): il cap autorevole di
    token resta Ollama, che chiude con ``finish_reason='length'`` — un
    COMPLETED onesto. Se però il partial cresce oltre
    ``max_output_tokens * token_safety_chars_per_token`` caratteri, il
    worker chiude come FAILED con ``TOKEN_BUDGET_EXCEEDED``: il valore
    generoso di default (20) intercetta solo runaway reali, non troncamenti
    di stile verboso.
    """

    __slots__ = (
        "poll_interval",
        "lease_ttl",
        "heartbeat_interval",
        "checkpoint_min_interval",
        "cancel_check_interval",
        "inactivity_timeout",
        "token_safety_chars_per_token",
    )

    def __init__(
        self,
        *,
        poll_interval: float = 1.0,
        lease_ttl: timedelta = timedelta(seconds=30),
        heartbeat_interval: timedelta = timedelta(seconds=10),
        checkpoint_min_interval: timedelta = timedelta(milliseconds=500),
        cancel_check_interval: timedelta = timedelta(seconds=1),
        inactivity_timeout: timedelta = timedelta(seconds=60),
        token_safety_chars_per_token: int = 20,
    ) -> None:
        if token_safety_chars_per_token < 1:
            raise ValueError("token_safety_chars_per_token deve essere >= 1")
        self.poll_interval = poll_interval
        self.lease_ttl = lease_ttl
        self.heartbeat_interval = heartbeat_interval
        self.checkpoint_min_interval = checkpoint_min_interval
        self.cancel_check_interval = cancel_check_interval
        self.inactivity_timeout = inactivity_timeout
        self.token_safety_chars_per_token = token_safety_chars_per_token


class DurableRunWorker:
    """Loop di consumo con claim atomico, lease, checkpoint e finalizzazione."""

    def __init__(
        self,
        *,
        store: RunStore,
        chat_model: ChatModel,
        conversations: ConversationService,
        config: WorkerConfig | None = None,
        clock: Clock | None = None,
        worker_id: uuid.UUID | None = None,
        compute_lease: ComputeLease | None = None,
    ) -> None:
        self._store = store
        self._chat_model = chat_model
        self._conversations = conversations
        self._config = config or WorkerConfig()
        self._clock = clock or SystemClock()
        self.worker_id = worker_id or new_id()
        self._compute_lease = compute_lease or NoopComputeLease()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        """Loop principale: claim un run alla volta, poi esegue."""
        logger.info("worker %s avviato", self.worker_id)
        while not self._stop.is_set():
            try:
                processed = await self._tick()
            except Exception:
                logger.exception("worker tick fallito")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._config.poll_interval
                    )
                except TimeoutError:
                    pass
        logger.info("worker %s fermato", self.worker_id)

    async def _tick(self) -> bool:
        # Compute lease prima del claim: se un altro worker tiene il lock
        # sull'inference, non prendere il run — resterà in coda per la prossima
        # finestra libera. Il lock è a livello file system, cross-processo su
        # host singolo, rilasciato dal kernel se il processo muore.
        acquired = await asyncio.to_thread(self._compute_lease.try_acquire)
        if not acquired:
            return False
        try:
            now = self._clock.now()
            lease_until = now + self._config.lease_ttl
            claim = await asyncio.to_thread(
                self._store.claim_next,
                worker_id=self.worker_id,
                lease_until=lease_until,
                now=now,
            )
            if claim is None:
                return False
            await self._process(claim.run, claim.fence)
            return True
        finally:
            await asyncio.to_thread(self._compute_lease.release)

    async def _process(self, run: DurableRun, fence: int) -> None:
        scope = Scope(run.organization_id, run.owner_id)
        try:
            request = _build_chat_request(run.snapshot)
        except ValueError as exc:
            now = self._clock.now()
            await asyncio.to_thread(
                self._store.fail,
                scope=scope,
                run_id=run.id,
                worker_id=self.worker_id,
                fence=fence,
                error_code="SNAPSHOT_INVALID",
                finish_reason="failed",
                partial_text=run.partial_text,
                now=now,
            )
            logger.error("snapshot run %s non valido: %s", run.id, exc)
            return

        accumulated = run.partial_text
        completion: Completion | None = None
        last_checkpoint = self._clock.now()
        stream = self._chat_model.stream(request)
        stop_reason: str | None = None
        try:
            # Pattern select-like: mantieni una singola `anext_task` viva; se
            # scade il timer di controllo la lasciamo pendente e ricontrolliamo.
            # `asyncio.wait_for` con timeout su `__anext__()` invece cancellerebbe
            # il generator (GeneratorExit → StopAsyncIteration alla ripresa).
            anext_task: asyncio.Task[StreamEvent] | None = None
            while True:
                if anext_task is None:
                    anext_task = asyncio.ensure_future(stream.__anext__())
                check = asyncio.ensure_future(
                    asyncio.sleep(self._config.cancel_check_interval.total_seconds())
                )
                done, _pending = await asyncio.wait(
                    {anext_task, check}, return_when=asyncio.FIRST_COMPLETED
                )
                if anext_task in done:
                    check.cancel()
                    try:
                        event = anext_task.result()
                    except StopAsyncIteration:
                        anext_task = None
                        break
                    anext_task = None

                    if isinstance(event, ContentDelta):
                        if completion is not None:
                            raise InferenceFailed(
                                "contenuto ricevuto dopo il terminale del modello"
                            )
                        accumulated += event.text
                        if _over_token_budget(
                            accumulated,
                            request.max_tokens,
                            self._config.token_safety_chars_per_token,
                        ):
                            stop_reason = "token_budget"
                            break
                        if (
                            self._clock.now() - last_checkpoint
                            >= self._config.checkpoint_min_interval
                        ):
                            await self._checkpoint(scope, run.id, fence, accumulated)
                            last_checkpoint = self._clock.now()
                    elif isinstance(event, Completion):
                        if completion is not None:
                            raise InferenceFailed("terminale duplicato dal modello")
                        completion = event

                    if await self._should_stop(run.id, scope, run.deadline_at):
                        stop_reason = self._pending_stop_reason
                        break
                else:
                    # Timer di controllo scaduto: cancel/deadline/heartbeat,
                    # senza toccare `anext_task` che resta in attesa del prossimo chunk.
                    if await self._should_stop(run.id, scope, run.deadline_at):
                        stop_reason = self._pending_stop_reason
                        anext_task.cancel()
                        break
                    await self._heartbeat(scope, run.id, fence)
        except InferenceTimeout as exc:
            await self._finalize_failed(
                scope, run, fence, accumulated, "INFERENCE_TIMEOUT", str(exc)
            )
            return
        except InferenceFailed as exc:
            await self._finalize_failed(
                scope, run, fence, accumulated, "INFERENCE_FAILED", str(exc)
            )
            return
        except DomainError as exc:
            await self._finalize_failed(scope, run, fence, accumulated, exc.code, exc.message)
            return
        except Exception:
            logger.exception("inferenza run %s fallita", run.id)
            await self._finalize_failed(scope, run, fence, accumulated, "INTERNAL", "")
            return
        finally:
            close: Any = getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    logger.exception("chiusura stream fallita")

        if stop_reason == "cancel":
            await self._finalize_cancelled(scope, run, fence, accumulated)
            return
        if stop_reason == "deadline":
            await self._finalize_failed(
                scope, run, fence, accumulated, "INFERENCE_TIMEOUT", "wall-clock deadline superata"
            )
            return
        if stop_reason == "token_budget":
            await self._finalize_failed(
                scope, run, fence, accumulated, "TOKEN_BUDGET_EXCEEDED",
                "runtime non ha onorato num_predict; partial oltre la safety net",
            )
            return
        if completion is None:
            await self._finalize_failed(
                scope, run, fence, accumulated, "INFERENCE_FAILED",
                "stream terminato senza completamento",
            )
            return
        if not accumulated:
            await self._finalize_failed(
                scope, run, fence, accumulated, "INFERENCE_FAILED",
                "nessun contenuto prodotto",
            )
            return

        await self._finalize_completed(scope, run, fence, accumulated, completion)

    _pending_stop_reason: str | None = None

    async def _should_stop(
        self, run_id: uuid.UUID, scope: Scope, deadline_at: datetime | None
    ) -> bool:
        now = self._clock.now()
        if deadline_at is not None and now >= deadline_at:
            self._pending_stop_reason = "deadline"
            return True
        # Legge cancel_requested_at con una query di sola lettura scoped.
        current = await asyncio.to_thread(self._store.get, scope, run_id)
        if current is None:
            raise NotFound("run rimosso mentre in esecuzione")
        if current.cancel_requested_at is not None:
            self._pending_stop_reason = "cancel"
            return True
        return False

    async def _heartbeat(self, scope: Scope, run_id: uuid.UUID, fence: int) -> None:
        now = self._clock.now()
        lease_until = now + self._config.lease_ttl
        try:
            await asyncio.to_thread(
                self._store.heartbeat,
                scope=scope,
                run_id=run_id,
                worker_id=self.worker_id,
                fence=fence,
                lease_until=lease_until,
                now=now,
            )
        except (LeaseLost, RunAlreadyTerminal):
            raise

    async def _checkpoint(
        self, scope: Scope, run_id: uuid.UUID, fence: int, partial_text: str
    ) -> None:
        now = self._clock.now()
        lease_until = now + self._config.lease_ttl
        await asyncio.to_thread(
            self._store.checkpoint,
            scope=scope,
            run_id=run_id,
            worker_id=self.worker_id,
            fence=fence,
            partial_text=partial_text,
            lease_until=lease_until,
            now=now,
        )

    async def _finalize_completed(
        self,
        scope: Scope,
        run: DurableRun,
        fence: int,
        text_body: str,
        completion: Completion,
    ) -> None:
        prompt = str(run.snapshot.get("prompt", ""))
        request_hash = str(run.snapshot.get("request_hash", ""))
        # Persistenza atomica prompt + assistente sulla conversazione. Un
        # eventuale precedente add_exchange è idempotente per idempotency_key.
        principal = _worker_principal(run)
        try:
            await asyncio.to_thread(
                self._conversations.add_exchange,
                principal,
                run.conversation_id,
                prompt,
                text_body,
                idempotency_key=run.idempotency_key,
                request_hash=request_hash or None,
            )
        except Exception:
            logger.exception("persistenza messaggi run %s fallita", run.id)
            await self._finalize_failed(
                scope, run, fence, text_body, "PERSISTENCE_FAILED",
                "impossibile persistere i messaggi",
            )
            return
        now = self._clock.now()
        try:
            await asyncio.to_thread(
                self._store.complete,
                scope=scope,
                run_id=run.id,
                worker_id=self.worker_id,
                fence=fence,
                partial_text=text_body,
                finish_reason=completion.finish_reason,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
                eval_duration_ns=completion.eval_duration_ns,
                now=now,
            )
        except LeaseLost:
            logger.warning("lease perso mentre finalizzavo run %s", run.id)

    async def _finalize_failed(
        self,
        scope: Scope,
        run: DurableRun,
        fence: int,
        partial_text: str,
        code: str,
        message: str,
    ) -> None:
        now = self._clock.now()
        try:
            await asyncio.to_thread(
                self._store.fail,
                scope=scope,
                run_id=run.id,
                worker_id=self.worker_id,
                fence=fence,
                error_code=code,
                finish_reason="failed",
                partial_text=partial_text,
                now=now,
            )
        except LeaseLost:
            logger.warning("lease perso finalizzando failed su run %s", run.id)
        if message:
            logger.info("run %s fallito: %s", run.id, message)

    async def _finalize_cancelled(
        self, scope: Scope, run: DurableRun, fence: int, partial_text: str
    ) -> None:
        now = self._clock.now()
        try:
            await asyncio.to_thread(
                self._store.finalize_cancelled,
                scope=scope,
                run_id=run.id,
                worker_id=self.worker_id,
                fence=fence,
                partial_text=partial_text,
                now=now,
            )
        except LeaseLost:
            logger.warning("lease perso finalizzando cancel su run %s", run.id)


def _over_token_budget(
    accumulated: str, max_tokens: int | None, chars_per_token: int
) -> bool:
    """Safety net: partial oltre `max_tokens * chars_per_token` = runaway.

    Il cap autorevole di token resta ``num_predict`` di Ollama; questa
    funzione non tronca un'inference regolare, ma protegge il worker da
    runtime che non chiudono con `finish_reason='length'`.
    """
    if max_tokens is None:
        return False
    return len(accumulated) > max_tokens * chars_per_token


def _build_chat_request(snapshot: Mapping[str, object]) -> ChatRequest:
    model = str(snapshot["model_name"])
    runtime = str(snapshot["runtime"])
    raw_messages = snapshot.get("messages")
    if not isinstance(raw_messages, tuple | list) or not raw_messages:
        raise ValueError("snapshot senza messaggi")
    messages: list[ChatMessage] = []
    for item in raw_messages:
        if not isinstance(item, Mapping):
            raise ValueError("messaggio snapshot non è una mappa")
        role = ChatRole(str(item["role"]))
        content = str(item["content"])
        messages.append(ChatMessage(role, content))
    parameters = snapshot.get("parameters") or {}
    if not isinstance(parameters, Mapping):
        raise ValueError("parametri snapshot non validi")
    max_tokens = snapshot.get("max_output_tokens")
    return ChatRequest(
        model=model,
        runtime=runtime,
        messages=tuple(messages),
        parameters=dict(parameters),
        max_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
    )


def _worker_principal(run: DurableRun) -> Any:
    """Principal minimo per riusare ``ConversationService.add_exchange``.

    Contiene solo scope; il worker non è un utente finale. La sessione è
    l'ID del run per tracciabilità nei log; il ruolo è OWNER perché la
    scrittura avviene nello scope del proprietario del run.
    """
    from newray.kernel.identity import Principal, Role

    return Principal(
        user_id=run.owner_id,
        organization_id=run.organization_id,
        session_id=run.id,
        role=Role.OWNER,
    )
