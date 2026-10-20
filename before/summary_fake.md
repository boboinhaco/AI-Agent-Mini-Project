# 측정 요약 — fake

- 백엔드: fake (fast=fast-model-stub, quality=quality-model-stub)
- supervisor_version: v2
- 쿼리 수: 10
- 총 지연시간: 1.3s (평균 0.1s)
- 총 cost_usd(가상 단가): 0.000685
- 정확도 체크 통과: 7/32 (22%)
- 검증 재작성 발생 쿼리: 1건
- 재시도(retry) 총합: 1

| # | 지연(s) | route | tasks | 검증시도 | cost | 조사 시스템 | runbook | 체크 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.03 | chat | 0 | 0 | 7e-06 |  |  | 0/4 ✗route_ok,systems_ok,runbook_ok,format_ok |
| 2 | 0.0 | chat | 0 | 0 | 5e-06 |  |  | 0/4 ✗route_ok,systems_ok,runbook_ok,format_ok |
| 3 | 0.0 | chat | 0 | 0 | 5e-06 |  |  | 0/4 ✗route_ok,systems_ok,runbook_ok,format_ok |
| 4 | 0.0 | chat | 0 | 0 | 7e-06 |  |  | 0/3 ✗route_ok,systems_ok,format_ok |
| 5 | 1.18 | incident | 3 | 2 | 0.000638 | auth-service,web-01 | high-latency | 3/3 |
| 6 | 0.01 | chat | 0 | 0 | 7e-06 |  |  | 0/4 ✗route_ok,systems_ok,analysis_ran,format_ok |
| 7 | 0.01 | chat | 0 | 0 | 5e-06 |  |  | 0/3 ✗route_ok,systems_ok,format_ok |
| 8 | 0.03 | chat | 0 | 0 | 5e-06 |  |  | 1/4 ✗route_ok,systems_ok,format_ok |
| 9 | 0.0 | chat | 0 | 0 | 3e-06 |  |  | 1/1 |
| 10 | 0.0 | chat | 0 | 0 | 3e-06 |  |  | 2/2 |

## 답변 전문

### q1: payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q2: cache-service 메모리 사용률이 계속 올라가고 있어요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q3: log-service 디스크 공간이 부족하다는 알림이 왔어요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q4: gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q5: web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘

```
'web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘' 진단 결과: 1) 모니터링: web-01 상태=healthy, CPU 39.2% (알림 0건) · auth-service 상태=degraded, CPU 83.5% (알림 2건) 2) 표준 절차: 'Runbook: 응답 지연(High Latency) 대응 절차'에 따라 커넥션 풀 사용률이 90%를 넘으면 풀 크기를 늘리거나 느린 쿼리/외부 호출에 타임아웃을 건다. 지연이 특정 리전/인스턴스에 몰려 있으면 해당 인스턴스를 로드밸런서에서 일시 제외한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q6: payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘

```
안녕하세요! 무엇을 도와드릴까요?
```

### q7: 로그인 응답이 느려요, 원인 확인해주세요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q8: billing-service 응답이 느려요

```
안녕하세요! 무엇을 도와드릴까요?
```

### q9: 안녕, 너는 누구야?

```
안녕하세요! 무엇을 도와드릴까요?
```

### q10: 제 이름은 민수입니다 → 제 이름이 뭐였죠?

```
민수님이라고 말씀하셨어요. 무엇을 도와드릴까요?
```
