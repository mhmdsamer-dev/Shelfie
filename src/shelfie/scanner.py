"""
scanner.py — File scanning, metadata extraction, cover generation, and Watchdog sync.
"""

import hashlib
import io
import logging
import os
from pathlib import Path

from sqlmodel import Session

from shelfie.config import COVERS_DIR
from shelfie.database import Book, engine, get_book_by_path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset = frozenset({".pdf", ".epub"})


# ── Utility ───────────────────────────────────────────────────────────────────

def _cover_filename(file_path: str) -> str:
    """Stable filename derived from the book's absolute path."""
    h = hashlib.md5(file_path.encode()).hexdigest()[:12]
    return f"{h}.jpg"

def _cover_url(fname: str) -> str:
    """URL path served by the StaticFiles mount (always forward-slashes)."""
    return f"static/covers/{fname}"


# ── PDF ───────────────────────────────────────────────────────────────────────

def extract_pdf_metadata(file_path: str) -> tuple[str, int | None, str | None]:
    """Return (title, total_pages, cover_url).  Never raises — returns fallbacks."""
    title       = Path(file_path).stem
    total_pages = None
    cover_url   = None

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        total_pages = doc.page_count

        meta = doc.metadata or {}
        if meta.get("title", "").strip():
            title = meta["title"].strip()

        try:
            page  = doc[0]
            mat   = fitz.Matrix(1.5, 1.5)
            pix   = page.get_pixmap(matrix=mat, alpha=False)
            fname = _cover_filename(file_path)
            out   = COVERS_DIR / fname
            pix.save(str(out), "JPEG")
            cover_url = _cover_url(fname)
        except Exception as e:
            logger.warning("Cover generation failed for %s: %s", file_path, e)

        doc.close()

    except Exception as e:
        logger.warning("PDF metadata extraction failed for %s: %s", file_path, e)

    return title, total_pages, cover_url


# ── EPUB ──────────────────────────────────────────────────────────────────────

def extract_epub_metadata(file_path: str) -> tuple[str, int | None, str | None]:
    """Return (title, None, cover_url).  EPUBs don't have a fixed page count."""
    title     = Path(file_path).stem
    cover_url = None

    try:
        import ebooklib
        from ebooklib import epub
        from PIL import Image

        book = epub.read_epub(file_path, options={"ignore_ncx": True})

        titles = book.get_metadata("DC", "title")
        if titles:
            title = titles[0][0].strip() or title

        cover_item = None
        try:
            cover_item = book.get_item_with_id("cover")
        except Exception:
            pass

        if cover_item is None:
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    if "cover" in (item.get_name() or "").lower():
                        cover_item = item
                        break

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
                cover_url = _cover_url(fname)
            except Exception as e:
                logger.warning("EPUB cover save failed for %s: %s", file_path, e)

    except Exception as e:
        logger.warning("EPUB metadata extraction failed for %s: %s", file_path, e)

    return title, None, cover_url


# ── Core DB helpers ───────────────────────────────────────────────────────────

def add_book_to_db(session: Session, file_path: str) -> Book | None:
    """Add a book if not already tracked.  Returns the new Book or None."""
    # Normalise to absolute path so Docker volume paths are stable
    file_path = str(Path(file_path).resolve())

    if get_book_by_path(session, file_path):
        return None

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


def remove_book_from_db(session: Session, file_path: str) -> None:
    """Remove a book (and its cover image) from the database."""
    file_path = str(Path(file_path).resolve())
    book = get_book_by_path(session, file_path)
    if book:
        if book.cover_path:
            try:
                Path(book.cover_path).unlink(missing_ok=True)
            except Exception:
                pass
        book.tags = []
        session.flush()
        session.delete(book)
        session.commit()
        logger.info("Removed: %s", file_path)


# ── Full scan ─────────────────────────────────────────────────────────────────

def scan_library(library_path: str) -> None:
    """Walk the library folder, add new files, remove stale DB entries."""
    folder = Path(library_path).resolve()
    if not folder.exists():
        logger.error("Library folder does not exist: %s", folder)
        return

    with Session(engine) as session:
        for root, _, files in os.walk(folder):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    full_path = str(Path(root).resolve() / fname)
                    add_book_to_db(session, full_path)

        # Prune books whose files have been deleted
        from shelfie.database import get_all_books
        for book in get_all_books(session):
            if not Path(book.file_path).exists():
                remove_book_from_db(session, book.file_path)

    logger.info("Scan complete for: %s", folder)


# ── Watchdog ──────────────────────────────────────────────────────────────────

def start_watchdog(library_path: str):
    """
    Start a background observer.

    Uses PollingObserver so events fire correctly on:
      - Docker bind-mounts
      - NFS / SMB / FUSE volumes
      - Any filesystem that doesn't propagate kernel inotify events
    """
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers.polling import PollingObserver

    class LibraryHandler(FileSystemEventHandler):
        @staticmethod
        def _supported(path: str) -> bool:
            return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS

        def on_created(self, event):
            if not event.is_directory and self._supported(event.src_path):
                with Session(engine) as s:
                    add_book_to_db(s, event.src_path)

        def on_deleted(self, event):
            if not event.is_directory and self._supported(event.src_path):
                with Session(engine) as s:
                    remove_book_from_db(s, event.src_path)

        def on_moved(self, event):
            if not event.is_directory:
                with Session(engine) as s:
                    if self._supported(event.src_path):
                        remove_book_from_db(s, event.src_path)
                    if self._supported(event.dest_path):
                        add_book_to_db(s, event.dest_path)

    observer = PollingObserver(timeout=5)   # poll every 5 s
    observer.schedule(LibraryHandler(), path=str(Path(library_path).resolve()), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("PollingObserver started on: %s", library_path)
    return observer
