"""Preserve PDF 1.7 logical structure when concatenating whole documents.

This carries existing accessibility information; it does not create tags for
untagged content or certify the result as PDF/UA or PDF/A.
"""

from pikepdf import Array, Dictionary, Name, Pdf, Stream, String


def _items(value):
    if value is None:
        return []
    return list(value) if isinstance(value, Array) else [value]


def _tree_entries(tree, key):
    """Flatten a number/name tree without changing values or MCID array slots."""
    result = []
    seen = set()

    def visit(node):
        if node.is_indirect:
            if node.objgen in seen:
                raise ValueError("Cyclic PDF structure index")
            seen.add(node.objgen)
        entries = list(node.get(key, []))
        if len(entries) % 2:
            raise ValueError("Malformed PDF structure index")
        result.extend(zip(entries[::2], entries[1::2]))
        for child in node.get("/Kids", []):
            visit(child)

    if tree is not None:
        visit(tree)
    return result


def _elements(root):
    seen = set()

    def visit(node):
        if not isinstance(node, Dictionary):
            return
        if node.is_indirect:
            if node.objgen in seen:
                raise ValueError("Cyclic or repeated PDF structure element")
            seen.add(node.objgen)
        if node.get("/Type") == Name.StructElem or "/S" in node:
            yield node
        for child in _items(node.get("/K")):
            yield from visit(child)

    yield from visit(root)


def ensure_pdf_metadata(pdf, *, merged=False):
    """Fill missing XMP from existing document information, without inventing it."""
    new_metadata = "/Metadata" not in pdf.Root
    with pdf.open_metadata(
        set_pikepdf_as_editor=False, update_docinfo=False
    ) as metadata:
        if new_metadata:
            metadata.load_from_docinfo(pdf.docinfo, delete_missing=False)
        for key, info in [("dc:title", "/Title"), ("dc:description", "/Subject")]:
            if not metadata.get(key) and pdf.docinfo.get(info):
                metadata[key] = str(pdf.docinfo[info])
        if not metadata.get("dc:creator") and pdf.docinfo.get("/Author"):
            metadata["dc:creator"] = [str(pdf.docinfo.Author)]
        if not metadata.get("dc:language") and pdf.Root.get("/Lang"):
            metadata["dc:language"] = [str(pdf.Root.Lang)]
        if merged:
            # A source's conformance declaration says nothing about a new bundle.
            for key in list(metadata):
                if key.startswith(
                    (
                        "{http://www.aiim.org/pdfua/ns/id/}",
                        "{http://www.aiim.org/pdfa/ns/id/}",
                        "pdfuaid:",
                        "pdfaid:",
                    )
                ):
                    del metadata[key]
    if pdf.docinfo.get("/Title") or metadata.get("dc:title"):
        if "/ViewerPreferences" not in pdf.Root:
            pdf.Root.ViewerPreferences = Dictionary()
        pdf.Root.ViewerPreferences.DisplayDocTitle = True


def ensure_pdf_file_metadata(path, password=""):
    with Pdf.open(path, password=password or "", allow_overwriting_input=True) as pdf:
        ensure_pdf_metadata(pdf)
        pdf.save(path, encryption=True if pdf.is_encrypted else False)


