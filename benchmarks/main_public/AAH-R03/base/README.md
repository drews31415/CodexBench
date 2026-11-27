# 날짜 형식을 추가하면서 기존 동작 유지

parse_day(value)는 달력 날짜의 YYYY-MM-DD 및 YYYYMMDD 형식을 지원하고 datetime.date를 반환한다. 입력은 ASCII 숫자와 필요한 하이픈으로 구성하며 달력에 없는 날짜와 지정 형식 밖의 문자열은 ValueError로 처리한다. 공백, 주차 날짜, 날짜·시간 혼합 형식은 지원하지 않는다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
