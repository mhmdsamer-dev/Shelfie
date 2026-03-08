"""
scanner.py — File scanning, metadata extraction, cover generation, and Watchdog sync.
"""

import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from sqlmodel import Session, select
from database import engine, Book, get_or_create_tag, get_book_by_path

logger = logging.getLogger("scanner")

COVERS_DIR = Path("static/covers")
COVERS_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = {".pdf", ".epub"}


# ── Utility ──────────────────────────────────────────────────────────────────

def _cover_filename(file_path: str) -> str:
    h = hashlib.md5(file_path.encode()).hexdigest()[:12]
    return f"{h}.jpg"


# ── PDF ──────────────────────────────────────────────────────────────────────

def extract_pdf_metadata(file_path: str) -> Tuple[str, Optional[int], Optional[str]]:
    """Returns (title, total_pages, cover_path). Never raises — returns fallbacks."""
    title = Path(file_path).stem
    total_pages = None
    cover_path  = None

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        total_pages = doc.page_count

        # Title from metadata or filename
        meta = doc.metadata or {}
        if meta.get("title", "").strip():
            title = meta["title"].strip()

        # Cover: render first page as JPEG
        try:
            page  = doc[0]
            mat   = fitz.Matrix(1.5, 1.5)   # 1.5× zoom
            pix   = page.get_pixmap(matrix=mat, alpha=False)
            fname = _cover_filename(file_path)
            out   = COVERS_DIR / fname
            pix.save(str(out), "JPEG")
            cover_path = f"static/covers/{fname}"
        except Exception as e:
            logger.warning("Cover generation failed for %s: %s", file_path, e)

        doc.close()

    except Exception as e:
        logger.warning("PDF metadata extraction failed for %s: %s", file_path, e)

    return title, total_pages, cover_path


# ── EPUB ─────────────────────────────────────────────────────────────────────

def extract_epub_metadata(file_path: str) -> Tuple[str, Optional[int], Optional[str]]:
    """Returns (title, None, cover_path). EPUBs don't have 'pages'."""
    title       = Path(file_path).stem
    cover_path  = None

    try:
        import ebooklib
        from ebooklib import epub
        from PIL import Image
        import io

        book = epub.read_epub(file_path, options={"ignore_ncx": True})

        # Title
        titles = book.get_metadata("DC", "title")
        if titles:
            title = titles[0][0].strip() or title

        # Cover image
        cover_item = None
        # Method 1: look for cover id
        try:
            cover_item = book.get_item_with_id("cover")
        except Exception:
            pass
        # Method 2: look for cover in manifest
        if cover_item is None:
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    name = (item.get_name() or "").lower()
                    if "cover" in name:
                        cover_item = item
                        break
        # Method 3: first image
        if cover_item is None:
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    cover_item = item
                    break

        if cover_item is not None:
            try:
                img_data = cover_item.get_content()
                img      = Image.open(io.BytesIO(img_data)).convert("RGB")
                img.thumbnail((400, 600))
                fname = _cover_filename(file_path)
                out   = COVERS_DIR / fname
                img.save(str(out), "JPEG")
                cover_path = f"static/covers/{fname}"
            except Exception as e:
                logger.warning("EPUB cover save failed for %s: %s", file_path, e)

    except Exception as e:
        logger.warning("EPUB metadata extraction failed for %s: %s", file_path, e)

    return title, None, cover_path


# ── Core scan helpers ────────────────────────────────────────────────────────

def add_book_to_db(session: Session, file_path: str) -> Optional[Book]:
    """Add a book if not already tracked. Returns the Book or None."""
    if get_book_by_path(session, file_path):
        return None   # already tracked

    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None

    if ext == ".pdf":
        title, total_pages, cover_path = extract_pdf_metadata(file_path)
        file_type = "pdf"
    else:
        title, total_pages, cover_path = extract_epub_metadata(file_path)
        file_type = "epub"

    book = Book(
        file_path   = file_path,
        title       = title,
        file_type   = file_type,
        cover_path  = cover_path,
        total_pages = total_pages,
    )
    session.add(book)
    session.commit()
    session.refresh(book)
    logger.info("Added: %s", title)
    return book


def remove_book_from_db(session: Session, file_path: str):
    """Remove a book (and its cover) from the database."""
    book = get_book_by_path(session, file_path)
    if book:
        if book.cover_path:
            try:
                Path(book.cover_path).unlink(missing_ok=True)
            except Exception:
                pass
        session.delete(book)
        session.commit()
        logger.info("Removed: %s", file_path)


def scan_library(library_path: str):
    """Full scan: add new files, remove missing ones."""
    folder = Path(library_path)
    if not folder.exists():
        logger.error("Library folder does not exist: %s", library_path)
        return

    with Session(engine) as session:
        # Add new files
        for root, _, files in os.walk(folder):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    full_path = str(Path(root) / fname)
                    add_book_to_db(session, full_path)

        # Remove books whose files are gone
        from database import get_all_books
        for book in get_all_books(session):
            if not Path(book.file_path).exists():
                remove_book_from_db(session, book.file_path)

    logger.info("Scan complete for: %s", library_path)


# ── Watchdog ─────────────────────────────────────────────────────────────────

def start_watchdog(library_path: str):
    """Start a background Watchdog observer to sync the folder in real-time."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileDeletedEvent, FileMovedEvent

    class LibraryHandler(FileSystemEventHandler):
        def _is_supported(self, path: str) -> bool:
            return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

        def on_created(self, event):
            if not event.is_directory and self._is_supported(event.src_path):
                with Session(engine) as s:
                    add_book_to_db(s, event.src_path)

        def on_deleted(self, event):
            if not event.is_directory and self._is_supported(event.src_path):
                with Session(engine) as s:
                    remove_book_from_db(s, event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                with Session(engine) as s:
                    if self._is_supported(event.src_path):
                        remove_book_from_db(s, event.src_path)
                    if self._is_supported(event.dest_path):
                        add_book_to_db(s, event.dest_path)

    observer = Observer()
    observer.schedule(LibraryHandler(), path=library_path, recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("Watchdog started on: %s", library_path)
    return observer
