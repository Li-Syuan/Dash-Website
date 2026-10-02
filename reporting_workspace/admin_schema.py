"""Additive schema for tenant-local report governance; no executable settings.

The DDL is trusted application code, not supplied by administrators. Composite
foreign keys retain the organization/report relationship, even for direct SQL
mistakes. StateStore enables foreign_keys on every connection.
"""

ADMIN_DDL = {
    ('table', 'managed_reports', 'managed_reports'): """
CREATE TABLE managed_reports (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    org TEXT NOT NULL CHECK (length(org) BETWEEN 1 AND 128),
    source_key TEXT NOT NULL CHECK (length(source_key) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    category TEXT NOT NULL CHECK (length(category) BETWEEN 1 AND 64),
    description TEXT NOT NULL CHECK (length(description) <= 240),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version > 0),
    created_by TEXT NOT NULL CHECK (length(created_by) BETWEEN 1 AND 255),
    create_key TEXT NOT NULL CHECK (length(create_key) = 32 AND create_key NOT GLOB '*[^0-9a-f]*'),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL,
    UNIQUE (org, id),
    UNIQUE (org, created_by, create_key)
)
""",
    ('table', 'report_grants', 'report_grants'): """
CREATE TABLE report_grants (
    org TEXT NOT NULL,
    report_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('role', 'user', 'org')),
    subject TEXT NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
    can_view INTEGER NOT NULL CHECK (can_view IN (0, 1)),
    can_export INTEGER NOT NULL CHECK (can_export IN (0, 1)),
    can_maintain INTEGER NOT NULL CHECK (can_maintain IN (0, 1)),
    updated_at REAL NOT NULL,
    PRIMARY KEY (org, report_id, kind, subject),
    FOREIGN KEY (org, report_id) REFERENCES managed_reports (org, id),
    CHECK (can_view = 1 OR (can_export = 0 AND can_maintain = 0))
)
""",
    ('table', 'mail_schedules', 'mail_schedules'): """
CREATE TABLE mail_schedules (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    org TEXT NOT NULL,
    report_id TEXT NOT NULL,
    recipients TEXT NOT NULL CHECK (length(recipients) BETWEEN 1 AND 6000),
    cadence TEXT NOT NULL CHECK (cadence IN ('daily', 'weekly')),
    send_time TEXT NOT NULL CHECK (length(send_time) = 5),
    timezone TEXT NOT NULL CHECK (timezone IN ('UTC', 'Asia/Taipei')),
    weekday INTEGER NOT NULL CHECK (typeof(weekday) = 'integer' AND weekday BETWEEN 0 AND 6),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version > 0),
    created_by TEXT NOT NULL CHECK (length(created_by) BETWEEN 1 AND 255),
    create_key TEXT NOT NULL CHECK (length(create_key) = 32 AND create_key NOT GLOB '*[^0-9a-f]*'),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (org, id, report_id),
    UNIQUE (org, created_by, create_key),
    FOREIGN KEY (org, report_id) REFERENCES managed_reports (org, id)
)
""",
    ('table', 'schedule_runs', 'schedule_runs'): """
CREATE TABLE schedule_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    org TEXT NOT NULL,
    report_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    schedule_version INTEGER NOT NULL CHECK (typeof(schedule_version) = 'integer' AND schedule_version > 0),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 255),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error_code TEXT CHECK (error_code IN ('provider_unavailable', 'configuration_changed', 'mock_failed')),
    started_at REAL NOT NULL,
    finished_at REAL,
    UNIQUE (schedule_id, schedule_version),
    FOREIGN KEY (org, schedule_id, report_id) REFERENCES mail_schedules (org, id, report_id),
    CHECK ((status = 'running' AND finished_at IS NULL AND error_code IS NULL)
        OR (status = 'succeeded' AND finished_at IS NOT NULL AND error_code IS NULL)
        OR (status = 'failed' AND finished_at IS NOT NULL AND error_code IS NOT NULL))
)
""",
    ('table', 'admin_changes', 'admin_changes'): """
CREATE TABLE admin_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org TEXT NOT NULL,
    report_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 255),
    action TEXT NOT NULL CHECK (action IN ('report.created', 'report.updated', 'report.archived', 'report.restored',
        'grant.changed', 'schedule.created', 'schedule.updated', 'mock.claimed', 'mock.succeeded', 'mock.failed')),
    changed_fields TEXT NOT NULL,
    before_version INTEGER,
    after_version INTEGER,
    occurred_at REAL NOT NULL,
    request_id TEXT,
    FOREIGN KEY (org, report_id) REFERENCES managed_reports (org, id)
)
""",
    ('index', 'managed_reports_org_updated', 'managed_reports'):
        'CREATE INDEX managed_reports_org_updated ON managed_reports (org, updated_at DESC, id)',
    ('index', 'mail_schedules_report', 'mail_schedules'):
        'CREATE INDEX mail_schedules_report ON mail_schedules (org, report_id, updated_at DESC, id)',
    ('index', 'schedule_runs_report', 'schedule_runs'):
        'CREATE INDEX schedule_runs_report ON schedule_runs (org, report_id, started_at DESC, id)',
    ('index', 'admin_changes_org', 'admin_changes'):
        'CREATE INDEX admin_changes_org ON admin_changes (org, id DESC)',
}

