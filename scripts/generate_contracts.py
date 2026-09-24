"""Genera i contratti dai DTO Pydantic del backend (NewRay.md §19.1).

Fonte autorevole: le route e i DTO dell'app FastAPI (`newray.bootstrap.api`).
Lo script produce, in ordine:

1. `contracts/openapi.json` — OpenAPI 3.1 esportato dall'app (deterministico:
   chiavi ordinate, indentazione fissa).
2. `web/src/shared/contracts/api.d.ts` — tipi TypeScript generati con
   `openapi-typescript` (versione bloccata nel lockfile di `web/`).

Con `--check` rigenera tutto in una cartella temporanea e confronta byte a
byte con i file versionati: drift → exit code 1 (usato dalla CI, A-06). I
file generati non si modificano a mano: si evolvono i DTO e si rigenera.

Uso (dal root del repository):

    python scripts/generate_contracts.py            # rigenera i file
    python scripts/generate_contracts.py --check    # verifica l'assenza di drift
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "contracts" / "openapi.json"
TYPESCRIPT_PATH = REPO_ROOT / "web" / "src" / "shared" / "contracts" / "api.d.ts"
WEB_DIR = REPO_ROOT / "web"


def _ensure_backend_env() -> None:
    """Il codegen importa le dipendenze locked del backend, non quelle
    accidentalmente disponibili nell'interprete chiamante."""
    venv_python = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        sys.exit("Ambiente uv del backend assente: esegui prima `cd backend && uv sync --locked`.")
    if Path(sys.executable).absolute() == venv_python.absolute():
        return
    os.execv(
        str(venv_python),
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def _build_openapi() -> dict:
    """Spec OpenAPI dall'app composta con i fake dei test (senza database)."""
    sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
    sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))
    from datetime import UTC, datetime

    from fakes import (
        FakeClock,
        InMemoryConversationRepository,
        InMemoryMessageStore,
        InMemoryModelBindingStore,
        InMemoryModelCatalog,
        InMemoryOwnerBootstrap,
        InMemoryProfileDefaultsSeeder,
        InMemoryProfileRepository,
        InMemorySessionStore,
        InMemoryUserRepository,
    )
    from newray.bootstrap.api import create_app
    from newray.modules.conversations import ConversationService
    from newray.modules.identity import IdentityService
    from newray.modules.profiles import ProfileService

    # ``IdentityService`` richiede la porta ``OwnerBootstrap`` (B-03.2-02):
    # la generazione dei contratti costruisce solo lo schema OpenAPI, non
    # esegue bootstrap; il fake condivide gli stessi store degli altri servizi.
    users = InMemoryUserRepository()
    sessions = InMemorySessionStore()
    identity = IdentityService(
        users,
        sessions,
        FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
        InMemoryOwnerBootstrap(users, sessions),
    )
    conversations = ConversationService(
        InMemoryConversationRepository(),
        InMemoryMessageStore(),
        FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )
    # B-03.2-03: ``ProfileService`` richiede la porta di seeding atomico;
    # il fake condivide gli store con il repository e il binding store.
    profile_repository = InMemoryProfileRepository()
    binding_store = InMemoryModelBindingStore()
    profiles = ProfileService(
        profile_repository,
        binding_store,
        InMemoryModelCatalog(),
        FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
        InMemoryProfileDefaultsSeeder(profile_repository, binding_store),
        default_model_name="llama3.1",
    )
    return create_app(identity, conversations, profiles, cookie_secure=False).openapi()


def render_openapi() -> str:
    """Testo OpenAPI deterministico (stesso DTO → stesso byte a byte)."""
    spec = _build_openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_typescript(openapi_text: str) -> str:
    """Tipi TypeScript via openapi-typescript (bloccato nel lockfile web/)."""
    binary = WEB_DIR / "node_modules" / ".bin" / "openapi-typescript"
    if not binary.exists():
        sys.exit("openapi-typescript non installato: esegui `npm ci --ignore-scripts` in web/.")
    with tempfile.TemporaryDirectory(prefix="newray-contracts-") as tmp:
        input_path = Path(tmp) / "openapi.json"
        output_path = Path(tmp) / "api.d.ts"
        input_path.write_text(openapi_text, encoding="utf-8")
        subprocess.run(
            [
                "npm",
                "exec",
                "--",
                "openapi-typescript",
                str(input_path),
                "-o",
                str(output_path),
            ],
            cwd=WEB_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        return output_path.read_text(encoding="utf-8")


def generate() -> dict[str, str]:
    openapi_text = render_openapi()
    return {
        str(OPENAPI_PATH): openapi_text,
        str(TYPESCRIPT_PATH): render_typescript(openapi_text),
    }


def check() -> bool:
    """Confronta la rigenerazione con i file versionati (file-based, la CI
    non può affidarsi a git: il repository può non essere un repo git)."""
    try:
        rendered = generate()
    except subprocess.CalledProcessError as exc:
        sys.exit(
            "openapi-typescript ha fallito: "
            f"{(exc.stderr or exc.stdout or '').strip()}\n"
            "Esegui `npm ci --ignore-scripts` in web/."
        )
    drifted = False
    for path, expected in rendered.items():
        target = Path(path)
        if not target.exists():
            print(f"DRIFT: {path} mancante — rigenerare con lo script")
            drifted = True
            continue
        current = target.read_text(encoding="utf-8")
        if current != expected:
            print(f"DRIFT: {path} diverge dai DTO — rigenerare con lo script")
            drifted = True
    return not drifted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="non scrive: verifica che la rigenerazione non produca drift",
    )
    args = parser.parse_args()
    if args.check:
        return 0 if check() else 1
    for path, content in generate().items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"generato: {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    _ensure_backend_env()
    sys.exit(main())
