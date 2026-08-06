"""Node I — recall 评测基线,量化 v1 中文盲区(ADR-9).

grill 实证:v1 recall 对中文同义/省称/改写三组全 miss(rust 命中,
铁锈 / rusty / 开发语言 全 [])。本评测集量化该盲区,作为 decay/pagerank
(v2)/向量层(v3)的回归对照门。

设计:
- **fact 集**:30-50 中文 fact,覆盖三类(技术词 / 中英混排 / 纯中文裸句,
  各 ~15)。经 ``cli.ingest`` 走真实 regex 抽取链灌入 KG —— 不绕过抽取器,
  否则评测的是 recall 而非端到端盲区。
- **query 组**:10 组,每组 = 一个正向命中 query(验证 recall 本身工作)+
  ≥1 个盲区 query(同义 / 省称 / 改写),标 expected fact value。
- **hit@k(k=3/5)**:expected value ∈ recall(query) 的前 k 条 → 命中。
  正向组应高命中(否则 recall 坏);盲区组低命中即基线盲区量化。

命中的 ``pytest`` 只是跑通(评测集正确隔离 + 断言不爆);命中率打印到
stdout,P3 regression 与 v2 后对照看数字升降。

Acceptance cmd: ``cd services/memory-service && python -m pytest tests/eval_recall.py -q``.
"""

from __future__ import annotations

import os
import sys

import pytest

# Make the service package importable as top-level modules (cli, db, store, ...)
# regardless of pytest's invocation cwd. Mirrors test_e2e.py.
_SRV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SRV_DIR not in sys.path:
    sys.path.insert(0, _SRV_DIR)

import cli  # noqa: E402
import db  # noqa: E402


# ── fixture: per-eval isolated KG ──────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    """Per-eval isolated SQLite; resets db's cached connection (cf. test_e2e)."""
    db_path = tmp_path / "memory.db"
    db.init(str(db_path))
    yield db_path


# ── eval dataset: 30-50 中文 fact,三类各 ~15(ADR-9) ──────────────────
# 每条 (label, ingest_text, expected_value_substring):
#   ingest 经 cli.ingest(真实抽取链);expected_value_substring 是该 ingest
#   应产出的 fact.value 的可识别子串,query 组用它定位命中。
# 三类: tech(技术词) / mix(中英混排) / bare(纯中文裸句,无 Latin 锚)。

_FACTS_TECH: list[tuple[str, str, str]] = [
    # 技术词:英文标识符为主,value 含技术名词
    ("tech-rust",       "用户使用 rust 进行开发",            "rust"),
    ("tech-pydantic",   "FastAPI uses Pydantic.",            "Pydantic"),
    ("tech-postgres",   "系统依赖 PostgreSQL 数据库",        "PostgreSQL"),
    ("tech-redis",      "服务使用 Redis 做缓存",             "Redis"),
    ("tech-docker",     "应用部署在 Docker 容器中",          "Docker"),
    ("tech-nginx",      "网关基于 Nginx 转发",               "Nginx"),
    ("tech-kafka",      "消息队列采用 Kafka",                "Kafka"),
    ("tech-grpc",       "内部通信使用 gRPC 协议",            "gRPC"),
    ("tech-pandas",     "数据处理用 Pandas 库",              "Pandas"),
    ("tech-tensorflow", "模型训练依赖 TensorFlow 框架",      "TensorFlow"),
    ("tech-redis-cli",  "运维通过 redis-cli 操作",           "redis-cli"),
    ("tech-jwt",        "认证基于 JWT 令牌",                 "JWT"),
    ("tech-graphql",    "接口采用 GraphQL 规范",             "GraphQL"),
    ("tech-prometheus", "监控使用 Prometheus 采集",          "Prometheus"),
    ("tech-elasticsearch", "日志写入 Elasticsearch 集群",    "Elasticsearch"),
]

