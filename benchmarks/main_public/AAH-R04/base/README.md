# 재시도 지연·우선순위·취소 유지

enqueue_retry(queue, job, now)에서 취소된 작업은 큐에 넣지 않고 False를 반환한다. 나머지는 재시도 지연 min(2^{failures-1}, 8) 틱을 적용한다. failures는 첫 실패부터 세는 1 이상의 정수다. queue의 항목은 (due_tick, -priority, job_id)이며 기존 정렬·높은 우선순위 동작을 유지한다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
