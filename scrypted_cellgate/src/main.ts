import sdk, { BinarySensor, DeviceProvider, Online, RTCSignalingChannel, RTCSignalingSession, ScryptedDeviceBase, ScryptedDeviceType, ScryptedInterface, Setting, Settings, SettingValue } from '@scrypted/sdk';
import { Backend, BackendError, BackendState } from './backend';
import { CallSignaling, RingState } from './signaling';

const NATIVE_ID = 'intercom';

class CellGateCamera extends ScryptedDeviceBase implements RTCSignalingChannel, BinarySensor, Online {
    private ring = new RingState();
    private pulse?: ReturnType<typeof setTimeout>;
    signaling?: CallSignaling;
    state?: BackendState;
    constructor() { super(NATIVE_ID); this.online = false; this.binaryState = false; }
    connect(backend: Backend) { this.signaling = new CallSignaling(backend, () => this.state); }
    update(state: BackendState) {
        this.state = state;
        this.online = !!state.call;
        void this.signaling?.callEnded(state.call?.id);
        if (this.ring.update(state)) {
            if (this.pulse) clearTimeout(this.pulse);
            this.binaryState = false;
            this.binaryState = true;
            this.pulse = setTimeout(() => { this.binaryState = false; }, 1000);
        }
        if (!state.call) this.binaryState = false;
    }
    async disconnect() {
        this.state = undefined;
        this.online = false;
        this.binaryState = false;
        if (this.pulse) clearTimeout(this.pulse);
        await this.signaling?.close();
    }
    async startRTCSignalingSession(session: RTCSignalingSession) {
        if (!this.signaling || !this.state?.call) throw new BackendError('No incoming CellGate call is available');
        return this.signaling.startRTCSignalingSession(session);
    }
}

export default class CellGateProvider extends ScryptedDeviceBase implements DeviceProvider, Settings {
    private camera!: CellGateCamera;
    private backend?: Backend;
    private worker?: Promise<void>;
    private generation = 0;
    private retryTimer?: ReturnType<typeof setTimeout>;
    private wakeRetry?: () => void;
    private status = 'Enter backend settings';
    private ready: Promise<void>;
    private settingsQueue: Promise<unknown> = Promise.resolve();
    constructor() {
        super();
        this.ready = this.initialize();
        this.ready.catch(() => { this.console.error('Unable to initialize the CellGate plugin'); });
    }
    private async initialize() {
        await sdk.deviceManager.onDevicesChanged({ devices: [{ nativeId: NATIVE_ID, name: 'CellGate Intercom', type: ScryptedDeviceType.Doorbell,
            interfaces: [ScryptedInterface.RTCSignalingChannel, ScryptedInterface.BinarySensor, ScryptedInterface.Online],
            info: { manufacturer: 'CellGate', model: 'Call camera' },
        }] });
        this.camera = new CellGateCamera();
        await this.restart();
    }
    async getDevice(nativeId: string) {
        await this.ready;
        if (nativeId !== NATIVE_ID) throw new Error('Unknown CellGate device');
        return this.camera;
    }
    async releaseDevice(_id: string, nativeId: string) {
        if (nativeId === NATIVE_ID) await this.camera?.disconnect();
    }
    async getSettings(): Promise<Setting[]> {
        return [
            { key: 'backend_url', title: 'CellGate Intercom Backend URL', description: 'Internal URL of the existing backend, for example http://192.168.2.196:28190', value: this.storage.getItem('backend_url') || '' },
            { key: 'api_token', title: 'Backend API Token', type: 'password', description: 'The same api_token configured in the CellGate Intercom app.', value: this.storage.getItem('api_token') || '' },
            { key: 'account_email', title: 'CellGate Account Email', description: 'The same CellGate email used in the Home Assistant integration. No CellGate password is needed.', value: this.storage.getItem('account_email') || '' },
            { key: 'status', title: 'Connection Status', readonly: true, value: this.status },
            { key: 'call_state', title: 'Call State', readonly: true, value: this.camera?.state?.state || 'idle' },
        ];
    }
    async putSetting(key: string, value: SettingValue) {
        if (!['backend_url', 'api_token', 'account_email'].includes(key) || typeof value !== 'string') throw new Error('Invalid CellGate setting');
        await this.ready;
        this.storage.setItem(key, value.trim());
        this.settingsQueue = this.settingsQueue.catch(() => {}).then(() => this.restart());
        await this.settingsQueue;
    }
    private async restart() {
        const generation = ++this.generation;
        this.backend?.closeEvents();
        if (this.retryTimer) clearTimeout(this.retryTimer);
        this.wakeRetry?.();
        await this.camera.disconnect();
        try {
            this.backend = new Backend(this.storage.getItem('backend_url') || '', this.storage.getItem('api_token') || '', this.storage.getItem('account_email') || '');
        } catch (error) {
            this.status = error instanceof BackendError ? error.message : 'Invalid backend settings';
            return;
        }
        const backend = this.backend;
        this.camera.connect(backend);
        this.worker = this.monitor(backend, generation);
        // monitor handles its own safe errors; no unhandled promise rejection.
        void this.worker;
    }
    private async monitor(backend: Backend, generation: number) {
        let delay = 3000;
        while (this.generation === generation) {
            try {
                const state = await backend.connect();
                if (generation !== this.generation) return;
                this.camera.update(state);
                this.status = 'Connected to CellGate Intercom';
                delay = 3000;
                await backend.events(state => { if (generation === this.generation) this.camera.update(state); });
                if (generation === this.generation) await this.camera.disconnect();
            } catch (error) {
                if (generation !== this.generation) return;
                this.status = error instanceof BackendError ? error.message : 'CellGate backend connection failed';
                this.console.warn(this.status);
                await this.camera.disconnect();
                if (error instanceof BackendError && error.fatal) return;
            }
            if (generation !== this.generation) return;
            this.status = 'Reconnecting to CellGate Intercom';
            await new Promise<void>(resolve => { this.wakeRetry = resolve; this.retryTimer = setTimeout(resolve, delay); });
            this.wakeRetry = undefined;
            delay = Math.min(delay * 2, 60000);
        }
    }
}
