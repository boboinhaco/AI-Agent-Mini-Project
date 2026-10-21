"""테스트 공용 픽스처 — 실제 LLM/MCP 없이 그래프 전체를 돌릴 수 있는 fake 의존성."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.deps import SupervisorDeps            # noqa: E402
from agent.fake_llm import FastLLM, QualityLLM    # noqa: E402
from agent.rag import HashEmbeddings, RunbookIndex  # noqa: E402
from agent.session_store import SqliteSessionStore  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


class FakeMCP:
    """mcp_server.py의 Tool/Resource/Prompt를 subprocess 없이 흉내 낸다 (결정론, jitter 없음)."""

    def __init__(self):
        import json
        self.monitoring = json.loads((DATA / "monitoring.json").read_text())
        self.logs = json.loads((DATA / "logs.json").read_text())
        self.calls: list[tuple] = []

    async def call_tool(self, name, arguments, server="ops"):
        self.calls.append((name, arguments.get("system_name")))
        sysname = arguments["system_name"]
        if name == "get_monitoring_status":
            d = self.monitoring.get(sysname)
            if d is None:
                return {"system_name": sysname, "status": "unknown", "cpu_percent": 0.0,
                        "memory_percent": 0.0, "alerts": [f"System '{sysname}' not found"]}
            return {"system_name": sysname, "status": d["status"], "cpu_percent": d["cpu_base"],
                    "memory_percent": d["memory_base"], "alerts": d.get("alerts", [])}
        if name == "get_recent_logs":
            return {"system_name": sysname, "lines": self.logs.get(sysname, [])[:arguments.get("limit", 5)]}
        raise ValueError(name)

    async def find_resource_template(self, template_name):
        return "ops", "runbook://incident-response/{incident_type}"

    async def read_resource(self, uri, server):
        incident_type = uri.rsplit("/", 1)[-1]
        path = DATA / "runbooks" / f"{incident_type}.md"
        return path.read_text() if path.exists() else f"# Runbook Not Found\n{incident_type}"

    async def find_prompt(self, name):
        return "ops"

    async def get_prompt(self, name, arguments, server="ops"):
        return (f"## 장애 정보\n- 시스템: {arguments['system_name']}\n- 증상: {arguments['symptom']}\n"
                "## 진단 절차\n1. get_monitoring_status 2. runbook 3. 종합")


@pytest.fixture
def deps(tmp_path):
    return SupervisorDeps(
        fast_llm=FastLLM("fast-stub", 0.00015),
        quality_llm=QualityLLM("quality-stub", 0.005),
        mcp=FakeMCP(),
        session_store=SqliteSessionStore(tmp_path / "test.db"),
        version="v2",
        rag=RunbookIndex(HashEmbeddings()).build(),
        hitl_enabled=False,   # 그래프 테스트는 승인 없이 끝까지 돌린다 (approval 전용 테스트는 따로)
    )


@pytest.fixture
def graph(deps):
    from agent.graph import build_graph
    return build_graph(deps)


def run_config(name: str) -> dict:
    return {"configurable": {"thread_id": f"test-{name}"}}
