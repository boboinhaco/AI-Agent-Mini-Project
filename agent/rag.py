"""RAG — runbook 벡터 인덱스로 incident_type을 의미 기반으로 찾는다.

Before: replan_check가 모니터링 알림 문구를 9줄짜리 키워드 규칙("cpu"→cpu-spike …)으로
훑고, 아무것도 안 걸리면 db-down으로 폴백했다(실측: web-01 CPU 급증 → db-down 선택).
After : data/runbooks/*.md 본문을 임베딩해 InMemoryVectorStore에 넣어두고(메타데이터에
incident_type), "사용자 질의 + 수집된 상태/알림"을 쿼리로 가장 가까운 runbook을 고른다.

임베딩 백엔드는 LLM 백엔드와 같은 축으로 선택한다:
  fake   → HashEmbeddings   (문자 n-gram 해싱, 결정론·무네트워크 — 테스트/회귀용)
  ollama → OllamaEmbeddings (기본 nomic-embed-text, 로컬)
  openai → OpenAIEmbeddings (기본 text-embedding-3-small, 과금)
"""
import hashlib
import math
import os
import re
from pathlib import Path

import httpx
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langsmith import traceable

RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "data" / "runbooks"


class HashEmbeddings(Embeddings):
    """문자 2~3-gram 해싱 임베딩 — 모델 없이도 어휘 겹침을 벡터로 표현한다."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        t = re.sub(r"\s+", " ", text.lower())
        for n in (2, 3):
            for i in range(len(t) - n + 1):
                h = int(hashlib.md5(t[i:i + n].encode()).hexdigest(), 16) % self.dim
                vec[h] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OllamaEmbeddings(Embeddings):
    def __init__(self, model: str | None = None,
                 url: str = "http://127.0.0.1:11434/api/embed"):
        self.model = model or os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self.url = url

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post(self.url, json={"model": self.model, "input": texts}, timeout=120.0)
        r.raise_for_status()
        return r.json()["embeddings"]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class OpenAIEmbeddings(Embeddings):
    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        r = httpx.post("https://api.openai.com/v1/embeddings",
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": self.model, "input": texts}, timeout=60.0)
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _lexical_overlap(query: str, doc: str) -> int:
    """문서(제목+증상)의 어휘 조각이 질의에 몇 개 등장하는지 — 한국어는 조사 때문에 단어 전체가 아니라
    앞 2글자 이상 접두(디스크, 메모리, 타임아웃…)로 본다. 너무 흔한 조각은 제외."""
    stop = {"알림", "영문", "사용률", "초과", "증가", "상승", "실패", "확인", "대응", "절차", "runbook",
            "high", "low", "status", "threshold"}
    q = query.lower()
    tokens = set()
    for raw in re.split(r"[\s,./()\[\]:;·]+", doc.lower()):
        raw = raw.strip("-%0123456789")
        if len(raw) < 2 or raw in stop:
            continue
        tokens.add(raw[:3] if re.search(r"[가-힣]", raw) else raw)   # 한글은 앞 3글자, 영문은 전체
    score = sum(1 for t in tokens if t in q)
    # 쉼표로 나뉜 증상 구절("커넥션 풀 사용률", "디스크 공간 부족")이 통째로(앞 5글자) 질문에 있으면 가중 —
    # 단어 하나("커넥션")가 두 매뉴얼에 다 있어 동점이 나던 문제 해결
    for phrase in re.split(r"[,.]", doc.lower()):
        phrase = re.sub(r"\(.*?\)", "", phrase).strip()
        if len(phrase) >= 4 and phrase[:5] in q:
            score += 2
    return score


def _section(text: str, heading: str) -> str:
    """'## {heading}' 아래 다음 '##'까지의 본문."""
    lines, grab, out = text.splitlines(), False, []
    for l in lines:
        if l.startswith("## "):
            grab = l[3:].strip() == heading
            continue
        if grab and l.strip():
            out.append(l.strip())
    return " ".join(out)


def make_embeddings(backend: str) -> Embeddings:
    if backend == "ollama":
        return OllamaEmbeddings()
    if backend == "openai":
        return OpenAIEmbeddings()
    return HashEmbeddings()


class RunbookIndex:
    """runbook 디렉터리 → 벡터 인덱스. 문서 1개 = runbook 1개, metadata.incident_type."""

    def __init__(self, embeddings: Embeddings, runbook_dir: Path = RUNBOOK_DIR):
        self.embeddings = embeddings
        self.runbook_dir = runbook_dir
        self.store = InMemoryVectorStore(embeddings)
        self.size = 0

    def build(self) -> "RunbookIndex":
        docs = []
        for path in sorted(self.runbook_dir.glob("*.md")):
            text = path.read_text()
            title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")),
                         path.stem)
            # 임베딩 대상은 "제목 + 증상" — 절차 본문은 runbook끼리 어휘가 겹쳐(커넥션 풀,
            # 재시작, 모니터링 확인 …) 검색 노이즈가 되므로 metadata에만 보관한다.
            symptoms = _section(text, "증상")
            docs.append(Document(page_content=f"{title}\n{symptoms or text}",
                                 metadata={"incident_type": path.stem, "title": title,
                                           "full_text": text}))
        if docs:
            self.store.add_documents(docs)
        self.size = len(docs)
        return self

    @traceable(run_type="retriever", name="runbook_index.search")
    async def search(self, text: str, k: int = 3) -> list[dict]:
        hits = await self.store.asimilarity_search_with_score(text, k=k)
        return [{"incident_type": d.metadata["incident_type"], "title": d.metadata["title"],
                 "score": round(float(s), 4),
                 "lexical": _lexical_overlap(text, d.page_content)} for d, s in hits]

    async def classify(self, text: str) -> dict:
        """하이브리드: 임베딩 상위 k개를 '증상 어휘 겹침(lexical)'으로 재정렬한다.
        실측: nomic-embed-text는 한국어가 약해 "디스크 공간이 부족하다고 알람이 왔어"를 cpu-spike로
        골랐다 — runbook 증상 섹션의 "디스크 공간 부족" 같은 어휘 일치가 더 믿을 만하다."""
        hits = await self.search(text, k=self.size or 3)
        if not hits:
            return {"incident_type": "db-down", "title": "(fallback)", "score": 0.0}
        best = max(hits, key=lambda h: (h["lexical"], h["score"]))
        return best


def build_query_text(query: str, collected: list[dict]) -> str:
    """RAG 쿼리 = 사용자 질의 + 저하된 시스템의 상태/알림 문구. healthy 시스템의 '알림 0건'은
    노이즈라 제외한다(Before의 db-down 오폴백 원인)."""
    parts = [query]
    for c in collected:
        if c.get("source") != "monitoring":
            continue
        d = c["data"]
        if d.get("status") != "healthy" or d.get("alerts"):
            parts.append(f"{d.get('system_name')} {d.get('status')} " + " ".join(d.get("alerts", [])))
    return "\n".join(parts)
