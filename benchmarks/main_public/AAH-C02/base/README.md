# 여러 품목 예약의 원자성

reserve_order(stock, order_id, lines)는 서로 다른 신규 order_id의 1개 이상 주문 품목을 예약한다. 각 품목 수량은 양의 정수이며 동일 SKU가 여러 줄에 나타날 수 있다. 성공 시 총 수량을 차감하고 주문의 원래 품목 순서로 예약을 남기고 True를 반환한다. 재고 부족 또는 미등록 SKU로 실패하면 False를 반환하고 available과 reservations의 모든 기존 데이터를 보존한다. 이미 예약한 order_id는 False다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
