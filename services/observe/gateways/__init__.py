"""Observe gateway modules.

ADR-2/ADR-4: observe 不连任何 harness —— 所有 harness 连接(connect/subscribe/send/
事件映射)已归 orchestrator(唯一 harness 客户端)。observe 纯被动收 /ws/ingest。

这里只留 mock_*(测试用的假 gateway,模拟 harness 推事件到 observe ingest)。
真实 harness 客户端(openclaw.py / claude_code.py)已归档到 .archive/(git 可恢复),
逻辑已搬 services/orchestrator/(Agent A 域)。
"""
