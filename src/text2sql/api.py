"""HTTP API and web UI for the guarded Text-to-SQL pipeline."""

from functools import lru_cache
from importlib import resources
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from text2sql import __version__
from text2sql.config import Settings, get_settings
from text2sql.models import PipelineResponse
from text2sql.pipeline import Text2SQLPipeline
from text2sql.sample_db import create_sample_database


class QueryRequest(BaseModel):
    question: str = Field(description="Natural-language question about the data.")
    execute: bool = Field(default=True, description="Run the SQL and return rows.")


class HealthResponse(BaseModel):
    status: str
    version: str
    provider: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. The pipeline is created on first use."""
    settings = settings or get_settings()

    app = FastAPI(
        title="Text-to-SQL with Guardrails",
        version=__version__,
        description=(
            "Ask questions in plain English. Every query passes input guardrails, "
            "SQL guardrails and hallucination detection before it is executed."
        ),
    )

    @lru_cache(maxsize=1)
    def pipeline() -> Text2SQLPipeline:
        if settings.db_path.name == "sample.db" and not settings.db_path.exists():
            create_sample_database(settings.db_path)
        return Text2SQLPipeline(settings)

    Pipeline = Annotated[Text2SQLPipeline, Depends(pipeline)]

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__, provider=settings.provider)

    @app.get("/api/schema", tags=["query"])
    def get_schema(p: Pipeline) -> dict[str, Any]:
        return p.schema.to_dict()

    @app.post("/api/query", response_model=PipelineResponse, tags=["query"])
    def query(req: QueryRequest, p: Pipeline) -> PipelineResponse:
        return p.run(req.question, execute=req.execute)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return resources.files("text2sql").joinpath("static/index.html").read_text("utf-8")

    return app


app = create_app()
