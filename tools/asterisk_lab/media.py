"""Synthetic SIP caller -> Asterisk -> WebRTC media test; no real accounts."""
from __future__ import annotations

import array
import asyncio
from fractions import Fraction
import math
import secrets

import aiohttp
import av
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.codecs import get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtp import RtpPacket
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from run import AUTH, BACK, Packet, response, wait_for, wire


class Port(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages = asyncio.Queue()
        self.transport = None
    def connection_made(self, transport):
        self.transport = transport
        self.address = transport.get_extra_info('sockname')
    def datagram_received(self, data, peer):
        self.messages.put_nowait((data, peer))


class Microphone(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.samples = 0
    async def recv(self):
        await asyncio.sleep(.02)
        frame = av.AudioFrame(format='s16', layout='mono', samples=960)
        frame.planes[0].update(array.array('h', (int(8000*math.sin(2*math.pi*900*(self.samples+i)/48000)) for i in range(960))).tobytes())
        frame.sample_rate = 48000
        frame.pts = self.samples
        frame.time_base = Fraction(1,48000)
        self.samples += 960
        return frame


async def evaluate_media(provider, instance):
    loop = asyncio.get_running_loop()
    audio, video = Port(), Port()
    for port in (audio, video):
        await loop.create_datagram_endpoint(lambda p=port:p, local_addr=(BACK[0],0))
    browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received = {}
    tasks = []
    @browser.on('track')
    def track_received(track):
        received[track.kind] = track
    browser.addTrack(Microphone())
    sip_id = secrets.token_hex(8)
    contact = '<sip:lab-browser@127.0.0.1;transport=ws>'
    browser_invite = None
    accepted = None
    browser_bye = asyncio.Event()
    acked = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect('http://127.0.0.1:18088/ws', protocols=['sip'])
        async def register(seq, auth=''):
            lines = ['REGISTER sip:127.0.0.1 SIP/2.0', f'Via: SIP/2.0/WS 127.0.0.1;branch=z9hG4bK{secrets.token_hex(6)};rport', 'Max-Forwards: 70',f'From: <sip:lab-browser@127.0.0.1>;tag={sip_id}', 'To: <sip:lab-browser@127.0.0.1>', f'Call-ID: {sip_id}', f'CSeq: {seq} REGISTER', f'Contact: {contact}', 'Expires: 120']
            if auth: lines.append('Authorization: '+auth)
            await ws.send_str(wire(lines).decode())
            message = await ws.receive(timeout=10)
            return Packet(message.data.encode())
        try:
            challenge = await register(1)
            assert challenge.status == 401, challenge.start
            auth = AUTH.digest('lab-browser','synthetic-browser-password','sip:127.0.0.1',AUTH.challenge_fields(challenge.get('www-authenticate')))
            registered = await register(2, auth)
            assert registered.status == 200, registered.start
            async def receive_browser():
                nonlocal browser_invite
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT: continue
                    packet = Packet(message.data.encode())
                    if packet.method == 'INVITE':
                        if browser_invite is not None: continue
                        browser_invite = packet
                        (instance.root/'browser-offer.sdp').write_text(packet.body)
                        await browser.setRemoteDescription(RTCSessionDescription(packet.body,'offer'))
                        await browser.setLocalDescription(await browser.createAnswer())
                        (instance.root/'browser-answer.sdp').write_text(browser.localDescription.sdp)
                        reply = response(packet,200,'OK',[f'Contact: {contact}'],browser.localDescription.sdp).decode()
                        reply = reply.replace('To: '+packet.get('to')+'\r\n','To: '+packet.get('to')+';tag=lab-answer\r\n')
                        await ws.send_str(reply)
                    elif packet.method == 'ACK': acked.set()
                    elif packet.method == 'BYE':
                        await ws.send_str(response(packet,200,'OK').decode())
                        browser_bye.set()
                    elif packet.method == 'OPTIONS': await ws.send_str(response(packet,200,'OK').decode())
            tasks.append(asyncio.create_task(receive_browser()))
            call_id = secrets.token_hex(8)
            peer = provider.peer
            to = '<sip:incoming-gate@127.0.0.1>'
            caller = '<sip:synthetic-gate@127.0.0.2>;tag=lab-caller'
            def request(method, seq, body=''):
                lines = [f'{method} sip:incoming-gate@127.0.0.1:15060 SIP/2.0', f'Via: SIP/2.0/UDP {BACK[0]}:{BACK[1]};branch=z9hG4bK{secrets.token_hex(6)};rport', 'Max-Forwards: 70', f'From: {caller}', f'To: {to}',f'Call-ID: {call_id}',f'CSeq: {seq} {method}',f'Contact: <sip:synthetic-gate@{BACK[0]}:{BACK[1]}>']
                if body: lines.append('Content-Type: application/sdp')
                provider.transport.sendto(wire(lines,body),peer)
            offer = '\r\n'.join(['v=0','o=synthetic 1 1 IN IP4 127.0.0.2','s=Synthetic intercom','c=IN IP4 127.0.0.2','t=0 0',f'm=audio {audio.address[1]} RTP/AVP 0','a=rtpmap:0 PCMU/8000','a=sendrecv',f'm=video {video.address[1]} RTP/AVP 99','a=rtpmap:99 H264/90000','a=fmtp:99 packetization-mode=1;profile-level-id=42e01f;level-asymmetry-allowed=1','a=sendonly',''])
            request('INVITE',1,offer)
            async with asyncio.timeout(20):
                while accepted is None:
                    packet,_ = await provider.messages.get()
                    if packet.get('call-id') != call_id: continue
                    if packet.status == 200 and packet.get('cseq').endswith('INVITE'): accepted = packet
                    elif packet.status and packet.status >= 300: raise RuntimeError('Caller rejected: '+packet.start)
            to = accepted.get('to')
            request('ACK',1)
            await asyncio.wait_for(acked.wait(),10)
            (instance.root/'provider-answer.sdp').write_text(accepted.body)
            targets = {}
            address = None
            for line in accepted.body.splitlines():
                if line.startswith('c='): address = line.split()[-1]
                elif line.startswith('m='):
                    fields = line[2:].split()
                    targets[fields[0]] = [address,int(fields[1]),int(fields[3])]
            assert targets['video'][1] != 0, accepted.body
            audio_codec = RTCRtpCodecParameters(mimeType='audio/PCMU',clockRate=8000,channels=1,payloadType=0)
            video_codec = RTCRtpCodecParameters(mimeType='video/H264',clockRate=90000,payloadType=99)
            ae, ve = get_encoder(audio_codec), get_encoder(video_codec)
            async def send_media():
                seq=0
                for i in range(800):
                    frame = av.AudioFrame(format='s16',layout='mono',samples=160)
                    frame.planes[0].update(array.array('h',(int(9000*math.sin(2*math.pi*400*(i*160+j)/8000)) for j in range(160))).tobytes())
                    frame.sample_rate=8000;frame.pts=i*160;frame.time_base=Fraction(1,8000)
                    payloads, stamp = await loop.run_in_executor(None,ae.encode,frame)
                    for payload in payloads:
                        audio.transport.sendto(RtpPacket(payload_type=targets['audio'][2],sequence_number=i,timestamp=stamp,ssrc=42,payload=payload).serialize(),tuple(targets['audio'][:2]))
                    if i%3 == 0:
                        frame = av.VideoFrame(160,120,'yuv420p')
                        for plane in frame.planes: plane.update(bytes([100])*plane.buffer_size)
                        frame.pts=i*1800;frame.time_base=Fraction(1,90000)
                        payloads,stamp=await loop.run_in_executor(None,ve.encode,frame,i%60==0)
                        for j,payload in enumerate(payloads):
                            packet=RtpPacket(payload_type=targets['video'][2],sequence_number=seq,timestamp=stamp,ssrc=43,payload=payload)
                            packet.marker=int(j==len(payloads)-1);seq+=1
                            video.transport.sendto(packet.serialize(),tuple(targets['video'][:2]))
                    await asyncio.sleep(.02)
            tasks.append(asyncio.create_task(send_media()))
            await wait_for(lambda:set(received)=={'audio','video'},15)
            af,vf=await asyncio.wait_for(asyncio.gather(received['audio'].recv(),received['video'].recv()),15)
            assert (vf.width,vf.height)==(160,120)
            incoming_samples=array.array('h',bytes(af.planes[0]))
            assert sum(abs(s) for s in incoming_samples)/len(incoming_samples)>100
            decoder=get_decoder(audio_codec)
            heard=False
            async with asyncio.timeout(15):
                while not heard:
                    data,_=await audio.messages.get()
                    if len(data)<12 or (data[1]&127)!=0:continue
                    packet=RtpPacket.parse(data)
                    for frame in decoder.decode(JitterFrame(packet.payload,packet.timestamp)):
                        samples=array.array('h',bytes(frame.planes[0]))
                        heard=sum(abs(s) for s in samples)/len(samples)>100
            channels=await instance.cli('core show channels concise')
            (instance.root/'active-channels.txt').write_text(channels)
            request('BYE',2)
            await asyncio.wait_for(browser_bye.wait(),10)
            return {'browser_registered':True,'incoming_call_answered':True,'video_decoded':[vf.width,vf.height],'incoming_audio_decoded':True,'microphone_return_decoded_pcmu':heard,'remote_bye_propagated':True,'browser_sdp_audio':'opus' if 'opus/48000' in browser.localDescription.sdp else 'PCMU','call_lifecycle_note':'Backend routing/media proof only; native HA viewer detach/reopen adapter not implemented.'}
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await browser.close()
            for port in (audio,video):port.transport.close()
            await ws.close()
