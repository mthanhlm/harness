"""Routes inbound events to the handler registered for them.

Event types arrive as strings from the message queue (see ROUTES), so a new
event type is one line here without touching handlers.py.
"""

import handlers

ROUTES = {
    "user.signup": "handle_signup",
    "order.purchase": "handle_purchase",
    "order.refund": "handle_refund",
    "account.password_reset": "handle_password_reset",
}


def dispatch(event_type, payload):
    """Look up and run the handler registered for event_type."""
    handler = getattr(handlers, ROUTES[event_type])
    return handler(payload)


def resend_welcome_email(user):
    """Manually re-send a welcome email, e.g. from the support console."""
    return handlers.handle_signup(user)
