"""Mobile contract and failure tests. All responses are synthetic."""

import asyncio
import base64
import importlib
import json
from pathlib import Path
import sys
import time
import types
import unittest

root = Path(__file__).resolve().parents[1]
package = types.ModuleType("cellgate_test_client")
package.__path__ = [str(root / "custom_components" / "cellgate")]
sys.modules[package.__name__] = package
api = importlib.import_module("cellgate_test_client.api")


def jwt(exp=None):
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"exp": exp or time.time() + 3600}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"header.{payload}.signature"


class Response:
    def __init__(self, status=200, data=None):
        self.status, self.data = status, data

    async def __aenter__(self):
        if isinstance(self.data, BaseException):
            raise self.data
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        await asyncio.sleep(0)
        return self.data


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected additional network request")
        return self.responses.pop(0)


def ok(data):
    return Response(data={"isSuccess": True, "data": data})


def login(token=None):
    return Response(data={"isSuccess": True, "token": token or jwt()})


def locations():
    return {
        "mapLocations": [
            {
                "Description": "Synthetic property",
                "DeviceList": [
                    {"DeviceId": 42, "ProertyLocaitonId": 12},
                ],
            }
        ]
    }


def device():
    return {
        "devices": {
            "description": "Synthetic gate",
            "hardwareType": "WTM-EVO",
            "ports": [
                {
                    "id": 7,
                    "description": "Gate",
                    "deviceType": "Gate",
                    "isOnline": 1,
                    "lastState": "0",
                },
                {
                    "id": 8,
                    "description": "Camera",
                    "deviceType": "Camera",
                    "isOnline": 1,
                },
            ],
        }
    }


def port():
    return api.gate_ports(device()["devices"], "42", "12", "Synthetic property")["42_7"]


def sip_config():
    return {
        "username": "synthetic-sip-user",
        "password": "synthetic-sip-password",
        "host": "sip.example.test:5060",
        "sipisRegServer": "",
        "codecOrder": "106,9,0,8",
        "codecOrder3G": "105,9,0,8",
        "videoCodecOrder": "99,34",
        "videoCodecOrder3G": "99,34",
        "dtmfOrder": "rfc2833",
        "extProvInterval": "3600",
    }