class PdfStructureMerger:
    """Append full PDFs while preserving their existing tag trees and language.

    PDF 2.0 namespace-based tags are rejected explicitly rather than silently
    flattened. The inputs are open working copies, never saved back to sources.
    """

    def __init__(self, destination):
        self.pdf = destination
        self.pdf_version = destination.pdf_version
        self.sequence = 0
        self.user_properties = bool(
            destination.Root.get("/MarkInfo", {}).get("/UserProperties", False)
        )
        self.suspects = bool(
            destination.Root.get("/MarkInfo", {}).get("/Suspects", False)
        )
        self.complete = bool(
            destination.Root.get("/MarkInfo", {}).get("/Marked", False)
            and destination.Root.get("/StructTreeRoot")
        )
        self.root = destination.Root.get("/StructTreeRoot")
        if self.root is not None:
            self._check(self.root)
            children = _items(self.root.get("/K"))
        else:
            self.root = destination.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            children = []
        self.document = destination.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Document, P=self.root, K=Array())
        )
        self.root.K = Array([self.document])
        self._adopt(children, destination.Root.get("/Lang"))
        parents = _tree_entries(self.root.get("/ParentTree"), "/Nums")
        ids = _tree_entries(self.root.get("/IDTree"), "/Names")
        self.parents = dict((int(k), v) for k, v in parents)
        self.ids = dict((bytes(k), v) for k, v in ids)
        if len(self.parents) != len(parents) or len(self.ids) != len(ids):
            raise ValueError("Duplicate PDF structure index key")
        self.has_structure = bool(children)

    @staticmethod
    def _check(root):
        if "/Namespaces" in root or any("/NS" in e for e in _elements(root)):
            raise ValueError(
                "Concatenating PDF 2.0 namespace-based tags is not supported"
            )

    def _adopt(self, children, language):
        for child in children:
            if not isinstance(child, Dictionary) or "/S" not in child:
                raise ValueError("Malformed top-level PDF structure element")
            child.P = self.document
            # Each source document is a part of the new document.
            if child.S == Name.Document:
                child.S = Name.Part
            if "/Lang" not in child:
                # Empty explicitly means unknown; do not inherit another source's language.
                child.Lang = str(language) if language is not None else ""
            self.document.K.append(child)

    def append(self, source):
        self.sequence += 1
        self.user_properties = self.user_properties or bool(
            source.Root.get("/MarkInfo", {}).get("/UserProperties", False)
        )
        self.pdf_version = max(
            self.pdf_version,
            source.pdf_version,
            key=lambda v: tuple(map(int, v.split("."))),
        )
        self.suspects = self.suspects or bool(
            source.Root.get("/MarkInfo", {}).get("/Suspects", False)
        )
        source_root = source.Root.get("/StructTreeRoot")
        self.complete = self.complete and bool(
            source_root and source.Root.get("/MarkInfo", {}).get("/Marked", False)
        )
        if source_root is not None:
            self._check(source_root)
            parents = _tree_entries(source_root.get("/ParentTree"), "/Nums")
            keys = [int(k) for k, _ in parents]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate PDF ParentTree key")
            # Indices on pages, annotations and Form XObjects all share this space.
            next_key = max(self.parents, default=-1) + 1
            remap = {old: next_key + i for i, old in enumerate(sorted(keys))}
            for obj in source.objects:
                if not isinstance(obj, (Dictionary, Stream)):
                    continue
                for key in ("/StructParents", "/StructParent"):
                    if key in obj:
                        old = int(obj[key])
                        if old not in remap:
                            raise ValueError(
                                "PDF structure index is missing from ParentTree"
                            )
                        obj[key] = remap[old]
            source_root.ParentTree = Dictionary(
                Nums=Array(
                    [
                        item
                        for old, value in parents
                        for item in (remap[int(old)], value)
                    ]
                )
            )
            self._rename_maps(source_root)
        # Copy pages before their structure: qpdf then maps /Pg, /Obj and /Stm
        # references to the actual copied pages, annotations and streams.
        start = len(self.pdf.pages)
        self.pdf.pages.extend(source.pages)
        if source_root is not None:
            copied = self.pdf.copy_foreign(
                source.make_indirect(source_root)
                if not source_root.is_indirect
                else source_root
            )
            actual_pages = {page.objgen for page in self.pdf.pages[start:]}
            for element in _elements(copied):
                for node in [element] + _items(element.get("/K")):
                    if (
                        isinstance(node, Dictionary)
                        and "/Pg" in node
                        and node.Pg.objgen not in actual_pages
                    ):
                        raise ValueError(
                            "PDF structure points outside the appended pages"
                        )
            self._adopt(_items(copied.get("/K")), source.Root.get("/Lang"))
            self.parents.update(
                (int(k), v)
                for k, v in _tree_entries(copied.get("/ParentTree"), "/Nums")
            )
            self.ids.update(
                (bytes(k), v) for k, v in _tree_entries(copied.get("/IDTree"), "/Names")
            )
            for key in ("/RoleMap", "/ClassMap"):
                if key in copied:
                    if key not in self.root:
                        self.root[key] = Dictionary()
                    for name, value in copied[key].items():
                        self.root[key][name] = value
            self.has_structure = True

    def _rename_maps(self, root):
        """Namespace imported custom roles, classes and IDs (including /C arrays)."""
        maps = {}
        for key in ("/RoleMap", "/ClassMap"):
            existing = self.root.get(key, {})
            rename = {}
            for name in root.get(key, {}):
                candidate = f"/DA{self.sequence}_{name[1:]}"
                while candidate in existing or candidate in rename.values():
                    candidate = "/DA_" + candidate[1:]
                rename[name] = candidate
            maps[key] = rename
        for e in _elements(root):
            if str(e.get("/S")) in maps["/RoleMap"]:
                e.S = Name(maps["/RoleMap"][str(e.S)])
            if "/C" in e:
                values = _items(e.C)
                renamed = [
                    (
                        Name(maps["/ClassMap"][str(v)])
                        if str(v) in maps["/ClassMap"]
                        else v
                    )
                    for v in values
                ]
                e.C = Array(renamed) if isinstance(e.C, Array) else renamed[0]
        for key, rename in maps.items():
            if key in root:
                replacement = Dictionary()
                for name, value in root[key].items():
                    if key == "/RoleMap" and str(value) in rename:
                        value = Name(rename[str(value)])
                    replacement[rename[name]] = value
                root[key] = replacement
        ids = _tree_entries(root.get("/IDTree"), "/Names")
        id_map = {}
        if len({bytes(k) for k, _ in ids}) != len(ids):
            raise ValueError("Duplicate PDF structure ID")
        for old, _ in ids:
            candidate = f"DA{self.sequence}_".encode() + bytes(old)
            while candidate in self.ids or candidate in id_map.values():
                candidate = b"DA_" + candidate
            id_map[bytes(old)] = candidate
        for e in _elements(root):
            if "/ID" in e:
                if bytes(e.ID) not in id_map:
                    raise ValueError("PDF structure element ID is missing from IDTree")
                e.ID = String(id_map[bytes(e.ID)])

        # Table headers refer to IDs by string, including attributes in ClassMap.
        def rename_headers(value):
            if isinstance(value, Array):
                for child in value:
                    rename_headers(child)
            elif isinstance(value, Dictionary):
                if "/Headers" in value:
                    value.Headers = Array(
                        [String(id_map.get(bytes(v), bytes(v))) for v in value.Headers]
                    )

        for e in _elements(root):
            if "/A" in e:
                rename_headers(e.A)
        for _, value in root.get("/ClassMap", {}).items():
            rename_headers(value)
        if ids:
            root.IDTree = Dictionary(
                Names=Array(
                    [
                        item
                        for old, value in sorted(
                            ids, key=lambda kv: id_map[bytes(kv[0])]
                        )
                        for item in (String(id_map[bytes(old)]), value)
                    ]
                )
            )

    def finish(self):
        if self.has_structure:
            self.pdf.Root.StructTreeRoot = self.root
            self.root.ParentTree = Dictionary(
                Nums=Array(
                    [
                        item
                        for key, value in sorted(self.parents.items())
                        for item in (key, value)
                    ]
                )
            )
            self.root.ParentTreeNextKey = max(self.parents, default=-1) + 1
            if self.ids:
                self.root.IDTree = Dictionary(
                    Names=Array(
                        [
                            item
                            for key, value in sorted(self.ids.items())
                            for item in (String(key), value)
                        ]
                    )
                )
            if "/MarkInfo" not in self.pdf.Root:
                self.pdf.Root.MarkInfo = Dictionary()
            self.pdf.Root.MarkInfo.Marked = bool(self.complete)
            if self.user_properties:
                self.pdf.Root.MarkInfo.UserProperties = True
            if not self.complete or self.suspects:
                self.pdf.Root.MarkInfo.Suspects = True
        ensure_pdf_metadata(self.pdf, merged=True)
