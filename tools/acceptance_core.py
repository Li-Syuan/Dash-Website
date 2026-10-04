"""Python 3.8 stdlib acceptance primitives; no product imports or Git required.

Inputs are trusted caller-owned paths/programmatic argv, never commands from
report metadata. Filesystem checks reject observed links/reparse points and
nonregular files; these helpers are not a sandbox against a hostile concurrent
filesystem administrator. Raw child output stays in the caller's private log.
"""
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import sys
import tempfile
import time


_EXCLUDED_DIRS = frozenset(('__pycache__', 'output', 'evidence', 'cache', '.cache',
                           '.pytest_cache', '.git', 'node_modules', 'instance'))
_EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '-wal', '-shm', '-journal')
_CHUNK = 1024 * 1024


def safe_output_path(path, *, directory=None):
    """Validate unresolved lexical ancestry, allowing not-yet-created leaves.

    Never call resolve() before this helper: doing so would erase a junction or
    symbolic-link ancestor. This function checks but does not create anything.
    Existing parents must be ordinary directories; an existing leaf can be
    constrained with directory=True/False. The returned path is absolute only.
    """
    if directory is not None and type(directory) is not bool:
        raise ValueError('directory must be a boolean or None.')
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Unsafe path.')
    for item in tuple(reversed(path.parents)) + (path,):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links and reparse points are not accepted.')
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Invalid path parent.')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('Nonregular filesystem entries are not accepted.')
        if item == path and directory is not None and stat.S_ISDIR(info.st_mode) != directory:
            raise ValueError('Invalid output path type.')
    return path


def _checked(path, directory=None):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Unsafe path.')
    for item in tuple(reversed(path.parents)) + (path,):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Links and reparse points are not accepted.')
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Invalid path parent.')
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise ValueError('A directory is required.')
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise ValueError('A regular file is required.')
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('Nonregular filesystem entries are not accepted.')
    return path


