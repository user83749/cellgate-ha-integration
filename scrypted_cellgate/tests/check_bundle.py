"""Start the compiled Python plugin using its bundled SDK, without credentials."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZipFile

bundle = Path(__file__).resolve().parents[1] / 'out/plugin.zip'
with ZipFile(bundle) as archive:
    names = set(archive.namelist())
    assert not any(n.endswith(('.pyc', '.pyo')) for n in names)
    assert {'main.py', 'requirements.txt', 'cellgate_plugin/provider.py', 'scrypted_sdk/__init__.py'} <= names
    for name in names:
        if name.endswith('.py') and name.startswith('cellgate_plugin/'):
            text = archive.read(name).decode()
            assert 'custom_components' not in text
            assert 'homeassistant' not in text
            compile(text, name, 'exec')
    assert 'aiortc==1.15.0' in archive.read('requirements.txt').decode()
sys.path.insert(0, str(bundle))
import scrypted_sdk as sdk
import main

class Storage(dict):
    def getItem(self, key): return self.get(key)
    def setItem(self, key, value): self[key] = value
    def removeItem(self, key): self.pop(key, None)

async def check():
    stores, states = {}, {}
    devices = []
    async def discovered(value): devices.extend(value['devices'])
    sdk.deviceManager = SimpleNamespace(
        getDeviceStorage=lambda key: stores.setdefault(key, Storage()),
        getDeviceState=lambda key: states.setdefault(key, SimpleNamespace()),
        onDevicesChanged=discovered,
    )
    plugin = main.create_scrypted_plugin()
    await plugin.ready
    assert devices[0]['nativeId'] == 'intercom'
    assert (await plugin.getDevice('intercom')).online is False
    settings = {setting['key']: setting for setting in await plugin.getSettings()}
    assert settings['password']['type'] == 'password'
    assert 'backend_url' not in settings and 'api_token' not in settings
    assert plugin.http is None and plugin.manager is None
    await plugin.close()

asyncio.run(check())
print('Compiled Python plugin startup and direct-login settings passed')
