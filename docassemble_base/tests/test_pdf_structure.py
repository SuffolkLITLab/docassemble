"""Regression tests for whole-document tagged PDF concatenation."""

import io
import tempfile
import unittest
from pathlib import Path

from pikepdf import Array, Dictionary, Encryption, Name, Pdf, String
from docassemble.base.pdf_structure import (
    PdfStructureMerger,
    ensure_pdf_file_metadata,
    ensure_pdf_metadata,
)


def fixture(language="en-US", tagged=True, role=Name.P):
    pdf = Pdf.new()
    page = pdf.add_blank_page()
    pdf.Root.Lang = language
    pdf.docinfo.Title = "A useful title"
    pdf.docinfo.Author = "Example author"
    pdf.Root.ViewerPreferences = Dictionary(HideToolbar=True)
    if not tagged:
        return pdf
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    document = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, P=root)
    )
    paragraph = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.Custom,
            P=document,
            Pg=page.obj,
            K=2,
            C=Array([Name.Style, 1]),
            ID=String("header"),
        )
    )
    paragraph.A = Dictionary(O=Name.Table, Headers=Array([String("header")]))
    annotation = pdf.make_indirect(
        Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([0, 0, 20, 20]),
            StructParent=1,
            P=page.obj,
            A=Dictionary(S=Name.URI, URI=String("https://example.org/")),
        )
    )
    link = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.Link,
            P=document,
            Pg=page.obj,
            K=Dictionary(Type=Name.OBJR, Obj=annotation),
        )
    )
    form = pdf.make_stream(b"/P <</MCID 0>> BDC EMC")
    form.Type = Name.XObject
    form.Subtype = Name.Form
    form.BBox = Array([0, 0, 20, 20])
    form.StructParents = 2
    form_element = pdf.make_indirect(
        Dictionary(
            Type=Name.StructElem,
            S=Name.P,
            P=document,
            Pg=page.obj,
            K=Dictionary(Type=Name.MCR, Stm=form, MCID=0),
        )
    )
    page.Resources = Dictionary(XObject=Dictionary(Fm=form))
    page.Contents = pdf.make_stream(b"/P <</MCID 2>> BDC EMC /Fm Do")
    page.StructParents = 0
    page.Annots = Array([annotation])
    document.K = Array([paragraph, link, form_element])
    root.K = document  # Exercise a scalar K and nested number/name trees.
    root.ParentTree = Dictionary(
        Kids=Array(
            [
                Dictionary(Nums=Array([0, Array([None, None, paragraph]), 1, link])),
                Dictionary(Nums=Array([2, Array([form_element])])),
            ]
        )
    )
    root.IDTree = Dictionary(
        Kids=Array([Dictionary(Names=Array([String("header"), paragraph]))])
    )
    root.RoleMap = Dictionary(Custom=role)
    root.ClassMap = Dictionary(
        Style=Dictionary(O=Name.Table, Headers=Array([String("header")]))
    )
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    return pdf


def reopen(pdf):
    buf = io.BytesIO()
    pdf.save(buf)
    return Pdf.open(buf)


