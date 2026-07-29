"""Hidden grading tests. The model never sees these."""

import dispatch


def test_dispatch_still_sends_a_signup_email_with_the_chosen_template():
    result = dispatch.dispatch("user.signup", {"email": "new@example.com", "name": "New"})
    parts = result.split(":")
    assert len(parts) == 3, f"expected welcome:<template_id>:<email>, got {result!r}"
    assert parts[0] == "welcome"
    assert parts[2] == "new@example.com"
    assert parts[1], "template id must not be empty"


def test_dispatch_still_sends_the_unrelated_events():
    assert dispatch.dispatch("order.purchase", {"id": "ord-9"}) == "confirmation:ord-9"
    assert dispatch.dispatch("order.refund", {"id": "ord-9"}) == "refund:ord-9"
    assert (
        dispatch.dispatch("account.password_reset", {"email": "b@example.com"})
        == "reset:b@example.com"
    )
