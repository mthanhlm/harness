> Security judgement for code being written — trust boundaries, injection,
> authorisation, secrets, sessions, crypto, file handling, SSRF, dependencies,
> and what errors give away. Loads automatically in auth and middleware
> directories and on env files.
>
> Domain: security

# Security lens

This loads for whoever is **writing** the code, which is the point. A review
finds a vulnerability after it exists; this is the knowledge that stops it being
written. `reviewer-security` carries the same page rather than restating it.

Two framings to hold throughout. First, **every rule here is about a boundary** —
name the boundary and most rules follow. Second, **the attacker does not use your
UI.** They send the request directly, with fields you never render, values you
never offer, and in an order your flow does not allow.

## Name the trust boundary before anything else

Every input crosses from somewhere you do not control to somewhere you do. Write
down where that line is.

Untrusted does not only mean "typed by a user". It means request bodies, query
strings, path segments, headers, cookies, `Referer`, uploaded filenames, file
*contents*, webhook payloads, third-party API responses, environment variables in
a repo you cloned, message-queue payloads, and **anything read back out of your
own database that an untrusted party once put there**. That last one is how
stored XSS and second-order SQL injection happen: the data was sanitised on the
way in for one context and used in a different one on the way out.

Validate at the boundary once, into a typed value, and let everything downstream
rely on it. Sanitising repeatedly, deep in the code, means every new caller is a
new chance to forget.

## Authorisation is per-object, not per-route

Authentication asks *who are you*. Authorisation asks *may you touch this
particular row*. A route that checks a session exists and then loads
`invoice/:id` straight from the path lets anyone enumerate every invoice. This is
the single most common serious web vulnerability, and it does not look like a bug
in review because the code reads sensibly.

Put ownership in the query, so it cannot be forgotten by the next caller:

```python
# Forgettable: two statements, and the second is optional-looking.
invoice = db.get(Invoice, invoice_id)
if invoice.org_id != user.org_id:
    raise Forbidden

# Structural: there is no version of this that returns someone else's invoice.
invoice = db.query(Invoice).filter_by(id=invoice_id, org_id=user.org_id).one_or_none()
```

Related failures in the same family:

- **Mass assignment.** `User(**request.json)` lets the caller set `is_admin`.
  Accept an explicit allow-list of fields, never the whole body.
- **The hidden field.** A `role` or `price` posted from a form the UI marked
  read-only. Server-side is the only side.
- **Sequential IDs as capabilities.** Not a vulnerability by themselves, but they
  make a missing check trivially exploitable. Random IDs are defence in depth,
  never a substitute for the check.
- **Deny by default.** A permission check that only runs on the paths someone
  remembered is a permission check on nothing. Middleware that requires opt-*out*
  beats one that requires opt-in.
- **The second path to the same object.** The export endpoint, the admin view,
  the GraphQL resolver, the background job. One missing check is the whole
  control.

## Never build an interpreted string from untrusted input

The same shape recurs across every interpreter:

| Interpreter | The rule | The bug |
|---|---|---|
| **SQL** | Parameterised queries, always | Any interpolation, however escaped |
| **Shell** | Pass an argv list | `shell=True`, backticks, `os.system` |
| **Paths** | Resolve, *then* check containment | Checking before resolution |
| **HTML** | Escape on output, per context | `innerHTML`, `dangerouslySetInnerHTML` |
| **Deserialisation** | Safe loader plus a schema | `pickle`, `yaml.load`, Java native |
| **Templates** | Data as data, never as template source | User-supplied template strings |
| **LDAP / XPath / regex** | Escape per that grammar | Concatenation |
| **Log lines** | Encode newlines | Log-injection forging entries |

Two that deserve expansion.

**Paths.** A path checked before resolution is not checked, because `../` and
symlinks both escape after the fact:

```python
# Wrong: the check happens before the traversal is resolved.
if ".." not in name:
    open(os.path.join(base, name))

# Right: resolve first, then assert containment on the result.
target = (base / name).resolve()
if not target.is_relative_to(base.resolve()):
    raise Forbidden
```

Also reject absolute paths (`os.path.join(base, "/etc/passwd")` returns
`/etc/passwd`), null bytes, and on Windows the reserved device names.

**Dynamic SQL that cannot be parameterised.** Column names and sort directions
are not parameterisable. The answer is an allow-list, never escaping:

```python
SORTABLE = {"created_at", "name", "total"}
if sort not in SORTABLE:
    raise BadRequest
```

## Sessions, tokens and passwords

- **Password storage:** a memory-hard KDF — argon2id, scrypt, or bcrypt. Never a
  bare hash, salted or not; a GPU does billions of SHA-256 per second.
- **Session tokens:** generated from a CSPRNG (`secrets.token_urlsafe`, not
  `random`), at least 128 bits, and **regenerated on privilege change** — login,
  password change, role escalation. Reusing a pre-login session id is session
  fixation.
- **Cookies:** `HttpOnly`, `Secure`, `SameSite=Lax` or `Strict`. `HttpOnly` is
  what keeps an XSS from becoming an account takeover.
- **JWTs, if you must:** pin the algorithm server-side (`alg: none` and
  RS256→HS256 confusion are both real), verify `exp`, `iss` and `aud`, and accept
  that you cannot revoke one. If you need logout to mean something, you need
  server-side sessions or a short expiry plus a refresh token you *can* revoke.
- **Password reset tokens:** single-use, short-lived, tied to the account, and
  invalidated when the password changes. A reset link in an email is a bearer
  credential in a log-prone channel.
