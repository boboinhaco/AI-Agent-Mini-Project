# all_total_vue — 운영 인시던트 대응 에이전트 (Vue 프론트엔드 버전)

`all_total`과 백엔드(server.py, agent/, mcp_server.py 등)는 완전히 동일하고, 브라우저
채팅 화면(`web_chat.html`)만 바닐라 JS 대신 **Vue 3**(CDN 글로벌 빌드, 별도 빌드 단계
없음)로 재작성한 버전이다. 나머지 설명은 원본 `all_total`과 같다.

Wave 1~15가 각각 증명한 패턴(싱글턴·MCP·SSE·라우팅·게이트·재계획·검증·재시도·병렬·비용분리·
세션·주입·버전 토글·가드)을 전부 조립한 **실제 동작하는 에이전트 서비스**다. LLM 자리는
`fake`(결정론)/`ollama`(로컬 실모델)/`openai`(실제 API) 세 백엔드 중 아무거나 꽂을 수 있고,
요청 한 건이 처리되는 전 과정은 LangSmith에 계층 트리로 기록·평가·피드백된다.

## 주요 실행 방법

**맨 처음 한 번만**: 이 폴더를 처음 받았다면(학생 배포 등) `.venv`부터 만든다.

```bash
bash sh/setup_venv.sh
```

| 방법 | 명령 | 비용 | 용도 |
|---|---|---|---|
| Python 가상환경 준비 | `bash sh/setup_venv.sh` | 없음 | `.venv` 생성 + `requirements.txt` 설치(최초 1회, 이미 있으면 설치만 재확인) |
| 로컬 Ollama 환경 준비 | `bash sh/setup_ollama.sh` | 없음(로컬 추론) | 모델(`qwen3:0.6b`/`8b`)이 이미 있으면 재사용, 없으면 새로 pull — `OLLAMA_FORCE_REFRESH=1`을 주면 지우고 재구성 |
| 에이전트만 기동 (로컬) | `bash sh/run_agent_ollama.sh` | 없음 | 서버만 띄워두고 직접 curl/웹으로 테스트 |
| 에이전트만 기동 (OpenAI) | `bash sh/run_agent_openai.sh` | **과금** | 위와 동일하되 실제 API 사용 (y/N 확인) |
| 브라우저 채팅 화면 | `bash sh/run_frontend.sh` (서버 기동 후) | 백엔드에 따름 | `http://localhost:8600`에서 실제 채팅 UI로 사용 |

`run_agent_*.sh`/`run_frontend.sh`는 `.venv`가 이미 있다는 가정 하에 PATH에
꽂아 쓰기만 한다(`.venv`가 없으면 시스템 `python3`로 조용히 떨어져 패키지
누락 오류가 날 수 있다) — `setup_venv.sh`를 먼저 돌려야 하는 이유다.

모든 스크립트는 `.env`가 없으면 `cp .env.example .env` 후 본인 API 키를 채우라는 안내를
출력하고 종료하며(외부 경로에 의존하지 않음 — 이 폴더만 떼어가도 그대로 동작),
LangSmith 프로젝트를 백엔드별로 분리해 기록한다(`capstone-all-total[-ollama|-openai]`).

> 더 쉬운 설명과 실제 캡처 화면이 필요하면 [manual_ollma.md](manual_ollma.md)(로컬
> 실모델) · [manual_openai.md](manual_openai.md)(OpenAI API)를 참고.

## 아키텍처 구성도

![아키텍처](images/architecture.svg)

(선은 90°로만 꺾이고 도형·선이 겹치지 않도록 좌표를 계산해 그린 SVG — 생성 스크립트 기준)

- **싱글턴**: `server.py`의 `lifespan`에서 `SupervisorDeps`(LLM·세션·MCP 연결)를 정확히
  1회 생성해 `app.state`로 공유한다 — 요청마다 재생성하지 않는다.
- **백엔드 교체**: `LLM_BACKEND` 환경변수 하나로 `fake_llm.py`/`ollama_llm.py`/
  `openai_llm.py`가 바뀌어 꽂히고, 그래프·MCP·세션 코드는 전혀 바뀌지 않는다.
- **버전 토글**: `SUPERVISOR_VERSION=v1`이면 라우팅·게이트·재계획·검증이 없는 legacy
  선형 파이프라인(`supervisor_v1.py`)으로, 재배포 없이 환경변수만으로 전환된다.
