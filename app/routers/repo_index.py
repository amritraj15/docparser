from typing import Literal

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.repo_index import build_index, index_status, RepoIndexError

router = APIRouter(prefix="/repo-index", tags=["repo-index"])


def _require_enabled():
    if not settings.repo_suggestion_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Repo change-suggestion is disabled (REPO_SUGGESTION_ENABLED=false). "
                "This is off by default so a deployed instance never indexes a real "
                "codebase unintentionally. Enable it only when running locally against "
                "a path you control."
            ),
        )


def _resolve_path(target: str) -> str:
    if target == "backend":
        return settings.backend_repo_path
    if target == "frontend":
        return settings.frontend_repo_path
    raise HTTPException(status_code=400, detail="target must be 'backend' or 'frontend'.")


@router.post("/build")
def build(target: Literal["backend", "frontend"]):
    _require_enabled()
    root_path = _resolve_path(target)
    try:
        return build_index(target, root_path)
    except RepoIndexError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def status():
    _require_enabled()
    return {
        "backend": index_status("backend"),
        "frontend": index_status("frontend"),
    }