# Explicit affinity/nullability/key contract, complementing exact DDL validation.
ADMIN_COLUMNS = {
    'managed_reports': (
        ('id', 'TEXT', 1, None, 1), ('org', 'TEXT', 1, None, 0),
        ('source_key', 'TEXT', 1, None, 0), ('name', 'TEXT', 1, None, 0),
        ('category', 'TEXT', 1, None, 0), ('description', 'TEXT', 1, None, 0),
        ('enabled', 'INTEGER', 1, None, 0), ('version', 'INTEGER', 1, None, 0),
        ('created_by', 'TEXT', 1, None, 0), ('create_key', 'TEXT', 1, None, 0),
        ('created_at', 'REAL', 1, None, 0), ('updated_at', 'REAL', 1, None, 0),
        ('archived_at', 'REAL', 0, None, 0)),
    'report_grants': (
        ('org', 'TEXT', 1, None, 1), ('report_id', 'TEXT', 1, None, 2),
        ('kind', 'TEXT', 1, None, 3), ('subject', 'TEXT', 1, None, 4),
        ('can_view', 'INTEGER', 1, None, 0), ('can_export', 'INTEGER', 1, None, 0),
        ('can_maintain', 'INTEGER', 1, None, 0), ('updated_at', 'REAL', 1, None, 0)),
    'mail_schedules': (
        ('id', 'TEXT', 1, None, 1), ('org', 'TEXT', 1, None, 0),
        ('report_id', 'TEXT', 1, None, 0), ('recipients', 'TEXT', 1, None, 0),
        ('cadence', 'TEXT', 1, None, 0), ('send_time', 'TEXT', 1, None, 0),
        ('timezone', 'TEXT', 1, None, 0), ('weekday', 'INTEGER', 1, None, 0),
        ('enabled', 'INTEGER', 1, None, 0), ('version', 'INTEGER', 1, None, 0),
        ('created_by', 'TEXT', 1, None, 0), ('create_key', 'TEXT', 1, None, 0),
        ('created_at', 'REAL', 1, None, 0), ('updated_at', 'REAL', 1, None, 0)),
    'schedule_runs': (
        ('id', 'TEXT', 1, None, 1), ('org', 'TEXT', 1, None, 0),
        ('report_id', 'TEXT', 1, None, 0), ('schedule_id', 'TEXT', 1, None, 0),
        ('schedule_version', 'INTEGER', 1, None, 0), ('actor_id', 'TEXT', 1, None, 0),
        ('status', 'TEXT', 1, None, 0), ('error_code', 'TEXT', 0, None, 0),
        ('started_at', 'REAL', 1, None, 0), ('finished_at', 'REAL', 0, None, 0)),
    'admin_changes': (
        ('id', 'INTEGER', 0, None, 1), ('org', 'TEXT', 1, None, 0),
        ('report_id', 'TEXT', 1, None, 0), ('resource_id', 'TEXT', 1, None, 0),
        ('actor_id', 'TEXT', 1, None, 0), ('action', 'TEXT', 1, None, 0),
        ('changed_fields', 'TEXT', 1, None, 0), ('before_version', 'INTEGER', 0, None, 0),
        ('after_version', 'INTEGER', 0, None, 0), ('occurred_at', 'REAL', 1, None, 0),
        ('request_id', 'TEXT', 0, None, 0)),
}
