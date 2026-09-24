"""Verifica dei confini architetturali del backend (NewRay.md §§4–8, 19).

Analisi statica (AST, solo standard library) di ``backend/src/newray``:

1. Kernel pulito: ``newray.kernel`` non importa framework né altri moduli.
2. Dominio/applicazione puliti: ``domain``/``application``/``ports``/
   ``public`` dei moduli non importano FastAPI, Pydantic, SQLAlchemy né i
   livelli ``interfaces``/``infrastructure``/``bootstrap``.
3. Adapter: possono toccare ``infrastructure`` e i driver (SQLAlchemy,
   psycopg), mai ``interfaces`` né ``bootstrap``.
4. ``interfaces`` traduce i confini: niente ``infrastructure`` diretta né
   ``bootstrap``; altri moduli solo tramite il loro package pubblico.
5. ``infrastructure`` non dipende da moduli né da interface.
6. Solo ``bootstrap`` compone: può importare tutto (root di composizione).
7. Cross-modulo: ``from newray.modules.<altro> import ...`` solo dal
   package pubblico ``newray.modules.<altro>`` (mai ``.adapters``,
   ``.domain`` o altri dettagli privati).
8. Nessun ciclo di import dentro ``newray``; import dinamici vietati.

Uso (dal root del repository):

    python scripts/check_architecture.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "backend" / "src"

# Pacchetti di runtime vietati fuori dai livelli autorizzati.
FRAMEWORK_MODULES = {
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "requests",
    "sqlalchemy",
    "psycopg",
    "alembic",
    "httpx",
    "uvicorn",
}
# I adapter PostgreSQL sono l'unico livello che può usare driver DB.
ADAPTER_FRAMEWORKS = {"sqlalchemy", "psycopg"}
# L'infrastruttura isola i vendor di egress di rete (httpx): un solo
# punto (infrastructure/network.py), gli adapter non lo vedono.
INFRASTRUCTURE_FRAMEWORKS = {"httpx"}


@dataclass
class FileRule:
    """Categorie di confine per file sorgente."""

    path: Path
    category: str  # kernel | module-core | module-adapter | interfaces | infrastructure | bootstrap
    module: str | None = None  # nome del modulo per i file di moduli

    @property
    def relative(self) -> str:
        return str(self.path.relative_to(SRC))


def categorize(path: Path) -> FileRule:
    parts = path.relative_to(SRC).parts  # es. ("newray", "modules", "identity", "ports.py")
    if parts[0] != "newray" or len(parts) < 2:
        return FileRule(path, "root")
    layer = parts[1]
    if layer == "kernel":
        return FileRule(path, "kernel")
    if layer == "infrastructure":
        return FileRule(path, "infrastructure")
    if layer == "interfaces":
        return FileRule(path, "interfaces")
    if layer == "bootstrap":
        return FileRule(path, "bootstrap")
    if layer == "modules" and len(parts) >= 3:
        module = parts[2]
        if "adapters" in parts[3:]:
            return FileRule(path, "module-adapter", module)
        return FileRule(path, "module-core", module)
    if parts == ("newray", "__init__.py"):
        return FileRule(path, "root-init")
    return FileRule(path, "unclassified")


def _imported_packages(node: ast.AST) -> list[str]:
    """Pacchetti importati da un statement (senza i nomi delle variabili)."""
    packages: list[str] = []
    if isinstance(node, ast.Import):
        packages.extend(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return []  # relativi: gestiti separatamente, restano nel package
        if node.module:
            packages.append(node.module)
    return packages


def collect_imports(path: Path, newray_only: bool = False) -> list[str]:
    """Tutti i target importati da un file (assoluti e relativi).

    Con ``newray_only`` filtra solo i target ``newray.*`` (usato dal
    rilevamento dei cicli).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    own_parts = path.relative_to(SRC).with_suffix("").parts
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                base = list(own_parts[: -node.level])
                module = node.module.split(".") if node.module else []
                targets.append(".".join(base + module))
                continue
            for name in _imported_packages(node):
                if newray_only and not (name == "newray" or name.startswith("newray.")):
                    continue
                targets.append(name)
    return targets


def framework_violations(rule: FileRule, imports: list[str]) -> list[str]:
    """Framework vietati per categoria (il nome del primo pacchetto basta).

    Autorizzazioni per layer:
    - ``infrastructure`` e adapter PostgreSQL: driver DB (SQLAlchemy,
      psycopg); ``infrastructure``: vendor di egress di rete (httpx);
    - ``interfaces``: framework web (confine HTTP);
    - ``bootstrap``: tutto (root di composizione).
    """
    allowed: set[str] = set()
    if rule.category in {"module-adapter", "infrastructure"}:
        allowed |= ADAPTER_FRAMEWORKS
    if rule.category == "infrastructure":
        allowed |= INFRASTRUCTURE_FRAMEWORKS
    if rule.category in {"interfaces", "bootstrap"}:
        allowed |= FRAMEWORK_MODULES
    bad = {
        name.split(".")[0]
        for name in imports
        if name.split(".")[0] in FRAMEWORK_MODULES and name.split(".")[0] not in allowed
    }
    return [f"{rule.relative}: import vietato di framework '{n}'" for n in sorted(bad)]


