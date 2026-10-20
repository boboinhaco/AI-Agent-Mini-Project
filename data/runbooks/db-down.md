# Runbook: DB Down 대응 절차

## 증상
데이터베이스 접속 불가, DB 다운, 커넥션 거부(connection refused), 상태 down, 서비스 전체 장애, 리플리케이션 끊김, failover 필요. 영문 알림: database down, db unreachable, status down.

## 절차
1. 모니터링 알람으로 장애를 즉시 인지한다 (get_monitoring_status로 현재 상태 확인).
2. 커넥션 풀/리플리케이션 상태를 점검하고 필요 시 failover 노드로 전환한다.
3. 서비스 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화한다.
