# Public route organization

Nerdo application features must remain under the existing `/nerdo/` public namespace.

Do not add a new reverse-proxy rule for each feature. Add feature routes beneath the existing application prefix instead.

Current public routes:

- `/nerdo/share/<opaque-token>` — one-time shared Chato session claim
- `/nerdo/share/session/<session-id>` — claimed browser session

The dashboard remains under its existing `/dashboard/` route.
