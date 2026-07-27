from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers import documents, review, query, reference, repo_index


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


@app.get("/health")
def health():
    return {"status": "ok"}