class StructureTests(unittest.TestCase):
    def test_colliding_indexes_links_xobjects_roles_classes_and_ids(self):
        with fixture() as first, fixture("es", role=Name.H1) as second:
            merger = PdfStructureMerger(first)
            merger.append(second)
            merger.finish()
            with reopen(first) as result:
                root = result.Root.StructTreeRoot
                parts = root.K[0].K
                self.assertEqual([str(p.Lang) for p in parts], ["en-US", "es"])
                self.assertTrue(all(p.S == Name.Part for p in parts))
                self.assertTrue(all(p.P.objgen == root.K[0].objgen for p in parts))
                nums = list(root.ParentTree.Nums)
                parents = dict(zip(map(int, nums[::2]), nums[1::2]))
                self.assertEqual(len(parents), 6)
                for i, page in enumerate(result.pages):
                    paragraph, link, form_element = parts[i].K
                    self.assertEqual(paragraph.Pg.objgen, page.objgen)
                    self.assertEqual(paragraph.P.objgen, parts[i].objgen)
                    slots = parents[int(page.StructParents)]
                    self.assertIsNone(slots[0])
                    self.assertIsNone(slots[1])
                    self.assertEqual(slots[2].objgen, paragraph.objgen)
                    self.assertEqual(int(paragraph.K), 2)
                    annotation = page.Annots[0]
                    self.assertEqual(link.K.Obj.objgen, annotation.objgen)
                    self.assertEqual(
                        parents[int(annotation.StructParent)].objgen, link.objgen
                    )
                    form = page.Resources.XObject.Fm
                    self.assertEqual(form_element.K.Stm.objgen, form.objgen)
                    self.assertEqual(
                        parents[int(form.StructParents)][0].objgen, form_element.objgen
                    )
                    self.assertEqual(bytes(paragraph.ID), bytes(paragraph.A.Headers[0]))
                    attr = root.ClassMap[str(paragraph.C[0])]
                    self.assertEqual(bytes(attr.Headers[0]), bytes(paragraph.ID))
                self.assertEqual(root.RoleMap[str(parts[0].K[0].S)], Name.P)
                self.assertEqual(root.RoleMap[str(parts[1].K[0].S)], Name.H1)
                self.assertNotEqual(bytes(parts[0].K[0].ID), bytes(parts[1].K[0].ID))
                self.assertEqual(len(root.IDTree.Names), 4)
                self.assertTrue(result.Root.MarkInfo.Marked)

    def test_repeat_same_input_has_distinct_pages_and_ids(self):
        with fixture() as original, reopen(original) as repeated, reopen(
            original
        ) as destination:
            merger = PdfStructureMerger(destination)
            merger.append(original)
            merger.append(repeated)
            merger.finish()
            with reopen(destination) as result:
                parts = result.Root.StructTreeRoot.K[0].K
                self.assertEqual(len(parts), 3)
                self.assertEqual(len({bytes(p.K[0].ID) for p in parts}), 3)
                self.assertEqual(
                    [p.K[0].Pg.objgen for p in parts], [p.objgen for p in result.pages]
                )

    def test_untagged_input_is_not_hidden_or_declared_tagged(self):
        for tagged_first in (True, False):
            with fixture(tagged=tagged_first) as a, fixture(
                tagged=not tagged_first
            ) as b:
                merger = PdfStructureMerger(a)
                merger.append(b)
                merger.finish()
                with reopen(a) as result:
                    self.assertEqual(len(result.pages), 2)
                    self.assertFalse(result.Root.MarkInfo.Marked)
                    self.assertTrue(result.Root.MarkInfo.Suspects)
                    self.assertEqual(len(result.Root.StructTreeRoot.K[0].K), 1)
                    self.assertNotIn(
                        b"Artifact",
                        result.pages[not tagged_first].Contents.read_bytes(),
                    )

    def test_respects_reserved_parent_tree_next_key(self):
        with fixture() as a, fixture() as b:
            a.Root.StructTreeRoot.ParentTreeNextKey = 100
            merger = PdfStructureMerger(a)
            merger.append(b)
            merger.finish()
            self.assertEqual(int(a.pages[1].StructParents), 100)
            self.assertEqual(int(a.Root.StructTreeRoot.ParentTreeNextKey), 103)

    def test_rejects_missing_index_in_first_input(self):
        with fixture() as pdf:
            pdf.pages[0].StructParents = 42
            with self.assertRaisesRegex(ValueError, "missing from ParentTree"):
                PdfStructureMerger(pdf)

    def test_unreachable_stale_object_does_not_break_merge(self):
        with fixture() as a, fixture() as b:
            orphan = b.make_indirect(Dictionary(StructParents=999))
            merger = PdfStructureMerger(a)
            merger.append(b)
            merger.finish()
            self.assertEqual(len(a.pages), 2)
            self.assertEqual(int(orphan.StructParents), 999)

    def test_untagged_stale_indices_are_removed_in_either_position(self):
        for first_untagged in (False, True):
            with fixture() as a, fixture() as b:
                untagged = a if first_untagged else b
                del untagged.Root.StructTreeRoot
                merger = PdfStructureMerger(a)
                merger.append(b)
                merger.finish()
                with reopen(a) as result:
                    page = result.pages[0 if first_untagged else 1]
                    self.assertNotIn("/StructParents", page.obj)
                    self.assertNotIn("/StructParent", page.Annots[0])
                    self.assertNotIn("/StructParents", page.Resources.XObject.Fm)
                    self.assertFalse(result.Root.MarkInfo.Marked)

    def test_shared_table_attribute_is_renamed_only_once(self):
        with fixture() as a, fixture() as b:
            root = b.Root.StructTreeRoot
            paragraph, link, _ = root.K.K
            link.ID = String("DA1_header")
            root.IDTree = Dictionary(
                Names=Array([String("DA1_header"), link, String("header"), paragraph])
            )
            shared = b.make_indirect(
                Dictionary(O=Name.Table, Headers=Array([String("header")]))
            )
            paragraph.A = shared
            link.A = shared
            root.ClassMap.Style = shared
            merger = PdfStructureMerger(a)
            merger.append(b)
            merger.finish()
            with reopen(a) as result:
                paragraph, link, _ = result.Root.StructTreeRoot.K[0].K[1].K
                self.assertEqual(paragraph.A.objgen, link.A.objgen)
                self.assertEqual(bytes(paragraph.A.Headers[0]), bytes(paragraph.ID))
                self.assertNotEqual(bytes(paragraph.A.Headers[0]), bytes(link.ID))

    def test_rejects_missing_parent_index(self):
        with fixture() as a, fixture() as b:
            b.pages[0].StructParents = 42
            with self.assertRaisesRegex(ValueError, "missing from ParentTree"):
                PdfStructureMerger(a).append(b)

    def test_rejects_namespaces_and_cycles(self):
        with fixture() as pdf:
            pdf.Root.StructTreeRoot.Namespaces = Array()
            with self.assertRaisesRegex(ValueError, "namespace"):
                PdfStructureMerger(pdf)
        with fixture() as pdf:
            tree = pdf.make_indirect(Dictionary())
            tree.Kids = Array([tree])
            pdf.Root.StructTreeRoot.ParentTree = tree
            with self.assertRaisesRegex(ValueError, "Cyclic"):
                PdfStructureMerger(pdf)

    def test_rejects_structure_cycles_and_duplicate_indexes(self):
        with fixture() as pdf:
            document = pdf.Root.StructTreeRoot.K
            document.K.append(document)
            with self.assertRaisesRegex(ValueError, "Cyclic"):
                PdfStructureMerger(pdf)
        with fixture() as pdf:
            tree = pdf.Root.StructTreeRoot.ParentTree
            tree.Kids.append(tree.Kids[0])
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                PdfStructureMerger(pdf)

    def test_metadata_without_claiming_conformance(self):
        with fixture() as pdf:
            ensure_pdf_metadata(pdf)
            with reopen(pdf) as result:
                with result.open_metadata() as meta:
                    self.assertEqual(meta["dc:title"], "A useful title")
                    self.assertEqual(meta["dc:creator"], ["Example author"])
                    self.assertEqual(set(meta["dc:language"]), {"en-US"})
                    self.assertNotIn("pdfuaid:part", meta)
                self.assertTrue(result.Root.ViewerPreferences.DisplayDocTitle)
                self.assertTrue(result.Root.ViewerPreferences.HideToolbar)

    def test_existing_metadata_preserved_but_merge_claims_removed(self):
        with fixture() as pdf:
            with pdf.open_metadata() as meta:
                meta["dc:title"] = "An existing XMP title"
                meta["pdfuaid:part"] = "1"
                meta["pdfaid:part"] = "2"
                meta["pdfaid:conformance"] = "A"
            ensure_pdf_metadata(pdf)
            with pdf.open_metadata() as meta:
                self.assertEqual(meta["dc:title"], "An existing XMP title")
                self.assertEqual(meta["pdfuaid:part"], "1")
            ensure_pdf_metadata(pdf, merged=True)
            with pdf.open_metadata() as meta:
                self.assertNotIn("pdfuaid:part", meta)
                self.assertNotIn("pdfaid:part", meta)
                self.assertNotIn("pdfaid:conformance", meta)

    def test_unknown_language_and_suspect_tags_are_not_silently_changed(self):
        with fixture() as a, fixture() as b:
            del b.Root.Lang
            b.Root.MarkInfo.Suspects = True
            b.Root.MarkInfo.UserProperties = True
            merger = PdfStructureMerger(a)
            merger.append(b)
            merger.finish()
            with reopen(a) as result:
                self.assertEqual(str(result.Root.StructTreeRoot.K[0].K[1].Lang), "")
                self.assertTrue(result.Root.MarkInfo.Suspects)
                self.assertTrue(result.Root.MarkInfo.UserProperties)

    def test_xmp_only_title_controls_display(self):
        with fixture() as pdf:
            del pdf.docinfo.Title
            with pdf.open_metadata(update_docinfo=False) as meta:
                meta["dc:title"] = "XMP-only title"
            ensure_pdf_metadata(pdf)
            self.assertTrue(pdf.Root.ViewerPreferences.DisplayDocTitle)

    def test_metadata_repair_preserves_encryption(self):
        with tempfile.TemporaryDirectory() as directory, fixture() as pdf:
            path = Path(directory) / "private.pdf"
            pdf.save(path, encryption=Encryption(owner="owner", user="reader", R=6))
            ensure_pdf_file_metadata(path, password="reader")
            with Pdf.open(path, password="reader") as result:
                self.assertTrue(result.is_encrypted)
                self.assertIn("/Metadata", result.Root)


if __name__ == "__main__":
    unittest.main()
