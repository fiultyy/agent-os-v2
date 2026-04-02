"""Orchestration engine — entry point for the orchestrator service."""

from fastapi import FastAPI

app = FastAPI(title="Agent OS — Orchestrator", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# TODO: register gRPC service, wire up graph engine
