import assert from 'node:assert/strict';
import { createServer, IncomingMessage, ServerResponse } from 'node:http';
import { AddressInfo } from 'node:net';
import { once } from 'node:events';
import { test } from 'node:test';
import { WebSocketServer } from 'ws';
import { Backend, backendAddress, ownerId } from '../src/backend';

const token = 'synthetic-' + 'x'.repeat(32);
const email = 'synthetic@example.test';
const current = { api_version: 1, owner: ownerId(email), epoch: 'one', state: 'idle', registered: true, call: null, ring_sequence: 0, can_talk: false };
async function server(handler: (req: IncomingMessage, res: ServerResponse) => void) {
    const http = createServer(handler);
    http.listen(0, '127.0.0.1');
    await once(http, 'listening');
    return { http, url: `http://127.0.0.1:${(http.address() as AddressInfo).port}`, close: async () => { http.closeAllConnections(); http.close(); await once(http, 'close'); } };
}
function json(res: ServerResponse, value: unknown, status = 200) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); }

test('reject HA URLs and embedded credentials before any connection', () => {
    for (const value of ['http://ha:8123', 'http://user:pass@host:28190', 'https://host/?token=secret', 'http://host/path']) assert.throws(() => backendAddress(value));
    assert.equal(ownerId(' SYNTHETIC@EXAMPLE.TEST '), ownerId(email));
});

test('identify service without credentials before authenticated health/state', async () => {
    const requests: string[] = [];
    const s = await server((req, res) => {
        requests.push(req.url!);
        if (req.url === '/v1/health') { assert.equal(req.headers.authorization, undefined); json(res, { product: 'cellgate-intercom', api_version: 1, ready: true }); }
        else { assert.equal(req.headers.authorization, 'Bearer ' + token); json(res, req.url!.includes('authenticated') ? {} : current); }
    });
    try { const backend = new Backend(s.url, token, email); assert.equal((await backend.connect()).owner, current.owner); assert.equal(requests.length, 3); }
    finally { await s.close(); }
});

test('wrong service or redirect never receives the API token', async () => {
    let count = 0;
    const s = await server((req, res) => { count++; assert.equal(req.headers.authorization, undefined); json(res, { product: 'home-assistant' }); });
    try { await assert.rejects(new Backend(s.url, token, email).connect(), /not a compatible/); assert.equal(count, 1); }
    finally { await s.close(); }
});

test('rejected authentication is blocked without repeated failed attempts', async () => {
    let auth = 0;
    const s = await server((req, res) => {
        if (!req.headers.authorization) json(res, { product: 'cellgate-intercom', api_version: 1, ready: true });
        else { auth++; json(res, {}, 401); }
    });
    try {
        const backend = new Backend(s.url, token, email);
        await assert.rejects(backend.connect(), /token rejected/);
        await assert.rejects(backend.connect(), /blocked/);
        assert.equal(auth, 1);
    } finally { await s.close(); }
});

test('backend error bodies never leak through to plugin errors', async () => {
    const s = await server((req, res) => {
        if (req.url === '/v1/health') json(res, { product: 'cellgate-intercom', api_version: 1, ready: true });
        else if (req.url === '/v1/health/authenticated') json(res, {});
        else json(res, { error: 'private-password-and-sdp' }, 409);
    });
    try { await assert.rejects(new Backend(s.url, token, email).connect(), error => { assert.doesNotMatch(String(error), /private-password/); return true; }); }
    finally { await s.close(); }
});

test('authenticated event channel supplies state and can be closed on settings change', async () => {
    const s = await server((req, res) => {
        if (req.url === '/v1/health') json(res, { product: 'cellgate-intercom', api_version: 1, ready: true });
        else json(res, req.url!.includes('authenticated') ? {} : current);
    });
    const ws = new WebSocketServer({ noServer: true });
    s.http.on('upgrade', (req, socket, head) => {
        assert.equal(req.headers.authorization, 'Bearer ' + token);
        ws.handleUpgrade(req, socket, head, connection => { connection.send(JSON.stringify(current)); });
    });
    const backend = new Backend(s.url, token, email);
    try {
        await backend.connect();
        let received!: () => void;
        const seen = new Promise<void>(resolve => { received = resolve; });
        const events = backend.events(value => { assert.equal(value.owner, current.owner); received(); });
        await seen;
        backend.closeEvents();
        await events;
    } finally { for (const client of ws.clients) client.terminate(); ws.close(); await s.close(); }
});
