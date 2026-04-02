"""Prompt Manager — FastAPI entry point."""

from fastapi import FastAPI

app = FastAPI(title="Agent OS — Prompt Manager", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
