import json
import os

import httpx
import pytest

from app.config import settings
from app.services import repo_index as ri
from app.services.repo_index import (
    _iter_source_files, _chunk_file, _file_tag, _embed_texts, _expand_query,
    build_index, search, RepoIndexError, CHUNK_LINES,
)


@pytest.fixture()
def index_dir(tmp_path, monkeypatch):
    d = tmp_path / "repo_index"
    monkeypatch.setattr(settings, "repo_index_dir", str(d))
    return str(d)


def _write(root, rel_path, content):
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return str(full)


# --- file discovery / exclusion -------------------------------------------------

def test_iter_source_files_skips_noise_dirs_and_extensions(tmp_path):
    _write(tmp_path, "app/order.py", "def submit_order(): pass\n")
    _write(tmp_path, "node_modules/pkg/index.js", "module.exports = {}\n")
    _write(tmp_path, "assets/logo.png", "not really an image but wrong extension")
    _write(tmp_path, ".git/HEAD", "ref: refs/heads/main\n")

    found = {os.path.relpath(p, tmp_path) for p in _iter_source_files(str(tmp_path))}
    assert found == {"app/order.py"}


def test_iter_source_files_skips_oversized_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "MAX_FILE_BYTES", 100)
    _write(tmp_path, "big.py", "x = 1\n" * 100)  # well over 100 bytes
    _write(tmp_path, "small.py", "x = 1\n")

    found = {os.path.relpath(p, tmp_path) for p in _iter_source_files(str(tmp_path))}
    assert found == {"small.py"}


# --- chunking ---------------------------------------------------------------------

def test_chunk_file_splits_large_files_with_overlap(tmp_path):
    path = _write(tmp_path, "big.py", "\n".join(f"line_{i} = {i}" for i in range(CHUNK_LINES * 2 + 10)))
    chunks = _chunk_file(str(tmp_path), path)

    assert len(chunks) > 1
    # consecutive chunks overlap
    assert chunks[1].start_line < chunks[0].end_line


def test_chunk_file_single_chunk_for_small_file(tmp_path):
    path = _write(tmp_path, "small.py", "def f(): return 1\n")
    chunks = _chunk_file(str(tmp_path), path)
    assert len(chunks) == 1
    assert chunks[0].path == "small.py"


def test_file_tag_heuristics():
    assert _file_tag("app/schemas/order_schema.py") == "schema"
    assert _file_tag("app/constants/segment_enum.py") == "constants"
    assert _file_tag("app/routers/order_controller.py") == "api"
    assert _file_tag("app/utils/random_helper.py") == "general"


# --- glossary expansion -----------------------------------------------------------

def test_expand_query_adds_glossary_terms_for_known_abbreviations():
    expanded = _expand_query("The STP cutoff time has changed.")
    assert "SystematicTransferPlan" in expanded


def test_expand_query_leaves_unrelated_text_unchanged():
    text = "This is a routine holiday notice with no BSE jargon."
    assert _expand_query(text) == text


# --- embedding error handling (real httpx path, not the fake used elsewhere) -----

