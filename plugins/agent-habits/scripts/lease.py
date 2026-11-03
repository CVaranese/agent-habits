#!/usr/bin/env python3
# copied from agent-habits <version> (plugins/agent-habits/scripts/lease.py); refresh with the habits-update skill
"""
Leases on shared tools (a game editor, an emulator, a device): one holder at a time, with an owner, a purpose and
an expiry, so parallel sessions queue instead of colliding.

    python -I tools/lease.py status                     who holds what
    python -I tools/lease.py hold editor --hours 2      take a tool by hand (the user; expiry only, no process)
    python -I tools/lease.py release editor [--force]   give it back (--force: whoever holds it)

One JSON file per resource in %LOCALAPPDATA%\\tool-leases (else ~/tool-leases; TOOL_LEASE_DIR overrides): token,
owner, purpose, the holder processes (pid + creation time, so a reused pid doesn't count), start and expiry. A lease
is stale once it has expired or all its holder processes are gone; a stale lease is taken over. The project's
launchers call acquire_or_exit(): on a held lease they print the holder and exit with BUSY (75), or poll with
--wait <s>. A child run inherits its parent's lease through the env var Lease.env() gives. The folder is per user,
not per project, so launchers in several repos share one lease per resource name.
"""

import contextlib
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

BUSY = 75   # exit code when the tool is held (EX_TEMPFAIL)
POLL = 2.0  # seconds between tries with --wait


def lease_dir():
    root = os.environ.get("TOOL_LEASE_DIR") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "tool-leases"
    return Path(root)


def default_owner():
    # the app's session id (local_..., what the sidebar and SendMessage use), else the CLI one; no Claude = the user
    return os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "user"


def token_var(resource):
    return "TOOL_LEASE_" + resource.upper()


# Processes --------------------------------------------------------------------------------------------------------
def process_created(pid):
    """Creation time of a running process (an opaque number), or None if it isn't running."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return 0
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) or code.value != 259:
            return None   # 259 = STILL_ACTIVE
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k32.GetProcessTimes(ctypes.c_void_p(handle), *[ctypes.byref(t) for t in times]):
            return None
        return times[0].value
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def running(*images):
    """A busy() check for acquire(): "<image> running" if any of these executables runs (no lease needed), else None."""
    if os.name == "nt":
        tasks = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW).stdout   # a launcher may poll from pythonw
    else:
        tasks = "".join(f'"{n.strip()}"' for n in subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True,
                                                                  text=True).stdout.splitlines())
    names = [n for n in images if f'"{n.lower()}"' in tasks.lower()]
    return f"{' and '.join(names)} running" if names else None


def holder_alive(info):
    pids = info.get("pids", [])
    if not pids:
        return True   # held by hand: only the expiry ends it
    return any(p["created"] is not None and process_created(p["pid"]) == p["created"] for p in pids)


def stale(info, now=None):
    return (now or time.time()) >= info.get("expires", 0) or not holder_alive(info)


def describe(info):
    since = time.strftime("%H:%M:%S", time.localtime(info.get("started", 0)))
    until = time.strftime("%H:%M:%S", time.localtime(info.get("expires", 0)))
    pids = ",".join(str(p["pid"]) for p in info.get("pids", [])) or "none, held by hand"
    return (f"{info.get('resource', '?')} is held by {info.get('owner', '?')} ({info.get('purpose', '?')}) "
            f"since {since}, expires {until}, pids {pids}")


class Held(Exception):
    def __init__(self, info):
        super().__init__(describe(info))
        self.info = info


# Files ------------------------------------------------------------------------------------------------------------
def _path(resource):
    return lease_dir() / f"{resource}.json"


def _read(resource):
    try:
        return json.loads(_path(resource).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(info):
    path = _path(info["resource"])
    tmp = path.with_suffix(f".{info['token']}.tmp")
    tmp.write_text(json.dumps(info, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _lock(fd, on):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK if on else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if on else fcntl.LOCK_UN)


@contextlib.contextmanager
def _mutex(resource):
    """Short lock around every read-decide-write of one lease: an OS byte lock on a file that is never deleted, so
    a crashed holder's lock goes with its process (no stale mutex to break, no delete for a scanner to block)."""
    lease_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(lease_dir() / f"{resource}.mutex", os.O_RDWR | os.O_CREAT)
    try:
        while True:
            try:
                _lock(fd, True)
                break
            except OSError:
                time.sleep(0.02)
        try:
            yield
        finally:
            _lock(fd, False)
    finally:
        os.close(fd)


