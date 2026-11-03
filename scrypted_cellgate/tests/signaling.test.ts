import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { RTCSignalingSession } from '@scrypted/sdk';
import { Backend, BackendError, BackendState } from '../src/backend';
import { CallSignaling, RingState } from '../src/signaling';

export function state(answered = false): BackendState {
    return { api_version: 1, owner: 'synthetic-account', epoch: 'process-one', registered: true, state: answered ? 'active' : 'ringing', call: { id: 'call-one', answered }, ring_sequence: 1, can_talk: true };
}
function fakeBackend() {
    const calls: string[] = [];
    return { calls,
        prepare: async (_sid: string, callId: string, sdp: string) => { assert.equal(callId, 'call-one'); assert.equal(sdp, 'v=0\r\n'); calls.push('prepare'); },
        candidate: async (_sid: string, candidate: RTCIceCandidateInit) => { calls.push('candidate:' + candidate.candidate); },
        answer: async () => { calls.push('answer'); return 'v=0\r\ns=answer\r\n'; },
        detach: async () => { calls.push('detach'); },
    };
}
function viewer(earlyCandidate = true): RTCSignalingSession {
    return { options: {}, __proxy_props: { options: {} }, getOptions: async () => ({}),
        createLocalDescription: async (type, setup, send) => {
            assert.equal(type, 'offer');
            assert.equal(setup.audio?.direction, 'sendrecv');
            assert.equal(setup.video?.direction, 'recvonly');
            if (earlyCandidate) await send?.({ candidate: 'candidate:synthetic', sdpMid: '0', sdpMLineIndex: 0 });
            return { type: 'offer', sdp: 'v=0\r\n' };
        },
        setRemoteDescription: async answer => { assert.equal(answer.type, 'answer'); assert.match(answer.sdp!, /s=answer/); },
        addIceCandidate: async () => {},
    };
}

test('early ICE waits for reservation, and closing or stopping talk never hangs up provider', async () => {
    const backend = fakeBackend();
    const signaling = new CallSignaling(backend as unknown as Backend, () => state());
    const control = await signaling.startRTCSignalingSession(viewer());
    assert.deepEqual(backend.calls, ['prepare', 'candidate:candidate:synthetic', 'answer']);
    await control.setPlayback({ audio: true, video: false });
    await control.setPlayback({ audio: false, video: false });
    assert.equal(backend.calls.length, 3);
    await control.endSession();
    await control.endSession();
    assert.deepEqual(backend.calls, ['prepare', 'candidate:candidate:synthetic', 'answer', 'detach']);
    // Reopening is the same provider call, with a new viewer reservation.
    const reopened = await signaling.startRTCSignalingSession(viewer(false));
    await reopened.endSession();
    assert.equal(backend.calls.filter(c => c === 'prepare').length, 2);
});

test('call ending while reserving cleans up late reservation without answering', async () => {
    const backend = fakeBackend();
    let finish!: () => void;
    let started!: () => void;
    const pending = new Promise<void>(resolve => { finish = resolve; });
    const reserved = new Promise<void>(resolve => { started = resolve; });
    backend.prepare = async () => { backend.calls.push('prepare'); started(); await pending; };
    const signaling = new CallSignaling(backend as unknown as Backend, () => state());
    const opening = signaling.startRTCSignalingSession(viewer());
    await reserved;
    await signaling.callEnded();
    finish();
    await assert.rejects(opening, /closed while connecting/);
    assert.deepEqual(backend.calls, ['prepare', 'detach']);
});

test('failed signaling cleans up its viewer and never replays an offer', async () => {
    const backend = fakeBackend();
    backend.answer = async () => { backend.calls.push('answer'); throw new BackendError('synthetic failure'); };
    const signaling = new CallSignaling(backend as unknown as Backend, () => state());
    await assert.rejects(signaling.startRTCSignalingSession(viewer(false)), /synthetic failure/);
    assert.deepEqual(backend.calls, ['prepare', 'answer', 'detach']);
});

test('idle camera cannot answer or create a call', async () => {
    const backend = fakeBackend();
    const signaling = new CallSignaling(backend as unknown as Backend, () => undefined);
    await assert.rejects(signaling.startRTCSignalingSession(viewer()), /No incoming/);
    assert.deepEqual(backend.calls, []);
});

test('backend reconnect and answered-state updates do not repeat a doorbell press', () => {
    const rings = new RingState();
    assert.equal(rings.update(state()), true);
    assert.equal(rings.update(state()), false);
    assert.equal(rings.update(state(true)), false);
    assert.equal(rings.update({ ...state(), call: null }), false);
    assert.equal(rings.update({ ...state(), ring_sequence: 2, call: { id: 'call-two', answered: false } }), true);
});
