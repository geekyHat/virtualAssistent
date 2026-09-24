"""Hash e verifica delle credenziali locali (NewRay.md §7.2, §20.3).

L'edizione personale non usa provider di identità esterni: la credenziale
del proprietario è custodita nel suo database locale come hash salato.
Non è mai in chiaro nei log, nei DTO o nei backup; il confronto è a
tempo costante.

Algoritmo principale: Argon2id via pwdlib (NewRay.md §§4.2/20.3).
Compatibilità: gli hash PBKDF2 creati prima di B-03.2-32 vengono
verificati e marcati per rehash; i nuovi hash sono sempre Argon2id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

#: Minima lunghezza accettata: taglia la banalità senza obbligare a
#: "regole di composizione" fuori tema (NIST SP 800-63B).
MIN_CREDENTIAL_LENGTH = 8

_PBKDF2_ALGORITHM = "pbkdf2_sha256"

_hasher = PasswordHash((Argon2Hasher(),))

_DUMMY_HASH = _hasher.hash("dummy-credential-for-timing")


class InvalidCredentialFormat(ValueError):
    """La credenziale in chiaro non rispetta lunghezza/composizione minima."""


class InvalidStoredHash(ValueError):
    """Il campo hash persistito non è nel formato atteso: dato corrotto o
    prodotto da un algoritmo non supportato — mai un fallimento di login."""


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Esito della verifica: valido e indicazione di rehash necessario."""

    valid: bool
    needs_rehash: bool


def normalize_credential(raw: str) -> str:
    """Normalizza la credenziale prima di hash/verifica."""
    return raw.strip()


def hash_credential(raw: str) -> str:
    """Produce il record hash Argon2id via pwdlib.

    Solleva ``InvalidCredentialFormat`` se la credenziale è troppo corta.
    """
    normalized = normalize_credential(raw)
    if len(normalized) < MIN_CREDENTIAL_LENGTH:
        raise InvalidCredentialFormat(
            f"la credenziale deve avere almeno {MIN_CREDENTIAL_LENGTH} caratteri"
        )
    return _hasher.hash(normalized)


def verify_credential(raw: str, stored: str) -> bool:
    """Confronto a tempo costante fra credenziale in chiaro e record hash.

    Accetta sia Argon2id (pwdlib) sia PBKDF2 legacy. Restituisce solo
    True/False — il rehash è responsabilità di ``verify_and_check_rehash``.
    """
    return verify_and_check_rehash(raw, stored).valid


def verify_and_check_rehash(raw: str, stored: str) -> VerifyResult:
    """Verifica e indica se serve rehash (PBKDF2 → Argon2id).

    Solleva ``InvalidStoredHash`` se il formato non è riconosciuto.
    """
    if not stored:
        return VerifyResult(valid=False, needs_rehash=False)
    normalized = normalize_credential(raw)
    if stored.startswith(f"{_PBKDF2_ALGORITHM}$"):
        valid = _verify_pbkdf2(normalized, stored)
        return VerifyResult(valid=valid, needs_rehash=valid)
    try:
        valid, updated = _hasher.verify_and_update(normalized, stored)
    except Exception as exc:
        raise InvalidStoredHash(f"record hash non riconosciuto: {exc}") from exc
    return VerifyResult(valid=valid, needs_rehash=valid and updated is not None)


def dummy_verify() -> None:
    """Esegue un hash-verify fittizio per uniformare il tempo di risposta
    quando l'utente non esiste (B-03.2-31)."""
    _hasher.verify("not-a-real-password", _DUMMY_HASH)


def _verify_pbkdf2(normalized: str, stored: str) -> bool:
    """Verifica di un hash PBKDF2 legacy (formato pre-B-03.2-32)."""
    try:
        algo, iterations_text, salt_text, hash_text = stored.split("$", 3)
    except ValueError as exc:
        raise InvalidStoredHash("record hash PBKDF2 malformato") from exc
    if algo != _PBKDF2_ALGORITHM:
        raise InvalidStoredHash(f"algoritmo non supportato: {algo}")
    try:
        iterations = int(iterations_text)
    except ValueError as exc:
        raise InvalidStoredHash("iterazioni non intere") from exc
    if iterations <= 0:
        raise InvalidStoredHash("iterazioni non positive")
    try:
        salt = _b64decode(salt_text)
        expected = _b64decode(hash_text)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise InvalidStoredHash("codifica base64 non valida") from exc
    candidate = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def _make_pbkdf2_hash(raw: str, *, iterations: int = 600_000) -> str:
    """Genera un hash PBKDF2 legacy — solo per i test di retrocompatibilità."""
    normalized = normalize_credential(raw)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
    return f"{_PBKDF2_ALGORITHM}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