_FACTS_MIX: list[tuple[str, str, str]] = [
    # 中英混排:中文锚 + 英文标识符 / CJK 引号术语
    ("mix-logseq",     "Logseq 是笔记工具",                 "笔记工具"),
    ("mix-fastapi-fw", "FastAPI 是框架",                    "框架"),
    ("mix-py-ver",     "项目使用 Python 3.11",              "Python"),
    ("mix-git",        "代码托管在 GitHub 上",              "GitHub"),
    ("mix-linux",      "服务器运行 Linux 系统",             "Linux"),
    ("mix-vscode",     "编辑器是 VS Code",                  "VS"),  # value 含 "VS Code"
    ("mix-mac",        "开发机是 macOS",                    "macOS"),
    ("mix-rust-lang",  "rust 是开发语言",                   "开发语言"),
    ("mix-api-rest",   "接口风格属于 RESTful",              "RESTful"),
    ("mix-book",       "《重构》是经典书籍",                "重构"),
    ("mix-bracket",    "「设计模式」是必读",                "设计模式"),
    ("mix-corner",     "『整洁代码』推荐阅读",              "整洁代码"),
    ("mix-ci",         "CI 使用 GitHub Actions",            "GitHub"),
    ("mix-cloud",      "部署在 AWS 云上",                   "AWS"),
    ("mix-db-pg",      "数据库选用 PostgreSQL",             "PostgreSQL"),
]

_FACTS_BARE: list[tuple[str, str, str]] = [
    # 纯中文裸句:无 Latin 锚,经 CJK 谓词(是/使用/包含/依赖)抽取
    ("bare-kg-entity",   "知识图谱包含实体",                 "实体"),
    ("bare-algo-sort",   "算法包含排序步骤",                 "排序步骤"),
    ("bare-sys-cache",   "系统使用缓存加速",                 "缓存"),
    ("bare-team-scrum",  "团队采用敏捷开发",                 "敏捷开发"),
    ("bare-data-lake",   "数据中台包含数据湖",               "数据湖"),
    ("bare-ui-mvc",      "前端属于视图层",                   "视图层"),
    ("bare-test-unit",   "测试包含单元测试",                 "单元测试"),
    ("bare-sec-auth",    "安全依赖身份认证",                 "身份认证"),
    ("bare-ml-model",    "模型是深度网络",                   "深度网络"),
    ("bare-net-tcp",     "协议属于传输层",                   "传输层"),
    ("bare-os-kernel",   "内核包含调度器",                   "调度器"),
    ("bare-db-index",    "索引是数据结构",                   "数据结构"),
    ("bare-dev-cicd",    "流程包含持续集成",                 "持续集成"),
    ("bare-arch-ms",     "架构属于微服务",                   "微服务"),
    ("bare-cloud-k8s",   "集群使用容器编排",                 "容器编排"),
]

ALL_FACTS: list[tuple[str, str, str]] = _FACTS_TECH + _FACTS_MIX + _FACTS_BARE
# 45 fact ∈ [30, 50],三类各 15。ADR-9 约束达成。


# ── query 组:10 组,每组 = 正向(literal hit)+ 盲区(同义/省称/改写) ──
# (group, query, expected_value, kind)
#   expected_value: recall(query) 前命中应含此 value 的 fact。
#   kind: "positive"(literal,应命中,验证 recall 工作) /
#         "synonym" / "abbr" / "rewrite"(盲区,基线预期低命中)。
#
# 盲区设计的核心 = query token 与 fact.value 无字面子串重叠。

