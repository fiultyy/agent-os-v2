"""mem-service bootstrap — KG init from CC memory .md (ADR-12).

cli ``init-memory`` entry: scan a CC memory dir (``*.md``), feed each file's
text through the autodream pipeline (LLM 蝴蝶翼 extract ADR-11 + 增量决策
ADD/UPDATE/DELETE/NOOP ADR-10 + 幂等) as a synthetic one-record transcript,
persisting facts with ``fact_type='permanent'`` (长期知识不衰减 ADR-8).

Reuses autodream wholesale — no独立 增量/抽取 logic (DRY): bootstrap is a thin
scan + tmp-transcript + autodream-call loop. Idempotent via autodream's 增量
contract: re-running on an unchanged dir ⇒ NOOP/UPDATE, not duplicate ADDs.

Returns ``{"files": n, "added": ..., "updated": ..., "deleted": ..., "noop": ...}``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import autodream as autodream_mod


def init_memory(
    memory_dir: str | Path,
    providers: list | None = None,
    fact_type: str = "permanent",
    source_cwd: str | None = None,
) -> dict[str, int]:
    """Seed the KG from CC memory ``.md`` files (ADR-12).

    For each ``*.md`` in ``memory_dir`` (sorted by name): read text → write a
    synthetic one-record transcript JSONL (``{type:user, message:{content:text}}``)
    → ``autodream.autodream(session_id="memory:<file>", ..., fact_type=fact_type)``
    →累加 counts. ``providers=None`` → autodream default (LLM 蝴蝶翼); ``providers=[]``
    → regex fallback (ADR-5 upheld).

    Idempotent: a re-run on an unchanged dir yields NOOP/UPDATE (autodream 增量
    decision), not duplicate ADDs — safe to re-run after editing memory files.
    """
    memory_dir = Path(memory_dir)
    if not memory_dir.is_dir():
        return {"files": 0, "added": 0, "updated": 0, "deleted": 0, "noop": 0,
                "skipped": str(memory_dir)}

    totals = {"files": 0, "added": 0, "updated": 0, "deleted": 0, "noop": 0}
    for md in sorted(memory_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        # Synthetic one-record transcript: autodream reads user/assistant
        # message.content; wrap the whole .md as one user message. ponytail:
        # NamedTemporaryFile(delete=False) + finally unlink (autodream reads
        # post-close; we clean up regardless of outcome).
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        try:
            tmp.write(json.dumps(
                {"type": "user", "message": {"content": text}}, ensure_ascii=False) + "\n")
            tmp.close()
            r = autodream_mod.autodream(
                session_id=f"memory:{md.name}",
                transcript_path=tmp.name,
                providers=providers,
                fact_type=fact_type,
                source_cwd=source_cwd,
            )
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        totals["files"] += 1
        for k in ("added", "updated", "deleted", "noop"):
            totals[k] += r.get(k, 0)
    return totals


def _demo() -> None:  # ponytail self-check
    import os
    import tempfile as _t
    d = _t.mkdtemp()
    open(os.path.join(d, "x.md"), "w").write("用户使用 rust")
    r = init_memory(d, providers=[])
    assert r["added"] > 0, r
    print("init_memory ok:", r)


if __name__ == "__main__":
    _demo()
