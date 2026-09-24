"""API pubblica del modulo profiles (NewRay.md §5.1; B-02, rifinito in B-03).

Espone profili, versioni e binding: la risoluzione. I fatti sui modelli
(``ModelInfo``, ``ModelStatus``, ``ModelCatalog``, runtime) appartengono
al modulo ``models`` (B-03) e si importano da lì. Questo è l'unico punto
di import per gli altri moduli e per il bootstrap: mai importare
``adapters`` o tabelle private da qui.
"""

from newray.modules.profiles.application import ProfileService
from newray.modules.profiles.domain import (
    DEFAULT_BINDING_NAME,
    DEFAULT_PROFILE_VERSION,
    MAX_BINDING_NAME_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_PROFILE_NAME_LENGTH,
    MAX_PROFILE_VERSION_LENGTH,
    AProfile,
    ModelBinding,
    Profile,
    ProfileVersion,
    ProfileView,
    ResolvedBinding,
)
from newray.modules.profiles.ports import (
    ModelBindingStore,
    ProfileDefaultsSeeder,
    ProfileRepository,
    ProfileVersionWriter,
)

__all__ = [
    "AProfile",
    "DEFAULT_BINDING_NAME",
    "DEFAULT_PROFILE_VERSION",
    "MAX_BINDING_NAME_LENGTH",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "MAX_INSTRUCTIONS_LENGTH",
    "MAX_PROFILE_NAME_LENGTH",
    "MAX_PROFILE_VERSION_LENGTH",
    "ModelBinding",
    "ModelBindingStore",
    "Profile",
    "ProfileDefaultsSeeder",
    "ProfileRepository",
    "ProfileService",
    "ProfileView",
    "ProfileVersion",
    "ProfileVersionWriter",
    "ResolvedBinding",
]
