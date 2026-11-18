"""fork v109: the shared test setup (conftest.py) refuses real network connections."""

import os
import socket
import unittest


@unittest.skipIf(os.environ.get("TAGCAST_TESTS_ALLOW_NETWORK") == "1" or socket.socket.connect.__name__ != "_guarded_connect",
                 "network allowed on purpose, or not run through pytest")
class NetworkGuardTests(unittest.TestCase):
    def test_a_real_site_is_refused(self):
        with socket.socket() as s, self.assertRaises(ConnectionRefusedError):
            s.connect(("93.184.216.34", 80))

    def test_this_machine_is_still_allowed(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            with socket.socket() as client:
                client.connect(server.getsockname())