- **MCP는 별도 프로세스**: `mcp_server.py`(ops)가 stdio subprocess로 떠서 서버 수명 동안
  재사용되며, 모니터링 조회(Tool)·대응 매뉴얼(Resource)·진단 절차(Prompt) 세 가지
  capability를 모두 실사용한다.
- **MCP 멀티 서버 탐색**: `mcp_client.py`는 `ops`(mcp_server.py, 실사용)·`docs`
  (mcp_server_docs.py, 내부 API 문서 Resource만 제공하는 decoy) 두 서버에 동시
  연결한다. `plan`/`research` 노드는 어느 서버가 필요한 prompt/resource를 들고
  있는지 코드에 하드코딩하지 않고, `find_prompt()`/`find_resource_template()`로
  연결된 서버들을 먼저 조회한 뒤에야 그 서버로 요청을 보낸다.
- **분석 단계 분리**: `replan_check`가 조사 결과가 충분하다고 판단하면 항상
  `analysis` 노드로 넘어가지만, gate가 살려둔 `agent=="analysis"` task가 없으면
  `analysis_node`가 내부적으로 스킵하고 바로 `write`로 넘어간다 — `needs_analysis`가
  실제로 유효한 경우에만 수집된 모니터링 결과들을 비교 분석한다.

## 주요 유스케이스별 고수준 시퀀스 다이어그램

### 1. 가벼운 대화

```mermaid
sequenceDiagram
    participant U as 사용자
    participant S as server.py
    participant G as graph (route)

    U->>S: POST /chat ("안녕하세요, 제 이름은 ...")
    S->>G: route 노드 실행
    G-->>S: route=chat (무거운 파이프라인 건너뜀)
    S-->>U: 바로 답변, task_count=0
```

### 2. 장애 진단 요청

```mermaid
sequenceDiagram
    participant U as 사용자
    participant G as graph
    participant M as MCP 서버군 (ops+docs)

    U->>G: POST /chat ("OO 서비스가 이상해요")
    G->>M: plan — find_prompt로 서버 탐색 후 절차 프롬프트 요청, 할 일 목록 수립
    G->>G: gate — 불필요한 작업 제거
    G->>M: research — find_resource_template로 서버 탐색 후 모니터링 조회(Tool) + 매뉴얼 조회(Resource)
    M-->>G: 실측 수치 + runbook 본문
    G->>G: replan_check — 부족하면 조사 추가
    G->>G: analysis — needs_analysis일 때만 수집 결과 비교 분석
    G->>G: write → validate (형식 미달 시 반려·재작성)
    G-->>U: 근거 기반 진단 결과
```

### 3. 실시간 진행 상황 스트리밍

```mermaid
sequenceDiagram
    participant U as 사용자
    participant S as server.py (/chat/stream)
    participant G as graph

    U->>S: GET /chat/stream (SSE)
    loop 노드가 끝날 때마다
        G-->>S: 노드 이벤트 (RouteDecided, PlanCreated, ...)
        S-->>U: data: {event}
    end
    G-->>S: FinalAnswer
    S-->>U: data: {reply} → data: [DONE]
```

### 4. 서버 재시작 후 대화 기억

```mermaid
sequenceDiagram
    participant U as 사용자
    participant S1 as server.py (프로세스 A)
    participant DB as session.db (SQLite)
    participant S2 as server.py (프로세스 B, 재시작 후)

    U->>S1: "제 이름은 OOO입니다"
    S1->>DB: 대화 이력 저장
    S1--xS1: 프로세스 종료
    S2->>S2: lifespan — 완전히 새로 기동
    U->>S2: "제 이름이 뭐였죠?"
    S2->>DB: 이력 조회
    DB-->>S2: 이전 대화 그대로
    S2-->>U: 이름을 정확히 기억해 답변
```

### 5. 웹 채팅 화면 사용

```mermaid
sequenceDiagram
    participant B as 브라우저 (web_chat.html)
    participant P as web_demo_server.py (:8600)
    participant S as server.py (:8100)

    B->>P: GET / (정적 페이지)
    B->>P: GET /chat/stream?message=... (same-origin, CORS 불필요)
    P->>S: GET /chat/stream (그대로 중계)
    S-->>P: SSE 이벤트
    P-->>B: SSE 이벤트 (그대로 relay)
    B->>B: 말풍선 + 진행 상태(⏳) 렌더링
```

## 소스 디렉토리 구조

