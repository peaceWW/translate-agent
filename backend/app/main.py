from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, llm
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="AcademicTranslate Agent",
    description="版面保真 + 内容保真的专业学术论文翻译智能体",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(llm.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "AcademicTranslate Agent"}