QUERY_GROUPS: list[tuple[str, str, str, str]] = [
    # 1. rust 同义:rust ↔ 铁锈 / rusty
    ("g1", "rust",        "rust",        "positive"),
    ("g1", "铁锈",        "rust",        "synonym"),
    ("g1", "rusty",       "rust",        "synonym"),
    # 2. rust 改写:开发语言
    ("g2", "开发语言",    "开发语言",    "positive"),   # mix-rust-lang value=开发语言
    ("g2", "编程语言",    "开发语言",    "rewrite"),
    ("g2", "程序设计语言", "开发语言",   "rewrite"),
    # 3. Pydantic 同义/省称
    ("g3", "Pydantic",    "Pydantic",    "positive"),
    ("g3", "pydantic",    "Pydantic",    "positive"),  # 大小写(case-fold 命中)
    ("g3", "验证库",      "Pydantic",    "synonym"),
    # 4. PostgreSQL 省称
    ("g4", "PostgreSQL",  "PostgreSQL",  "positive"),
    ("g4", "PgSQL",       "PostgreSQL",  "abbr"),
    ("g4", "postgres",    "PostgreSQL",  "abbr"),
    # 5. Logseq 笔记 同义
    ("g5", "笔记工具",    "笔记工具",    "positive"),
    ("g5", "日志工具",    "笔记工具",    "rewrite"),
    ("g5", "知识管理",    "笔记工具",    "synonym"),
    # 6. Redis 缓存 同义
    ("g6", "Redis",       "Redis",       "positive"),
    ("g6", "缓存数据库",  "Redis",       "synonym"),
    ("g6", "内存数据库",  "Redis",       "synonym"),
    # 7. Docker 容器 改写
    ("g7", "Docker",      "Docker",      "positive"),
    ("g7", "集装箱",      "Docker",      "rewrite"),   # 容器↔集装箱
    ("g7", "容器引擎",    "Docker",      "rewrite"),
    # 8. JWT 令牌 同义/省称
    ("g8", "JWT",         "JWT",         "positive"),
    ("g8", "json web token", "JWT",      "abbr"),
    ("g8", "令牌",        "JWT",         "synonym"),
    # 9. Kafka 消息队列 同义
    ("g9", "Kafka",       "Kafka",       "positive"),
    ("g9", "消息中间件",  "Kafka",       "synonym"),
    ("g9", "事件流",      "Kafka",       "rewrite"),
    # 10. 知识图谱 纯中文盲区(无 Latin 锚)
    ("g10", "实体",       "实体",        "positive"),  # bare-kg-entity value=实体
    ("g10", "节点",       "实体",        "synonym"),
    ("g10", "图谱元素",   "实体",        "rewrite"),
]


# ── hit@k 评测核心 ──────────────────────────────────────────────────────

def _hit_at_k(hits: list[dict], expected_value: str, k: int) -> bool:
    """expected_value ∈ top-k recall hits(by ``value`` field)→ 命中。

    ADR-9 hit@k:expected fact 的 value 出现在 recall 结果前 k 条。
    """
    top = hits[: max(0, k)]
    return any(h.get("value") == expected_value for h in top)


def _seed_kg() -> None:
    """Ingest 全部 fact 经真实抽取链(cli.ingest)。"""
    for _label, text, _val in ALL_FACTS:
        cli.ingest(text)


def _eval() -> dict:
    """跑全部 query 组,返回 hit@3 / hit@5 按 kind 分桶 + 总览。

    调用方负责 db.init()(测试用 fresh_db fixture;手动跑见 __main__)。

    返回结构:
        {"by_kind": {kind: {"total": n, "hit@3": n, "hit@5": n}}, "overall": {...}}
    """
    _seed_kg()
    kinds = ("positive", "synonym", "abbr", "rewrite")
    by_kind = {k: {"total": 0, "hit@3": 0, "hit@5": 0} for k in kinds}
    overall = {"total": 0, "hit@3": 0, "hit@5": 0}

    for _grp, query, expected, kind in QUERY_GROUPS:
        hits = cli.recall(query)
        h3 = _hit_at_k(hits, expected, 3)
        h5 = _hit_at_k(hits, expected, 5)
        overall["total"] += 1
        overall["hit@3"] += int(h3)
        overall["hit@5"] += int(h5)
        if kind in by_kind:
            by_kind[kind]["total"] += 1
            by_kind[kind]["hit@3"] += int(h3)
            by_kind[kind]["hit@5"] += int(h5)

    return {"by_kind": by_kind, "overall": overall}


def _pct(n: int, d: int) -> str:
    return f"{(n / d * 100):.1f}%" if d else "n/a"


# ── pytest entry:跑通即 exit=0;命中率打印 + 关键断言 ─────────────────