```
all_total_vue/
├── server.py              # FastAPI 진입점 — lifespan 싱글턴, /health·/version·/chat·/chat/stream
├── mcp_server.py           # fastmcp 서버("ops") — Tool/Resource/Prompt (실사용, stdio subprocess)
├── mcp_server_docs.py       # fastmcp 서버("docs") — API 문서 Resource만 제공하는 decoy (멀티 서버 탐색 데모용)
├── web_chat.html            # 브라우저 채팅 화면 (정적 1파일)
├── web_demo_server.py       # web_chat.html 서빙 + /chat·/chat/stream·/health 프록시(:8600)
├── requirements.txt
├── .env.example
│
├── agent/
│   ├── deps.py              # SupervisorDeps 의존성 주입
│   ├── graph.py              # v2 LangGraph StateGraph (route/plan/gate/research/replan_check/analysis/write/validate/finalize)
│   ├── supervisor_v1.py      # legacy 선형 파이프라인 (버전 토글 대상)
│   ├── fake_llm.py           # LLM_BACKEND=fake — 결정론적 fast/quality 모델 (route/plan/analyze/chat/write)
│   ├── ollama_llm.py         # LLM_BACKEND=ollama — 로컬 실모델 백엔드
│   ├── openai_llm.py         # LLM_BACKEND=openai — 실제 OpenAI API 백엔드
│   ├── mcp_client.py          # MCP 멀티 서버(ops+docs) stdio 클라이언트 — find_resource_template/find_prompt로 서버 탐색 + 의도적 flaky 1회(재시도 데모)
│   ├── retry.py              # Transient 전용 backoff 재시도
│   └── session_store.py       # SQLite 세션 저장소
│
├── data/
│   ├── monitoring.json        # MCP Tool이 조회하는 모니터링 데이터
│   └── runbooks/               # MCP Resource가 조회하는 대응 매뉴얼 6종
│       ├── db-down.md
│       ├── cpu-spike.md
│       ├── disk-full.md
│       ├── high-latency.md
│       ├── memory-leak.md
│       └── network-timeout.md
│
├── sh/                        # 실행 스크립트 모음 (학생 배포용 — 내부 회귀 테스트 스크립트는 제외)
│   ├── setup_venv.sh            # .venv 생성 + requirements.txt 설치 (맨 처음 1회)
│   ├── setup_ollama.sh          # 로컬 Ollama 모델 준비 — 있으면 재사용, 없으면 pull (리셋은 OLLAMA_FORCE_REFRESH=1)
│   ├── run_agent_ollama.sh       # 에이전트만 기동 — 로컬 실모델
│   ├── run_agent_openai.sh      # 에이전트만 기동 — OpenAI API (과금)
│   ├── run_frontend.sh          # 브라우저 채팅 화면(web_demo_server.py) 기동
│
├── doc/                       # 문서
│   ├── README.md                # 이 문서
│   ├── manual_ollma.md          # 사용자 가이드 — 로컬 실모델 기준
│   ├── manual_openai.md         # 사용자 가이드 — OpenAI API 기준
│   ├── regression_test_queries.md  # 시스템명 하드코딩 수정 검증용 회귀 테스트 쿼리 목록
│   ├── USER_GUIDE.md            # 운영자 빠른 안내 (1쪽)
│   ├── REVIEW.md                # 두 관점 검토·정리 내역
│   └── images/                  # 아키텍처·그래프 SVG, 수치 캡처
```

---

## Mini-project 개선 내역 (1차 — 기본 기능 개선)

Before 기준선은 [`before/README.md`](../before/README.md), After 측정은 `after/`에 같은 스크립트
(`eval/measure.py`)로 기록했다.

