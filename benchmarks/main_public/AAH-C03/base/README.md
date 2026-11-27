# 배치에서 유효 레코드 저장과 행별 오류 보고

ingest_batch(rows, store)는 행을 순서대로 처리한다. id와 label이 모두 비어 있지 않은 문자열인 경우만 유효하다. 행 유효성을 먼저 검사하고, 기존 store 또는 유효한 앞 행과 id가 같으면 덮어쓰지 않는다. 유효하지 않은 행은 같은 id를 가진 뒤 행의 저장을 막지 않는다. 저장한 각 행은 store[id]에 원래 값을 보존하고 accepted에 id를 순서대로 추가한다. errors에는 1부터 시작하는 행 번호 및 INVALID 또는 DUPLICATE 코드를 순서대로 기록한다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
