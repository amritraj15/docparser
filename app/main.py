from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.database import init_db
from app.routers import documents, review, query, reference, repo_index, change_suggestions

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Doc Parser",
    description=(
        "Classifies BSE notices/circulars for a mutual-fund transacting platform: does this "
        "require a system change, is it backend or frontend, which segment does it apply to."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(review.router)
app.include_router(query.router)
app.include_router(reference.router)
app.include_router(repo_index.router)
app.include_router(change_suggestions.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    """
    Serves the review-queue UI. Single self-contained HTML file (app/static/index.html) -
    no build step, no separate frontend deploy, works at whatever URL the API itself is
    deployed to. Fetches are same-origin, so CORS being wide-open above doesn't matter
    for this page specifically.
    """
    return (STATIC_DIR / "index.html").read_text()
