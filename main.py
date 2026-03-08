"""
main.py — FastAPI application for Local Library Manager.
"""

import os
import sys
import logging
import subprocess
import threading
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, select

import database as db
from database import (
    Book, Tag, BookTagLink,
    create_db_and_tables, get_session,
    get_or_create_tag, get_all_books, get_book_by_path, get_stats,
    engine,
)
from scanner import scan_library, start_watchdog

# ── Config ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("main")

DEFAULT_LIBRARY_PATH = os.environ.get("LIBRARY_PATH", str(Path.home() / "Books"))

CONFIG_FILE = Path("library_config.txt")

def load_library_path() -> str:
    if CONFIG_FILE.exists():
        return CONFIG_FILE.read_text().strip()
    return DEFAULT_LIBRARY_PATH

def save_library_path(path: str):
    CONFIG_FILE.write_text(path.strip())

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Local Library Manager", version="1.0.0")

Path("static/covers").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

_watchdog_observer = None


@app.on_event("startup")
def on_startup():
    global _watchdog_observer
    create_db_and_tables()
    lib = load_library_path()
    if Path(lib).exists():
        logger.info("Scanning library: %s", lib)
        threading.Thread(target=scan_library, args=(lib,), daemon=True).start()
        _watchdog_observer = start_watchdog(lib)
    else:
        logger.warning("Library folder not found: %s — set it via the UI.", lib)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class BookUpdate(BaseModel):
    title:        Optional[str]  = None
    current_page: Optional[int]  = None
    category:     Optional[str]  = None
    notes:        Optional[str]  = None
    is_read:      Optional[bool] = None
    tags:         Optional[List[str]] = None

class LibraryPathIn(BaseModel):
    path: str

class BookOut(BaseModel):
    id:              int
    file_path:       str
    title:           str
    file_type:       str
    cover_path:      Optional[str]
    total_pages:     Optional[int]
    current_page:    int
    progress:        float
    category:        Optional[str]
    notes:           Optional[str]
    is_read:         bool
    date_added:      str
    last_opened:     Optional[str]
    tags:            List[str]

    class Config:
        from_attributes = True


def book_to_out(book: Book) -> dict:
    return {
        "id":           book.id,
        "file_path":    book.file_path,
        "title":        book.title,
        "file_type":    book.file_type,
        "cover_path":   book.cover_path,
        "total_pages":  book.total_pages,
        "current_page": book.current_page,
        "progress":     book.progress_percent,
        "category":     book.category,
        "notes":        book.notes,
        "is_read":      book.is_read,
        "date_added":   book.date_added.isoformat(),
        "last_opened":  book.last_opened.isoformat() if book.last_opened else None,
        "tags":         [t.name for t in book.tags],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {
        "request":      request,
        "library_path": load_library_path(),
    })


@app.get("/api/books")
def list_books(
    category: Optional[str] = None,
    tag:      Optional[str] = None,
    q:        Optional[str] = None,
    session:  Session       = Depends(get_session),
):
    stmt = select(Book)
    books = session.exec(stmt).all()

    if category:
        books = [b for b in books if (b.category or "").lower() == category.lower()]
    if tag:
        books = [b for b in books if any(t.name == tag.lower() for t in b.tags)]
    if q:
        ql = q.lower()
        books = [b for b in books if ql in b.title.lower() or ql in (b.notes or "").lower()]

    return [book_to_out(b) for b in books]


@app.get("/api/books/{book_id}")
def get_book(book_id: int, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    return book_to_out(book)


@app.patch("/api/books/{book_id}")
def update_book(book_id: int, data: BookUpdate, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")

    if data.title        is not None: book.title        = data.title
    if data.current_page is not None: book.current_page = data.current_page
    if data.category     is not None: book.category     = data.category
    if data.notes        is not None: book.notes        = data.notes
    if data.is_read      is not None: book.is_read      = data.is_read

    if data.tags is not None:
        book.tags = [get_or_create_tag(session, t) for t in data.tags if t.strip()]

    session.add(book)
    session.commit()
    session.refresh(book)
    return book_to_out(book)


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if book.cover_path:
        try:
            Path(book.cover_path).unlink(missing_ok=True)
        except Exception:
            pass
    session.delete(book)
    session.commit()
    return {"ok": True}


@app.post("/api/books/{book_id}/open")
def open_book(book_id: int, session: Session = Depends(get_session)):
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(404, "Book not found")
    if not Path(book.file_path).exists():
        raise HTTPException(404, "File not found on disk")

    book.last_opened = datetime.utcnow()
    session.add(book)
    session.commit()

    try:
        if sys.platform == "win32":
            os.startfile(book.file_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", book.file_path])
        else:
            subprocess.Popen(["xdg-open", book.file_path])
    except Exception as e:
        raise HTTPException(500, f"Could not open file: {e}")

    return {"ok": True}


@app.get("/api/stats")
def stats(session: Session = Depends(get_session)):
    return get_stats(session)


@app.get("/api/tags")
def list_tags(session: Session = Depends(get_session)):
    tags = session.exec(select(Tag)).all()
    return [{"id": t.id, "name": t.name} for t in tags]


@app.get("/api/categories")
def list_categories(session: Session = Depends(get_session)):
    books = get_all_books(session)
    cats  = sorted({b.category for b in books if b.category})
    return cats


@app.get("/api/library-path")
def get_library_path():
    return {"path": load_library_path()}


@app.post("/api/library-path")
def set_library_path(data: LibraryPathIn):
    global _watchdog_observer
    p = Path(data.path.strip())
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, "Path does not exist or is not a directory")

    save_library_path(str(p))

    # Stop old observer
    if _watchdog_observer:
        try:
            _watchdog_observer.stop()
            _watchdog_observer.join(timeout=2)
        except Exception:
            pass

    # Rescan + restart watchdog
    threading.Thread(target=scan_library, args=(str(p),), daemon=True).start()
    _watchdog_observer = start_watchdog(str(p))
    return {"ok": True, "path": str(p)}


@app.post("/api/rescan")
def rescan():
    lib = load_library_path()
    threading.Thread(target=scan_library, args=(lib,), daemon=True).start()
    return {"ok": True, "message": f"Rescan started for: {lib}"}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
