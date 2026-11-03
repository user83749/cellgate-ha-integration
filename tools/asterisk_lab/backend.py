"""Exercise the shipped companion API + HA manager + Asterisk with fake SIP/RTP."""

from __future__ import annotations
import array
import asyncio
import hashlib
import os
from fractions import Fraction
import json
import math
from pathlib import Path
import secrets
import sys
import types

import aiohttp
import av
from aiortc import (
    RTCConfiguration,
    RTCBundlePolicy,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.codecs import get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtp import RtpPacket
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from media import Microphone, Port
from run import BACK, FRONT, Provider, command, wait_for, wire

ROOT = Path(__file__).resolve().parents[2]
package = types.ModuleType("lab_cellgate")
package.__path__ = [str(ROOT / "custom_components/cellgate")]
sys.modules["lab_cellgate"] = package
from lab_cellgate.intercom import AsteriskCallManager


async def evaluate():
    output = ROOT / "asterisk-backend-results"
    output.mkdir(exist_ok=True)
    token = secrets.token_hex(32)
    scrypted_mode = os.environ.get("CELLGATE_LAB_ADAPTER") == "scrypted"
    owner = (
        hashlib.sha256(b"synthetic@example.test").hexdigest()[:24]
        if scrypted_mode
        else "synthetic-account"
    )
    url = "http://127.0.0.1:28190"
    options = {
        "api_token": token,
        "stun_server": "",
        "api_port": 28190,
        "sip_port": 15060,
        "asterisk_http_port": 18088,
    }
    (output / "options.json").write_text(json.dumps(options))
    report = {
        "scope": "shipped companion and HA manager, synthetic accounts, local network only"
    }
    loop = asyncio.get_running_loop()
    front = Provider(True)
    provider = Provider()
    for obj, addr in ((front, FRONT), (provider, BACK)):
        await loop.create_datagram_endpoint(lambda p=obj: p, local_addr=addr)
    name = "cellgate-backend-" + secrets.token_hex(4)
    manager = None
    browsers = []
    tasks = []
    ports = []
    relay = None
    relay_url = None
    async with aiohttp.ClientSession() as client:
        try:
            await command(
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "-v",
                f"{output}:/data",
                "cellgate-intercom:lab",
            )

            async def ready():
                async with asyncio.timeout(40):
                    while True:
                        try:
                            async with client.get(
                                url + "/v1/health",
                                headers={"Authorization": "Bearer " + token},
                            ) as response:
                                if response.status == 200:
                                    return
                        except aiohttp.ClientError:
                            pass
                        await asyncio.sleep(0.5)

            await ready()
            account = types.SimpleNamespace(
                username="synthetic-cellgate",
                password="synthetic-lab-password",
                host=f"{FRONT[0]}:{FRONT[1]}",
            )

            async def account_provider():
                return account

            manager = AsteriskCallManager(client, account_provider, owner)
            await manager.set_options({"backend_url": url, "backend_token": token})
            async with client.put(
                url + "/v1/accounts/" + owner,
                headers={"Authorization": "Bearer " + token},
                json={
                    "username": account.username,
                    "password": account.password,
                    "host": account.host,
                },
            ) as response:
                report["configuration_status"] = response.status
                report["configuration_response"] = await response.json()
            await manager.start()
            await wait_for(lambda: manager.registered, 30)
            report["registered"] = True
            if scrypted_mode:
                plugin = ROOT / "scrypted_cellgate"
                relay = await asyncio.create_subprocess_exec(
                    "node",
                    "--import",
                    "tsx",
                    "tools/lab-relay.ts",
                    str(output / "options.json"),
                    cwd=plugin,
                    stdout=asyncio.subprocess.PIPE,
                )
                line = await asyncio.wait_for(relay.stdout.readline(), 20)
                relay_url = json.loads(line)["url"]
                report["adapter"] = (
                    "scrypted_cellgate CallSignaling + Backend (SDK-shaped signaling peer)"
                )

            audio, video = Port(), Port()
            ports = [audio, video]
            for port in ports:
                await loop.create_datagram_endpoint(
                    lambda p=port: p, local_addr=(BACK[0], 0)
                )
            call_id = secrets.token_hex(12)
            to = "<sip:gate@127.0.0.1>"
            seq = 1
            invite_branch = "z9hG4bK" + secrets.token_hex(8)

            def request(method, body=""):
                branch = (
                    invite_branch
                    if method in {"INVITE", "CANCEL"}
                    else "z9hG4bK" + secrets.token_hex(8)
                )
                lines = [
                    f"{method} sip:gate@127.0.0.1:15060 SIP/2.0",
                    f"Via: SIP/2.0/UDP {BACK[0]}:{BACK[1]};branch={branch};rport",
                    "Max-Forwards: 70",
                    "From: <sip:synthetic-gate@127.0.0.2>;tag=caller",
                    f"To: {to}",
                    f"Call-ID: {call_id}",
                    f"CSeq: {seq} {method}",
                    f"Contact: <sip:synthetic-gate@{BACK[0]}:{BACK[1]}>",
                ]
                if body:
                    lines += ["Content-Type: application/sdp"]
                provider.transport.sendto(wire(lines, body), provider.peer)

            async def response_for(code, method):
                async with asyncio.timeout(20):
                    while True:
                        packet, _ = await provider.messages.get()
                        if packet.get("call-id") == call_id and packet.get(
                            "cseq"
                        ).endswith(method):
                            if packet.status == code:
                                return packet
                            if packet.status and packet.status >= 300:
                                raise RuntimeError(
                                    "Provider call rejected: " + packet.start
                                )

            sdp = "\r\n".join(
                [
                    "v=0",
                    "o=synthetic 1 1 IN IP4 127.0.0.2",
                    "s=Synthetic intercom",
                    "c=IN IP4 127.0.0.2",
                    "t=0 0",
                    f"m=audio {audio.address[1]} RTP/AVP 0",
                    "a=rtpmap:0 PCMU/8000",
                    "a=sendrecv",
                    f"m=video {video.address[1]} RTP/AVP 99",
                    "a=rtpmap:99 H264/90000",
                    "a=fmtp:99 packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1",
                    "a=sendonly",
                    "",
                ]
            )
            request("INVITE", sdp)
            await response_for(180, "INVITE")
            await wait_for(lambda: manager.state == "ringing", 10)
            original_call = manager.call.id
            report["ring_without_answer"] = not manager.call.answered
            encoded = set()
            forwarded = set()

            async def open_viewer(viewer_id, trickle=False, bundled=False):
                browser = RTCPeerConnection(
                    RTCConfiguration(
                        iceServers=[],
                        bundlePolicy=RTCBundlePolicy.MAX_BUNDLE
                        if bundled
                        else RTCBundlePolicy.BALANCED,
                    )
                )
                browsers.append(browser)
                tracks = {}

                @browser.on("track")
                def track(track):
                    tracks[track.kind] = track

                browser.addTrack(Microphone())
                browser.addTransceiver("video", direction="recvonly")
                await browser.setLocalDescription(await browser.createOffer())
                local = browser.localDescription.sdp
                candidates = []
                if bundled:
                    lines = []
                    video_section = False
                    for line in local.splitlines():
                        if line.startswith("m="):
                            video_section = line.startswith("m=video")
                        if video_section and line.startswith("a=candidate:"):
                            continue
                        lines.append(line)
                    local = "\r\n".join(lines) + "\r\n"
                if trickle:
                    lines = []
                    mid = None
                    index = -1
                    for line in local.splitlines():
                        if line.startswith("m="):
                            index += 1
                        if line.startswith("a=mid:"):
                            mid = line[6:]
                        if line.startswith("a=candidate:"):
                            candidates.append((line[2:], mid, index))
                        elif line != "a=end-of-candidates":
                            lines.append(line)
                    local = "\r\n".join(lines) + "\r\n"
                if scrypted_mode:
                    async with client.post(
                        relay_url + "/offer",
                        json={
                            "sdp": local,
                            "candidates": [
                                {
                                    "candidate": candidate,
                                    "sdpMid": mid,
                                    "sdpMLineIndex": index,
                                }
                                for candidate, mid, index in candidates
                            ]
                            + ([{"candidate": ""}] if trickle else []),
                        },
                    ) as response:
                        assert response.status == 200, "Scrypted adapter offer failed"
                        answer = (await response.json())["answer"]
                else:
                    offer_task = asyncio.create_task(manager.offer(local, viewer_id))
                    if trickle:
                        await wait_for(lambda: viewer_id in manager.sessions)
                        for candidate, mid, index in candidates:
                            await manager.candidate(viewer_id, candidate, mid, index)
                        await manager.candidate(viewer_id, "")
                    answer = await asyncio.wait_for(offer_task, 35)
                (output / (viewer_id + "-answer.sdp")).write_text(answer)
                await browser.setRemoteDescription(
                    RTCSessionDescription(answer, "answer")
                )
                receiver = next(
                    r for r in browser.getReceivers() if r.track.kind == "video"
                )
                handle = receiver._handle_rtp_packet

                async def capture(packet, *args, **kwargs):
                    forwarded.add(packet.payload)
                    await handle(packet, *args, **kwargs)

                receiver._handle_rtp_packet = capture
                return browser, tracks

            first_task = asyncio.create_task(open_viewer("viewer-one", True))
            tasks.append(first_task)
            accepted = await response_for(200, "INVITE")
            to = accepted.get("to")
            request("ACK")
            browser, tracks = await first_task
            targets = {}
            address = None
            for line in accepted.body.splitlines():
                if line.startswith("c="):
                    address = line.split()[-1]
                elif line.startswith("m="):
                    fields = line[2:].split()
                    targets[fields[0]] = [address, int(fields[1]), int(fields[3])]
            ac = RTCRtpCodecParameters(
                mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
            )
            vc = RTCRtpCodecParameters(
                mimeType="video/H264", clockRate=90000, payloadType=99
            )
            ae, ve = get_encoder(ac), get_encoder(vc)

            async def send_media():
                video_seq = 0
                for i in range(1600):
                    frame = av.AudioFrame(format="s16", layout="mono", samples=160)
                    frame.planes[0].update(
                        array.array(
                            "h",
                            (
                                int(
                                    9000
                                    * math.sin(2 * math.pi * 400 * (i * 160 + j) / 8000)
                                )
                                for j in range(160)
                            ),
                        ).tobytes()
                    )
                    frame.sample_rate = 8000
                    frame.pts = i * 160
                    frame.time_base = Fraction(1, 8000)
                    payloads, stamp = await loop.run_in_executor(None, ae.encode, frame)
                    for payload in payloads:
                        audio.transport.sendto(
                            RtpPacket(
                                payload_type=targets["audio"][2],
                                sequence_number=i,
                                timestamp=stamp,
                                ssrc=42,
                                payload=payload,
                            ).serialize(),
                            tuple(targets["audio"][:2]),
                        )
                    if i % 3 == 0:
                        frame = av.VideoFrame(160, 120, "yuv420p")
                        for plane in frame.planes:
                            plane.update(bytes([100]) * plane.buffer_size)
                        frame.pts = i * 1800
                        frame.time_base = Fraction(1, 90000)
                        payloads, stamp = await loop.run_in_executor(
                            None, ve.encode, frame, i % 30 == 0
                        )
                        for j, payload in enumerate(payloads):
                            encoded.add(payload)
                            packet = RtpPacket(
                                payload_type=targets["video"][2],
                                sequence_number=video_seq,
                                timestamp=stamp,
                                ssrc=43,
                                payload=payload,
                            )
                            packet.marker = int(j == len(payloads) - 1)
                            video_seq += 1
                            video.transport.sendto(
                                packet.serialize(), tuple(targets["video"][:2])
                            )
                    await asyncio.sleep(0.02)

            tasks.append(asyncio.create_task(send_media()))

            async def frames(tracks):
                await wait_for(lambda: set(tracks) == {"audio", "video"})
                af, vf = await asyncio.wait_for(
                    asyncio.gather(tracks["audio"].recv(), tracks["video"].recv()), 15
                )
                assert (vf.width, vf.height) == (160, 120) and af.samples > 0

            await frames(tracks)
            await wait_for(
                lambda: (
                    manager.media.get("packets", {})
                    .get("provider", {})
                    .get("video", {})
                    .get("rx_packets", 0)
                ),
                20,
            )
            await wait_for(
                lambda: (
                    manager.media.get("packets", {})
                    .get("viewer", {})
                    .get("audio", {})
                    .get("rx_packets", 0)
                ),
                20,
            )
            report["packet_diagnostics"] = manager.media
            decoder = get_decoder(ac)
            heard = False
            async with asyncio.timeout(15):
                while not heard:
                    data, _ = await audio.messages.get()
                    if len(data) < 12 or data[1] & 127 != 0:
                        continue
                    rtp = RtpPacket.parse(data)
                    for frame in decoder.decode(
                        JitterFrame(rtp.payload, rtp.timestamp)
                    ):
                        samples = array.array("h", bytes(frame.planes[0]))
                        crossings = sum(
                            (a < 0) != (b < 0) for a, b in zip(samples, samples[1:])
                        )
                        frequency = crossings * 8000 / (2 * len(samples))
                        heard = 750 < frequency < 1050
            report.update(
                {
                    "scrypted_adapter_offer_and_trickle"
                    if scrypted_mode
                    else "native_ha_manager_offer_and_trickle": True,
                    "h264_video_decoded": True,
                    "audio_decoded": True,
                    "microphone_900hz_return_pcmu": heard,
                }
            )
            assert forwarded and forwarded.issubset(encoded), (
                "Video payload changed instead of being forwarded"
            )
            report["encoded_h264_payloads_preserved"] = True
            if scrypted_mode:
                async with client.post(relay_url + "/close", json={}) as response:
                    assert response.status == 200
            else:
                await manager.close_viewer("viewer-one")
            await browser.close()
            await asyncio.sleep(1)
            assert (
                manager.call
                and manager.call.id == original_call
                and manager.call.answered
            )
            report["viewer_close_preserves_intercom_call"] = True
            browser, tracks = await open_viewer("viewer-two", bundled=True)
            await frames(tracks)
            assert manager.call.id == original_call
            report["reopen_same_intercom_call"] = True
            report["max_bundle_shared_ice_transport"] = True
            seq = 2
            request("BYE")
            await response_for(200, "BYE")
            await wait_for(lambda: manager.call is None, 10)
            async with client.get(
                url + "/v1/accounts/" + owner,
                headers={"Authorization": "Bearer " + token},
            ) as response:
                call_state = await response.json()
            assert call_state["call"] is None and call_state["viewer_stage"] == "idle"
            report["remote_bye_clears_call_and_viewers"] = not manager.sessions
            # Cancellation before opening the camera must not answer the gate.
            call_id = secrets.token_hex(12)
            to = "<sip:gate@127.0.0.1>"
            seq = 1
            invite_branch = "z9hG4bK" + secrets.token_hex(8)
            request("INVITE", sdp)
            await response_for(180, "INVITE")
            await wait_for(lambda: manager.call is not None)
            assert not manager.call.answered
            request("CANCEL")
            await response_for(200, "CANCEL")
            await wait_for(lambda: manager.call is None)
            report["remote_cancel_before_answer"] = True
            await manager.stop()
            await wait_for(
                lambda: any(
                    r["authenticated"] and r["expires"] == "0" for r in provider.records
                ),
                10,
            )
            report["contact_specific_deregistration"] = True
        finally:
            if relay:
                relay.terminate()
                try:
                    await asyncio.wait_for(relay.wait(), 8)
                except TimeoutError:
                    relay.kill()
                    await relay.wait()
            if manager:
                report["final_manager_state"] = manager.state
                report["final_manager_error"] = manager.last_error
            report["front_requests"] = list(front.records)
            report["provider_requests"] = list(provider.records)
            for diagnostic in (
                "core show channels",
                "pjsip show registrations",
                "ari show status",
                "pjsip show endpoints",
            ):
                data = await command(
                    "docker",
                    "exec",
                    name,
                    "/usr/sbin/asterisk",
                    "-C",
                    "/data/runtime/asterisk.conf",
                    "-rx",
                    diagnostic,
                    check=False,
                )
                (
                    output / (diagnostic.replace(" ", "-") + "-before-stop.txt")
                ).write_text(data)
            if manager:
                await manager.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(
                *(browser.close() for browser in browsers), return_exceptions=True
            )
            for port in ports:
                port.transport.close()
            for diagnostic in (
                "core show channels",
                "pjsip show registrations",
                "ari show status",
            ):
                data = await command(
                    "docker",
                    "exec",
                    name,
                    "/usr/sbin/asterisk",
                    "-C",
                    "/data/runtime/asterisk.conf",
                    "-rx",
                    diagnostic,
                    check=False,
                )
                (output / (diagnostic.replace(" ", "-") + ".txt")).write_text(data)
            (output / "backend.log").write_text(
                await command("docker", "logs", name, check=False)
            )
            await command("docker", "rm", "-f", name, check=False)
            front.close()
            provider.close()
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(evaluate())