def test_eval_recall_baseline(fresh_db, capsys):
    """ADR-9 基线:量化 v1 中文召回盲区。

    断言(评测集自身正确性,非命中率阈值):
    - 评测集规模合规:fact ∈ [30,50],三类各 ~15;query 组 = 10。
    - 正向组命中率 > 0:证明 recall 本身工作(否则全 0 是 recall 坏,
      不是盲区)。
    - 盲区组量化:同义/省称/改写命中率 < 正向组(盲区存在性确认)。

    命中率明细打印到 stdout,P3 与 v2 对照看升降。
    """
    # ── 评测集规模合规(ADR-9 约束)──
    assert 30 <= len(ALL_FACTS) <= 50, len(ALL_FACTS)
    n_tech, n_mix, n_bare = len(_FACTS_TECH), len(_FACTS_MIX), len(_FACTS_BARE)
    # 各 ~15(容忍 ±3,三类覆盖性而非精确计数是 ADR-9 意图)
    assert abs(n_tech - 15) <= 3 and abs(n_mix - 15) <= 3 and abs(n_bare - 15) <= 3
    groups = {g for g, *_ in QUERY_GROUPS}
    assert len(groups) == 10, f"expected 10 query groups, got {len(groups)}"

    # ── 跑评测 ──
    result = _eval()
    bk = result["by_kind"]
    ov = result["overall"]

    # ── 正向组必须命中(验证 recall 工作)──
    pos = bk["positive"]
    assert pos["total"] > 0, "no positive queries — eval set broken"
    pos_hit5_rate = pos["hit@5"] / pos["total"]
    assert pos_hit5_rate > 0.0, (
        f"positive queries all miss — recall pipeline broken, not a blind spot: {pos}"
    )

    # ── 盲区组量化(ADR-9:中文同义/省称/改写 < 30% 预期)──
    blind_total = sum(bk[k]["total"] for k in ("synonym", "abbr", "rewrite"))
    blind_hit5 = sum(bk[k]["hit@5"] for k in ("synonym", "abbr", "rewrite"))
    blind_rate = blind_hit5 / blind_total if blind_total else 0.0
    # 盲区命中率必须显著低于正向(grill 实证:盲区全 miss)。宽松上界避免
    # 评测集与抽取器偶发重叠误判为"无盲区";下界保护 recall 真坏时正向也 0。
    assert blind_rate < pos_hit5_rate, (
        f"blind-spot rate {blind_rate:.2%} >= positive {pos_hit5_rate:.2%} — "
        "blind spot not demonstrated (eval set may overlap extractor lexicon)"
    )

    # ── 打印基线表(P3 / v2 对照锚)──
    with capsys.disabled():
        print("\n" + "=" * 64)
        print("ADR-9 recall baseline (v1) — hit@k 命中率")
        print("=" * 64)
        print(f"facts: {len(ALL_FACTS)} (tech={n_tech} mix={n_mix} bare={n_bare})")
        print(f"queries: {ov['total']} (10 groups)")
        print("-" * 64)
        print(f"{'kind':<12} {'total':>6} {'hit@3':>6} {'hit@5':>6} {'rate@5':>8}")
        for k in ("positive", "synonym", "abbr", "rewrite"):
            r = bk[k]
            print(f"{k:<12} {r['total']:>6} {r['hit@3']:>6} {r['hit@5']:>6} {_pct(r['hit@5'], r['total']):>8}")
        print("-" * 64)
        print(f"{'OVERALL':<12} {ov['total']:>6} {ov['hit@3']:>6} {ov['hit@5']:>6} {_pct(ov['hit@5'], ov['total']):>8}")
        print(f"blind(syn+abbr+rewrite) hit@5: {blind_hit5}/{blind_total} = {blind_rate:.1%}")
        print("=" * 64)


if __name__ == "__main__":
    # 直接运行:初始化默认 db 跑评测(不入 pytest)。供手动对照 v2。
    db.init()
    _r = _eval(None)  # fresh_db=None 时用默认 db;_seed_kg 已 init
