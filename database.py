"""
database.py — SQLModel models and database initialization for Local Library Manager.
v4: adds BookQuote table with safe migration (zero data loss).
"""

import logging
from datetime import datetime
from typing import Optional, List
from sqlmodel import Field, SQLModel, create_engine, Session, select, Relationship
from sqlalchemy import text

logger = logging.getLogger("database")

DATABASE_URL = "sqlite:///./library.db"
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


# ── Many-to-many: Book ↔ Tag ──────────────────────────────────────────────────

class BookTagLink(SQLModel, table=True):
    book_id: Optional[int] = Field(default=None, foreign_key="book.id", primary_key=True)
    tag_id:  Optional[int] = Field(default=None, foreign_key="tag.id",  primary_key=True)


class Tag(SQLModel, table=True):
    id:    Optional[int] = Field(default=None, primary_key=True)
    name:  str           = Field(index=True, unique=True)
    books: List["Book"]  = Relationship(back_populates="tags", link_model=BookTagLink)


# ── Progress log ──────────────────────────────────────────────────────────────

class ProgressLog(SQLModel, table=True):
    """One row per page-update event for a book."""
    id:        Optional[int] = Field(default=None, primary_key=True)
    book_id:   int           = Field(foreign_key="book.id", index=True)
    page:      int
    timestamp: datetime      = Field(default_factory=datetime.utcnow)
    note:      Optional[str] = None

    book: Optional["Book"] = Relationship(back_populates="progress_logs")


# ── Book Quote ─────────────────────────────────────────────────────────────────

class BookQuote(SQLModel, table=True):
    """A highlighted quote saved from a book."""
    id:          Optional[int] = Field(default=None, primary_key=True)
    book_id:     int           = Field(foreign_key="book.id", index=True)
    quote_text:  str
    page_number: Optional[int] = None   # Optional — EPUBs may not have pages
    date_added:  datetime      = Field(default_factory=datetime.utcnow)

    book: Optional["Book"] = Relationship(back_populates="quotes")


# ── Book ──────────────────────────────────────────────────────────────────────

class Book(SQLModel, table=True):
    id:             Optional[int]      = Field(default=None, primary_key=True)
    file_path:      str                = Field(index=True, unique=True)

    title:          str                          # original extracted title
    custom_title:   Optional[str]      = None    # user-overridden display title

    file_type:      str                          # "pdf" | "epub"
    cover_path:     Optional[str]      = None
    total_pages:    Optional[int]      = None
    current_page:   int                = 0
    category:       Optional[str]      = None

    notes:          Optional[str]      = None    # reader-facing notes
    admin_notes:    Optional[str]      = None    # internal/maintenance notes

    is_read:        bool               = False
    date_added:     datetime           = Field(default_factory=datetime.utcnow)
    last_opened:    Optional[datetime] = None

    date_started:   Optional[datetime] = None
    date_finished:  Optional[datetime] = None

    tags:           List[Tag]          = Relationship(back_populates="books", link_model=BookTagLink)
    progress_logs:  List[ProgressLog]  = Relationship(back_populates="book")
    quotes:         List[BookQuote]    = Relationship(back_populates="book")

    @property
    def display_title(self) -> str:
        return (self.custom_title or self.title or "Untitled").strip()

    @property
    def progress_percent(self) -> float:
        if self.total_pages and self.total_pages > 0:
            return round((self.current_page / self.total_pages) * 100, 1)
        return 0.0


# ── Safe initialisation ────────────────────────────────────────────────────────

def create_db_and_tables():
    """
    Create ALL tables declared above.
    SQLModel.metadata.create_all uses IF NOT EXISTS semantics internally,
    so existing tables (and their data) are never touched.
    """
    SQLModel.metadata.create_all(engine)
    _safe_migrate()


def _safe_migrate():
    """
    Belt-and-suspenders migration: explicitly run IF NOT EXISTS DDL for any
    table that might be missing from an older database file.
    This guarantees zero data loss even when users upgrade from v1/v2/v3.
    """
    ddl_statements = [
        # BookQuote — new in v4
        """
        CREATE TABLE IF NOT EXISTS bookquote (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id     INTEGER NOT NULL REFERENCES book(id),
            quote_text  TEXT    NOT NULL,
            page_number INTEGER,
            date_added  DATETIME NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_bookquote_book_id ON bookquote (book_id)",

        # ProgressLog — added in v2; safe to re-run
        """
        CREATE TABLE IF NOT EXISTS progresslog (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id   INTEGER NOT NULL REFERENCES book(id),
            page      INTEGER NOT NULL,
            timestamp DATETIME NOT NULL DEFAULT (datetime('now')),
            note      TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_progresslog_book_id ON progresslog (book_id)",
    ]

    # Add any missing columns to the book table (ALTER TABLE ADD COLUMN is
    # idempotent-safe via the try/except; SQLite ignores duplicate columns only
    # when we catch the error ourselves).
    book_columns_to_add = [
        ("custom_title",  "TEXT"),
        ("admin_notes",   "TEXT"),
        ("date_started",  "DATETIME"),
        ("date_finished", "DATETIME"),
    ]

    with engine.connect() as conn:
        for stmt in ddl_statements:
            conn.execute(text(stmt.strip()))

        for col_name, col_type in book_columns_to_add:
            try:
                conn.execute(text(f"ALTER TABLE book ADD COLUMN {col_name} {col_type}"))
                logger.info("Migration: added column book.%s", col_name)
            except Exception:
                pass  # Column already exists — safe to ignore

        conn.commit()

    logger.info("Safe migration complete.")


# ── Session & helpers ──────────────────────────────────────────────────────────

def get_session():
    with Session(engine) as session:
        yield session

def get_or_create_tag(session: Session, name: str) -> Tag:
    name = name.strip().lower()
    tag = session.exec(select(Tag).where(Tag.name == name)).first()
    if not tag:
        tag = Tag(name=name)
        session.add(tag); session.commit(); session.refresh(tag)
    return tag

def get_all_books(session: Session) -> List[Book]:
    return session.exec(select(Book)).all()

def get_book_by_path(session: Session, file_path: str) -> Optional[Book]:
    return session.exec(select(Book).where(Book.file_path == file_path)).first()

def get_progress_logs(session: Session, book_id: int) -> List[ProgressLog]:
    return session.exec(
        select(ProgressLog)
        .where(ProgressLog.book_id == book_id)
        .order_by(ProgressLog.timestamp)
    ).all()

def add_progress_log(session: Session, book_id: int, page: int, note: Optional[str] = None) -> ProgressLog:
    log = ProgressLog(book_id=book_id, page=page, note=note)
    session.add(log); session.commit(); session.refresh(log)
    return log

def get_quotes(session: Session, book_id: int) -> List[BookQuote]:
    return session.exec(
        select(BookQuote)
        .where(BookQuote.book_id == book_id)
        .order_by(BookQuote.date_added)
    ).all()

def get_stats(session: Session) -> dict:
    books = get_all_books(session)
    total       = len(books)
    read        = sum(1 for b in books if b.is_read)
    in_progress = sum(1 for b in books if b.current_page > 0 and not b.is_read)

    tag_counts: dict[str, int] = {}
    for book in books:
        for tag in book.tags:
            tag_counts[tag.name] = tag_counts.get(tag.name, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total":       total,
        "read":        read,
        "in_progress": in_progress,
        "unread":      total - read - in_progress,
        "top_tags":    [{"name": n, "count": c} for n, c in top_tags],
    }