| # | 영역 | 무엇을 바꿨나 | 파일 |
|---|---|---|---|
| 1 | 모듈화 | 338줄짜리 `graph.py`를 노드별 모듈로 분리. `graph.py`는 조립(엣지)만 담당 | `agent/nodes/*.py`, `agent/state.py` |
| 2 | RAG | 키워드 9줄 규칙 + db-down 폴백으로 고르던 incident_type을 runbook 벡터 인덱스(InMemoryVectorStore) 의미 검색으로 교체. 임베딩은 백엔드별(fake=해싱, ollama=nomic-embed-text, openai=text-embedding-3-small) | `agent/rag.py`, `agent/nodes/replan.py`, `data/runbooks/*.md`(증상 섹션 추가) |
| 3 | 라우팅 | "느려요/타임아웃/메모리/디스크" 등 확장 패턴 + 알려진 시스템명 언급 시 incident로 코드 교정 (fake 회귀 10건 중 9건 오분류 → 0건) | `agent/state.py`, `agent/nodes/route.py` |
| 4 | 검증 강화 | 길이·번호 검사에 더해 ① 조사한 시스템 전부 언급 ② CPU 수치 인용 ③ 조사·질문에 없는 시스템명 금지. 반려 사유를 write에 피드백 | `agent/nodes/validate.py` |
| 5 | 안정성 | Ollama/OpenAI 호출을 동기 `httpx.post` → 비동기로 바꿔 한 요청이 서버 전체를 막던 문제 해결. 소형 모델 타임아웃(기본 90s) 시 코드 레벨 계획으로 복구 (billing-service 500 오류 해결) | `agent/ollama_llm.py`, `agent/openai_llm.py` |
| 6 | fake 계획 | 목록 밖 시스템(billing-service)을 조사 대상으로 삼던 정규식을 KNOWN_SYSTEMS 클램프로 교체, 분석 요청 시 needs_analysis=True | `agent/fake_llm.py` |

측정(회귀 쿼리 10개, 정확도 체크 통과 수): fake 7/32 → 32/32.

## Mini-project 추가 기능 (2차 — 기능 추가)

| # | 영역 | 무엇을 추가했나 | 파일 |
|---|---|---|---|
| 1 | 멀티 에이전트 | 단일 `research_node`를 **compiled subgraph**로 교체. `dispatch → (metrics_agent ∥ log_agent ∥ runbook_agent) → merge`. 세 서브에이전트는 같은 superstep에서 동시에 돌고 `Annotated[list, add]` 채널에 결과만 추가하며, merge가 task 상태·재시도·소요시간을 한 번에 정리한다(supervisor-of-supervisors). log_agent용으로 MCP Tool `get_recent_logs`와 `data/logs.json`을 추가 | `agent/subgraphs/research.py`, `mcp_server.py`, `data/logs.json` |
| 2 | Human-in-the-loop | `write → approval → validate`. 초안에 재시작·롤백·failover·스케일 아웃·LB 제외·볼륨 확장·삭제 같은 조치가 있으면 `interrupt()`로 멈추고 `ApprovalRequired`를 보낸다. 승인 → 검수 진행, 거절 → 거절 사유를 피드백으로 write에 되돌려 재작성, 2회 거절 후에도 같은 조치가 나오면 "실행 금지" 경고를 달아 검수로 진행(무한 루프 방지). 체크포인터는 MemorySaver, run마다 고유 thread id | `agent/nodes/approval.py`, `agent/graph.py`, `server.py`(`/chat/approve`, `/chat/approve/stream`), `web_chat.html`(승인/거절 카드) |
| 3 | 대화 요약 메모리 | 요약되지 않은 메시지가 10개를 넘으면 최근 4개만 원문으로 남기고 나머지를 `fast_llm.summarize()`로 요약해 `summaries` 테이블에 누적 저장. `session_store.load()`는 `[요약(system)] + 최근 원문`만 돌려준다. `/session/{thread_id}`로 원문 수·요약된 수·LLM에 실리는 이력 크기를 확인 | `agent/memory.py`, `agent/session_store.py`, `agent/*_llm.py`(`summarize`), `eval/memory_check.py` |

### 그래프 (2차 이후)

![개선 후 그래프](images/graph_after.svg)

개선 전 그래프: ![개선 전 그래프](images/graph_before.svg)

### 한계·트레이드오프

- **서브그래프**: 노드 수가 늘어 LangSmith 트리가 깊어지고, 에이전트 3개가 각각 MCP를 호출해 호출 수가 늘었다(로그 조회는 모니터링 대상마다 1회 추가). 병렬이라 벽시계 시간은 늘지 않았다(fake 기준 0.36s 유지).
- **HITL**: 위험 조치 감지는 정규식이라 "재시작은 하지 마세요" 같은 부정문도 승인을 요구한다(오탐). interrupt 상태는 MemorySaver에만 있어 서버 재시작 시 승인 대기 중인 run은 사라진다. 승인 대기 중 같은 thread로 새 질문을 보내면 이전 승인 요청은 폐기된다.
- **요약 메모리**: 요약은 손실 압축이다 — fake 백엔드는 추출 요약(이름·질문 앞부분)만 남기고, 실모델 요약은 비결정적이라 "이름을 정확히 보존하라"는 지시에 의존한다. 요약 LLM 호출 비용이 10메시지마다 1회 추가된다.

