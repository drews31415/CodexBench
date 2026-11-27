# CSV 점수의 빈값과 0 구분

load_rows(text)는 id,score 열이 있는 CSV를 받아 입력 순서의 딕셔너리 목록을 반환한다. score가 정확히 빈 문자열이면 None, 값이 있으면 int로 저장한다. 문자 0은 정수 0으로 보존한다. id는 원문 문자열이며 잘못된 정수에는 ValueError를 발생시킨다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
