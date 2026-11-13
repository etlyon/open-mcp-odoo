"""The per-visitor limits key on the visitor's real address, never on a header the visitor writes."""
import unittest

from starlette.requests import Request

from open_connectors.oauth.server import client_ip


def request(client, forwarded=None):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": client})


class ClientIp(unittest.TestCase):
    def test_a_forged_forwarded_header_is_ignored(self):
        # The proxy appended the real address; the visitor forged the first entry.
        self.assertEqual(client_ip(request(("203.0.113.7", 1234), "198.51.100.1, 203.0.113.7")), "203.0.113.7")
        self.assertEqual(client_ip(request(("203.0.113.7", 1234), "198.51.100.2")), "203.0.113.7")

    def test_no_client(self):
        self.assertEqual(client_ip(request(None)), "-")


if __name__ == "__main__":
    unittest.main()
