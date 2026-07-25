"""
IELTS Vocabulary App — FastAPI Backend Entry Point
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routers import auth

app = FastAPI(
    title="IELTS Vocabulary API",
    description="雅思单词听写应用后端 API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 词库音频静态目录（seed 阶段生成，见 docs/09-wordlist-research.md）
_audio_dir = Path(settings.AUDIO_DIR)
_audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/audio", StaticFiles(directory=_audio_dir), name="audio")


app.include_router(auth.router)


@app.get("/api/health", tags=["运维"])
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
