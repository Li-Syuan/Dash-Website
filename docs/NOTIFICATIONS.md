# Shared in-app notifications

The application uses the approved `dash-mantine-components==0.12.0`, with its
actual component API verified. Mantine's provider wraps the Bootstrap shell
without global normalization; notifications share the workspace typography,
color and radius. It does not upgrade Mantine or add icon packages, web fonts,
CDNs, sockets, pollers, mobile push, SMTP or background workers.

`NotifyService(identity_getter)` creates success/error/warning/info events using
a fixed catalog. Callers provide a catalog code and optional safe request ID,
not arbitrary error text or report data. Public/anonymous events are rejected.
Identity scope is a SHA-256 pseudonym, not a claim of anonymity. Events remain in
memory-only client Stores; there is no durable notification center or history.

Each action owns a distinct `notification_store_id(action)` output. One shell
callback consumes these stores, checks current identity scope, validates the
catalog/schema, deduplicates bounded event IDs, and renders at most three toast
commands. Stable per-scope/catalog toast IDs prevent repeated same-code stacking.
No process-global message queue is shared across users. Client event data does
not authorize a business operation; forging one's own visual event cannot modify
state or gain access.

Report load/refresh/export and manual simulation use the shared adapter. Failed
UI actions return a safe notification and leave the prior primary output alone
where appropriate. Data/API/service errors retain their server authorization and
error handling; toast messages do not replace those boundaries. Request IDs help
operators correlate failures without exposing exception messages.

Mantine's bundled alert role and native close control are used; its supported
theme defaults label the close control. The container is viewport-bounded and
motion preferences are respected by local CSS. Actual keyboard dismissal,
responsive positioning, screen-reader behavior and browser interaction require
real UI validation; component serialization tests alone do not prove them.
