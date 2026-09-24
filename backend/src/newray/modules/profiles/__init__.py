"""Modulo profiles: versioni, istruzioni e binding dei profili AI (NewRay.md §§6, 7.2).

L'identità del profilo (``AProfile``) nasce in A-02; versioni
immutabili, binding dei modelli e risoluzione esplicita in B-02. I fatti
sui modelli (``ModelInfo``, ``ModelCatalog``, runtime) appartengono al
modulo ``models`` (B-03). L'API pubblica è ``public.py``; qui si
riesportano solo i nomi pubblici, così ``from newray.modules.profiles
import ...`` resta l'unico punto di import per i consumatori (mai
``adapters`` né tabelle private altrui).
"""

from newray.modules.profiles.public import (
    DEFAULT_BINDING_NAME,
    DEFAULT_PROFILE_VERSION,
    MAX_BINDING_NAME_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_PROFILE_NAME_LENGTH,
    MAX_PROFILE_VERSION_LENGTH,
    AProfile,
    ModelBinding,
    ModelBindingStore,
    Profile,
    ProfileRepository,
    ProfileService,
    ProfileVersion,
    ProfileView,
    ResolvedBinding,
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
    "ProfileRepository",
    "ProfileService",
    "ProfileVersion",
    "ProfileView",
    "ResolvedBinding",
]
