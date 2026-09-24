import hashlib
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_models import (
    load_catalog,
    local_host,
    modelfile,
    projector_path,
    selected_models,
    source_path,
    verify_source_digest,
)
from smoke_gemma_vision import solid_png


class Tests(unittest.TestCase):
    def test_vision_fixture_png_has_requested_pixels(self):
        image = solid_png((255, 0, 0))
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        idat_length = int.from_bytes(image[33:37], "big")
        self.assertEqual(image[37:41], b"IDAT")
        rows = zlib.decompress(image[41 : 41 + idat_length])
        self.assertEqual(rows[:4], b"\x00\xff\x00\x00")

    def test_pilot_import_requires_explicit_expansion(self):
        catalog = load_catalog()
        self.assertEqual(
            [model["id"] for model in selected_models(catalog, "import", None, False)],
            ["gemma"],
        )
        self.assertEqual(len(selected_models(catalog, "list", None, False)), 4)
        self.assertEqual(len(selected_models(catalog, "import", None, True)), 4)
        self.assertEqual(
            [
                model["id"]
                for model in selected_models(catalog, "import", "base", False)
            ],
            ["base"],
        )
        with self.assertRaises(ValueError):
            selected_models(catalog, "import", "base", True)
        with self.assertRaises(ValueError):
            selected_models(catalog, "import", "missing", False)

    def test_loopback_only(self):
        for host in (
            "https://example.org",
            "http://user:pw@localhost",
            "http://localhost/path",
            "http://localhost?x=1",
        ):
            with self.assertRaises(ValueError):
                local_host(host)
        self.assertEqual(
            local_host("http://127.0.0.1:11434/"), "http://127.0.0.1:11434"
        )

    def test_source_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inside = root / "inside"
            inside.mkdir()
            outside = root / "outside.gguf"
            outside.write_bytes(b"GGUF")
            link = inside / "escape.gguf"
            link.symlink_to(outside)
            catalog = {"model_root": str(inside)}
            for name in ("../outside.gguf", "escape.gguf"):
                with self.assertRaises(ValueError):
                    source_path(catalog, {"file": name})
            (inside / "bad.gguf").write_bytes(b"fake")
            with self.assertRaises(ValueError):
                source_path(catalog, {"file": "bad.gguf"})
            (inside / "good.gguf").write_bytes(b"GGUF")
            self.assertEqual(
                source_path(catalog, {"file": "good.gguf"}).name, "good.gguf"
            )

    def test_digest_required_for_qualified_pilot_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "pilot.gguf"
            source.write_bytes(b"GGUFtest")
            digest = hashlib.sha256(b"GGUFtest").hexdigest()
            self.assertEqual(
                verify_source_digest({"id": "gemma", "sha256": digest}, source), digest
            )
            with self.assertRaises(ValueError):
                verify_source_digest({"id": "gemma", "sha256": "0" * 64}, source)
            with self.assertRaises(ValueError):
                verify_source_digest({"id": "gemma"}, source)

    def test_proiettore_validato_e_modelfile_riproducibile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            main = root / "main.gguf"
            projector = root / "mmproj.gguf"
            main.write_bytes(b"GGUFmodel")
            projector.write_bytes(b"GGUFprojector")
            catalog = {
                "model_root": str(root),
                "minimum_ollama_version": "0.34.0",
                "context_length": 8192,
            }
            model = {"vision_projector_file": "mmproj.gguf"}
            self.assertEqual(projector_path(catalog, model), projector)
            text = modelfile(catalog, main, projector)
            self.assertEqual(text.count("FROM "), 2)
            self.assertIn('FROM "' + str(projector) + '"', text)
            self.assertIn("PARAMETER num_ctx 8192", text)


if __name__ == "__main__":
    unittest.main()
