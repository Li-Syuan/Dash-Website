"""Offline adapters: no company services or background execution."""
import csv
import io
import threading
import time


class AccessDenied(Exception):
    pass


def allowed(user, roles=None, org=None):
    return bool(user and (roles is None or user['role'] in roles)
                and (org is None or user['org'] == org))


def require(user, roles=None, org=None):
    if not allowed(user, roles, org):
        raise AccessDenied('Access denied')


class SyntheticReports:
    columns = ['period', 'department', 'revenue', 'cost', 'profit']

    def rows(self, user):
        require(user, ['admin'])
        return [dict(period='2026-{:02d}'.format(m), department=d,
                     revenue=100000+m*2500, cost=60000+m*1400, profit=40000+m*1100)
                for m in range(1, 7) for d in ['Demo Sales', 'Demo Operations']]

    def export(self, user):
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=self.columns)
        writer.writeheader()
        writer.writerows(self.rows(user))
        return output.getvalue()


class DemoLocks:
    """Process-local lease simulation, NOT a production file lock."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.leases = clock, {}
        self.mutex = threading.Lock()

    def acquire(self, key, owner, ttl=30):
        if ttl <= 0:
            raise ValueError('ttl must be positive')
        with self.mutex:
            lease = self.leases.get(key)
            if lease and lease[1] > self.clock():
                return False
            self.leases[key] = (owner, self.clock()+ttl)
            return True

    def release(self, key, owner):
        with self.mutex:
            lease = self.leases.get(key)
            if not lease or lease[0] != owner or lease[1] <= self.clock():
                return False
            del self.leases[key]
            return True


class DemoScheduler:
    """Manual, once per (job, run) per process; no scheduler threads."""
    def __init__(self):
        self.seen, self.mutex = set(), threading.Lock()

    def run_once(self, job_id, run_key, action):
        with self.mutex:
            key = (job_id, run_key)
            if key in self.seen:
                return False
            self.seen.add(key)
        action()
        return True


class MailSink:
    def __init__(self):
        self.messages = []

    def send(self, subject, body, recipients):
        if any(not x.endswith('@example.invalid') for x in recipients):
            raise ValueError('Demo recipients only')
        self.messages.append(dict(subject=subject, body=body, recipients=list(recipients)))


class OptionalDVCBackend:
    def rows(self, user):
        raise NotImplementedError('Interface stub only: DVC is not integrated')
