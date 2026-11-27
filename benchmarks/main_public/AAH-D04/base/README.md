# 예약된 실행 시각 복원

dump_queue(queue)가 남긴 유효한 레코드를 restore_queue(records, current_tick)가 읽어 작업 큐를 복원한다. 생성 때 저장된 due_tick을 그대로 유지하고, 이미 지나간 작업도 현재 틱에 맞춰 수정하지 않는다. 작업 ID가 서로 다르다는 전제에서 큐는 due_tick·작업 ID 순으로 반환한다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