class ParsingTests(unittest.TestCase):
    def test_sip_account_fields_and_private_repr(self):
        account = api.sip_account(sip_config())
        self.assertEqual(account.username, "synthetic-sip-user")
        self.assertEqual(account.password, "synthetic-sip-password")
        self.assertEqual(account.host, "sip.example.test:5060")
        self.assertIsNone(account.registration_server)
        self.assertEqual(account.audio_codec_order, (106, 9, 0, 8))
        self.assertEqual(account.dtmf_order, ("rfc2833",))
        self.assertEqual(account.provisioning_interval, 3600)
        self.assertNotIn(account.password, repr(account))
        self.assertNotIn(account.username, repr(account))

    def test_sip_errors_do_not_include_values(self):
        value = {**sip_config(), "codecOrder": "synthetic-private-value"}
        with self.assertRaises(api.CellGateError) as error:
            api.sip_account(value)
        self.assertNotIn("synthetic-private-value", str(error.exception))
        with self.assertRaises(api.CellGateError):
            api.sip_account({**sip_config(), "password": ""})

    def test_server_state_labels_and_device_momentary_capability(self):
        data = device()["devices"]
        data["supportsMomentary"] = False
        data["ports"][0].update(
            descriptionState0="Synthetic closed label",
            descriptionState1="Synthetic open label",
        )
        result = api.gate_ports(data, "42", "12", "")["42_7"]
        self.assertEqual(result["state_label"], "Synthetic closed label")
        self.assertFalse(result["momentary_allowed"])

    def test_typo_and_correct_property_keys(self):
        data = locations()["mapLocations"]
        self.assertEqual(
            api.device_locations(data), [("42", "12", "Synthetic property")]
        )
        data[0]["DeviceList"][0] = {"DeviceId": "42", "PropertyLocationId": "12"}
        self.assertEqual(api.device_locations(data)[0][:2], ("42", "12"))

    def test_missing_identifiers_fail(self):
        with self.assertRaises(api.CellGateError):
            api.device_locations([{"DeviceList": [{"DeviceId": 42}]}])

    def test_deduplicate_locations(self):
        data = locations()["mapLocations"]
        self.assertEqual(len(api.device_locations(data + data)), 1)

    def test_only_gate_and_door_ports(self):
        ports = api.gate_ports(device()["devices"], "42", "12", "")
        self.assertEqual(set(ports), {"42_7"})
        self.assertEqual(ports["42_7"]["last_state"], "0")
        self.assertTrue(ports["42_7"]["is_online"])

    def test_malformed_port_schema(self):
        with self.assertRaises(api.CellGateError):
            api.gate_ports({"ports": {}}, "42", "12", "")

    def test_jwt_cache_hint(self):
        self.assertEqual(api.token_expiry(jwt(123456)), 123456)
        for token in ["opaque", "a.e30.c", "a.b.c"]:
            self.assertIsNone(api.token_expiry(token))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_supplies_sip_without_another_request(self):
        response = login()
        response.data["sipUser"] = {
            "IsSipAppUser": True,
            "ResponseDetails": {"account": sip_config()},
        }
        session = Session(response)
        client = api.CellGateClient(session, "synthetic", "synthetic")
        account = await client.get_sip_account()
        self.assertEqual(account.host, "sip.example.test:5060")
        self.assertEqual(len(session.calls), 1)

    async def test_authenticated_sip_post_format(self):
        session = Session(login(), ok({"sipInfo": {"account": sip_config()}}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        account = await client.get_sip_account()
        self.assertEqual(account.username, "synthetic-sip-user")
        url, options = session.calls[1]
        self.assertEqual(
            url, "https://cellgate.zapopen.com/api/CellCam/GetSipInformationForUser"
        )
        self.assertTrue(options["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(options["headers"]["AppVersion"], "17")
        self.assertIsNone(options["data"])
        await client.get_sip_account()
        self.assertEqual(len(session.calls), 2)

    async def test_sip_refresh_replaces_in_memory_configuration(self):
        response = login()
        response.data["sipUser"] = {
            "IsSipAppUser": True,
            "ResponseDetails": {"account": sip_config()},
        }
        updated = {**sip_config(), "password": "synthetic-updated-password"}
        session = Session(response, ok({"sipInfo": {"account": updated}}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        await client.get_sip_account()
        account = await client.get_sip_account(refresh=True)
        self.assertEqual(account.password, "synthetic-updated-password")

    async def test_missing_sip_support(self):
        session = Session(login(), ok({"sipInfo": None}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        self.assertIsNone(await client.get_sip_account())

    async def test_invalid_sip_information_is_rejected(self):
        session = Session(
            login(), ok({"sipInfo": {"account": {"username": "synthetic"}}})
        )
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateError):
            await client.get_sip_account()

    async def test_mobile_login_headers_and_no_portal_request(self):
        session = Session(login())
        client = api.CellGateClient(
            session, "synthetic@example.test", "synthetic-password"
        )
        await client.login()
        url, options = session.calls[0]
        self.assertEqual(url, "https://cellgate.zapopen.com/api/CellCam/Login")
        self.assertEqual(options["headers"]["AuthUsername"], "synthetic@example.test")
        self.assertEqual(options["headers"]["AuthPassword"], "synthetic-password")
        self.assertNotIn("Authorization", options["headers"])
        self.assertEqual(options["headers"]["DeviceToken"], "")
        self.assertIsNone(options["data"])
        self.assertFalse(options["allow_redirects"])

    async def test_mobile_discovery_header_and_form_patterns(self):
        session = Session(login(), ok(locations()), ok(device()))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        gates = await client.discover()
        self.assertEqual(set(gates), {"42_7"})
        self.assertTrue(session.calls[1][0].endswith("/GetMapLocationList"))
        self.assertEqual(session.calls[1][1]["headers"]["AppVersion"], "17")
        self.assertIsNone(session.calls[1][1]["data"])
        self.assertEqual(
            session.calls[2][1]["data"], {"DeviceID": "42", "PropertyLocationID": "12"}
        )
        self.assertEqual(
            session.calls[2][1]["headers"]["Content-Type"],
            "application/x-www-form-urlencoded",
        )

    async def test_exact_momentary_form(self):
        session = Session(login(), ok({"result": "queued"}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        await client.command(port())
        self.assertTrue(session.calls[1][0].endswith("/GateCommand"))
        self.assertEqual(
            session.calls[1][1]["data"],
            {
                "GateCommand": "MOMENTARY_OPEN",
                "Hours": "0",
                "DeviceID": "42",
                "PortID": "7",
                "AccessCode": "1",
            },
        )

    async def test_timeout_never_replays_command(self):
        session = Session(login(), Response(data=TimeoutError()))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateCommandUncertain):
            await client.command(port())
        self.assertEqual(len(session.calls), 2)

    async def test_auth_failure_never_replays_command(self):
        session = Session(login(), Response(401))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateAuthError):
            await client.command(port())
        self.assertEqual(len(session.calls), 2)
        self.assertIsNone(client._token)

    async def test_permission_failure_does_not_refresh_session(self):
        session = Session(login(), Response(403))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGatePermissionError):
            await client.command(port())
        self.assertIsNotNone(client._token)

    async def test_read_401_refreshes_once(self):
        session = Session(login(), Response(401), login(), ok({"mapLocations": []}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        self.assertEqual(await client.discover(), {})
        self.assertEqual(len(session.calls), 4)

    async def test_repeated_read_401_stops(self):
        session = Session(login(), Response(401), login(), Response(401))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateAuthError):
            await client.discover()
        with self.assertRaises(api.CellGateAuthError):
            await client.discover()
        self.assertEqual(len(session.calls), 4)

    async def test_rejected_login_not_repeated_automatically(self):
        session = Session(
            Response(
                data={
                    "isSuccess": False,
                    "message": "server text deliberately not propagated",
                }
            )
        )
        client = api.CellGateClient(session, "synthetic", "synthetic")
        for _ in range(2):
            with self.assertRaises(api.CellGateAuthError):
                await client.discover()
        self.assertEqual(len(session.calls), 1)

    async def test_concurrent_reads_share_session(self):
        session = Session(login(), ok({"mapLocations": []}), ok({"mapLocations": []}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        await asyncio.gather(client.discover(), client.discover())
        self.assertEqual(sum(url.endswith("/Login") for url, _ in session.calls), 1)

    async def test_camera_and_restricted_ports_never_send(self):
        session = Session()
        client = api.CellGateClient(session, "synthetic", "synthetic")
        for candidate in [
            {**port(), "device_type": "Camera"},
            {**port(), "momentary_allowed": False},
        ]:
            with self.assertRaises(api.CellGatePermissionError):
                await client.command(candidate)
        self.assertEqual(session.calls, [])

    async def test_explicit_rejection_is_not_reported_as_success(self):
        session = Session(login(), Response(data={"isSuccess": False}))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateRejectedError):
            await client.command(port())
        self.assertEqual(len(session.calls), 2)

    async def test_processing_reply_is_accepted(self):
        session = Session(
            login(),
            Response(data={"isSuccess": False, "data": {"result": "Processing"}}),
        )
        client = api.CellGateClient(session, "synthetic", "synthetic")
        await client.command(port())
        self.assertEqual(len(session.calls), 2)

    async def test_invalid_ack_is_uncertain(self):
        session = Session(login(), Response(data=[]))
        client = api.CellGateClient(session, "synthetic", "synthetic")
        with self.assertRaises(api.CellGateCommandUncertain):
            await client.command(port())


if __name__ == "__main__":
    unittest.main()
