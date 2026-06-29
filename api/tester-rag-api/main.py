import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import RateLimitError

from app.api.ingest import router as ingest_router
from app.api.query import router as query_router
from app.api.context import router as context_router
from app.api.generate import router as generate_router
from app.api.explorer import router as explorer_router
from app.api.code_agent import code_agent_projects_router, code_agent_router
from app.api.po import po_router
from app.core.config import settings
from app.core.logging_config import setup_logging

setup_logging()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CODE_AGENT_CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router, prefix="/api")
app.include_router(query_router, prefix="/api")
app.include_router(context_router, prefix="/api")
app.include_router(generate_router, prefix="/api")
app.include_router(explorer_router, prefix="/api")
app.include_router(code_agent_router, prefix="/api/code-agent")
app.include_router(code_agent_projects_router, prefix="/api/code-agent")
app.include_router(po_router, prefix="/api/po")


@app.exception_handler(RateLimitError)
async def openai_rate_limit_handler(_request, exc: RateLimitError):
    message = str(exc).lower()
    if "quota" in message or "insufficient_quota" in message:
        detail = (
            "Embedding provider quota exceeded. Check your API plan and billing."
        )
    else:
        detail = f"OpenAI rate limit hit for embeddings: {exc}"
    return JSONResponse(status_code=429, content={"detail": detail})


@app.get("/")
async def root():
    return {"message": "Hello World"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
