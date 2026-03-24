from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from apps.contract_intelligence.domain.enums import DocumentType
from apps.contract_intelligence.ingestion.document_classifier import classify_document


PLAIN_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".rst",
}

IGNORED_PATH_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "artifacts",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class LoadedDocument:
    document_id: str
    relative_path: str
    document_type: DocumentType
    text: str
    text_available: bool


def _document_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"doc_{digest}"


def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        raw_xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(raw_xml)
    text_parts = [node.text for node in root.iter() if node.text]
    return re.sub(r"\s+", " ", " ".join(text_parts)).strip()


def _load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PLAIN_TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    if suffix == ".docx":
        try:
            return _extract_docx_text(path)
        except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
            return ""
    return ""


def iter_project_documents(project_dir: Path) -> list[LoadedDocument]:
    documents: list[LoadedDocument] = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(project_dir).as_posix()
        if any(part in IGNORED_PATH_PARTS for part in path.relative_to(project_dir).parts):
            continue
        text = _load_text(path)
        documents.append(
            LoadedDocument(
                document_id=_document_id(relative_path),
                relative_path=relative_path,
                document_type=classify_document(path.name),
                text=text,
                text_available=bool(text),
            )
        )
    return documents
