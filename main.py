"""
main.py — FastAPI application for Local Library Manager. v3.
New: custom cover upload, page-aware file open, auto-date logic, log note editing.
"""

import os
import sys
import io
import logging
import subprocess
import threading
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select
from PIL import Image

import database as db
from database import (
    Book, Tag, BookTagLink, ProgressLog, BookQuote,
    create_db_and_tables, get_session,
    get_or_create_tag, get_all_books, get_book_by_path, get_stats,
    get_progress_logs, add_progress_log, get_quotes,
    engine,
)
from scanner import scan_library, start_watchdog

# ── Config ─────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("main")

DEFAULT_LIBRARY_PATH = os.environ.get("LIBRARY_PATH", str(Path.home() / "Books"))
CONFIG_FILE   = Path("library_config.txt")
COVERS_DIR    = Path("static/covers")
MAX_IMG_BYTES = 10 * 1024 * 1024
ALLOWED_IMG   = {"image/jpeg", "image/png", "image/webp"}

def load_library_path() -> str:
    return CONFIG_FILE.read_text().strip() if CONFIG_FILE.exists() else DEFAULT_LIBRARY_PATH

def save_library_path(path: str):
    CONFIG_FILE.write_text(path.strip())

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Local Library Manager", version="3.0.0")

COVERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_watchdog_observer = None


@app.on_event("startup")
def on_startup():
    global _watchdog_observer
    create_db_and_tables()
    lib = load_library_path()
    if Path(lib).exists():
        threading.Thread(target=scan_library, args=(lib,), daemon=True).start()
        _watchdog_observer = start_watchdog(lib)
    else:
        logger.warning("Library folder not found: %s", lib)


# ── Schemas ────────────────────────────────────────────────────────────────────

class BookUpdate(BaseModel):
    current_page: Optional[int]       = None
    category:     Optional[str]       = None
    notes:        Optional[str]       = None
    is_read:      Optional[bool]      = None
    tags:         Optional[List[str]] = None

class BookDetailUpdate(BaseModel):
    custom_title:  Optional[str]       = None
    admin_notes:   Optional[str]       = None
    date_started:  Optional[str]       = None   # ISO or "" to clear
    date_finished: Optional[str]       = None
    current_page:  Optional[int]       = None
    category:      Optional[str]       = None
    notes:         Optional[str]       = None
    is_read:       Optional[bool]      = None
    tags:          Optional[List[str]] = None
    log_progress:  bool                = False   # force a ProgressLog entry

class ProgressLogIn(BaseModel):
    page: int
    note: Optional[str] = None

class ProgressLogNoteUpdate(BaseModel):
    note: str

class QuoteIn(BaseModel):
    quote_text:  str
    page_number: Optional[int] = None

class LibraryPathIn(BaseModel):
    path: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val or not val.strip():
        return None
    try:
        return datetime.fromisoformat(val.strip())
    except ValueError:
        return None

def _apply_auto_dates(book: Book, new_page: int):
    """Set date_started / date_finished automatically based on page changes."""
    now = datetime.utcnow()
    if new_page > 0 and book.current_page == 0 and not book.date_started:
        book.date_started = now
    if book.total_pages and new_page >= book.total_pages and not book.date_finished:
        book.date_finished = now
        book.is_read = True

def book_to_out(book: Book) -> dict:
    return {
        "id":             book.id,
        "file_path":      book.file_path,
        "title":          book.display_title,
        "original_title": book.title,
        "custom_title":   book.custom_title,
        "file_type":      book.file_type,
        "cover_path":     book.cover_path,
        "total_pages":    book.total_pages,
        "current_page":   book.current_page,
        "progress":       book.progress_percent,
        "category":       book.category,
        "notes":          book.notes,
        "admin_notes":    book.admin_notes,
        "is_read":        book.is_read,
        "date_added":     book.date_added.isoformat(),
        "last_opened":    book.last_opened.isoformat() if book.last_opened else None,
        "date_started":   book.date_started.isoformat() if book.date_started else None,
        "date_finished":  book.date_finished.isoformat() if book.date_finished else None,
        "tags":           [t.name for t in book.tags],
    }

def log_to_out(log: ProgressLog) -> dict:
    return {"id": log.id, "page": log.page, "timestamp": log.timestamp.isoformat(), "note": log.note}


# ── Core routes ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "library_path": load_library_path()})


@app.get("/api/books")
def list_books(category: Optional[str]=None, tag: Optional[str]=None, q: Optional[str]=None, session: Session=Depends(get_session)):
    books = session.exec(select(Book)).all()
    if category: books = [b for b in books if (b.category or "").lower() == category.lower()]
    if tag:      books = [b for b in books if any(t.name == tag.lower() for t in b.tags)]
    if q:
        ql = q.lower()
        books = [b for b in books if ql in b.display_title.lower() or ql in (b.notes or "").lower()]
    return [book_to_out(b) for b in books]


