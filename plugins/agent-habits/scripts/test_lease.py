# copied from agent-habits <version> (plugins/agent-habits/scripts/test_lease.py); refresh with the habits-update skill
"""lease.py's tests: python -I test_lease.py [-v] (~3.5 s). Finds lease.py next to this file, else in ../tools.

Holders are separate python processes (a real pid each); every test uses its own temp lease dir.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE if (HERE / "lease.py").exists() else HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
import lease  # noqa: E402

HOLD = """import sys, time; sys.path.insert(0, {tools!r}); import lease
l = lease.acquire("res", "child hold", {ttl}, owner="child")
print("held", flush=True)
time.sleep({secs})
l.release()
"""


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, TOOL_LEASE_DIR=self.tmp.name)
        for k in [k for k in self.env if k.startswith("TOOL_LEASE_") and k != "TOOL_LEASE_DIR"]:
            del self.env[k]
        self.old = {k: os.environ.get(k) for k in ("TOOL_LEASE_DIR", lease.token_var("res"))}
        os.environ["TOOL_LEASE_DIR"] = self.tmp.name
        os.environ.pop(lease.token_var("res"), None)
        self.procs = []
        self.poll = lease.POLL
        lease.POLL = 0.2

    def tearDown(self):
        lease.POLL = self.poll
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def child(self, secs, ttl=60):
        """A process holding "res" for `secs` seconds; returns once it holds it."""
        code = HOLD.format(tools=str(TOOLS), ttl=ttl, secs=secs)
        p = subprocess.Popen([sys.executable, "-I", "-c", code], env=self.env, stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_1_free_then_release(self):
        held = lease.acquire("res", "purpose 1", 60, owner="me")
        info = lease._read("res")
        self.assertEqual((info["owner"], info["purpose"], info["pids"][0]["pid"]), ("me", "purpose 1", os.getpid()))
        self.assertAlmostEqual(info["expires"] - info["started"], 60, places=3)
        held.release()
        self.assertFalse((Path(self.tmp.name) / "res.json").exists())

    def test_2_held_names_holder_and_cli_exits_75(self):
        self.child(10)
        with self.assertRaises(lease.Held) as e:
            lease.acquire("res", "mine", 60)
        self.assertIn("held by child (child hold)", str(e.exception))
        out = []
        with self.assertRaises(SystemExit) as ex:
            lease.acquire_or_exit("res", "mine", 60, out=out.append)
        self.assertEqual(ex.exception.code, lease.BUSY)
        self.assertIn("child hold", out[0])

    def test_3_dead_holder_taken_over_at_once(self):
        p = self.child(30)
        p.kill()
        p.wait()
        t = time.time()
        held = lease.acquire("res", "after kill", 60, owner="me")
        self.assertLess(time.time() - t, 1.0)
        self.assertEqual(held.info["owner"], "me")

    def test_4_expired_with_live_holder_taken_over(self):
        self.child(30, ttl=0.5)
        time.sleep(0.7)
        self.assertEqual(lease.acquire("res", "after expiry", 60, owner="me").info["owner"], "me")

    def test_5_wait_until_free_and_wait_timeout(self):
        self.child(1.0)
        t = time.time()
        lease.acquire("res", "queued", 60, wait=3).release()
        self.assertGreater(time.time() - t, 0.5)   # waited for the child; past wait=3 acquire raises Held
        self.child(10)
        t = time.time()
        with self.assertRaises(SystemExit) as ex:
            lease.acquire_or_exit("res", "queued", 60, wait=1, out=lambda s: None)
        self.assertEqual(ex.exception.code, lease.BUSY)
        # waited its second, then gave up well before the child's 10 s (loose upper bound: runs in the pre-commit)
        self.assertGreaterEqual(time.time() - t, 0.9)
        self.assertLess(time.time() - t, 5.0)

    def test_6_busy_without_lease_counts_as_held(self):
        with self.assertRaises(lease.Held) as e:
            lease.acquire("res", "mine", 60, busy=lambda: "Tool.exe running")
        self.assertIn("no lease (Tool.exe running)", str(e.exception))
        self.assertFalse((Path(self.tmp.name) / "res.json").exists())

    def test_7_inherited_token_is_reentrant(self):
        parent = lease.acquire("res", "parent", 60, owner="me")
        os.environ.update(parent.env())
        child = lease.acquire("res", "child run", 60, busy=lambda: "would be busy")
        self.assertFalse(child.owned)
        child.release()
        self.assertEqual(lease._read("res")["token"], parent.info["token"])
        parent.release()

    def test_8_release_by_other_token_keeps_lease(self):
        held = lease.acquire("res", "mine", 60)
        self.assertFalse(lease.release("res", "not-the-token"))
        self.assertTrue((Path(self.tmp.name) / "res.json").exists())
        held.release()

    def test_9_holder_pids_dead_at_handover_frees_lease(self):
        p = subprocess.Popen([sys.executable, "-I", "-c", "pass"])
        p.wait()
        held = lease.acquire("res", "launcher", 60)
        held.set_pids([p.pid])   # the tool it started is already gone
        self.assertFalse((Path(self.tmp.name) / "res.json").exists())

    def cli(self, *args, owner=None):
        env = {k: v for k, v in self.env.items() if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID")}
        if owner:
            env["CLAUDE_CODE_SESSION_ID"] = owner
        return subprocess.run([sys.executable, "-I", str(TOOLS / "lease.py")] + list(args), env=env,
                              capture_output=True, text=True)

    def test_10_cli_release_only_your_own_without_force(self):
        lease_file = Path(self.tmp.name) / "res.json"
        self.assertEqual(self.cli("hold", "res").returncode, 0)   # the user's hold
        self.assertNotEqual(self.cli("release", "res", owner="session-a").returncode, 0)   # a session can't drop it
        self.assertTrue(lease_file.exists())
        self.assertEqual(self.cli("release", "res", "--force", owner="session-a").returncode, 0)
        self.assertFalse(lease_file.exists())
        self.assertEqual(self.cli("hold", "res").returncode, 0)
        self.assertEqual(self.cli("release", "res").returncode, 0)   # the user drops their own
        self.assertFalse(lease_file.exists())
        self.assertEqual(self.cli("hold", "res", "--owner", "session-a").returncode, 0)
        self.assertEqual(self.cli("release", "res", owner="session-a").returncode, 0)   # a session drops its own
        self.assertFalse(lease_file.exists())

    def test_11_cli_release_of_stale_lease_keeps_a_new_holder(self):
        """Checked stale, then another run takes it before the delete: the delete must miss the new holder's lease."""
        old = lease.acquire("res", "old", 60, owner="old")
        real_stale = lease.stale
        taken = []

        def stale_then_taken(info, now=None):
            lease._write(dict(old.info, token="new-holder", owner="new"))
            taken.append(True)
            return True

        lease.stale = stale_then_taken
        argv = sys.argv
        sys.argv = ["lease.py", "release", "res"]
        try:
            lease.main()
        finally:
            lease.stale, sys.argv = real_stale, argv
        self.assertTrue(taken)
        self.assertEqual(lease._read("res")["token"], "new-holder")


if __name__ == "__main__":
    unittest.main()
