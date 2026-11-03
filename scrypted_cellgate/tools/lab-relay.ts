/** Test-only loopback transport: drive the real adapter with aiortc-generated SDP. */
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { AddressInfo } from 'node:net';
import type { RTCSessionControl, RTCSignalingSession } from '@scrypted/sdk';
import { Backend } from '../src/backend';
import { CallSignaling } from '../src/signaling';

async function main() {
    const options = JSON.parse(readFileSync(process.argv[2], 'utf8'));
    const backend = new Backend('http://127.0.0.1:28190', options.api_token, 'synthetic@example.test');
    let current = await backend.connect();
    const signaling = new CallSignaling(backend, () => current);
    let control: RTCSessionControl | undefined;
    const server = createServer(async (req, res) => {
        try {
            const chunks: Buffer[] = [];
            for await (const chunk of req) chunks.push(chunk);
            const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};
            let result = {};
            if (req.url === '/offer') {
                current = await backend.state();
                let answer: string | undefined;
                const session: RTCSignalingSession = {
                    options: {}, __proxy_props: { options: {} }, getOptions: async () => ({}),
                    createLocalDescription: async (_type, setup, send) => {
                        if (setup.audio?.direction !== 'sendrecv' || setup.video?.direction !== 'recvonly') throw new Error('Incorrect media directions');
                        for (const candidate of body.candidates || []) await send?.(candidate);
                        return { type: 'offer', sdp: body.sdp };
                    },
                    setRemoteDescription: async value => { answer = value.sdp; },
                    addIceCandidate: async () => {},
                };
                control = await signaling.startRTCSignalingSession(session);
                result = { answer };
            } else if (req.url === '/close') {
                await control?.endSession();
                control = undefined;
            } else if (req.url === '/state') {
                current = await backend.state();
                await signaling.callEnded(current.call?.id);
                result = current;
            } else { res.writeHead(404); res.end(); return; }
            res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(result));
        } catch { res.writeHead(500); res.end(JSON.stringify({ error: 'Scrypted adapter lab request failed' })); }
    });
    server.listen(0, '127.0.0.1', () => process.stdout.write(JSON.stringify({ url: `http://127.0.0.1:${(server.address() as AddressInfo).port}` }) + '\n'));
    const close = () => { void signaling.close().finally(() => { backend.closeEvents(); server.closeAllConnections(); server.close(); }); };
    process.on('SIGTERM', close);
    process.on('SIGINT', close);
}
main().catch(() => { console.error('Unable to start the synthetic Scrypted adapter lab'); process.exit(1); });
