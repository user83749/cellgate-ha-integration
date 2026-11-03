"""Run the private companion service and its dedicated Asterisk process."""

import asyncio
import json
import logging
import os
import re
import signal
from pathlib import Path

from aiohttp import web

from .config import BackendError, Configuration
from .server import Bridge, application

SECRET = re.compile(r"(password|secret|response|nonce|cnonce)=\S+", re.I)


async def relay_log(path):
    """Copy Asterisk notices/warnings (rejected calls, codec/NAT errors) to the add-on log."""
    position = 0
    while True:
        try:
            size = path.stat().st_size
            if size < position:
                position = 0
            if size > position:
                with path.open(errors="replace") as handle:
                    handle.seek(position)
                    for line in handle.read().splitlines():
                        if line.strip():
                            logging.info("asterisk: %s", SECRET.sub(r"\1=***", line))
                    position = handle.tell()
        except OSError:
            pass
        await asyncio.sleep(1)


async def main():
    options = json.loads(
        Path(os.environ.get("CELLGATE_OPTIONS", "/data/options.json")).read_text()
    )
    token = options.get("api_token", "")
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 128
        or not token.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise BackendError(
            "Set an independent API token with at least 32 characters in the add-on options"
        )
    root = Path(os.environ.get("CELLGATE_RUNTIME", "/data/runtime"))
    config = Configuration(root, options)
    await asyncio.to_thread(config.initialize)
    if config.external:
        logging.info("Advertising public address %s to CellGate", config.external)
    else:
        logging.warning(
            "Public address unknown (STUN failed and external_address empty); "
            "intercom audio/video will not reach this backend"
        )
    (root / "log" / "messages.log").unlink(missing_ok=True)
    log_task = asyncio.create_task(relay_log(root / "log" / "messages.log"))
    engine = await asyncio.create_subprocess_exec(
        "/usr/sbin/asterisk",
        "-f",
        "-g",
        "-C",
        str(root / "asterisk.conf"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    bridge = Bridge(config, token)
    runner = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await bridge.start()
        runner = web.AppRunner(application(bridge), access_log=None)
        await runner.setup()
        await web.TCPSite(
            runner, "0.0.0.0", int(options.get("api_port", 28190))
        ).start()
        logging.info("CellGate intercom backend ready")
        stop_task = asyncio.create_task(stopped.wait())
        exit_task = asyncio.create_task(engine.wait())
        await asyncio.wait([stop_task, exit_task], return_when=asyncio.FIRST_COMPLETED)
        for task in (stop_task, exit_task):
            task.cancel()
        await asyncio.gather(stop_task, exit_task, return_exceptions=True)
    finally:
        if runner:
            await runner.cleanup()
        await bridge.close()
        if engine.returncode is None:
            engine.terminate()
            try:
                await asyncio.wait_for(engine.wait(), 10)
            except TimeoutError:
                engine.kill()
                await engine.wait()
        await asyncio.to_thread(config.write, "provider.conf", "")
        log_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        asyncio.run(main())
    except BackendError as err:
        logging.error("%s", err)
        raise SystemExit(1) from None
    except Exception:
        # Do not print account values, URLs, passwords, or remote descriptions.
        logging.error(
            "CellGate intercom backend stopped; check its configuration and port availability"
        )
        raise SystemExit(1) from None