def _relative(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or '\x00' in value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Unsafe relative path.')
    return PurePosixPath(value).as_posix()


def _inside(root, value):
    path = Path(value)
    if path.is_absolute():
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            raise ValueError('Path is outside the artifact root.') from None
    else:
        relative = path.as_posix()
    relative = _relative(relative)
    path = _checked(root / relative, directory=False)
    if path.resolve().parent != root and root not in path.resolve().parents:
        raise ValueError('Path is outside the artifact root.')
    return relative, path


def _hash_file(path):
    path = _checked(path, directory=False)
    before = path.stat()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(str(path), flags)
    digest, count = hashlib.sha256(), 0
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError('A regular file is required.')
        while True:
            block = stream.read(_CHUNK)
            if not block:
                break
            digest.update(block)
            count += len(block)
        finished = os.fstat(stream.fileno())
    after = _checked(path, directory=False).stat()
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if not (signature(before) == signature(opened) == signature(finished) == signature(after)):
        raise ValueError('File changed while hashing.')
    if count != after.st_size:
        raise ValueError('File changed while hashing.')
    return count, digest.hexdigest()


def fingerprint(project):
    """Hash the source inventory, including additions/deletions, without Git."""
    root = _checked(project, directory=True)
    _checked(root / 'app.py', directory=False)
    for required in ('reporting_workspace', 'tests'):
        _checked(root / required, directory=True)
    files = {}

    def add(path):
        if path.name.lower().endswith(_EXCLUDED_SUFFIXES):
            return
        relative, safe_path = _inside(root, path)
        files[relative] = _hash_file(safe_path)[1]

    def walk(directory):
        for path in sorted(directory.iterdir()):
            checked = _checked(path)
            if checked.is_dir():
                if checked.name.lower() not in _EXCLUDED_DIRS:
                    walk(checked)
            else:
                add(checked)

    for path in sorted(root.iterdir()):
        if (path.name.endswith('.py') or
                (path.name.startswith('requirements') and path.name.endswith('.txt'))):
            add(path)
    for name in ('reporting_workspace', 'assets', 'tests', 'tools'):
        path = root / name
        if path.exists() or path.is_symlink():
            walk(_checked(path, directory=True))
    for name, suffix in (('benchmarks', '.py'), ('.github/workflows', None)):
        path = root / name
        if path.exists() or path.is_symlink():
            for child in sorted(_checked(path, directory=True).iterdir()):
                _checked(child)
                if suffix is None or child.name.endswith(suffix):
                    add(child)
    ordered = dict(sorted(files.items()))
    canonical = json.dumps({'schema': 1, 'files': ordered}, sort_keys=True,
                           separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return {'schema': 1, 'files': ordered, 'digest': hashlib.sha256(canonical).hexdigest()}


def artifact_manifest(root, paths):
    root = _checked(root, directory=True)
    if isinstance(paths, (str, bytes, os.PathLike)):
        raise ValueError('Artifact paths must be an iterable of paths.')
    manifest, seen = [], set()
    for value in paths:
        relative, path = _inside(root, value)
        if relative.casefold() in seen:
            raise ValueError('Duplicate artifact path.')
        seen.add(relative.casefold())
        count, digest = _hash_file(path)
        manifest.append({'path': relative, 'bytes': count, 'sha256': digest})
    return sorted(manifest, key=lambda item: item['path'])


def verify_artifacts(root, manifest):
    """Return fixed validation codes; metadata is never executed or imported."""
    try:
        root = _checked(root, directory=True)
    except (OSError, ValueError, TypeError):
        return ['invalid_root']
    if type(manifest) is not list:
        return ['invalid_manifest']
    errors, seen = [], set()
    for index, item in enumerate(manifest):
        code = 'artifact[{}]:'.format(index)
        if type(item) is not dict or set(item) != {'path', 'bytes', 'sha256'}:
            errors.append(code + 'invalid_entry')
            continue
        try:
            relative = _relative(item['path'])
        except ValueError:
            errors.append(code + 'unsafe_path')
            continue
        if relative.casefold() in seen:
            errors.append(code + 'duplicate_path')
            continue
        seen.add(relative.casefold())
        if type(item['bytes']) is not int or item['bytes'] < 0:
            errors.append(code + 'invalid_size')
            continue
        digest = item['sha256']
        if type(digest) is not str or len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            errors.append(code + 'invalid_hash')
            continue
        try:
            _, path = _inside(root, relative)
            actual_size, actual_hash = _hash_file(path)
        except FileNotFoundError:
            errors.append(code + 'missing')
            continue
        except (OSError, ValueError, TypeError):
            errors.append(code + 'unsafe_or_unreadable')
            continue
        if actual_size != item['bytes']:
            errors.append(code + 'size_mismatch')
        if actual_hash != digest:
            errors.append(code + 'hash_mismatch')
    return errors


def write_json(path, value, overwrite=False):
    """Atomic same-directory JSON write; default refuses existing evidence."""
    if type(overwrite) is not bool:
        raise ValueError('overwrite must be a boolean.')
    path = Path(path).absolute()
    _relative(path.name)
    parent = _checked(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        _checked(path, directory=False)
        if not overwrite:
            raise FileExistsError('Destination already exists.')
    encoded = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(prefix='.acceptance-json-', dir=str(parent))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _checked(parent, directory=True)
        if overwrite:
            os.replace(temporary, str(path))
        else:
            os.link(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _WindowsJob:
    """Unnamed, noninheritable Job Object; descendants cannot break away.

    API contract: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
    """
    def __init__(self):
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [('user', ctypes.c_longlong), ('job_user', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t),
                        ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [('times', ctypes.c_longlong * 4), ('faults', wintypes.DWORD),
                        ('total', wintypes.DWORD), ('active', wintypes.DWORD),
                        ('terminated', wintypes.DWORD)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'IsProcessInJob': ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = args, result
        self.kernel, self.accounting = kernel, Accounting
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def cleanup(self):
        from ctypes import wintypes

        # Job active counts can reach zero before the process object signals
        # final exit and releases inherited log handles. Retain handles for the
        # current owned members before terminating; never terminate by PID.
        handles, snapshot_ok = [], False
        capacity = 64
        try:
            for attempt in range(4):
                buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
                okay = self.kernel.QueryInformationJobObject(
                    self.handle, 3, buffer, ctypes.sizeof(buffer), None)
                header = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))
                if not okay:
                    if ctypes.get_last_error() == 234 and header[0] <= 4096:
                        capacity = max(capacity * 2, header[0])
                        continue
                    break
                if header[1] > capacity:
                    break
                members = ctypes.cast(ctypes.addressof(buffer) + 8, ctypes.POINTER(ctypes.c_size_t))
                snapshot_ok = True
                for index in range(header[1]):
                    process_handle = self.kernel.OpenProcess(0x101000, False, members[index])
                    if not process_handle:
                        if ctypes.get_last_error() != 87:  # Already exited PID is benign.
                            snapshot_ok = False
                        continue
                    belongs = wintypes.BOOL()
                    if not self.kernel.IsProcessInJob(process_handle, self.handle, ctypes.byref(belongs)):
                        snapshot_ok = False
                        self.kernel.CloseHandle(process_handle)
                    elif belongs.value:
                        handles.append(process_handle)
                    else:
                        # Exited/reused ID is not this job's process. Do not wait
                        # on or signal an unrelated process merely sharing it.
                        self.kernel.CloseHandle(process_handle)
                break
            if not self.kernel.TerminateJobObject(self.handle, 124):
                return False
            deadline = time.monotonic() + 5
            for process_handle in handles:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if self.kernel.WaitForSingleObject(process_handle, remaining) != 0:
                    return False
            if not snapshot_ok:
                return False
            while time.monotonic() < deadline:
                info = self.accounting()
                if not self.kernel.QueryInformationJobObject(
                        self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                    return False
                if info.active == 0:
                    return True
                time.sleep(0.02)
            return False
        finally:
            for process_handle in handles:
                self.kernel.CloseHandle(process_handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _cleanup_posix(process):
    def exists():
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    for sent, wait_seconds in ((signal.SIGTERM, 1), (signal.SIGKILL, 4)):
        try:
            os.killpg(process.pid, sent)
        except ProcessLookupError:
            process.wait(timeout=1)
            return True
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            process.poll()
            if not exists():
                return True
            time.sleep(0.02)
    return not exists()


def run_process(argv, cwd, env, log_path, timeout_seconds):
    """Run trusted argv once, preserving raw bytes; clean up only the owned tree.

    Success requires returncode == 0, no launch_error, no timeout AND confirmed
    cleanup. Windows Job assignment failure never releases the GO handshake.
    POSIX uses a dedicated process group; trusted children must not detach into
    another session. This is lifecycle control, not a hostile-program sandbox.
    """
    started = time.monotonic()
    result = dict(returncode=None, timed_out=False, launch_error=None,
                  elapsed_seconds=0.0, cleanup_confirmed=True)
    process, job, assigned, status_path = None, None, False, None
    try:
        if (type(argv) is not list or not argv or
                any(type(value) is not str or '\x00' in value for value in argv) or not argv[0]):
            raise ValueError('argv must be an explicit list of strings.')
        if os.name == 'nt' and Path(argv[0]).suffix.lower() in ('.bat', '.cmd'):
            raise ValueError('Shell script execution is not supported.')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError('A positive finite timeout is required.')
        if type(env) is not dict or any(type(key) is not str or not key or '=' in key or '\x00' in key
                                       or type(value) is not str or '\x00' in value for key, value in env.items()):
            raise ValueError('Environment must be an explicit string mapping.')
        cwd = _checked(cwd, directory=True)
        log_path = Path(log_path).absolute()
        _relative(log_path.name)
        _checked(log_path.parent, directory=True)
        if log_path.exists() or log_path.is_symlink():
            raise FileExistsError('Log destination already exists.')
        with log_path.open('xb') as log:
            if os.name == 'nt':
                job = _WindowsJob()
                descriptor, status_path = tempfile.mkstemp(prefix='.acceptance-host-', dir=str(log_path.parent))
                os.close(descriptor)
                host = _checked(Path(__file__).with_name('_acceptance_process.py'), directory=False)
                process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(host), status_path, '--'] + argv,
                                           cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                           stdout=log, stderr=subprocess.STDOUT, shell=False,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(process)
                assigned = True
                process.stdin.write(b'GO\n')
                process.stdin.flush()
                process.stdin.close()
            else:
                process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, shell=False,
                                           start_new_session=True)
            try:
                process.wait(timeout=max(0.001, timeout_seconds - (time.monotonic() - started)))
                if os.name == 'nt':
                    with open(status_path, encoding='utf-8') as status:
                        outcome = json.load(status)
                    if (set(outcome) != {'returncode', 'launch_error'}
                            or (outcome['returncode'] is not None and type(outcome['returncode']) is not int)
                            or (outcome['launch_error'] is not None and
                                (type(outcome['launch_error']) is not str or not outcome['launch_error'].isidentifier()))):
                        raise ValueError('Invalid process host result.')
                    result.update(outcome)
                else:
                    result['returncode'] = process.returncode
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
    except Exception as error:
        result['launch_error'] = type(error).__name__
    finally:
        if process is not None:
            try:
                if os.name == 'nt':
                    if assigned:
                        result['cleanup_confirmed'] = job.cleanup()
                    else:
                        process.terminate()  # The host never received GO; it has no children.
                    process.wait(timeout=5)
                else:
                    result['cleanup_confirmed'] = _cleanup_posix(process)
                    process.wait(timeout=5)
                if result['timed_out']:
                    result['returncode'] = process.returncode
            except Exception:
                result['cleanup_confirmed'] = False
            finally:
                if process.stdin is not None:
                    process.stdin.close()
        if job is not None:
            job.close()
        if status_path is not None:
            try:
                os.unlink(status_path)
            except OSError:
                result['cleanup_confirmed'] = False
        result['elapsed_seconds'] = round(time.monotonic() - started, 6)
    return result
