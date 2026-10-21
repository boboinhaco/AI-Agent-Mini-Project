# After — 개선 후 측정 기록 (2026-10-20)

`before/`와 같은 스크립트(`eval/measure.py`, `eval/screenshot.py`, `eval/memory_check.py`)로 같은
입력을 돌린 결과다. 1차(기본 기능 개선)와 2차(기능 추가) 커밋 이후 각각 측정했다.

## Before / After 요약 (회귀 쿼리 10개)

| 지표 | Before | After 1차 (개선) | After 2차 (추가) |
|---|---|---|---|
| fake — 정확도 체크 | 7 / 32 (22%) | 32 / 32 | 32 / 32 |
| fake — 총 지연 | 1.3s (9건이 chat으로 오분류돼 파이프라인을 안 탐) | 6.6s | 6.2s |
| ollama — 정확도 체크 | 24 / 29 (83%) | 29 / 32 (91%) | 29 / 32 (91%) |
| ollama — 서버 오류 | 2건 (q8 500, q9 연결 끊김) | 0건 | 0건 |
| ollama — 총 지연 | 292s (8건 완료 기준) | 588s | 575s |
| ollama — cost_usd(가상) | 0.0400 | 0.0798 | 0.0841 |
| ollama — 검증 반려→재작성 | 0건 (검사가 길이뿐) | 3건 | 3건 |
| ollama — HITL 승인 요청 | — | — | 8회 (자동 승인) |
| 서브에이전트 병렬 소요 (fake) | 0.36s | 0.36s | 0.36s (3 에이전트 동시) |

지연·비용이 늘어난 이유: (1) 검증이 실제로 반려해 재작성이 일어난다(Before는 어떤 답도 1회 통과),
(2) billing-service처럼 소형 모델이 90초 무응답인 경우를 기다렸다가 복구한다(Before는 300초 후 500),
(3) 2차에서 log_agent가 시스템마다 로그 조회를 추가한다. 정확도·안정성과 맞바꾼 비용이다.

## 항목별 Before → After

| Before 문제 (`before/README.md`) | After |
|---|---|
| fake 라우터가 "느려요/타임아웃/메모리/디스크" 9건을 chat으로 오분류 | 확장 패턴 + 시스템명 언급 교정 → 10/10 올바른 route |
| web-01 CPU 급증 → `db-down` runbook 선택 (키워드 폴백) | RAG(runbook 벡터 검색) → `cpu-spike`. 단독 분류 테스트 8/8 (해싱·nomic 임베딩 모두) |
| billing-service → plan 단계 300초 타임아웃 → 500 | 90초 타임아웃 후 코드 레벨 계획으로 복구, 정상 답변 (156s) |
| 동기 httpx 호출이 이벤트 루프를 막아 q9 연결 끊김 | 비동기 호출 → 서버 오류 0건 |
| 검증이 "30자 + 1)"만 검사 | 시스템 언급·수치 인용·미조사 시스템 금지 → 실모델에서 3건 반려·재작성 |
| 두 시스템 언급 시 첫 번째만 조사 (q4·q5·q6) | **미해결** — 소형 모델 계획 단계 문제, 재계획 개선 항목으로 남김 |
| UI 진행 표시가 ⏳로 남음 | **미해결** — UI 개선 항목으로 남김 |

## 2차 기능 확인

- **Research 서브그래프**: SSE에 `ResearchDispatched → SubAgent(metrics/log/runbook) → ResearchMerged`가
  찍히고, 로그 수집(`LogsCollected`)이 모니터링과 같은 superstep에서 병렬로 끝난다. `screens/*_approve.png`.
- **HITL**: 위험 조치(재시작·스케일 아웃 등) 포함 초안 8건 모두 `ApprovalRequired`로 멈췄고, 승인 시 검수로
  진행, 거절 시 재작성, 2회 거절 후 "실행 금지" 경고를 달아 종료(`screens/fake_uc5_reject.png`).
- **요약 메모리** (`memory_fake.json`, `memory_ollama.json`): 12번째 메시지에서 과거 8개를 요약해
  LLM에 실리는 이력이 10개 → 5개 메시지로 줄었고, 9턴째 "제 이름이 뭐였죠?"에 두 백엔드 모두 "민수"를
  회상했다. 실모델 요약: `사용자 이름: 민수 / 주요 사실: … / 진행 중인 요청: …`.

## 파일

| 경로 | 내용 |
|---|---|
| `metrics_{fake,ollama}.{json,csv}`, `summary_*.md` | 회귀 쿼리 측정 (2차 코드 기준) |
| `memory_{fake,ollama}.json` | 요약 메모리 9턴 테스트 |
| `screens/` | fake 승인·거절, ollama 승인·두 시스템 화면 |
| `server_log_ollama.txt` | 측정 중 서버 로그 (개발 중 재현한 오류 traceback 포함) |

---

## 3차 — 남은 과제 처리 후 재측정

| 지표 | 2차 | 3차 |
|---|---|---|
| ollama 정확도 | 29 / 32 | **32 / 32** |
| ollama 조사 시스템 (q4 / q5 / q6) | gateway / web-01 / payments 하나씩 | gateway+payments / auth+web-01 / cache+payments |
| ollama 총 지연 · cost | 575s · 0.084 | 657s · 0.094 (조사 대상 증가분) |
| LLM-as-judge (ollama, 8건 × 3점) | — | 21 / 24 (q5 채점 타임아웃, 나머지 7건 만점) |
| LLM-as-judge (fake, 규칙) | — | 24 / 24 |
| pytest | — | 32 passed |
| 인시던트 리포트 | — | 장애 쿼리마다 1건 저장 (`/incidents`) |

리포트 예시(`GET /incidents/1?format=md`):

```
# 인시던트 리포트 — payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요
## 개요
- 장애 유형(runbook): cpu-spike · 조사 시스템: payments-service, web-01
- 검증 시도: 2회 · 재계획: 1회 · 운영자 승인: 승인 (인스턴스 증설(스케일 아웃))
## 타임라인
- 라우팅: incident → 계획 수립 → 조사 분배 {'metrics': 2, 'logs': 2} → 재조사: runbook=cpu-spike → …
## 수집된 사실 / ## 진단 및 권장 조치
```

추가 파일: `judge_{fake,ollama}.{md,json}`, `screens/fake_uc6_done_marks_report.png`