# Leases -----------------------------------------------------------------------------------------------------------
class Lease:
    def __init__(self, info, owned=True):
        self.info = info
        self.owned = owned   # False: inherited from a parent run, which releases it

    @property
    def resource(self):
        return self.info["resource"]

    def env(self):
        """Env vars that let a child run use this lease instead of waiting for it."""
        return {token_var(self.resource): self.info["token"]}

    def set_pids(self, pids):
        """Make these processes the holders (e.g. the emulator a launcher started), replacing the current ones."""
        with _mutex(self.resource):
            info = _read(self.resource)
            if not info or info["token"] != self.info["token"]:
                # expired and taken over (or released): say so, the caller's tool is already running
                print(f"WARNING: lease lost: {describe(info) if info else self.resource + ' is free'}",
                      file=sys.stderr, flush=True)
                return
            info["pids"] = [{"pid": p, "created": process_created(p)} for p in pids]
            info["pids"] = [p for p in info["pids"] if p["created"] is not None]
            if not info["pids"]:   # all gone already: an empty list would mean "held by hand" until expiry
                _path(self.resource).unlink()
                self.owned = False
                return
            _write(info)
            self.info = info

    def add_pid(self, pid):
        self.set_pids([p["pid"] for p in self.info["pids"]] + [pid])

    def release(self):
        if not self.owned:
            return
        self.owned = False
        release(self.resource, self.info["token"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def acquire(resource, purpose, ttl, owner=None, wait=0.0, busy=None, pids=None):
    """Take `resource` for `ttl` seconds. Held and not stale -> raise Held, or poll up to `wait` seconds.
    `busy()` returns a reason when the tool runs without any lease (opened by hand); that counts as held.
    `pids` = the holder processes (default this one; [] = held by hand, the expiry alone ends it)."""
    deadline = time.time() + wait
    pids = [os.getpid()] if pids is None else pids
    while True:
        try:
            return _try(resource, purpose, ttl, owner or default_owner(), busy, pids)
        except Held:
            left = deadline - time.time()
            if left <= 0:
                raise
            time.sleep(min(POLL, left))


def _try(resource, purpose, ttl, owner, busy, pids):
    inherited = os.environ.get(token_var(resource))
    with _mutex(resource):
        info = _read(resource)
        if info and inherited and info["token"] == inherited and not stale(info):
            return Lease(info, owned=False)
        if info and not stale(info):
            raise Held(info)
        reason = busy() if busy else None
        if reason:
            now = time.time()
            raise Held({"resource": resource, "owner": "no lease", "purpose": reason, "started": now,
                        "expires": now, "pids": []})
        now = time.time()
        info = {"resource": resource, "token": uuid.uuid4().hex, "owner": owner, "purpose": purpose,
                "host": socket.gethostname(), "started": now, "expires": now + ttl,
                "pids": [{"pid": p, "created": process_created(p)} for p in pids]}
        _write(info)
        return Lease(info)


def flush_print(text):
    print(text, flush=True)   # a session reads the launcher's output from a file: the holder line now, not at exit


def acquire_or_exit(resource, purpose, ttl, wait=0.0, busy=None, owner=None, pids=None, out=flush_print):
    """acquire() for a launcher: on a held lease print the holder and exit BUSY; release at exit."""
    try:
        lease = acquire(resource, purpose, ttl, owner=owner, wait=0.0, busy=busy, pids=pids)
    except Held as e:
        if wait <= 0:
            out(f"BUSY: {e}. Retry later or pass --wait <s>.")
            sys.exit(BUSY)
        out(f"waiting up to {wait:.0f}s: {e}")
        try:
            lease = acquire(resource, purpose, ttl, owner=owner, wait=wait, busy=busy, pids=pids)
        except Held as e2:
            out(f"BUSY after {wait:.0f}s: {e2}")
            sys.exit(BUSY)
    import atexit
    atexit.register(lease.release)
    return lease


def release(resource, token=None):
    """Delete the lease if `token` holds it (None = whoever holds it). True if a lease was deleted."""
    with _mutex(resource):
        info = _read(resource)
        if not info or (token is not None and info.get("token") != token):
            return False
        _path(resource).unlink()
        return True


def status():
    """Every lease, each read under its mutex: on Windows an open reader makes the writer's replace / unlink fail."""
    out = []
    for path in sorted(lease_dir().glob("*.json")):
        with _mutex(path.stem):
            info = _read(path.stem)
        if info:
            out.append(info)
    return out


def pop_wait(args):
    """Take `--wait <s>` / `--wait=<s>` out of a launcher's argument list; returns (seconds, the other args)."""
    rest, wait, i = [], 0.0, 0
    while i < len(args):
        if args[i] == "--wait":
            value, i = (args[i + 1] if i + 1 < len(args) else ""), i + 2
        elif args[i].startswith("--wait="):
            value, i = args[i].split("=", 1)[1], i + 1
        else:
            rest.append(args[i])
            i += 1
            continue
        try:
            wait = float(value)
        except ValueError:
            sys.exit(f"--wait needs seconds, got: {value!r}")
    return wait, rest


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    h = sub.add_parser("hold")
    h.add_argument("resource")
    h.add_argument("--hours", type=float, default=2.0)
    h.add_argument("--purpose", default="held by hand")
    h.add_argument("--owner", default="user")
    r = sub.add_parser("release")
    r.add_argument("resource")
    r.add_argument("--force", action="store_true", help="release whoever holds it")
    a = ap.parse_args()
    if a.cmd == "status":
        leases = status()
        for info in leases:
            print(("stale: " if stale(info) else "") + describe(info))
        if not leases:
            print("no leases")
    elif a.cmd == "hold":
        try:
            info = acquire(a.resource, a.purpose, a.hours * 3600, owner=a.owner, pids=[]).info
        except Held as e:
            print(f"BUSY: {e}")
            sys.exit(BUSY)
        print("held: " + describe(info))
    elif a.cmd == "release":
        with _mutex(a.resource):
            info = _read(a.resource)
        if info and not a.force and info.get("owner") != "user" and not stale(info):
            sys.exit(f"{describe(info)}: not yours; --force to release it anyway")
        print("released" if release(a.resource) else "not held")


if __name__ == "__main__":
    main()