@app.get("/api/books/{book_id}")
def get_book(book_id: int, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    return book_to_out(book)


@app.patch("/api/books/{book_id}")
def patch_book(book_id: int, data: BookUpdate, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    if data.current_page is not None:
        _apply_auto_dates(book, data.current_page)
        book.current_page = data.current_page
    if data.category is not None: book.category = data.category
    if data.notes    is not None: book.notes    = data.notes
    if data.is_read  is not None: book.is_read  = data.is_read
    if data.tags     is not None:
        book.tags = [get_or_create_tag(session, t) for t in data.tags if t.strip()]
    session.add(book); session.commit(); session.refresh(book)
    return book_to_out(book)


@app.put("/api/books/{book_id}")
def put_book(book_id: int, data: BookDetailUpdate, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")

    if data.current_page is not None:
        old_page = book.current_page
        _apply_auto_dates(book, data.current_page)
        book.current_page = data.current_page
        # Auto-log when page changes or caller requests it
        if data.log_progress or data.current_page != old_page:
            pct  = round((data.current_page / book.total_pages) * 100) if book.total_pages else 0
            note = f"Reached page {data.current_page}" + (f" ({pct}%)" if book.total_pages else "")
            add_progress_log(session, book_id, data.current_page, note)

    if data.custom_title  is not None: book.custom_title = data.custom_title or None
    if data.admin_notes   is not None: book.admin_notes  = data.admin_notes
    if data.category      is not None: book.category     = data.category
    if data.notes         is not None: book.notes        = data.notes
    if data.is_read       is not None: book.is_read      = data.is_read
    if data.date_started  is not None: book.date_started  = _parse_dt(data.date_started)
    if data.date_finished is not None: book.date_finished = _parse_dt(data.date_finished)
    if data.tags          is not None:
        book.tags = [get_or_create_tag(session, t) for t in data.tags if t.strip()]

    session.add(book); session.commit(); session.refresh(book)
    return book_to_out(book)


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    if book.cover_path:
        try: Path(book.cover_path).unlink(missing_ok=True)
        except: pass
    for log in get_progress_logs(session, book_id):
        session.delete(log)
    session.delete(book); session.commit()
    return {"ok": True}


# ── Cover upload ───────────────────────────────────────────────────────────────

@app.post("/api/books/{book_id}/cover")
async def upload_cover(book_id: int, file: UploadFile=File(...), session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    if file.content_type not in ALLOWED_IMG:
        raise HTTPException(400, f"Unsupported type: {file.content_type}. Use JPEG, PNG or WebP.")

    raw = await file.read()
    if len(raw) > MAX_IMG_BYTES:
        raise HTTPException(413, "Image too large (max 10 MB)")

    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((600, 900), Image.LANCZOS)
        fname    = f"custom_{book_id}.jpg"
        out_path = COVERS_DIR / fname
        img.save(str(out_path), "JPEG", quality=88)
    except Exception as e:
        logger.warning("Cover save failed for book %d: %s", book_id, e)
        raise HTTPException(500, "Could not process image")

    # Remove old auto-generated cover if it's different
    if book.cover_path and book.cover_path != f"static/covers/{fname}":
        try:
            old = Path(book.cover_path)
            if old.exists() and not old.name.startswith("custom_"):
                old.unlink(missing_ok=True)
        except: pass

    book.cover_path = f"static/covers/{fname}"
    session.add(book); session.commit(); session.refresh(book)
    return book_to_out(book)


@app.delete("/api/books/{book_id}/cover")
def delete_cover(book_id: int, session: Session=Depends(get_session)):
    """Revert to auto-generated cover (or placeholder if none exists)."""
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")

    custom = COVERS_DIR / f"custom_{book_id}.jpg"
    if custom.exists():
        custom.unlink(missing_ok=True)

    # Try to regenerate from the original file
    from scanner import extract_pdf_metadata, extract_epub_metadata
    new_cover = None
    try:
        if book.file_type == "pdf":
            _, _, new_cover = extract_pdf_metadata(book.file_path)
        else:
            _, _, new_cover = extract_epub_metadata(book.file_path)
    except: pass

    book.cover_path = new_cover
    session.add(book); session.commit(); session.refresh(book)
    return book_to_out(book)


# ── File open (page-aware) ─────────────────────────────────────────────────────

@app.post("/api/books/{book_id}/open")
def open_book(book_id: int, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    fp = Path(book.file_path)
    if not fp.exists(): raise HTTPException(404, "File not found on disk")

    book.last_opened = datetime.utcnow()
    session.add(book); session.commit()

    page = max(book.current_page or 1, 1)

    try:
        if book.file_type == "pdf" and page > 1:
            # file:// URL with #page= fragment — supported by Chrome, Firefox, Edge
            file_url = fp.as_uri() + f"#page={page}"
            if sys.platform == "win32":
                import webbrowser; webbrowser.open(file_url)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", file_url])
            else:
                subprocess.Popen(["xdg-open", file_url])
        else:
            if sys.platform == "win32":   os.startfile(str(fp))
            elif sys.platform == "darwin": subprocess.Popen(["open", str(fp)])
            else:                          subprocess.Popen(["xdg-open", str(fp)])
    except Exception as e:
        raise HTTPException(500, f"Could not open file: {e}")

    return {"ok": True, "page": page}


# ── Progress log endpoints ─────────────────────────────────────────────────────

@app.get("/api/books/{book_id}/progress")
def get_book_progress(book_id: int, session: Session=Depends(get_session)):
    if not session.get(Book, book_id): raise HTTPException(404, "Book not found")
    return [log_to_out(l) for l in get_progress_logs(session, book_id)]


@app.post("/api/books/{book_id}/progress")
def log_progress(book_id: int, data: ProgressLogIn, session: Session=Depends(get_session)):
    book = session.get(Book, book_id)
    if not book: raise HTTPException(404, "Book not found")
    if data.page < 0: raise HTTPException(400, "Page cannot be negative")

    note = data.note
    if not note:
        pct  = round((data.page / book.total_pages) * 100) if book.total_pages else 0
        note = f"Reached page {data.page}" + (f" ({pct}%)" if book.total_pages else "")

    _apply_auto_dates(book, data.page)
    book.current_page = data.page
    session.add(book)
    return log_to_out(add_progress_log(session, book_id, data.page, note))


@app.patch("/api/books/{book_id}/progress/{log_id}")
def update_log_note(book_id: int, log_id: int, data: ProgressLogNoteUpdate, session: Session=Depends(get_session)):
    log = session.get(ProgressLog, log_id)
    if not log or log.book_id != book_id: raise HTTPException(404, "Log entry not found")
    log.note = data.note
    session.add(log); session.commit(); session.refresh(log)
    return log_to_out(log)


@app.delete("/api/books/{book_id}/progress/{log_id}")
def delete_progress_log(book_id: int, log_id: int, session: Session=Depends(get_session)):
    log = session.get(ProgressLog, log_id)
    if not log or log.book_id != book_id: raise HTTPException(404, "Log entry not found")
    session.delete(log); session.commit()
    return {"ok": True}


# ── Misc endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats(session: Session=Depends(get_session)): return get_stats(session)

@app.get("/api/tags")
def list_tags(session: Session=Depends(get_session)):
    return [{"id": t.id, "name": t.name} for t in session.exec(select(Tag)).all()]

@app.get("/api/categories")
def list_categories(session: Session=Depends(get_session)):
    return sorted({b.category for b in get_all_books(session) if b.category})

@app.get("/api/library-path")
def get_library_path(): return {"path": load_library_path()}

@app.post("/api/library-path")
def set_library_path(data: LibraryPathIn):
    global _watchdog_observer
    p = Path(data.path.strip())
    if not p.exists() or not p.is_dir(): raise HTTPException(400, "Invalid path")
    save_library_path(str(p))
    if _watchdog_observer:
        try: _watchdog_observer.stop(); _watchdog_observer.join(timeout=2)
        except: pass
    threading.Thread(target=scan_library, args=(str(p),), daemon=True).start()
    _watchdog_observer = start_watchdog(str(p))
    return {"ok": True, "path": str(p)}

@app.post("/api/rescan")
def rescan():
    lib = load_library_path()
    threading.Thread(target=scan_library, args=(lib,), daemon=True).start()
    return {"ok": True}


# ── Quote endpoints ────────────────────────────────────────────────────────────

def quote_to_out(q: BookQuote) -> dict:
    return {
        "id":          q.id,
        "book_id":     q.book_id,
        "quote_text":  q.quote_text,
        "page_number": q.page_number,
        "date_added":  q.date_added.isoformat(),
    }


@app.get("/api/books/{book_id}/quotes")
def list_quotes(book_id: int, session: Session = Depends(get_session)):
    if not session.get(Book, book_id):
        raise HTTPException(404, "Book not found")
    return [quote_to_out(q) for q in get_quotes(session, book_id)]


@app.post("/api/books/{book_id}/quotes")
def add_quote(book_id: int, data: QuoteIn, session: Session = Depends(get_session)):
    if not session.get(Book, book_id):
        raise HTTPException(404, "Book not found")
    if not data.quote_text.strip():
        raise HTTPException(400, "Quote text cannot be empty")

    quote = BookQuote(
        book_id     = book_id,
        quote_text  = data.quote_text.strip(),
        page_number = data.page_number,
    )
    session.add(quote)
    session.commit()
    session.refresh(quote)
    return quote_to_out(quote)


@app.delete("/api/books/{book_id}/quotes/{quote_id}")
def delete_quote(book_id: int, quote_id: int, session: Session = Depends(get_session)):
    quote = session.get(BookQuote, quote_id)
    if not quote or quote.book_id != book_id:
        raise HTTPException(404, "Quote not found")
    session.delete(quote)
    session.commit()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