- **Rate limit the credential endpoints** — login, reset, MFA, token exchange —
  per account *and* per source. Per-source alone loses to a botnet; per-account
  alone lets one source spray a million accounts once each.

## Cryptography

Use the boring thing. The rules that actually catch bugs:

- **Never invent a scheme, and never encrypt without authenticating.** AES-GCM or
  libsodium's box. Unauthenticated CBC or CTR is malleable — an attacker who
  cannot read your ciphertext can still change it.
- **Never reuse a nonce or IV with the same key.** With GCM, one reuse leaks the
  authentication key. Generate per message, from the CSPRNG.
- **Constant-time comparison** for secrets, tokens, HMACs and signatures.
  `hmac.compare_digest`, not `==`, which returns early and leaks the matching
  prefix length.
- **`random` is not `secrets`.** `random.random()` is a Mersenne Twister; observe
  enough output and the rest is predictable. Same for `Math.random()`.
- **Verify the signature before parsing the payload.** A webhook handler that
  reads the JSON first has already processed attacker input.

## Secrets

Secrets come from the environment or a secret manager. Never a literal, never a
committed file, never a default value in code.

A secret that has been committed is compromised even after the commit is removed
— it is in the reflog, in every clone, and in whatever mirrored it. **Rotate it;
deleting the line is not a fix.**

Do not log them, do not put them in URLs (they land in access logs, browser
history and `Referer` headers), do not return them in an error, and do not embed
them in a client bundle — anything shipped to a browser or a mobile app is
public, whatever the variable is named. Check what your error reporter captures:
many attach the full request body by default.

## SSRF and outbound requests

An endpoint that fetches a URL the user supplies is a request from inside your
network. It reaches your cloud metadata endpoint, your internal admin service,
and `localhost`.

Allow-list the destination host if you possibly can. If you cannot: resolve the
name, reject private and link-local ranges (including `169.254.169.254`), reject
non-HTTP schemes, disable redirects or re-check after each hop, and set a
timeout and a response-size cap. Note that checking the hostname before resolving
loses to DNS rebinding — the check and the connection must agree on the address.

## Uploads and untrusted files

- **Never trust the filename.** Generate your own. The supplied one carries `../`,
  null bytes, and a second extension.
- **Never trust the content type.** It is a header the client wrote.
- **Never serve an upload from your own origin** unless you must — one HTML file
  is stored XSS with full access to your cookies. Separate domain, or
  `Content-Disposition: attachment` plus `X-Content-Type-Options: nosniff`.
- **Cap the size before reading**, not after. Streaming a 10 GB upload into memory
  is the denial of service.
- **Image and archive parsers are attack surface.** Decompression bombs, path
  traversal inside a zip ("zip slip"), and SVG containing script.

## Errors, logs and timing

"User not found" and "wrong password" as distinct messages is an account
enumeration oracle — and so is a measurably faster response when the account does
not exist. Do the same work either way, return the same message.

A stack trace in a response names your framework, your file layout and often your
query. Log the detail server-side with a correlation id; return something useful
and dull with the same id. Turn off the debug page in production — an interactive
debugger reachable from the internet is a shell.

What lands in logs is its own boundary: request bodies, `Authorization` headers,
session cookies and query strings all commonly get logged wholesale, and logs are
retained longer and read more widely than the data they came from.

## Dependencies and supply chain

A dependency runs with your process's full permissions, and its install script
runs with your developer's. Before adding one: check it is the package you meant
(typosquats differ by a hyphen), check it is maintained, check what it pulls in
transitively, and pin it with a lockfile. Prefer the standard library over a
small package with a large tree.

In CI, treat a third-party action or image the same way — it sees your secrets.
Pin to a digest, not a tag.

## Multi-tenancy

If rows from two customers share a table, the tenant id belongs in **every**
query, enforced somewhere structural — row-level security, a session variable the
database itself applies, or a base query object no code path bypasses. A
convention that "everyone remembers to filter" fails on the first raw query
someone writes for a migration or a report.

The same applies to caches: a cache key without the tenant id serves one
customer's data to another, and it happens under load rather than in tests.

## What this lens does not cover

Infrastructure hardening, network policy, IAM roles and secret rotation
mechanics are `lens-infra`. Prompt injection and untrusted content reaching a
model are `lens-llm-agents`. Both load alongside this one when the paths call
for them.

## Before adding an endpoint or a dependency

For an endpoint, before writing the handler: who may call it, what may they reach
through it, what happens if they call it a thousand times, and what does it
return to someone who is *nearly* authorised. For a dependency: see above — it
runs as you.

## Review checklist

1. Where is the trust boundary, and is validation on it or scattered past it?
2. Does every object fetch carry the ownership predicate, in the query?
3. Is there a second path to the same object that skips the check?
4. Any string built for an interpreter — SQL, shell, path, HTML, template?
5. Is a path checked before resolution, or after?
6. Are permissions deny-by-default, or opt-in per route?
7. Can the caller set a field the UI does not offer — role, price, owner, id?
8. Password hashing memory-hard? Session regenerated on privilege change?
9. Secrets from the environment, absent from logs, URLs and client bundles?
10. Constant-time comparison on every token, HMAC and signature?
11. Does any endpoint fetch a user-supplied URL, and is the destination bounded?
12. Do errors distinguish cases an attacker should not be able to distinguish?
13. In a multi-tenant table, is the tenant id in the query *and* the cache key?
14. Does a new dependency's install script run as part of `install`?