def newray_violations(rule: FileRule, imports: list[str]) -> list[str]:
    """Regole di dipendenza tra layer e cross-modulo via package pubblico.

    ``bootstrap`` è il punto di composizione (NewRay.md §5.1): può importare
    tutto, inclusi gli adapter concreti. Le regole valgono per tutti gli
    altri layer.
    """
    if rule.category == "bootstrap":
        return []
    problems: list[str] = []
    for name in sorted(set(imports)):
        parts = name.split(".")
        if len(parts) < 2 or parts[0] != "newray":
            continue
        layer = parts[1]
        # Cross-modulo: solo dal package pubblico (mai sotto-moduli altrui).
        if layer == "modules" and len(parts) >= 3:
            other = parts[2]
            if rule.module is not None and other == rule.module:
                if rule.category == "module-core" and len(parts) > 3 and parts[3] == "adapters":
                    problems.append(
                        f"{rule.relative}: core del modulo non può importare adapter ('{name}')"
                    )
                continue
            if len(parts) > 3:
                problems.append(
                    f"{rule.relative}: cross-modulo privato '{name}' — "
                    "importa solo il package pubblico newray.modules." + other
                )
            elif rule.category in {"kernel", "infrastructure"}:
                problems.append(
                    f"{rule.relative}: {rule.category} non può dipendere da moduli ('{name}')"
                )
        elif layer == "interfaces" and rule.category in {
            "kernel",
            "module-core",
            "module-adapter",
            "infrastructure",
        }:
            problems.append(f"{rule.relative}: dipendenza vietata verso interfaces ('{name}')")
        elif layer == "infrastructure" and rule.category in {"kernel", "module-core", "interfaces"}:
            problems.append(
                f"{rule.relative}: dipendenza vietata verso infrastructure ('{name}')"
            )
        elif layer == "bootstrap":
            problems.append(f"{rule.relative}: bootstrap non è importabile ('{name}')")
        # kernel non importa altri layer newray
        if rule.category == "kernel" and layer != "kernel":
            problems.append(f"{rule.relative}: kernel pulito, vietato importare '{name}'")
    return problems


def dynamic_import_violations(path: Path) -> list[str]:
    """La policy è vietare import dinamici, non fingere di analizzarli.

    Il grafo di import deve restare ispezionabile dal gate. Un'eventuale
    eccezione futura richiederà una decisione esplicita e un controllo mirato,
    non un import dinamico silenzioso.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            problems.append(f"{path.relative_to(SRC)}: import dinamico vietato (__import__)")
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
        ):
            problems.append(f"{path.relative_to(SRC)}: import dinamico vietato (importlib.import_module)")
    return problems


def find_cycles(rules: list[FileRule]) -> list[str]:
    """Cicli di import tra file ``newray`` (grafo file → file importati)."""
    files = {r.path for r in rules}

    def resolve(target: str) -> Path | None:
        rel = Path(*target.split("."))
        for candidate in (rel / "__init__.py", rel.with_suffix(".py")):
            full = SRC / candidate
            if full in files:
                return full
        return None

    graph: dict[Path, set[Path]] = {path: set() for path in files}
    for rule in rules:
        for target in collect_imports(rule.path, newray_only=True):
            resolved = resolve(target)
            if resolved is not None and resolved != rule.path:
                graph[rule.path].add(resolved)

    cycles: list[list[Path]] = []
    visited: set[Path] = set()

    def visit(node: Path, stack: list[Path]) -> None:
        if node in visited:
            return
        visited.add(node)
        stack.append(node)
        for neighbor in graph[node]:
            if neighbor in stack:
                idx = stack.index(neighbor)
                cycles.append(stack[idx:] + [neighbor])
            else:
                visit(neighbor, stack)
        stack.pop()

    for node in files:
        visit(node, [])
    if cycles:
        return [
            " -> ".join(str(c.relative_to(SRC)) for c in cycle)
            for cycle in cycles
        ]
    return []


def main() -> int:
    rules = [categorize(p) for p in sorted(SRC.rglob("*.py"))]
    problems: list[str] = []
    for rule in rules:
        if rule.category == "unclassified":
            problems.append(f"{rule.relative}: file non classificato dal checker architetturale")
            continue
        if rule.category == "root-init":
            continue
        imports = collect_imports(rule.path)
        problems.extend(framework_violations(rule, imports))
        problems.extend(newray_violations(rule, imports))
        problems.extend(dynamic_import_violations(rule.path))
    problems.extend(find_cycles(rules))
    if problems:
        print("Violazioni architetturali:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"Architettura coerente: {len(rules)} file verificati "
        "(kernel pulito, confine moduli via public, nessun ciclo di import)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
