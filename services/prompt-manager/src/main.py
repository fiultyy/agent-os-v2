"""Prompt Manager — FastAPI entry point.

Endpoints:
- Template CRUD (create, read, update, delete)
- Version management (list versions, roll back)
- Variable rendering
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.templates import PromptTemplate
from src.versioning import PromptVersion

app = FastAPI(title="Agent OS — Prompt Manager", version="0.1.0", redirect_slashes=False)

# ── In-memory stores ─────────────────────────────────────────────

_templates: dict[str, dict[str, Any]] = {}
_version_stores: dict[str, PromptVersion] = {}


# ── Models ───────────────────────────────────────────────────────


class CreateTemplateRequest(BaseModel):
    name: str
    content: str
    description: str = ""
    variables: list[str] = []


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    content: str | None = None
    description: str | None = None
    variables: list[str] | None = None


class RenderRequest(BaseModel):
    variables: dict[str, Any] = {}


# ── Health ───────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Template CRUD ────────────────────────────────────────────────


@app.post("/templates")
async def create_template(req: CreateTemplateRequest) -> dict:
    template_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    if not req.variables:
        req.variables = re.findall(r"\{\{(\w+)\}\}", req.content)


    template = {
        "id": template_id,
        "name": req.name,
        "content": req.content,
        "description": req.description,
        "variables": req.variables,
        "created_at": now,
        "updated_at": now,
    }
    _templates[template_id] = template

    vs = PromptVersion(template_id)
    vs.create_version(req.content, {"description": req.description, "variables": req.variables})
    _version_stores[template_id] = vs

    return template


@app.get("/templates")
async def list_templates() -> list[dict]:
    return list(_templates.values())


@app.get("/templates/{template_id}")
async def get_template(template_id: str) -> dict:
    t = _templates.get(template_id)
    if not t:
        return {"error": "Template not found"}
    return t


@app.put("/templates/{template_id}")
async def update_template(template_id: str, req: UpdateTemplateRequest) -> dict:
    t = _templates.get(template_id)
    if not t:
        return {"error": "Template not found"}

    if req.name is not None:
        t["name"] = req.name
    if req.content is not None:
        t["content"] = req.content
        t["variables"] = re.findall(r"\{\{(\w+)\}\}", req.content)
        vs = _version_stores.get(template_id)
        if vs:
            vs.create_version(req.content, {"description": t.get("description", "")})
    if req.description is not None:
        t["description"] = req.description
    if req.variables is not None:
        t["variables"] = req.variables

    t["updated_at"] = datetime.now(timezone.utc).isoformat()
    return t


@app.delete("/templates/{template_id}")
async def delete_template(template_id: str) -> dict:
    if template_id in _templates:
        del _templates[template_id]
        _version_stores.pop(template_id, None)
        return {"deleted": True}
    return {"error": "Template not found"}


# ── Rendering ────────────────────────────────────────────────────


@app.post("/templates/{template_id}/render")
async def render_template(template_id: str, req: RenderRequest) -> dict:
    t = _templates.get(template_id)
    if not t:
        return {"error": "Template not found"}

    engine = PromptTemplate(t["content"])
    try:
        rendered = engine.render(**req.variables)
    except KeyError as e:
        return {"error": f"Missing variable: {e}"}

    return {"template_id": template_id, "rendered": rendered}


# ── Version management ───────────────────────────────────────────


@app.get("/templates/{template_id}/versions")
async def list_versions(template_id: str) -> dict:
    vs = _version_stores.get(template_id)
    if not vs:
        return {"error": "Template not found"}
    return {"template_id": template_id, "versions": vs.versions}


@app.get("/templates/{template_id}/versions/{version}")
async def get_version(template_id: str, version: int) -> dict:
    vs = _version_stores.get(template_id)
    if not vs:
        return {"error": "Template not found"}
    v = vs.get_version(version)
    if not v:
        return {"error": "Version not found"}
    return v


@app.post("/templates/{template_id}/rollback/{version}")
async def rollback_template(template_id: str, version: int) -> dict:
    t = _templates.get(template_id)
    vs = _version_stores.get(template_id)
    if not t or not vs:
        return {"error": "Template not found"}

    v = vs.get_version(version)
    if not v:
        return {"error": "Version not found"}

    t["content"] = v["content"]
    t["variables"] = re.findall(r"\{\{(\w+)\}\}", v["content"])
    t["updated_at"] = datetime.now(timezone.utc).isoformat()

    vs.create_version(v["content"], {"rollback_from": version})
    return t
