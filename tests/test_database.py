from app.database import _normalize_db_url


def test_normalizes_legacy_postgres_scheme():
    assert _normalize_db_url("postgres://user:pass@host/db") == "postgresql://user:pass@host/db"


def test_leaves_postgresql_scheme_unchanged():
    url = "postgresql://user:pass@host/db"
    assert _normalize_db_url(url) == url


def test_leaves_sqlite_url_unchanged():
    url = "sqlite:///./docparser.db"
    assert _normalize_db_url(url) == url


def test_only_replaces_the_scheme_prefix_not_later_occurrences():
    # A password or db name containing the literal substring "postgres://" (contrived,
    # but cheap to guard) shouldn't get double-mangled beyond the scheme itself.
    url = "postgres://user:postgres://pass@host/db"
    assert _normalize_db_url(url) == "postgresql://user:postgres://pass@host/db"
