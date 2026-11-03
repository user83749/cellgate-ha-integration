import { createHash } from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import WebSocket from 'ws';

export interface BackendState {
    api_version: number;
    owner: string | null;
    epoch: string;
    registered: boolean;
    state: string;
    call: { id: string; answered: boolean } | null;
    ring_sequence: number;
    can_talk: boolean;
    viewer_stage?: string;
}
export class BackendError extends Error {
    constructor(message: string, public readonly fatal = false) { super(message); }
}
export function backendAddress(value: string): URL {
    let url: URL;
    try { url = new URL(value.trim()); } catch { throw new BackendError('Enter the CellGate Intercom backend URL, including port 28190', true); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/' || url.port === '8123')
        throw new BackendError('Use the CellGate Intercom backend URL, not the Home Assistant dashboard URL', true);
    return url;
}
export function ownerId(email: string): string {
    if (!email.trim()) throw new BackendError('Enter the same CellGate account email used in Home Assistant', true);
    return createHash('sha256').update(email.trim().toLowerCase()).digest('hex').slice(0, 24);
}
export function parseState(value: unknown, owner: string): BackendState {
    const v = value as BackendState;
    if (!v || v.api_version !== 1 || v.owner !== owner || typeof v.epoch !== 'string' || typeof v.state !== 'string' || !Number.isSafeInteger(v.ring_sequence) || typeof v.registered !== 'boolean' || (v.call !== null && (!v.call || typeof v.call.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(v.call.id) || typeof v.call.answered !== 'boolean')))
        throw new BackendError('Invalid backend state or a different CellGate account is configured', true);
    return v;
}

export class Backend {
    readonly url: URL;
    readonly owner: string;
    readonly path: string;
    private verified = false;
    private blocked = false;
    private sockets = new Set<WebSocket>();
    constructor(address: string, private token: string, email: string) {
        this.url = backendAddress(address);
        this.owner = ownerId(email);
        this.path = '/v1/accounts/' + this.owner;
        if (!/^[\x21-\x7e]{32,128}$/.test(token)) throw new BackendError('Use the backend API token: 32–128 visible ASCII characters', true);
    }
    private raw(method: string, path: string, data?: unknown, authorized = true): Promise<unknown> {
        if (this.blocked) return Promise.reject(new BackendError('Backend authentication is blocked; update plugin settings', true));
        if (authorized && !this.verified) return Promise.reject(new BackendError('Backend service has not been identified', true));
        return new Promise((resolve, reject) => {
            const body = data === undefined ? undefined : JSON.stringify(data);
            const transport = this.url.protocol === 'https:' ? https : http;
            const request = transport.request(new URL(path, this.url), {
                method,
                headers: {
                    Accept: 'application/json',
                    ...(authorized ? { Authorization: 'Bearer ' + this.token } : {}),
                    ...(body ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } : {}),
                },
            }, response => {
                const chunks: Buffer[] = [];
                let size = 0;
                response.on('data', (chunk: Buffer) => {
                    size += chunk.length;
                    if (size > 128000) { response.destroy(); reject(new BackendError('Backend response is too large')); return; }
                    chunks.push(chunk);
                });
                response.on('error', () => reject(new BackendError('Unable to read the backend response')));
                response.on('end', () => {
                    const status = response.statusCode || 0;
                    if (authorized && (status === 401 || status === 403)) {
                        this.blocked = true;
                        reject(new BackendError('Backend API token rejected; update plugin settings', true)); return;
                    }
                    if (status !== 200) {
                        reject(new BackendError(status === 409 ? 'Call unavailable, viewer already active, or media negotiation failed; check the backend log' : `Backend request failed (HTTP ${status})`, [301, 302, 303, 307, 308, 404].includes(status))); return;
                    }
                    try { resolve(JSON.parse(Buffer.concat(chunks).toString())); }
                    catch { reject(new BackendError('Backend returned invalid JSON')); }
                });
            });
            request.setTimeout(35000, () => request.destroy());
            request.on('error', () => reject(new BackendError('Unable to reach the CellGate Intercom backend')));
            if (body) request.write(body);
            request.end();
        });
    }
    async connect(): Promise<BackendState> {
        const health = await this.raw('GET', '/v1/health', undefined, false) as Record<string, unknown>;
        if (health?.product !== 'cellgate-intercom' || health.api_version !== 1)
            throw new BackendError('This address is not a compatible CellGate Intercom backend', true);
        if (health.ready !== true) throw new BackendError('CellGate Intercom is not ready');
        this.verified = true;
        await this.raw('GET', '/v1/health/authenticated');
        return this.state();
    }
    async state(): Promise<BackendState> { return parseState(await this.raw('GET', this.path), this.owner); }
    async events(onState: (state: BackendState) => void): Promise<void> {
        if (!this.verified || this.blocked) throw new BackendError('Backend is not authenticated', true);
        const url = new URL(this.path + '/events', this.url);
        url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
        await new Promise<void>((resolve, reject) => {
            const ws = new WebSocket(url, { headers: { Authorization: 'Bearer ' + this.token }, handshakeTimeout: 8000, maxPayload: 128000, followRedirects: false });
            this.sockets.add(ws);
            let failure: BackendError | undefined;
            let heartbeat: ReturnType<typeof setInterval> | undefined;
            let alive = true;
            ws.on('open', () => {
                heartbeat = setInterval(() => {
                    if (!alive) { failure = new BackendError('Backend event connection stopped responding'); ws.terminate(); return; }
                    alive = false; ws.ping();
                }, 20000);
            });
            ws.on('pong', () => { alive = true; });
            ws.on('message', data => {
                try { onState(parseState(JSON.parse(data.toString()), this.owner)); }
                catch { failure = new BackendError('Invalid backend event', true); ws.terminate(); }
            });
            ws.on('unexpected-response', (_req, response) => {
                response.resume();
                if ([401, 403].includes(response.statusCode || 0)) this.blocked = true;
                failure = new BackendError('Backend event connection rejected', true);
                ws.terminate();
            });
            ws.on('error', () => { failure ||= new BackendError('Backend event connection failed'); });
            ws.on('close', () => {
                if (heartbeat) clearInterval(heartbeat);
                this.sockets.delete(ws);
                failure ? reject(failure) : resolve();
            });
        });
    }
    prepare(sid: string, callId: string, sdp: string) { return this.raw('PUT', `${this.path}/sessions/${sid}`, { call_id: callId, sdp }); }
    async answer(sid: string): Promise<string> {
        const result = await this.raw('POST', `${this.path}/sessions/${sid}`, {}) as { answer?: string };
        if (typeof result?.answer !== 'string' || !result.answer.startsWith('v=0') || result.answer.length > 60000) throw new BackendError('Backend returned an invalid WebRTC answer');
        return result.answer;
    }
    candidate(sid: string, candidate: RTCIceCandidateInit) {
        return this.raw('POST', `${this.path}/sessions/${sid}/candidates`, { candidate: candidate.candidate || '', mid: candidate.sdpMid, index: candidate.sdpMLineIndex });
    }
    detach(sid: string) { return this.raw('DELETE', `${this.path}/sessions/${sid}`); }
    closeEvents() { for (const ws of this.sockets) ws.terminate(); }
}
