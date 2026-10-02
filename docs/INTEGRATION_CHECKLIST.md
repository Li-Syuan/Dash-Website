# Company integration acceptance checklist

The foundation and synthetic tests do not establish company compatibility.
Complete this checklist using private, authorized sources before rollout.

- [ ] Map real identity IDs, role/org claims, revocation, password handling and
      SSO/LDAP error behavior to the provider contract; never relabel demo auth
- [ ] Inventory real pages/callback IDs, data-level permissions and masked fields
- [ ] Map report schema, types, units, dates/timezones and export semantics;
      test Oracle transaction/error behavior against representative fixtures
- [ ] Identify filesystem/platform lock semantics and every worker/process owner;
      decide whether same-host SQLite is suitable or requires a different adapter
- [ ] Inventory stable scheduler/job IDs, triggers, timezone, misfires,
      coalescing, retry/restart policy and one explicit scheduler owner
- [ ] Define external-effect idempotency, fencing and ambiguous-result recovery
- [ ] Define approved mail templates/recipients, durable outbox, delivery failures
      and replay policy; keep outbound delivery disabled until approved
- [ ] Validate exact Linux Python 3.8.13 and Windows Python 3.10.4 with the full
      approved dependency inventory, without package upgrades
- [ ] Verify desktop/mobile rendering, keyboard/focus, login interruptions,
      Back/Forward, repeated controls, table edit/filter/export and logout
- [ ] Review TLS, owner-only test gateway, secret provisioning, cookie/proxy
      settings, CSRF/browser policy, rate limits and production exposure
- [ ] Run realistic load/concurrency, database corruption/disk-full, clock-jump,
      provider outage, backup/restore and rollback drills
- [ ] Configure audit/log retention, monitoring, alert ownership and incident
      response; confirm private metadata never reaches the public repository

Until these gates are complete, label the deliverable “reporting foundation with
offline fixtures,” not a production-ready company migration.
