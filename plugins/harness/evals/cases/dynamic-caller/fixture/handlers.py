"""Handlers for account and order events, invoked by the event dispatcher.

Each handler takes the event payload and returns a short human-readable
message describing what was sent, so the caller can log or display it.
"""


def handle_signup(user):
    """Send a welcome email to a newly registered user."""
    return f"welcome:{user['email']}"


def handle_purchase(order):
    """Send an order confirmation email."""
    return f"confirmation:{order['id']}"


def handle_refund(order):
    """Send a refund notice."""
    return f"refund:{order['id']}"


def handle_password_reset(user):
    """Send a password reset link."""
    return f"reset:{user['email']}"
