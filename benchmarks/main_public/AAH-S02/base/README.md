# 주문 합계의 센트 반올림

가격은 소수 문자열, 수량은 양의 정수다. line_total.py의 invoice_total(lines)는 모든 가격×수량을 정확히 합산한 뒤 최종 청구액에만 소수 2자리 ROUND_HALF_UP을 적용한다. Decimal을 반환하며 빈 주문은 0.00이다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
