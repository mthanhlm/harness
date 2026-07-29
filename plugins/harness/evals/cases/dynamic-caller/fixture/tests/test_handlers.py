from handlers import handle_password_reset, handle_purchase, handle_refund


def test_handle_purchase_confirms_the_order():
    assert handle_purchase({"id": "ord-1"}) == "confirmation:ord-1"


def test_handle_refund_notes_the_order():
    assert handle_refund({"id": "ord-2"}) == "refund:ord-2"


def test_handle_password_reset_addresses_the_user():
    assert handle_password_reset({"email": "a@example.com"}) == "reset:a@example.com"
