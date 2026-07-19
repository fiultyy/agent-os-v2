#!/usr/bin/env python3
"""E2E 后端契约验证:memory lifecycle + orchestrate graph 事件经 observe WS 流到 observe store。

前置:`./start.sh` 已起 orchestrator(:8001)+ observe(:8002),智谱 key 已 export
(start.sh 启动即真跑 glm turn)。

流程:
  POST /v1/execute  → 拉 observe REST 断言 agent-os-v2 + memory lifecycle 事件序列
  POST /v1/orchestrate → 拉 observe REST 断言 orchestrate node_complete/execution_complete
  dump 真实事件 → /tmp/e2e_observe_events.json(供 TUI `--dump --replay` 段 2 渲染)

红线:不断言 LLM 输出文本(不定),只断言事件存在 + 关键字段。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

ORCH = "http://127.0.0.1:8001"
OBSERVE = "http://127.0.0.1:8002"
DUMP_PATH = "/tmp/e2e_observe_events.json"
PID = os.getpid()


def http(method, url, body=None, timeout=60, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = r.read()
        if raw:
            return out  # SSE text/event-stream → 原始 bytes(本脚本忽略 body)
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            return json.loads(out)
        return out


def get_events(ht, sid, timeout=60):
    """轮询 observe REST /sessions/{ht}/{sid}/events 直到有事件或超时。"""
    url = f"{OBSERVE}/sessions/{ht}/{sid}/events"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = http("GET", url, timeout=5)
            evs = r.get("events", []) if isinstance(r, dict) else []
            if evs:
                return evs
        except Exception:
            pass
        time.sleep(0.5)
    return []


def healthcheck():
    for name, base in [("orchestrator", ORCH), ("observe", OBSERVE)]:
        try:
            http("GET", f"{base}/health", timeout=3)
        except Exception as e:
            print(f"✗ {name} healthcheck 失败:{e}\n  请先跑 ./start.sh", file=sys.stderr)
            sys.exit(2)


def assert_any(evs, pred, msg):
    if not any(pred(e) for e in evs):
        raise AssertionError(f"{msg}(共 {len(evs)} 事件)")


def main():
    healthcheck()
    agent_id = os.environ.get("E2E_AGENT_ID")
    if not agent_id:
        print("✗ 未设 E2E_AGENT_ID。default agent id 是启动时随机 UUID(非字面 'default'),\n"
              "  从 orchestrator 启动日志取:\n"
              "    export E2E_AGENT_ID=$(grep -oP 'Default agent initialized: \\K[a-f0-9-]+' <start.sh-log>)",
              file=sys.stderr)
        sys.exit(2)
    print(f"[0/3] agent_id = {agent_id[:8]}…")
    time.sleep(1.5)  # 让 memory observe emitter WS 连上(startup hook 异步 connect)
    results = {}

    # ── 1. /v1/execute turn(native agent-os-v2 harness)──
    exec_sid = f"e2e-exec-{PID}"
    print(f"[1/3] POST /v1/execute  (sid={exec_sid}) …", flush=True)
    try:
        http("POST", f"{ORCH}/v1/execute",
             {"agent_id": agent_id, "input": "用一句话说你好", "session_id": exec_sid},
             timeout=120, raw=True)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"/v1/execute HTTP {e.code}: {e.read()[:200]!r}")

    aov = get_events("agent-os-v2", exec_sid, timeout=60)
    assert_any(aov, lambda e: e.get("event_type") == "tick_started",
               "[execute] 缺 tick_started")
    assert_any(aov, lambda e: e.get("event_type") == "tick_completed"
               and e.get("data", {}).get("status") == "success",
               "[execute] 缺 tick_completed(status=success)")
    print(f"  ✓ agent-os-v2/{exec_sid}: {len(aov)} 事件(tick_started + tick_completed)")
    results[f"agent-os-v2/{exec_sid}"] = aov

    # memory lifecycle(SESSION_START + MemoryWriterCapability TURN_END/INGEST)
    mem = get_events("memory", "memory", timeout=30)
    mem_kinds = {e.get("data", {}).get("memory_event") for e in mem}
    if "session_start" not in mem_kinds:
        raise AssertionError(f"[memory] 缺 session_start(有 {sorted(mem_kinds)})")
    if not (mem_kinds & {"turn_end", "ingest"}):
        raise AssertionError(f"[memory] 缺 turn_end/ingest(有 {sorted(mem_kinds)})")
    print(f"  ✓ memory/memory: {sorted(k for k in mem_kinds if k)}")
    results["memory/memory"] = mem

    # ── 2. /v1/orchestrate(multi_agent/fan_in/synthesizer 图)──
    orch_sid = f"e2e-orch-{PID}"
    print(f"[2/3] POST /v1/orchestrate  (sid={orch_sid}) …", flush=True)
    payload = {
        "orchestrator_agent_id": agent_id,
        "sub_agents": [
            {"role": "role_a", "config": {"system_prompt": "你是研究员 A,用一句话回答。"}},
            {"role": "role_b", "config": {"system_prompt": "你是研究员 B,用一句话回答。"}},
        ],
        "input": "用一句话介绍 Python",
        "session_id": orch_sid,
    }
    try:
        http("POST", f"{ORCH}/v1/orchestrate", payload, timeout=180, raw=True)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"/v1/orchestrate HTTP {e.code}: {e.read()[:200]!r}")

    orch = get_events("orchestrate", orch_sid, timeout=60)
    orch_kinds = {e.get("data", {}).get("orch_event") for e in orch}
    for need in ("agent_status", "node_complete", "execution_complete"):
        if need not in orch_kinds:
            raise AssertionError(f"[orchestrate] 缺 {need}(有 {sorted(orch_kinds)})")
    print(f"  ✓ orchestrate/{orch_sid}: {sorted(k for k in orch_kinds if k)}")
    results[f"orchestrate/{orch_sid}"] = orch

    # ── 3. dump 真实事件 → JSON(供 TUI --replay)──
    with open(DUMP_PATH, "w") as f:
        json.dump(results, f, ensure_ascii=False)
    total = sum(len(v) for v in results.values())
    print(f"[3/3] dump → {DUMP_PATH}({total} 事件)")

    print("\n[E2E] agent-os-v2 ✓ | memory ✓ | orchestrate ✓")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n✗ E2E FAIL: {e}", file=sys.stderr)
        sys.exit(1)
