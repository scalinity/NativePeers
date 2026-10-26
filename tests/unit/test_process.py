import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from nativepeers.process import ancestry, descendants, identity, matches, signal_owned


class ProcessTests(unittest.TestCase):
    def test_identity_and_stale_start(self):
        record = identity(os.getpid()).json()
        self.assertTrue(matches(record))
        self.assertFalse(matches(dict(record, start="stale")))
        self.assertFalse(signal_owned(dict(record, start="stale")))
        self.assertEqual(ancestry()[0].pid, os.getpid())

    def test_descendant_inspection_timeout_preserves_cause_and_birth_records(self):
        record = dict(pid=101, ppid=1, pgid=101, uid=os.getuid(), start='fixture', executable='/fixture')
        before = dict(record)
        failure = subprocess.TimeoutExpired(['ps', '-axo', 'pid=,ppid='], .5)
        with patch('nativepeers.process.owned_status', return_value='LIVE'), \
             patch('subprocess.run', side_effect=failure) as run:
            with self.assertRaises(TimeoutError) as error:
                descendants([record])
        self.assertIs(error.exception.__cause__, failure)
        self.assertEqual(record, before)
        self.assertEqual(run.call_args.kwargs['timeout'], .5)

    def test_terminal_topology_does_not_advertise_private_stdio_as_a_tui(self):
        import pty
        from nativepeers.process import terminal_owner
        master,slave=pty.openpty()
        child=subprocess.Popen(['sleep','5'],stdin=slave,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        try:self.assertTrue(terminal_owner(identity(child.pid).json()))
        finally:child.terminate();child.wait(timeout=3);os.close(master);os.close(slave)
        child=subprocess.Popen(['sleep','5'],stdin=subprocess.DEVNULL,start_new_session=True)
        try:self.assertFalse(terminal_owner(identity(child.pid).json()))
        finally:child.terminate();child.wait(timeout=3)

    def test_lease_eof_ends_only_owned_child(self):
        read_fd, write_fd = os.pipe()
        probe = Path(__file__).resolve().parents[1]/"probes/seams.py"
        child = subprocess.Popen([sys.executable, str(probe), "lease-child", "--lease-fd", str(read_fd)],
                                 pass_fds=(read_fd,), close_fds=True, start_new_session=True)
        os.close(read_fd)
        self.assertIsNone(child.poll())
        os.close(write_fd)
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertTrue(matches(identity(os.getpid())))


if __name__ == "__main__": unittest.main()