## Mini-project 남은 과제 처리 (3차)

| # | 과제 | 무엇을 했나 | 파일 |
|---|---|---|---|
| 1 | 두 시스템 언급 시 첫 번째만 조사 | (원인 차단) plan 정규화에서 쿼리에 언급된 KNOWN_SYSTEMS 중 모델이 빠뜨린 것을 task로 보충. (안전망) replan_check가 "언급됐지만 조사도 계획도 안 된 시스템"을 gap task로 추가(최대 2라운드) | `agent/ollama_llm.py`, `agent/openai_llm.py`, `agent/nodes/replan.py` |
| 2 | UI 진행 표시가 ⏳로 남음 | 단계 항목을 `{text, done}` 객체로 바꾸고, 다음 단계가 오거나 FinalAnswer/ApprovalRequired가 오면 이전 단계를 ✓로 표시 | `web_chat.html` |
| 3 | 단위 테스트 | pytest 32개: 라우팅·시스템명 헬퍼, RAG 분류(해싱 임베딩, 결정론), 검증 규칙·위험 조치 감지, 그래프 통합(fake LLM + FakeMCP로 v2 파이프라인 끝까지, HITL interrupt/resume, 재계획 보충), 요약 메모리·리포트 저장 | `tests/`, `pytest.ini` |
| 4 | LLM-as-judge 평가 | `eval/judge.py` — 측정 결과의 답변이 실측 수치(facts)를 정확히 인용했는지, 지어내지 않았는지, 조치가 구체적인지 3항목 0/1 채점. ollama/openai는 품질 모델이 JSON으로 채점, fake는 규칙(validate.run_checks)으로 결정론 채점. `/chat` 응답에 `facts`(모니터링 실측)를 포함시켜 채점 근거로 쓴다 | `eval/judge.py`, `server.py`, `eval/measure.py` |
| 5 | 인시던트 리포트 저장 | 장애 진단이 끝나면 타임라인·수집 사실·최종 답변을 Markdown 리포트로 `incidents` 테이블에 저장. `GET /incidents`, `GET /incidents/{id}?format=md`. 채팅 UI에 "📄 리포트 #id 열기" 링크 | `agent/report.py`, `agent/session_store.py`, `server.py`, `web_demo_server.py`, `web_chat.html` |

실행:

```bash
.venv/bin/python -m pytest -q                                   # 단위 테스트
.venv/bin/python eval/judge.py --metrics after/metrics_ollama.json --backend ollama --out after
curl -s localhost:8100/incidents | python3 -m json.tool --no-ensure-ascii
curl -s "localhost:8100/incidents/1?format=md"
```

한계: 재계획 보충은 KNOWN_SYSTEMS(monitoring.json)에 있는 이름만 잡는다(목록 밖 이름은 여전히 폴백). LLM-as-judge는
채점 모델 자체가 틀릴 수 있어 규칙 검사(validate)와 함께 봐야 하고, 실모델 채점은 쿼리당 10~20초가 더 든다.

## 사용성 수정 (4차 — 실사용 피드백 반영)

| 피드백 | 원인 | 수정 |
|---|---|---|
| 검수 반려 시 답변 말풍선이 두 개로 나옴, 반려 단계가 잘 안 보임 | 재작성(attempt 증가)마다 새 말풍선 생성 | 말풍선 하나에 내용 교체 + 상태 배지(✍️ 작성 중 / 🔍 검수 중 / ⚠️ 검수 반려 — 다시 작성). 반려 단계는 ⚠️ 강조. 최종 답변은 항상 맨 아래. 승인 후 재개 스트림은 이미 보낸 단계를 건너뜀 | `web_chat.html`, `server.py` |
| "넌 누구야 뭘 할 수 있어"에 일반 AI 답변 | 대화 프롬프트에 역할이 없음 | 대화 시스템 프롬프트에 "운영 인시던트 대응 에이전트" 정체성과 안내 문구. fake도 동일 | `agent/*_llm.py` |
| "난 홍길동이야"에 이전 장애 보고를 반복 | 소형 모델이 incident로 오분류, 시스템명 없으면 auth-service 폴백 | 라우팅 양방향 교정(`decide_route`): 장애 신호도 약한 신호도 없으면 chat. 이름 패턴 확장 | `agent/state.py`, `agent/nodes/route.py` |
| "질문 몇 개 했어"에 장애 보고가 나옴 / 대화 품질 낮음 | 위와 동일 + 대화를 0.6b가 담당 | ollama는 품질 모델(8b)이 대화 담당(`deps.chat_llm`), 이력 질문은 user 메시지를 세어 답하도록 지시 | `server.py`, `agent/nodes/route.py` |
| "디스크 공간이 부족하다고 알람이 왔어"에 auth-service 지연 답변 | 시스템명이 없으면 auth-service 고정 폴백 | **시스템 탐색**: 시스템명이 없으면 전체 시스템을 병렬 조회한 뒤 증상(RAG)과 맞는 시스템만 선별(`SystemDiscovery`→`SystemsScreened`). RAG는 임베딩 + 증상 어휘 겹침 하이브리드 재정렬(nomic의 한국어 약점 보완) | `agent/nodes/plan.py`, `agent/nodes/replan.py`, `agent/rag.py`, `agent/state.py` |
| 답변이 길고 느림 | 8b 모델이 하위 목록까지 길게 작성 | write 프롬프트에 "항목당 1~2문장, 전체 7문장 이내, 하위 목록 금지" | `agent/*_llm.py` |

