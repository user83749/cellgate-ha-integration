import { randomBytes } from 'node:crypto';
import type { RTCAVSignalingSetup, RTCSessionControl, RTCSignalingChannel, RTCSignalingSession } from '@scrypted/sdk';
import { Backend, BackendError, BackendState } from './backend';

/** Pure signaling adapter: Scrypted's WebRTC plugin owns decoding and microphone RTP. */
export class CallSignaling implements RTCSignalingChannel {
    private active?: { sid: string; callId: string; close: () => Promise<void> };
    constructor(private backend: Backend, private getState: () => BackendState | undefined) {}
    async startRTCSignalingSession(session: RTCSignalingSession): Promise<RTCSessionControl> {
        const current = this.getState();
        if (!current?.call) throw new BackendError('No incoming CellGate call is available');
        if (this.active) throw new BackendError('This intercom already has a viewer');
        const sid = randomBytes(16).toString('hex');
        const callId = current.call.id;
        let closed = false;
        let reserved = false;
        const pending: RTCIceCandidateInit[] = [];
        let chain: Promise<unknown> = Promise.resolve();
        let iceFailure = false;
        const close = async () => {
            if (closed) return;
            closed = true;
            if (this.active?.sid === sid) this.active = undefined;
            if (reserved) await this.backend.detach(sid).catch(() => {});
        };
        this.active = { sid, callId, close };
        const queueCandidate = async (candidate: RTCIceCandidateInit) => {
            if (closed) return;
            if (!reserved) { if (pending.length < 64) pending.push(candidate); return; }
            chain = chain.then(async () => { if (!closed) await this.backend.candidate(sid, candidate); }).catch(() => { iceFailure = true; });
            await chain;
        };
        const setup: RTCAVSignalingSetup = {
            type: 'offer',
            audio: { direction: 'sendrecv' },
            video: { direction: 'recvonly' },
        };
        try {
            const options = session.options || await session.getOptions();
            if (options?.requiresOffer) throw new BackendError('This viewer requires a proxy; enable the Scrypted WebRTC extension');
            const offer = options?.offer || await session.createLocalDescription('offer', setup, options?.disableTrickle ? undefined : queueCandidate);
            if (offer.type !== 'offer' || !offer.sdp?.startsWith('v=0') || offer.sdp.length > 60000) throw new BackendError('Viewer returned an invalid WebRTC offer');
            if (closed || this.getState()?.call?.id !== callId) throw new BackendError('CellGate call ended before the viewer connected');
            await this.backend.prepare(sid, callId, offer.sdp);
            reserved = true;
            if (closed) { await this.backend.detach(sid).catch(() => {}); throw new BackendError('Viewer was closed while connecting'); }
            for (const candidate of pending) await queueCandidate(candidate);
            pending.length = 0;
            if (options?.disableTrickle) await this.backend.candidate(sid, { candidate: "" });
            if (iceFailure) throw new BackendError('Unable to forward viewer ICE candidates');
            const answer = await this.backend.answer(sid);
            if (closed || this.getState()?.call?.id !== callId) throw new BackendError('CellGate call ended while connecting');
            await session.setRemoteDescription({ type: 'answer', sdp: answer }, setup);
            return {
                getRefreshAt: async () => {},
                extendSession: async () => {},
                endSession: close,
                // The WebRTC extension starts/stops its microphone RTP forwarder.
                // Asterisk accepts that return audio on this same sendrecv media leg;
                // it needs no vendor-specific StartTalk request or renegotiation.
                setPlayback: async () => {},
            };
        } catch (error) {
            await close();
            if (error instanceof BackendError) throw error;
            throw new BackendError('Unable to negotiate the Scrypted intercom viewer');
        }
    }
    async callEnded(callId?: string) {
        if (this.active && this.active.callId !== callId) await this.active.close();
    }
    async close() { await this.active?.close(); }
}

/** Reconnecting the event stream must not generate a second doorbell press. */
export class RingState {
    private seen?: string;
    update(state: BackendState): boolean {
        const key = `${state.epoch}:${state.ring_sequence}:${state.call?.id || ''}`;
        const ring = !!state.call && !state.call.answered && key !== this.seen;
        if (state.call) this.seen = key;
        return ring;
    }
}
