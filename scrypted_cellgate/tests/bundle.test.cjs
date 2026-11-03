const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

test('built plugin starts with SDK runtime globals and discovers one persistent call-only doorbell', async () => {
    const devices = [];
    const state = {};
    const settings = new Map();
    const log = { log() {}, warn() {}, error() {} };
    const context = {
        require, Buffer, URL, process: { env: {} }, console: log,
        setTimeout, clearTimeout, setInterval, clearInterval,
        exports: {},
        deviceManager: {
            onDevicesChanged: async manifest => { devices.push(...manifest.devices); },
            getDeviceLogger: () => log,
            getDeviceConsole: () => log,
            getDeviceStorage: () => ({ getItem: key => settings.get(key) || null, setItem: (key, value) => settings.set(key, value) }),
            getDeviceState: () => state,
        },
        endpointManager: {}, mediaManager: {}, systemManager: {}, pluginHostAPI: {},
    };
    vm.runInNewContext(fs.readFileSync('out/main.nodejs.js', 'utf8'), context, { filename: 'cellgate-plugin-bundle.js' });
    const Provider = context.exports.default;
    assert.equal(typeof Provider, 'function');
    const provider = new Provider();
    const camera = await provider.getDevice('intercom');
    assert.equal(devices.length, 1);
    assert.equal(devices[0].type, 'Doorbell');
    assert.equal(devices[0].nativeId, 'intercom');
    assert.deepEqual(Array.from(devices[0].interfaces), ['RTCSignalingChannel', 'BinarySensor', 'Online']);
    assert.equal(camera, await provider.getDevice('intercom'));
    assert.equal(camera.online, false);
    assert.equal(camera.binaryState, false);
    const fields = await provider.getSettings();
    assert.equal(fields.filter(field => !field.readonly).length, 3);
    assert.equal(fields.find(field => field.key === 'api_token').type, 'password');
    await assert.rejects(camera.startRTCSignalingSession({}), /No incoming/);
    await provider.releaseDevice('synthetic', 'intercom');
});
