# Flask-RESTX compatibility boundary — v9 checkpoint

The owner confirmed Flask-RESTX is part of the company system. This checkpoint
cannot certify that integration: no flask_restx/flask_restplus import, Api,
Namespace, add_namespace, Swagger/OpenAPI registration or RESTX dependency pin
was found in the supplied v7 source, v9 delivery, protected local checkout, or
remote main at 2057a515fbf071b293ab5f844d9fe0f928c56619. The approved Windows test
runtime has Flask 2.2.3, Werkzeug 2.2.3 and Dash 2.9.1; Flask-RESTX is absent.
No package was installed, no company API was executed, and no product source
was changed for this compatibility check.

## Verified against the actual v9 application

The isolated synthetic Flask composition probe passed 16/16 checks. This is an
HTTP test, not a RESTX runtime test or browser test. The original application
source remained byte-identical. Run it with the approved Python environment:

```sh
python -B tests/probes/flask_composition.py . output/flask-composition
```

- Explicit Blueprint API and documentation routes take precedence over Dash's
  catch-all when registered before the first request. JSON values, custom
  headers, status codes and content types remain intact.
- The application AccessDenied, ProviderUnavailable and HTTPException handlers
  return sanitized 401, 503 and 400 responses. A specific Blueprint exception
  handler retains its own response contract.
- The request boundary does not impose the Dash callback registry or its
  cross-origin restriction on ordinary API endpoints. Therefore new RESTX
  endpoints require their own authentication, current-identity authorization,
  tenant checks and cookie-auth CSRF/origin controls.
- The global body-size limit and response headers also apply to added APIs.
  The factory default is 1 MiB; direct app.py defaults to a 15 MiB envelope
  unless explicitly configured. X-Request-ID, security headers and no-store
  caching must be included in any company API contract review.
- /swagger.json, /docs and /openapi.json currently return the Dash HTML shell,
  not an API schema. HTTP 200 alone does not prove documentation registration.
- StaticTransport only chunks GET/HEAD responses under /assets/ and
  /_dash-component-suites/. The fetch workaround only affects same-origin POST
  /_dash-update-component with 400/401. Neither changes ordinary API JSON.

Machine-readable checks and the original route map are in
[flask-composition-results.json](flask-composition-results.json).

## Conditions still requiring the real RESTX integration

1. Confirm the installed RESTX version and its pinned dependency matrix. Attach
   Api to the actual Flask `server` returned by create_app, not the Dash `app`
   object. Existing Api/Namespace objects on a different Flask app are not
   automatically imported or transferred by this factory.
2. Inventory exact Blueprint/Api/Namespace prefixes and endpoint names. Dash
   already owns `/`; RESTX's default documentation root can overlap it. Reserve
   an explicit documentation location and check existing /api/reports and
   /api/managed-reports routes before integration. This is a conditional risk,
   not an observed conflict in the supplied source.
3. Check RESTX's error_router/handle_error and namespace error handlers with
   authentication failures, revoked users, cross-tenant IDs, validation errors,
   404/405, 413 and provider failures. The app uses `error`/`request_id`; RESTX
   may use `message`. Plain Flask checks do not establish RESTX precedence or
   guarantee that the company response contract remains identical.
4. Exercise the real Swagger schema, models, marshal_with/marshal, parsers,
   validation, pagination, null/date/decimal values and error envelopes. None
   of those company definitions is present here, so these checks remain unrun.
5. Verify docs/schema authorization, CORS/OPTIONS and any reverse-proxy prefix
   using synthetic fixtures. Existing Oracle/LDAP/SMTP and SQLALCHEMY_BINDS
   contracts are not certified by this checkpoint.

The current upstream implementation documents the registration and error-router
behavior referenced above: [Flask-RESTX Api source](https://flask-restx.readthedocs.io/en/latest/_modules/flask_restx/api.html).
It does not identify the company's installed version. No unconditional claim
that all existing RESTX APIs are conflict-free is made.