def test_embed_texts_raises_clear_error_on_connection_refused(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(ri.httpx, "post", fake_post)
    with pytest.raises(RepoIndexError, match="ollama serve"):
        _embed_texts(["some code"])


def test_embed_texts_raises_on_missing_embedding_field(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    monkeypatch.setattr(ri.httpx, "post", lambda url, json, timeout: FakeResponse())
    with pytest.raises(RepoIndexError, match="no embedding"):
        _embed_texts(["some code"])


# --- build_index + search, with a deterministic fake embedder --------------------

KEYWORDS = ["scheme_code", "holiday_calendar", "validate_order"]


def _fake_embed(texts):
    """Keyword-indicator vectors: cosine similarity is 1.0 for shared keywords, 0 otherwise."""
    vectors = []
    for t in texts:
        lower = t.lower()
        vectors.append([1.0 if kw in lower else 0.0 for kw in KEYWORDS])
    return vectors


def test_build_index_and_search_finds_relevant_chunk(tmp_path, index_dir, monkeypatch):
    monkeypatch.setattr(ri, "_embed_texts", _fake_embed)

    _write(tmp_path, "app/order_schema.py", "SCHEME_CODE_FIELD = 'scheme_code'\n")
    _write(tmp_path, "app/holidays.py", "HOLIDAY_CALENDAR = ['2026-01-26']\n")

    stats = build_index("backend", str(tmp_path))
    assert stats["files_scanned"] == 2
    assert stats["chunks_newly_embedded"] == 2

    result = search("backend", "New mandatory scheme_code field in order file")
    assert result["matched"] is True
    assert result["candidates"][0]["path"] == "app/order_schema.py"
    assert result["candidates"][0]["file_tag"] == "schema"


def test_search_returns_no_match_below_threshold(tmp_path, index_dir, monkeypatch):
    monkeypatch.setattr(ri, "_embed_texts", _fake_embed)
    monkeypatch.setattr(settings, "repo_similarity_threshold", 0.35)

    _write(tmp_path, "app/holidays.py", "HOLIDAY_CALENDAR = ['2026-01-26']\n")
    build_index("backend", str(tmp_path))

    # Query shares no keywords with the indexed content at all.
    result = search("backend", "completely unrelated topic with no overlap")
    assert result["matched"] is False
    assert "new functionality" in result["reason"]
    assert result["candidates"] == []


def test_search_without_built_index_raises(index_dir):
    with pytest.raises(RepoIndexError, match="No index built"):
        search("backend", "anything")


def test_build_index_reuses_unchanged_chunks_on_second_run(tmp_path, index_dir, monkeypatch):
    call_count = {"n": 0}

    def counting_embed(texts):
        call_count["n"] += len(texts)
        return _fake_embed(texts)

    monkeypatch.setattr(ri, "_embed_texts", counting_embed)

    root = tmp_path / "root"
    _write(root, "app/order_schema.py", "SCHEME_CODE_FIELD = 'scheme_code'\n")
    build_index("backend", str(root))
    first_call_count = call_count["n"]
    assert first_call_count > 0

    # Re-run against the same unchanged file - nothing new should be embedded.
    stats = build_index("backend", str(root))
    assert stats["chunks_newly_embedded"] == 0
    assert stats["chunks_reused"] == stats["chunks_total"]
    assert call_count["n"] == first_call_count  # no additional embedding calls


def test_build_index_excludes_its_own_cache_dir_when_nested_inside_repo_root(tmp_path, monkeypatch):
    # Regression test: repo_index_dir is intentionally allowed to live inside the repo
    # root being indexed (this is exactly how the README suggests testing locally, by
    # pointing BACKEND_REPO_PATH at this project's own directory). Without the exclusion
    # in _iter_source_files, a rebuild would re-index its own previous JSON output as
    # "source," reporting a spurious change on every single run.
    monkeypatch.setattr(settings, "repo_index_dir", str(tmp_path / "repo_index"))
    monkeypatch.setattr(ri, "_embed_texts", _fake_embed)

    _write(tmp_path, "app/order_schema.py", "SCHEME_CODE_FIELD = 'scheme_code'\n")

    build_index("backend", str(tmp_path))
    stats = build_index("backend", str(tmp_path))

    assert stats["files_scanned"] == 1  # not 2 - the index cache's own .json must not count
    assert stats["chunks_newly_embedded"] == 0


def test_build_index_rejects_missing_directory(index_dir):
    with pytest.raises(RepoIndexError, match="not a local directory"):
        build_index("backend", "/definitely/does/not/exist")


def test_build_index_rejects_unconfigured_path(index_dir):
    with pytest.raises(RepoIndexError, match="No path configured"):
        build_index("backend", "")
