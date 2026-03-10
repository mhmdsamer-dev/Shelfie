"""
database.py — SQLModel models and database initialization for Local Library Manager.
"""

from datetime import datetime
from typing import Optional, List
from sqlmodel import Field, SQLModel, create_engine, Session, select, Relationship

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

    @property
    def display_title(self) -> str:
        return (self.custom_title or self.title or "Untitled").strip()

    @property
    def progress_percent(self) -> float:
        if self.total_pages and self.total_pages > 0:
            return round((self.current_page / self.total_pages) * 100, 1)
        return 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

def get_or_create_tag(session: Session, name: str) -> Tag:
    name = name.strip().lower()
    tag = session.exec(select(Tag).where(Tag.name == name)).first()
    if not tag:
        tag = Tag(name=name)
        session.add(tag)
        session.commit()
        session.refresh(tag)
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
    session.add(log)
    session.commit()
    session.refresh(log)
    return log

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
