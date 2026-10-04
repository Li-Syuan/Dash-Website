# v9 Git checkpoint

This checkpoint integrates the approved ETL dispatch and request-isolated
maintenance increments from the verified v7 Library baseline, followed by the
v9 authorization, identity refresh, lifecycle, browser-error and static-transport
fixes. The starting remote main is
`2057a515fbf071b293ab5f844d9fe0f928c56619`. Existing dependency pins and the GitHub
Actions workflow are unchanged. The owner's separate approval authorizes this
checkpoint to main; it does not authorize later recovery/performance work.

[Validation](VALIDATION_V9.md) records the full Python and native Chrome results.
[Flask-RESTX review](FLASK_RESTX_COMPATIBILITY.md) distinguishes 16 passing Flask
composition checks from the missing company RESTX integration and unrun cases.
No known RESTX code was removed: it is absent from all supplied source trees.

The Git repository contains source, tests and sanitized textual evidence. Binary
screenshots/exports and the immutable full delivery remain in the separately
prepared archive set indexed by EVIDENCE_INDEX.json. The archive manifest hashes
refer to those original archive bytes; Git normalizes line endings and removes
presentation-only trailing whitespace in captured text. A redundant final blank
line in definition_domain.py was removed without changing Python syntax or
behavior. These formatting changes do not alter the published archive set.

Library batch writeback was blocked before upload preparation; Library still
held v8 when checked. This Git checkpoint must not be described as a successful
v9 Library upload. No deployment or company Oracle/LDAP/SMTP call was made.

Raw DOM dumps, per-sample clock JSONL and earlier attempt logs are retained in
the immutable complete ZIP, rather than duplicated in Git. Result summaries,
current regression logs and diagnostic scripts remain in this checkpoint.
