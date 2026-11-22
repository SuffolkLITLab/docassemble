"""Exercise the public conversion pipeline, not just PDF structure helpers."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pikepdf import Encryption, Pdf
from docassemble.base import pandoc
from docassemble.base.error import DAError
from test_pdf_structure import fixture


class ConcatenateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.paths = []
        for name in ("one", "two"):
            path = self.directory / (name + ".pdf")
            with fixture() as pdf:
                pdf.save(path)
            self.paths.append(str(path))
        self.outputs = []
        self.addCleanup(self.remove_outputs)

    def remove_outputs(self):
        for path in self.outputs:
            Path(path).unlink(missing_ok=True)

    def concatenate(self, paths=None, **kwargs):
        result = pandoc.concatenate_files(paths or self.paths, **kwargs)
        self.outputs.append(result)
        return result

    def test_public_merge_sets_bundle_title_and_keeps_both_structures(self):
        result = self.concatenate(title="Motion and filing instructions")
        with Pdf.open(result) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            self.assertEqual(len(pdf.Root.StructTreeRoot.K[0].K), 2)
            self.assertEqual(str(pdf.docinfo.Title), "Motion and filing instructions")
            self.assertEqual(
                pdf.open_metadata()["dc:title"], "Motion and filing instructions"
            )

    def test_single_file_is_byte_identical_unless_title_requested(self):
        result = self.concatenate(self.paths[:1])
        self.assertEqual(Path(result).read_bytes(), Path(self.paths[0]).read_bytes())
        result = self.concatenate(self.paths[:1], title="New title")
        with Pdf.open(result) as pdf:
            self.assertEqual(pdf.open_metadata()["dc:title"], "New title")
            self.assertIn("/StructTreeRoot", pdf.Root)

    def test_highest_input_version_is_preserved(self):
        with fixture() as pdf:
            pdf.save(self.paths[0], force_version="1.4")
            pdf.save(self.paths[1], min_version="1.7")
        with Pdf.open(self.concatenate()) as pdf:
            self.assertGreaterEqual(tuple(map(int, pdf.pdf_version.split("."))), (1, 7))

    def test_requested_title_sets_display_when_source_has_no_title(self):
        with fixture() as pdf:
            del pdf.docinfo.Title
            pdf.save(self.paths[0])
        with Pdf.open(self.concatenate(title="Bundle title")) as pdf:
            self.assertTrue(pdf.Root.ViewerPreferences.DisplayDocTitle)

    def test_encrypted_output_keeps_structure(self):
        result = self.concatenate(password="reader", owner_password="owner")
        with Pdf.open(result, password="reader") as pdf:
            self.assertTrue(pdf.is_encrypted)
            self.assertEqual(len(pdf.Root.StructTreeRoot.K[0].K), 2)

    def test_pdfa_will_not_silently_discard_tags(self):
        for paths in (self.paths, self.paths[:1]):
            with patch.object(pandoc, "pdf_to_pdfa") as convert:
                with self.assertRaisesRegex(DAError, "pdfa=False"):
                    self.concatenate(paths, pdfa=True)
                convert.assert_not_called()

    def test_rejected_pdfa_removes_temporary_output(self):
        created = []
        original = pandoc.tempfile.NamedTemporaryFile

        def record(*args, **kwargs):
            handle = original(*args, **kwargs)
            created.append(handle.name)
            return handle

        with patch.object(pandoc.tempfile, "NamedTemporaryFile", side_effect=record):
            with self.assertRaises(DAError):
                self.concatenate(pdfa=True)
        self.assertTrue(created)
        self.assertTrue(all(not Path(path).exists() for path in created))

    def test_untagged_pdfa_still_uses_existing_converter(self):
        with fixture(tagged=False) as pdf:
            pdf.save(self.paths[0])
        with patch.object(pandoc, "pdf_to_pdfa") as convert:
            self.concatenate(self.paths[:1], pdfa=True)
            convert.assert_called_once()

    def test_provider_encryption_with_different_owner_password(self):
        provider_pdf = self.directory / "provider.pdf"
        with fixture() as pdf:
            pdf.save(
                provider_pdf,
                encryption=Encryption(user="reader", owner="provider-owner", R=6),
            )

        def provider(from_file, to_file, *args):
            shutil.copyfile(provider_pdf, to_file)

        output = self.directory / "converted.pdf"
        # A provider may not use the caller's requested owner password.
        with patch.dict(pandoc.daconfig, {"gotenberg": {"enable": True}}), patch.object(
            pandoc, "gotenberg_to_pdf", side_effect=provider
        ):
            self.assertTrue(
                pandoc.word_to_pdf(
                    self.paths[0],
                    "docx",
                    str(output),
                    tagged=True,
                    password="reader",
                    owner_password="requested-owner",
                    retry=False,
                )
            )
        with Pdf.open(output, password="reader") as pdf:
            self.assertTrue(pdf.is_encrypted)
            self.assertIn("/Metadata", pdf.Root)
            self.assertIn("/StructTreeRoot", pdf.Root)


if __name__ == "__main__":
    unittest.main()
