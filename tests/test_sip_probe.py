import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "sip_probe", Path(__file__).parents[1] / "tools/sip_probe.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SipProbeTests(unittest.TestCase):
    def test_rfc2617_digest_vector(self):
        # Same RFC 2617 credentials/nonce; method is REGISTER for this probe.
        import hashlib

        f = {
            "realm": "testrealm@host.com",
            "nonce": "dcd98b7102dd2f0e8b11d0f600bfb0c093",
            "qop": "auth",
            "algorithm": "MD5",
        }
        value = m.digest(
            "Mufasa", "Circle Of Life", "sip:host.com", f, cnonce="0a4f113b"
        )

        def h(s):
            return hashlib.md5(s.encode()).hexdigest()

        expected = h(
            h("Mufasa:testrealm@host.com:Circle Of Life")
            + ":"
            + f["nonce"]
            + ":00000001:0a4f113b:auth:"
            + h("REGISTER:sip:host.com")
        )
        self.assertIn('response="' + expected + '"', value)
        self.assertNotIn("Circle Of Life", value)

    def test_challenge_quoted_comma_and_escape(self):
        f = m.challenge_fields(
            'Digest realm="a,b", nonce="abc", qop="auth,auth-int", opaque="a\\"b"'
        )
        self.assertEqual(f["realm"], "a,b")
        self.assertEqual(f["opaque"], 'a"b')

    def test_algorithms(self):
        for alg in ("MD5", "MD5-sess", "SHA-256", "SHA-256-sess", "SHA-512-256"):
            self.assertIn(
                "algorithm=" + alg.upper(),
                m.digest(
                    "u",
                    "p",
                    "sip:x",
                    {"realm": "r", "nonce": "n", "algorithm": alg, "qop": "auth"},
                ),
            )

    def test_unsupported_auth_fails(self):
        for f in (
            {"realm": "r", "nonce": "n", "algorithm": "unknown"},
            {"realm": "r", "nonce": "n", "qop": "auth-int"},
        ):
            with self.assertRaises(m.ProbeError):
                m.digest("u", "p", "sip:x", f)

    def test_header_injection_rejected(self):
        with self.assertRaises(m.ProbeError):
            m.quote("a\r\nInjected: yes")

    def test_response_and_compact_headers(self):
        parsed = m.parse_response(
            b"SIP/2.0 200 OK\r\ni: call\r\nv: SIP/2.0/UDP x;branch=b\r\nCSeq: 1 REGISTER\r\n\r\n"
        )
        self.assertEqual(parsed[0], 200)
        self.assertEqual(parsed[1]["call-id"], ["call"])

    def test_requests_are_not_responses(self):
        self.assertIsNone(m.parse_response(b"INVITE sip:u@x SIP/2.0\r\n\r\n"))


class RegistrationTests(unittest.TestCase):
    def probe(self):
        from unittest.mock import Mock

        p = object.__new__(m.RegistrationProbe)
        p.sock = Mock()
        p.host = "sip.example.test"
        p.uri = "sip:sip.example.test"
        p.redirected = False
        p.auth = {}
        p.auth_count = {}
        return p

    def test_public_redirect_preserves_account_uri(self):
        p = self.probe()
        p.redirect({"contact": ["<sip:8.8.8.8:5060>"]})
        p.sock.connect.assert_called_once_with(("8.8.8.8", 5060))
        self.assertEqual(p.uri, "sip:sip.example.test")
        with self.assertRaises(m.ProbeError):
            p.redirect({"contact": ["sip:8.8.8.8:5060"]})

    def test_private_or_nonliteral_redirect_rejected(self):
        for value in (
            "sip:127.0.0.1:5060",
            "sip:10.0.0.1:5060",
            "sip:someone@8.8.8.8:5060",
            "sip:host.test:5060",
        ):
            with self.assertRaises(m.ProbeError):
                self.probe().redirect({"contact": [value]})

    def test_digest_challenge_then_success(self):
        from unittest.mock import Mock

        p = self.probe()
        p.request = Mock(
            side_effect=[
                (
                    401,
                    {
                        "www-authenticate": [
                            'Digest realm="test", nonce="n", qop="auth"'
                        ]
                    },
                ),
                (200, {}),
            ]
        )
        self.assertEqual(p.register(60), 200)
        self.assertEqual(p.request.call_count, 2)

    def test_bad_password_does_not_loop(self):
        from unittest.mock import Mock

        p = self.probe()
        p.request = Mock(
            return_value=(401, {"www-authenticate": ['Digest realm="test", nonce="n"']})
        )
        with self.assertRaises(m.ProbeError):
            p.register(60)
        self.assertEqual(p.request.call_count, 2)
