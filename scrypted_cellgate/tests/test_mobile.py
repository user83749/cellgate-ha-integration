import asyncio
import json
from types import SimpleNamespace
import pytest
from cellgate_plugin.mobile import MobileClient, CellGateAuthError, CellGateError

ACCOUNT = {'username': 'synthetic-sip', 'password': 'synthetic-secret', 'host': 'sip.example.test'}


class Response:
    def __init__(self, body, status=200):
        self.status = status
        self.body = json.dumps(body).encode()
        self.content = self
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def iter_chunked(self, size):
        # Deliberately fragment JSON across reads.
        for offset in range(0, len(self.body), 7):
            yield self.body[offset:offset+7]


class Session:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        assert url.startswith('https://cellgate.zapopen.com/api/CellCam/')
        assert kwargs['allow_redirects'] is False
        return self.replies.pop(0)


def login(account=ACCOUNT):
    return Response({'isSuccess': True, 'token': 'synthetic-token', 'sipUser': {
        'IsSipAppUser': True, 'ResponseDetails': {'account': account}}})


async def test_direct_mobile_login_is_cached_and_never_uses_a_backend():
    session = Session([login()])
    mobile = MobileClient(session, 'synthetic@example.test', ' secret ')
    results = await asyncio.gather(*(mobile.get_sip_account() for _ in range(10)))
    assert len(session.requests) == 1
    assert all(a.host == 'sip.example.test' for a in results)
    assert 'synthetic-secret' not in repr(results[0])
    url, options = session.requests[0]
    assert url.endswith('/Login')
    assert options['headers']['AuthUsername'] == 'synthetic@example.test'
    assert options['headers']['AuthPassword'] == ' secret '
    assert options['headers']['AppType'] == 'android'
    assert 'data' not in options


async def test_missing_login_provisioning_uses_authenticated_mobile_endpoint():
    session = Session([login(None), Response({'isSuccess': True, 'data': {'sipInfo': {'account': ACCOUNT}}})])
    mobile = MobileClient(session, 'x', 'y')
    assert (await mobile.get_sip_account()).username == ACCOUNT['username']
    url, options = session.requests[1]
    assert url.endswith('/GetSipInformationForUser')
    assert options['headers']['Authorization'] == 'Bearer synthetic-token'


async def test_rejected_credentials_do_not_loop_or_log_server_message():
    session = Session([Response({'isSuccess': False, 'message': 'private-server-body'})])
    mobile = MobileClient(session, 'x', 'secret')
    for _ in range(4):
        with pytest.raises(CellGateAuthError) as error:
            await mobile.get_sip_account()
        assert 'private-server-body' not in str(error.value)
    assert len(session.requests) == 1


async def test_authenticated_read_retries_only_once_then_blocks():
    session = Session([login(None), Response({}, 401), login(None), Response({}, 401)])
    mobile = MobileClient(session, 'x', 'y')
    for _ in range(3):
        with pytest.raises(CellGateAuthError):
            await mobile.get_sip_account()
    assert len(session.requests) == 4


async def test_oversized_response_is_rejected_and_clear_drops_private_data():
    response = Response({'large': 'x' * 1_048_577})
    session = Session([response])
    mobile = MobileClient(session, 'x', 'y')
    with pytest.raises(CellGateError, match='size limit'):
        await mobile.get_sip_account()
    mobile.clear()
    assert mobile.password is None and mobile.token is None and mobile.account is None
