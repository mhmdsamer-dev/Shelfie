"""
test_api.py — Endpoint tests for every FastAPI route in shelfie/main.py.

Fixtures used (from conftest.py)
---------------------------------
client       — TestClient with in-memory DB injected via dependency_overrides.
db_session   — Direct SQLModel Session to the same in-memory DB.
sample_book  — A committed Book row (pdf, 200 pages) ready for use.
test_engine  — The raw SQLAlchemy engine (needed when mixing fixtures).
"""

import pytest
from shelfie.database import Book, BookQuote, ProgressLog, Tag


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_book(db_session, *, file_path="/tmp/book.pdf", title="A Book",
               file_type="pdf", total_pages=100, current_page=0,
               category=None, is_read=False):
    book = Book(
        file_path=file_path,
        title=title,
        file_type=file_type,
        total_pages=total_pages,
        current_page=current_page,
        category=category,
        is_read=is_read,
    )
    db_session.add(book)
    db_session.commit()
    db_session.refresh(book)
    return book


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/books
# ══════════════════════════════════════════════════════════════════════════════

class TestListBooks:
    def test_empty_library_returns_empty_list(self, client):
        resp = client.get("/api/books")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_books(self, client, sample_book):
        resp = client.get("/api/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == sample_book.id
        assert data[0]["title"] == sample_book.title

    def test_filter_by_category(self, client, db_session):
        _make_book(db_session, file_path="/tmp/sci.pdf", title="Sci-Fi", category="sci-fi")
        _make_book(db_session, file_path="/tmp/bio.pdf", title="Bio",    category="biography")

        resp = client.get("/api/books?category=sci-fi")
        assert resp.status_code == 200
        titles = [b["title"] for b in resp.json()]
        assert "Sci-Fi" in titles
        assert "Bio" not in titles

    def test_filter_by_tag(self, client, db_session):
        book = _make_book(db_session, file_path="/tmp/tagged.pdf", title="Tagged Book")
        tag = Tag(name="classics")
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)
        book.tags.append(tag)
        db_session.add(book)
        db_session.commit()

        resp = client.get("/api/books?tag=classics")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_search_by_title(self, client, db_session):
        _make_book(db_session, file_path="/tmp/dune.pdf",   title="Dune")
        _make_book(db_session, file_path="/tmp/hobbit.pdf", title="The Hobbit")

        resp = client.get("/api/books?q=dune")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["title"] == "Dune"

    def test_search_is_case_insensitive(self, client, db_session):
        _make_book(db_session, file_path="/tmp/case.pdf", title="CaseSensitive")

        resp = client.get("/api/books?q=casesensitive")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/books/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestGetBook:
    def test_returns_book_by_id(self, client, sample_book):
        resp = client.get(f"/api/books/{sample_book.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sample_book.id

    def test_404_for_nonexistent_id(self, client):
        resp = client.get("/api/books/99999")
        assert resp.status_code == 404

    def test_response_contains_expected_fields(self, client, sample_book):
        data = client.get(f"/api/books/{sample_book.id}").json()
        for field in ("id", "title", "file_type", "total_pages", "current_page",
                      "progress", "is_read", "date_added", "tags"):
            assert field in data


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /api/books/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestPatchBook:
    def test_update_notes(self, client, sample_book):
        resp = client.patch(f"/api/books/{sample_book.id}", json={"notes": "great read"})
        assert resp.status_code == 200
        assert resp.json()["notes"] == "great read"

    def test_update_category(self, client, sample_book):
        resp = client.patch(f"/api/books/{sample_book.id}", json={"category": "fiction"})
        assert resp.status_code == 200
        assert resp.json()["category"] == "fiction"

    def test_update_current_page(self, client, sample_book):
        resp = client.patch(f"/api/books/{sample_book.id}", json={"current_page": 50})
        assert resp.status_code == 200
        assert resp.json()["current_page"] == 50

    def test_mark_as_read(self, client, sample_book):
        resp = client.patch(f"/api/books/{sample_book.id}", json={"is_read": True})
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

    def test_assign_tags(self, client, sample_book):
        resp = client.patch(f"/api/books/{sample_book.id}", json={"tags": ["python", "tech"]})
        assert resp.status_code == 200
        assert set(resp.json()["tags"]) == {"python", "tech"}

    def test_replace_tags(self, client, sample_book):
        client.patch(f"/api/books/{sample_book.id}", json={"tags": ["old"]})
        resp = client.patch(f"/api/books/{sample_book.id}", json={"tags": ["new"]})
        assert resp.json()["tags"] == ["new"]

    def test_404_for_nonexistent_book(self, client):
        resp = client.patch("/api/books/99999", json={"notes": "ghost"})
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# PUT /api/books/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestPutBook:
    def test_update_custom_title(self, client, sample_book):
        resp = client.put(f"/api/books/{sample_book.id}", json={"custom_title": "My Custom Title"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My Custom Title"
        assert data["original_title"] == sample_book.title

    def test_clear_custom_title_with_empty_string(self, client, sample_book):
        client.put(f"/api/books/{sample_book.id}", json={"custom_title": "Temp"})
        resp = client.put(f"/api/books/{sample_book.id}", json={"custom_title": ""})
        assert resp.status_code == 200
        assert resp.json()["custom_title"] is None

    def test_update_admin_notes(self, client, sample_book):
        resp = client.put(f"/api/books/{sample_book.id}", json={"admin_notes": "staff note"})
        assert resp.status_code == 200
        assert resp.json()["admin_notes"] == "staff note"

    def test_set_date_started_iso(self, client, sample_book):
        resp = client.put(
            f"/api/books/{sample_book.id}",
            json={"date_started": "2024-01-15T08:00:00"},
        )
        assert resp.status_code == 200
        assert "2024-01-15" in resp.json()["date_started"]

    def test_clear_date_started_with_empty_string(self, client, sample_book):
        client.put(f"/api/books/{sample_book.id}", json={"date_started": "2024-01-15T08:00:00"})
        resp = client.put(f"/api/books/{sample_book.id}", json={"date_started": ""})
        assert resp.status_code == 200
        assert resp.json()["date_started"] is None

    def test_auto_date_started_when_page_advances(self, client, sample_book):
        resp = client.put(f"/api/books/{sample_book.id}", json={"current_page": 1})
        assert resp.status_code == 200
        assert resp.json()["date_started"] is not None

    def test_auto_date_finished_at_last_page(self, client, sample_book):
        resp = client.put(
            f"/api/books/{sample_book.id}",
            json={"current_page": sample_book.total_pages},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["date_finished"] is not None
        assert data["is_read"] is True

    def test_logs_progress_when_page_changes(self, client, db_session, sample_book):
        client.put(f"/api/books/{sample_book.id}", json={"current_page": 10})
        logs_resp = client.get(f"/api/books/{sample_book.id}/progress")
        assert len(logs_resp.json()) >= 1

    def test_404_for_nonexistent_book(self, client):
        resp = client.put("/api/books/99999", json={"custom_title": "ghost"})
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/books/{id}
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteBook:
    def test_deletes_book_successfully(self, client, sample_book):
        resp = client.delete(f"/api/books/{sample_book.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        assert client.get(f"/api/books/{sample_book.id}").status_code == 404

    def test_404_for_nonexistent_book(self, client):
        resp = client.delete("/api/books/99999")
        assert resp.status_code == 404

    def test_cascades_progress_logs(self, client, sample_book):
        client.post(f"/api/books/{sample_book.id}/progress", json={"page": 5})
        client.delete(f"/api/books/{sample_book.id}")
        # Book is gone; progress endpoint returns 404 too
        assert client.get(f"/api/books/{sample_book.id}/progress").status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Progress log endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestProgressLog:
    def test_log_progress_returns_entry(self, client, sample_book):
        resp = client.post(
            f"/api/books/{sample_book.id}/progress",
            json={"page": 20, "note": "halfway there"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 20
        assert data["note"] == "halfway there"

    def test_auto_note_generated_when_omitted(self, client, sample_book):
        resp = client.post(f"/api/books/{sample_book.id}/progress", json={"page": 50})
        assert resp.status_code == 200
        assert resp.json()["note"] is not None

    def test_negative_page_is_rejected(self, client, sample_book):
        resp = client.post(f"/api/books/{sample_book.id}/progress", json={"page": -1})
        assert resp.status_code == 400

    def test_get_progress_returns_list(self, client, sample_book):
        client.post(f"/api/books/{sample_book.id}/progress", json={"page": 10})
        client.post(f"/api/books/{sample_book.id}/progress", json={"page": 20})

        resp = client.get(f"/api/books/{sample_book.id}/progress")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_progress_404_for_unknown_book(self, client):
        resp = client.get("/api/books/99999/progress")
        assert resp.status_code == 404

    def test_patch_log_note(self, client, sample_book):
        log = client.post(f"/api/books/{sample_book.id}/progress", json={"page": 1}).json()
        resp = client.patch(
            f"/api/books/{sample_book.id}/progress/{log['id']}",
            json={"note": "revised note"},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "revised note"

    def test_patch_log_wrong_book_returns_404(self, client, db_session):
        b1 = _make_book(db_session, file_path="/tmp/b1.pdf", title="B1")
        b2 = _make_book(db_session, file_path="/tmp/b2.pdf", title="B2")
        log = client.post(f"/api/books/{b1.id}/progress", json={"page": 1}).json()

        resp = client.patch(
            f"/api/books/{b2.id}/progress/{log['id']}",
            json={"note": "wrong book"},
        )
        assert resp.status_code == 404

    def test_delete_log(self, client, sample_book):
        log = client.post(f"/api/books/{sample_book.id}/progress", json={"page": 5}).json()
        resp = client.delete(f"/api/books/{sample_book.id}/progress/{log['id']}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_log_wrong_book_returns_404(self, client, db_session):
        b1 = _make_book(db_session, file_path="/tmp/c1.pdf", title="C1")
        b2 = _make_book(db_session, file_path="/tmp/c2.pdf", title="C2")
        log = client.post(f"/api/books/{b1.id}/progress", json={"page": 1}).json()

        resp = client.delete(f"/api/books/{b2.id}/progress/{log['id']}")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Quote endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestQuotes:
    def test_add_quote(self, client, sample_book):
        resp = client.post(
            f"/api/books/{sample_book.id}/quotes",
            json={"quote_text": "To be or not to be", "page_number": 42},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quote_text"] == "To be or not to be"
        assert data["page_number"] == 42
        assert data["book_id"] == sample_book.id

    def test_empty_quote_text_is_rejected(self, client, sample_book):
        resp = client.post(
            f"/api/books/{sample_book.id}/quotes",
            json={"quote_text": "   "},
        )
        assert resp.status_code == 400

    def test_add_quote_unknown_book_returns_404(self, client):
        resp = client.post("/api/books/99999/quotes", json={"quote_text": "ghost"})
        assert resp.status_code == 404

    def test_list_quotes(self, client, sample_book):
        client.post(f"/api/books/{sample_book.id}/quotes", json={"quote_text": "Quote A"})
        client.post(f"/api/books/{sample_book.id}/quotes", json={"quote_text": "Quote B"})

        resp = client.get(f"/api/books/{sample_book.id}/quotes")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_quotes_empty(self, client, sample_book):
        resp = client.get(f"/api/books/{sample_book.id}/quotes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_quotes_unknown_book_returns_404(self, client):
        resp = client.get("/api/books/99999/quotes")
        assert resp.status_code == 404

    def test_delete_quote(self, client, sample_book):
        quote = client.post(
            f"/api/books/{sample_book.id}/quotes",
            json={"quote_text": "to delete"},
        ).json()

        resp = client.delete(f"/api/books/{sample_book.id}/quotes/{quote['id']}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        remaining = client.get(f"/api/books/{sample_book.id}/quotes").json()
        assert len(remaining) == 0

    def test_delete_quote_wrong_book_returns_404(self, client, db_session):
        b1 = _make_book(db_session, file_path="/tmp/q1.pdf", title="Q1")
        b2 = _make_book(db_session, file_path="/tmp/q2.pdf", title="Q2")
        quote = client.post(f"/api/books/{b1.id}/quotes", json={"quote_text": "mine"}).json()

        resp = client.delete(f"/api/books/{b2.id}/quotes/{quote['id']}")
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# Stats, Tags, Categories
# ══════════════════════════════════════════════════════════════════════════════

class TestStats:
    def test_empty_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["read"] == 0
        assert data["in_progress"] == 0
        assert data["unread"] == 0

    def test_stats_counts_correctly(self, client, db_session):
        _make_book(db_session, file_path="/tmp/s1.pdf", title="S1", is_read=True)
        _make_book(db_session, file_path="/tmp/s2.pdf", title="S2", current_page=5)
        _make_book(db_session, file_path="/tmp/s3.pdf", title="S3")

        data = client.get("/api/stats").json()
        assert data["total"] == 3
        assert data["read"] == 1
        assert data["in_progress"] == 1
        assert data["unread"] == 1


class TestTags:
    def test_list_tags_empty(self, client):
        resp = client.get("/api/tags")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tags_after_assigning(self, client, sample_book):
        client.patch(f"/api/books/{sample_book.id}", json={"tags": ["alpha", "beta"]})
        tags = client.get("/api/tags").json()
        names = {t["name"] for t in tags}
        assert {"alpha", "beta"} <= names


class TestCategories:
    def test_list_categories_empty(self, client):
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_categories_returns_sorted_unique(self, client, db_session):
        _make_book(db_session, file_path="/tmp/cat1.pdf", title="C1", category="fiction")
        _make_book(db_session, file_path="/tmp/cat2.pdf", title="C2", category="biography")
        _make_book(db_session, file_path="/tmp/cat3.pdf", title="C3", category="fiction")

        resp = client.get("/api/categories")
        assert resp.status_code == 200
        cats = resp.json()
        assert cats == sorted(set(cats))
        assert cats.count("fiction") == 1


# ══════════════════════════════════════════════════════════════════════════════
# Library-path endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestLibraryPath:
    def test_get_library_path_returns_string(self, client):
        resp = client.get("/api/library-path")
        assert resp.status_code == 200
        assert "path" in resp.json()

    def test_set_valid_library_path(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("shelfie.main.save_library_path", lambda p: None)
        monkeypatch.setattr("shelfie.main.scan_library", lambda p: None)
        monkeypatch.setattr("shelfie.main.start_watchdog", lambda p: None)

        resp = client.post("/api/library-path", json={"path": str(tmp_path)})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_nonexistent_path_returns_400(self, client):
        resp = client.post("/api/library-path", json={"path": "/totally/fake/path/xyz"})
        assert resp.status_code == 400

    def test_set_file_instead_of_dir_returns_400(self, client, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("hi")
        resp = client.post("/api/library-path", json={"path": str(f)})
        assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Rescan endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestRescan:
    def test_rescan_returns_ok(self, client, monkeypatch):
        monkeypatch.setattr("shelfie.main.scan_library", lambda p: None)
        resp = client.post("/api/rescan")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Database integrity edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestDatabaseIntegrity:
    def test_duplicate_file_path_not_inserted_twice(self, db_session):
        """file_path has a UNIQUE constraint — second insert must fail at the DB level."""
        from sqlalchemy.exc import IntegrityError

        book1 = Book(file_path="/tmp/unique.pdf", title="First",  file_type="pdf")
        book2 = Book(file_path="/tmp/unique.pdf", title="Second", file_type="pdf")

        db_session.add(book1)
        db_session.commit()

        db_session.add(book2)
        with pytest.raises(IntegrityError):
            db_session.commit()

        db_session.rollback()

    def test_get_or_create_tag_deduplicates(self, db_session):
        from shelfie.database import get_or_create_tag

        t1 = get_or_create_tag(db_session, "python")
        t2 = get_or_create_tag(db_session, "python")
        assert t1.id == t2.id

    def test_get_or_create_tag_normalises_case(self, db_session):
        from shelfie.database import get_or_create_tag

        t1 = get_or_create_tag(db_session, "Python")
        t2 = get_or_create_tag(db_session, "PYTHON")
        assert t1.id == t2.id

    def test_progress_log_requires_valid_book_id(self, db_session):
        from sqlalchemy.exc import IntegrityError

        log = ProgressLog(book_id=99999, page=1)
        db_session.add(log)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_quote_requires_valid_book_id(self, db_session):
        from sqlalchemy.exc import IntegrityError

        quote = BookQuote(book_id=99999, quote_text="orphan")
        db_session.add(quote)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
