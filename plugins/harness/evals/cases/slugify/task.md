Add a `slugify(title)` function to text_utils.py for building URL slugs.

It must lowercase the text, strip leading and trailing whitespace, remove any
character that is not a letter, digit, space or hyphen, and replace each run of
whitespace with a single hyphen. Collapse repeated hyphens into one, and strip
hyphens from the start and end of the result.

Reuse what already exists in the module where it makes sense. Add tests, and
make sure the existing test suite still passes.
