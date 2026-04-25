"""
test_scanner.py — Unit tests for shelfie/scanner.py.

File-system mocking
-------------------
* tmp_path (pytest built-in) creates real temporary directories.
* Dummy .pdf and .epub files are written as empty/minimal byte content so the
  OS 'sees' them, but PyMuPDF / EbookLib are never invoked:
  extract_pdf_metadata and extract_epub_metadata are monkeypatched to return
  deterministic tuples.

DB isolation
------------
Every test uses the 'test_engine' / 'db_session' fixtures from conftest.py,
which provide a fresh in-memory SQLite database.
"""

from pathlib import Path

import pytest
from sqlmodel import Session

from shelfie.scanner import (
    SUPPORTED_EXTENSIONS,
    _cover_filename,
    add_book_to_db,
    remove_book_from_db,
    scan_library,
)
from shelfie.database import Book, get_book_by_path


# ── Constants ─────────────────────────────────────────────────────────────────

class TestSupportedExtensions:
    def test_pdf_is_supported(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS

    def test_epub_is_supported(self):
        assert ".epub" in SUPPORTED_EXTENSIONS

    def test_txt_is_not_supported(self):
        assert ".txt" not in SUPPORTED_EXTENSIONS

    def test_mobi_is_not_supported(self):
        assert ".mobi" not in SUPPORTED_EXTENSIONS


# ── _cover_filename ───────────────────────────────────────────────────────────

class TestCoverFilename:
    def test_is_deterministic(self):
        assert _cover_filename("/a/b/c.pdf") == _cover_filename("/a/b/c.pdf")

    def test_different_paths_produce_different_names(self):
        assert _cover_filename("/a/one.pdf") != _cover_filename("/a/two.pdf")

    def test_has_jpg_extension(self):
        assert _cover_filename("/any/path.pdf").endswith(".jpg")

    def test_length_is_fixed(self):
        fname = _cover_filename("/some/path.pdf")
        assert len(fname) == len("aabbccddeeff.jpg")


# ── add_book_to_db ─────────────────────────────────────────────────────────────

class TestAddBookToDb:
    def test_adds_pdf_and_returns_book(self, db_session, monkeypatch, tmp_path):
        pdf = tmp_path / "novel.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: ("My Novel", 42, None),
        )

        book = add_book_to_db(db_session, str(pdf))

        assert book is not None
        assert book.title == "My Novel"
        assert book.file_type == "pdf"
        assert book.total_pages == 42
        assert book.id is not None

    def test_adds_epub_and_returns_book(self, db_session, monkeypatch, tmp_path):
        epub = tmp_path / "story.epub"
        epub.write_bytes(b"PK fake epub content")

        monkeypatch.setattr(
            "shelfie.scanner.extract_epub_metadata",
            lambda path: ("Great Story", None, None),
        )

        book = add_book_to_db(db_session, str(epub))

        assert book is not None
        assert book.title == "Great Story"
        assert book.file_type == "epub"
        assert book.total_pages is None

    def test_duplicate_path_returns_none(self, db_session, monkeypatch, tmp_path):
        pdf = tmp_path / "dup.pdf"
        pdf.write_bytes(b"%PDF fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: ("Dup Book", 10, None),
        )

        first = add_book_to_db(db_session, str(pdf))
        second = add_book_to_db(db_session, str(pdf))

        assert first is not None
        assert second is None

    def test_unsupported_extension_returns_none(self, db_session, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")

        result = add_book_to_db(db_session, str(txt))

        assert result is None

    def test_corrupt_pdf_falls_back_to_stem_title(self, db_session, tmp_path):
        """
        extract_pdf_metadata never raises; on failure it returns stem as title.
        Passing a truly corrupt file exercises the real fallback path without
        needing to mock anything.
        """
        corrupt = tmp_path / "broken.pdf"
        corrupt.write_bytes(b"NOT A REAL PDF CONTENT AT ALL")

        book = add_book_to_db(db_session, str(corrupt))

        assert book is not None
        assert book.title == "broken"     # stem fallback
        assert book.file_type == "pdf"


# ── remove_book_from_db ───────────────────────────────────────────────────────

class TestRemoveBookFromDb:
    def test_removes_existing_book(self, db_session, tmp_path):
        pdf = tmp_path / "remove_me.pdf"
        pdf.write_bytes(b"%PDF fake")

        book = Book(
            file_path=str(pdf.resolve()),
            title="To Remove",
            file_type="pdf",
        )
        db_session.add(book)
        db_session.commit()

        remove_book_from_db(db_session, str(pdf))

        assert get_book_by_path(db_session, str(pdf.resolve())) is None

    def test_remove_nonexistent_path_is_noop(self, db_session, tmp_path):
        remove_book_from_db(db_session, str(tmp_path / "ghost.pdf"))


# ── scan_library ──────────────────────────────────────────────────────────────

class TestScanLibrary:
    """
    scan_library uses Session(engine) internally, so the test_engine fixture
    must be present to patch the module-level engine before the call.
    """

    def test_empty_folder_adds_nothing(self, test_engine, tmp_path):
        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 0

    def test_nonexistent_folder_is_noop(self, test_engine, tmp_path):
        scan_library(str(tmp_path / "does_not_exist"))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 0

    def test_discovers_pdf_files(self, test_engine, monkeypatch, tmp_path):
        (tmp_path / "book1.pdf").write_bytes(b"%PDF fake")
        (tmp_path / "book2.pdf").write_bytes(b"%PDF fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: (Path(path).stem, 10, None),
        )

        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 2

    def test_discovers_epub_files(self, test_engine, monkeypatch, tmp_path):
        (tmp_path / "a.epub").write_bytes(b"PK fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_epub_metadata",
            lambda path: (Path(path).stem, None, None),
        )

        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 1

    def test_ignores_unsupported_files(self, test_engine, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "image.png").write_bytes(b"\x89PNG fake")
        (tmp_path / "notes.docx").write_bytes(b"PK fake")

        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 0

    def test_discovers_files_in_subdirectories(self, test_engine, monkeypatch, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.pdf").write_bytes(b"%PDF fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: (Path(path).stem, 5, None),
        )

        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 1

    def test_prunes_books_whose_files_are_deleted(self, test_engine, monkeypatch, tmp_path):
        pdf = tmp_path / "gone.pdf"
        pdf.write_bytes(b"%PDF fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: (Path(path).stem, 1, None),
        )

        scan_library(str(tmp_path))

        # Delete the file and rescan — the DB entry should be pruned
        pdf.unlink()
        scan_library(str(tmp_path))

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 0

    def test_does_not_duplicate_existing_entries(self, test_engine, monkeypatch, tmp_path):
        (tmp_path / "once.pdf").write_bytes(b"%PDF fake")

        monkeypatch.setattr(
            "shelfie.scanner.extract_pdf_metadata",
            lambda path: (Path(path).stem, 1, None),
        )

        scan_library(str(tmp_path))
        scan_library(str(tmp_path))  # second scan must not duplicate

        with Session(test_engine) as s:
            books = s.exec(__import__("sqlmodel").select(Book)).all()
        assert len(books) == 1
