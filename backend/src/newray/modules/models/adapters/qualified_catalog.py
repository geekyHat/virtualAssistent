"""Catalogo che compone dichiarato + qualificato (P-19).

Avvolge un :class:`ModelCatalog` runtime e uno :class:`QualificationStore`
per esporre correttamente ``qualified_capabilities`` in
:class:`ModelReadiness`. Non modifica il runtime, non concede capacità: se
lo store non ha un record applicabile, il campo resta ``()`` e il
chiamante mantiene ``INSTALLED_UNVERIFIED`` come stato effettivo dal punto
di vista del prodotto.

Il fingerprint dell'hardware corrente è fornito dall'esterno (dal
bootstrap): il modulo dominio non legge ``/sys`` né sonda GPU. Un
fingerprint ``None`` implica che l'ambiente non si può stabilire → nessuna
qualifica applicabile.
"""

from __future__ import annotations

from ..domain import ModelInfo, ModelReadiness, ModelStatus
from ..ports import ModelCatalog
from ..qualification import (
    HardwareFingerprint,
    QualificationStore,
    qualified_capabilities_for,
)


class QualifiedModelCatalog:
    """Aggiunge le capacità qualificate al readiness e al catalogo.

    ``list_models`` non promuove lo status a ``QUALIFIED`` da solo: quello
    resta responsabilità di chi ha già promosso lo status a monte. Ma
    inserisce le capacità qualificate al posto di quelle dichiarate quando
    lo store ha un record applicabile e il digest coincide.
    """

    def __init__(
        self,
        base: ModelCatalog,
        store: QualificationStore,
        hardware: HardwareFingerprint | None,
        runtime: str,
    ) -> None:
        self._base = base
        self._store = store
        self._hardware = hardware
        self._runtime = runtime

    async def list_models(self) -> list[ModelInfo]:
        models = await self._base.list_models()
        if self._hardware is None:
            return models
        result: list[ModelInfo] = []
        for model in models:
            caps = self._qualified_for(model.name, model.digest)
            if caps:
                # Lo status non è promosso qui: resta quello dichiarato dal
                # runtime. La qualifica arricchisce le capacità note al
                # consumatore, non concede QUALIFIED implicitamente.
                result.append(
                    ModelInfo(
                        name=model.name,
                        runtime=model.runtime,
                        digest=model.digest,
                        status=model.status,
                        capabilities=caps,
                    )
                )
            else:
                result.append(model)
        return result

    async def readiness(self, model_name: str | None) -> ModelReadiness:
        base = await self._base.readiness(model_name)
        if base.digest is None or base.model_name is None or self._hardware is None:
            return base
        caps = self._qualified_for(base.model_name, base.digest)
        if not caps:
            return base
        return ModelReadiness(
            state=base.state,
            model_name=base.model_name,
            digest=base.digest,
            declared_capabilities=base.declared_capabilities,
            qualified_capabilities=caps,
        )

    def _qualified_for(self, model_name: str, digest: str | None) -> tuple[str, ...]:
        if digest is None or self._hardware is None:
            return ()
        return qualified_capabilities_for(
            self._store,
            model_name=model_name,
            digest=digest,
            runtime=self._runtime,
            hardware=self._hardware,
        )


# Il tipo esposto è quello base: chi ne dipende usa ``ModelCatalog``.
__all__ = ["QualifiedModelCatalog"]


# Nota: ``ModelStatus`` re-importato per completezza dell'export (non usato
# direttamente qui) — il wrapper non promuove QUALIFIED automaticamente.
_ = ModelStatus
