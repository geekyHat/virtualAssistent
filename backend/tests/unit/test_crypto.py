"""Kernel crypto: hash e verifica delle credenziali locali (B-03.2-14, B-03.2-32)."""

from __future__ import annotations

import pytest

from newray.kernel.crypto import (
    InvalidCredentialFormat,
    InvalidStoredHash,
    _make_pbkdf2_hash,
    dummy_verify,
    hash_credential,
    normalize_credential,
    verify_and_check_rehash,
    verify_credential,
)


def test_hash_e_verifica_riconoscono_la_stessa_credenziale() -> None:
    stored = hash_credential("test-passphrase-1234")
    assert verify_credential("test-passphrase-1234", stored) is True


def test_hash_e_verifica_rifiutano_una_credenziale_diversa() -> None:
    stored = hash_credential("test-passphrase-1234")
    assert verify_credential("test-passphrase-4321", stored) is False


def test_hash_e_diverso_ogni_volta_grazie_al_sale() -> None:
    """Il salt cambia ad ogni hashing: due record hash non collidono."""
    a = hash_credential("test-passphrase-1234")
    b = hash_credential("test-passphrase-1234")
    assert a != b
    assert verify_credential("test-passphrase-1234", a)
    assert verify_credential("test-passphrase-1234", b)


def test_hash_rifiuta_credenziale_sotto_la_soglia() -> None:
    with pytest.raises(InvalidCredentialFormat):
        hash_credential("corta")


def test_hash_rifiuta_credenziale_solo_whitespace() -> None:
    with pytest.raises(InvalidCredentialFormat):
        hash_credential("   \t\n  ")


def test_normalize_rimuove_solo_whitespace_di_contorno() -> None:
    assert normalize_credential("  parola d'ordine  ") == "parola d'ordine"
    assert normalize_credential("parola d'ordine") == "parola d'ordine"


def test_verify_su_record_malformato_solleva_invalidstoredhash() -> None:
    with pytest.raises(InvalidStoredHash):
        verify_credential("qualunque", "senza-dollari")


def test_verify_su_algoritmo_ignoto_solleva_invalidstoredhash() -> None:
    with pytest.raises(InvalidStoredHash):
        verify_credential("qualunque", "sha1$100$Zg$Zg")


def test_verify_su_hash_vuoto_e_false() -> None:
    """Owner senza credenziale: non un'eccezione, un rifiuto controllato."""
    assert verify_credential("qualunque", "") is False


def test_verify_su_iterazioni_non_positive_solleva() -> None:
    with pytest.raises(InvalidStoredHash):
        verify_credential("qualunque", "pbkdf2_sha256$0$Zg$Zg")


# --- B-03.2-32: Argon2id e retrocompatibilità PBKDF2 ---


def test_nuovo_hash_e_argon2id() -> None:
    stored = hash_credential("test-passphrase-1234")
    assert stored.startswith("$argon2id$")


def test_pbkdf2_legacy_verificato_e_segnala_rehash() -> None:
    legacy = _make_pbkdf2_hash("test-passphrase-1234")
    result = verify_and_check_rehash("test-passphrase-1234", legacy)
    assert result.valid is True
    assert result.needs_rehash is True


def test_pbkdf2_legacy_password_sbagliata() -> None:
    legacy = _make_pbkdf2_hash("test-passphrase-1234")
    result = verify_and_check_rehash("sbagliata-1234567", legacy)
    assert result.valid is False
    assert result.needs_rehash is False


def test_argon2_non_richiede_rehash() -> None:
    stored = hash_credential("test-passphrase-1234")
    result = verify_and_check_rehash("test-passphrase-1234", stored)
    assert result.valid is True
    assert result.needs_rehash is False


def test_argon2_password_sbagliata() -> None:
    stored = hash_credential("test-passphrase-1234")
    result = verify_and_check_rehash("sbagliata-1234567", stored)
    assert result.valid is False
    assert result.needs_rehash is False


def test_dummy_verify_non_solleva() -> None:
    dummy_verify()


def test_verify_and_check_rehash_hash_vuoto() -> None:
    result = verify_and_check_rehash("qualunque", "")
    assert result.valid is False
    assert result.needs_rehash is False


def test_verify_and_check_rehash_record_malformato() -> None:
    with pytest.raises(InvalidStoredHash):
        verify_and_check_rehash("qualunque", "non-un-hash-valido")
