# 주문 취소 시 예약분 반환

cancel(orders, stock, order_id)는 활성 주문의 각 재고 예약을 해제한 후 주문을 cancelled로 기록하고 True를 반환한다. 주문과 예약은 서로 다른 식별자를 가질 수 있다. 이미 취소된 주문이면 False를 반환하고 재고를 추가하지 않으며, 모르는 주문 ID에는 KeyError를 발생시킨다.

공개 검사는 작업공간에서 `python -m unittest discover -s public_checks -v`로 실행합니다.
