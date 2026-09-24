"""Test negativi del checker architetturale (B-03.2-12)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_architecture as checker


class ArchitectureCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_src = checker.SRC
        checker.SRC = Path(self._tmp.name)

    def tearDown(self) -> None:
        checker.SRC = self._old_src
        self._tmp.cleanup()

    def _source(self, relative: str, contents: str) -> checker.FileRule:
        path = checker.SRC / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return checker.categorize(path)

    def test_core_non_puo_importare_adapter_dello_stesso_modulo(self) -> None:
        rule = self._source(
            "newray/modules/demo/application.py",
            "from newray.modules.demo.adapters.postgres import Store\n",
        )
        errors = checker.newray_violations(rule, checker.collect_imports(rule.path))
        self.assertTrue(any("non può importare adapter" in error for error in errors))

    def test_adapter_non_puo_importare_interfaces(self) -> None:
        rule = self._source(
            "newray/modules/demo/adapters/vendor.py",
            "from newray.interfaces.http.errors import error_body\n",
        )
        errors = checker.newray_violations(rule, checker.collect_imports(rule.path))
        self.assertTrue(any("verso interfaces" in error for error in errors))

    def test_core_non_puo_importare_infrastructure(self) -> None:
        rule = self._source(
            "newray/modules/demo/application.py",
            "from newray.infrastructure.network import HttpClient\n",
        )
        errors = checker.newray_violations(rule, checker.collect_imports(rule.path))
        self.assertTrue(any("verso infrastructure" in error for error in errors))

    def test_framework_e_import_dinamico_vietati(self) -> None:
        rule = self._source(
            "newray/modules/demo/domain.py",
            "import importlib\nfrom pydantic_settings import BaseSettings\n"
            "importlib.import_module('x')\n",
        )
        imports = checker.collect_imports(rule.path)
        self.assertTrue(checker.framework_violations(rule, imports))
        self.assertTrue(checker.dynamic_import_violations(rule.path))

    def test_file_fuori_dalle_categorie_e_segnalato(self) -> None:
        rule = self._source("newray/stray.py", "x = 1\n")
        self.assertEqual(rule.category, "unclassified")
