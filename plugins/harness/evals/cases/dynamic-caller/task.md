Marketing wants to A/B test the welcome email copy for new signups.
`handle_signup` in `handlers.py` currently sends the same fixed message to
everyone.

Add a way to select which template a signup email uses, and include the
chosen template in the message it returns, formatted as
`welcome:<template_id>:<email>` (it currently returns `welcome:<email>`).

Keep the module working end to end — nothing that already sends signup
emails should start failing. Update tests for the handler you change, and
make sure the existing test suite still passes.
