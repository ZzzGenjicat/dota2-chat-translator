import socket
import sys
import unittest
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class OfflineSmokeGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('dota2_map_assistant.release_smoke'), 'Offline release self-test is missing')

    def test_remote_connections_are_rejected_before_network_access(self):
        from dota2_map_assistant import release_smoke
        with release_smoke.offline_network_guard():
            with socket.socket() as client:
                with self.assertRaisesRegex(RuntimeError, 'remote network'):
                    client.connect(('203.0.113.1', 443))
            with self.assertRaisesRegex(RuntimeError, 'remote network'):
                socket.create_connection(('example.com', 443))

    def test_loopback_gsi_still_works_and_guard_restores_socket(self):
        from dota2_map_assistant import release_smoke
        original = socket.socket.connect
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            with release_smoke.offline_network_guard(), socket.socket() as client:
                client.connect(server.getsockname())
                connection, _ = server.accept()
                with connection:
                    client.sendall(b'GSI')
                    self.assertEqual(connection.recv(3), b'GSI')
        self.assertIs(socket.socket.connect, original)