실측(ollama): "넌 누구야 뭘 할 수 있어" → 역할 소개 · "난 홍길동이야" → 인사(chat) · "질문 몇 개 했어" → "user 메시지: 2개" ·
"디스크 공간이 부족하다고 알람이 왔어" → 6개 시스템 탐색 → log-service 선별(disk-full) → 디스크 runbook 기반 답변.


## 5차 — 두 관점 검토(시니어 개발자 / 사용자) 반영

검토 항목 전체와 상태는 [REVIEW.md](REVIEW.md). 핵심 변경:

| 영역 | 변경 | 파일 |
|---|---|---|
| 승인 흐름 | `write → validate → approval → finalize`로 순서 변경(검증 통과 초안만 승인). 승인·거절한 조치를 state에 기억해 재질문 없음. 거절된 조치가 재작성에 또 나오면 즉시 '실행 금지' 경고로 종료. "~은 안 돼요/금지" 같은 부정 문맥은 감지 제외 | `agent/nodes/approval.py`, `agent/graph.py` |
| 서버 수명주기 | 승인 대기 중 새 질문 → 대기 run 명시적 폐기(`PendingDiscarded`) + 체크포인트 삭제. interrupt 시 사용자 메시지 즉시 저장. run 종료마다 `adelete_thread`. SSE 예외는 `Error` 이벤트 + `[DONE]` 보장. `subgraphs=True`로 서브에이전트 이벤트 전달. FinalAnswer를 요약 압축보다 먼저 전송 | `server.py` |
| 답변 품질 | 코드가 만드는 📌 머리말(시스템·상태·수치·매뉴얼), 5항목 답변 틀(상태/원인/지금 할 일/담당팀 요청/근거), 용어 한글 병기, 반려 사유 운영자 문구, 최대 시도 도달 시 '참고용' 표시 | `agent/nodes/validate.py`, `agent/*_llm.py` |
| 업무 용어 별칭 | "결제/로그인/캐시/…" → 시스템명 매핑으로 시스템명을 몰라도 조사 | `agent/state.py`, `agent/plan_utils.py` |
| 측정 공정성 | `systems_ok`를 선별된 facts 기준(정확 일치 옵션), 과잉 조사 지표, git SHA 기록. fake plan의 web-01 자동 추가 제거 | `eval/measure.py`, `agent/fake_llm.py` |
| 정리 | MCP 전역 락 제거, 데모용 강제 타임아웃·인위 지연은 opt-in, plan 정규화 공용화(`plan_utils.py`), 죽은 코드·미사용 import 제거 | `agent/mcp_client.py`, `agent/subgraphs/research.py` |
| UI/문서 | 운영자 문구 단계 라벨, 승인 카드(실행 안 함 안내·사유 입력), 오류 말풍선·중단 표시·경과 시간, 지난 리포트 HTML, 운영자 빠른 안내 | `web_chat.html`, `doc/manual_ollma.md` |
| 테스트 | 49개 — 서버 계층(승인 1회, 거절→경고 종료, 대기 폐기, SSE 오류·서브에이전트 이벤트, 리포트 HTML), 별칭·부정 문맥 | `tests/test_server.py`, `tests/test_aliases_negation.py` |
