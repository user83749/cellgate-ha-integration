"""Isolated Linux/Docker evaluation; synthetic accounts and loopback SIP only.

Never reads CellGate credentials or contacts an owner's Home Assistant instance.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets

SPEC=importlib.util.spec_from_file_location('lab_digest',Path(__file__).parents[1]/'sip_probe.py')
AUTH=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(AUTH)
IMAGE='ghcr.io/tech7fox/asterisk-hass-addon:6.4.0'
FRONT=('127.0.0.2',15061)
BACK=('127.0.0.2',15062)
USER='synthetic-cellgate'
PASSWORD='synthetic-lab-password'
REALM='cellgate-lab'


class Packet:
    def __init__(self,data):
        head,_,self.body=data.decode().partition('\r\n\r\n')
        lines=head.split('\r\n');self.start=lines[0];self.headers={}
        for line in lines[1:]:
            if ':' in line:
                key,value=line.split(':',1);key=key.lower().strip();key={'v':'via','f':'from','t':'to','i':'call-id','m':'contact','l':'content-length','c':'content-type'}.get(key,key);self.headers.setdefault(key,[]).append(value.strip())
        self.method=self.start.split()[0] if self.start and not self.start.startswith('SIP/') else ''
        self.status=int(self.start.split()[1]) if self.start.startswith('SIP/2.0 ') else None
    def get(self,key,default=''):return self.headers.get(key.lower(),[default])[0]


def wire(lines,body=''):
    data=body.encode()
    return ('\r\n'.join(lines+[f'Content-Length: {len(data)}','',''])).encode()+data


def response(request,code,reason,extra=(),body=''):
    headers=[f'SIP/2.0 {code} {reason}']+[f'Via: {v}' for v in request.headers.get('via',[])]+[f'From: {request.get("from")}',f'To: {request.get("to")}',f'Call-ID: {request.get("call-id")}',f'CSeq: {request.get("cseq")}']
    if body:headers+=['Content-Type: application/sdp']
    return wire(headers+list(extra),body)


class Provider(asyncio.DatagramProtocol):
    def __init__(self,redirect=False):
        self.transport=None;self.redirect=redirect;self.messages=asyncio.Queue();self.records=[];self.peer=None
    def connection_made(self,transport):self.transport=transport
    def datagram_received(self,data,peer):
        if not data.strip():return
        packet=Packet(data)
        if packet.method=='REGISTER':
            self.records.append({'uri':packet.start.split()[1],'identity':packet.get('from').split(';')[0],'expires':packet.get('expires'),'authenticated':False})
            if self.redirect:
                self.transport.sendto(response(packet,302,'Moved Temporarily',[f'Contact: <sip:{BACK[0]}:{BACK[1]}>']),peer);return
            if not packet.get('authorization'):
                self.transport.sendto(response(packet,401,'Unauthorized',[f'WWW-Authenticate: Digest realm="{REALM}", nonce="lab-nonce", algorithm=MD5, qop="auth"']),peer);return
            fields=AUTH.challenge_fields(packet.get('authorization'))
            expected=AUTH.challenge_fields(AUTH.digest(USER,PASSWORD,packet.start.split()[1],{'realm':REALM,'nonce':'lab-nonce','qop':'auth'},int(fields.get('nc','1'),16),fields.get('cnonce')))
            if fields.get('response')!=expected['response']:
                self.transport.sendto(response(packet,403,'Forbidden'),peer);return
            self.records[-1]['authenticated']=True;self.peer=peer
            self.transport.sendto(response(packet,200,'OK',['Contact: '+packet.get('contact'),'Expires: '+(packet.get('expires') or '60')]),peer);return
        if packet.method=='OPTIONS':
            self.transport.sendto(response(packet,200,'OK'),peer);return
        self.messages.put_nowait((packet,peer))
    def close(self):
        if self.transport:self.transport.close()


async def command(*args,check=True):
    process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
    data,_=await process.communicate();text=data.decode(errors='replace')
    if check and process.returncode:raise RuntimeError(f'Command failed ({process.returncode}): {args[:3]}\n{text[-4000:]}')
    return text


class Asterisk:
    def __init__(self,root,image,proxy):
        self.root=root;self.image=image;self.proxy=proxy;self.name='cellgate-lab-'+secrets.token_hex(4)
    def configure(self):
        config=self.root/'config';config.mkdir(parents=True,exist_ok=True)
        for name in ('run','log','state','spool','data'):(self.root/name).mkdir(exist_ok=True)
        (config/'asterisk.conf').write_text('''[directories]
astetcdir=/lab/config
astmoddir=/usr/lib/asterisk/modules
astvarlibdir=/var/lib/asterisk
astdbdir=/lab/state
astkeydir=/lab/data
astdatadir=/var/lib/asterisk
astagidir=/lab/data
astspooldir=/lab/spool
astrundir=/lab/run
astlogdir=/lab/log
[options]
verbose=3
debug=0
nocolor=yes
''')
        (config/'modules.conf').write_text('''[modules]
autoload=yes
noload=chan_dongle.so
noload=res_http_post.so
noload=app_voicemail.so
''')
        (config/'logger.conf').write_text('[general]\n[logfiles]\nconsole=notice,warning,error,verbose\n')
        (config/'rtp.conf').write_text('[general]\nrtpstart=28000\nrtpend=28200\nicesupport=yes\n')
        (config/'http.conf').write_text('[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport=18088\n')
        (config/'extensions.conf').write_text('''[from-cellgate]
exten => incoming-gate,1,NoOp(Synthetic incoming intercom)
same => n,Dial(PJSIP/lab-browser,30)
same => n,Hangup()
''')
        proxy=f'outbound_proxy=sip:{BACK[0]}:{BACK[1]}\\;lr\n' if self.proxy else ''
        (config/'pjsip.conf').write_text(f'''[global]
type=global
user_agent=CellGate-Asterisk-Lab
endpoint_identifier_order=ip,username,anonymous
[udp]
type=transport
protocol=udp
bind=127.0.0.1:15060
[ws]
type=transport
protocol=ws
bind=127.0.0.1
[provider-auth]
type=auth
auth_type=userpass
username={USER}
password={PASSWORD}
[provider-registration]
type=registration
transport=udp
outbound_auth=provider-auth
server_uri=sip:cellgate.test:{FRONT[1]}
client_uri=sip:{USER}@cellgate.test
contact_user=incoming-gate
expiration=60
retry_interval=0
fatal_retry_interval=0
forbidden_retry_interval=0
max_retries=1
{proxy}
[provider]
type=endpoint
transport=udp
context=from-cellgate
disallow=all
allow=ulaw,h264
outbound_auth=provider-auth
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
direct_media=no
[provider-identify]
type=identify
endpoint=provider
match=127.0.0.2
[lab-browser]
type=endpoint
transport=ws
context=default
disallow=all
allow=opus,ulaw,h264
webrtc=yes
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
aors=lab-browser
auth=browser-auth
[lab-browser]
type=aor
max_contacts=1
remove_existing=yes
[browser-auth]
type=auth
auth_type=userpass
username=lab-browser
password=synthetic-browser-password
''')
    async def start(self):
        self.configure()
        await command('docker','run','-d','--name',self.name,'--network','host','--add-host','cellgate.test:127.0.0.2','--entrypoint','/usr/sbin/asterisk','-v',f'{self.root}:/lab',self.image,'-f','-g','-C','/lab/config/asterisk.conf')
        for _ in range(60):
            text=await self.cli('core show version',check=False)
            if 'Asterisk ' in text:return text.strip()
            await asyncio.sleep(.25)
        raise RuntimeError('Asterisk did not start')
    async def cli(self,text,check=True):return await command('docker','exec',self.name,'/usr/sbin/asterisk','-C','/lab/config/asterisk.conf','-rx',text,check=check)
    async def stop(self):
        logs=await command('docker','logs',self.name,check=False)
        (self.root/'asterisk.log').write_text(logs)
        await command('docker','rm','-f',self.name,check=False)


async def wait_for(predicate,seconds=12):
    async with asyncio.timeout(seconds):
        while not predicate():await asyncio.sleep(.05)


async def evaluate(args):
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    report={'scope':'synthetic loopback-only lab; no vendor or HA access','image':args.image}
    loop=asyncio.get_running_loop();front=Provider(redirect=True);backend=Provider()
    await loop.create_datagram_endpoint(lambda:front,local_addr=FRONT)
    await loop.create_datagram_endpoint(lambda:backend,local_addr=BACK)
    try:
        report['image_digest']=(await command('docker','image','inspect',args.image,'--format','{{index .RepoDigests 0}}')).strip()
        for mode,proxy in [('direct_redirect',False),('resolved_proxy',True)]:
            front.records.clear();backend.records.clear();backend.peer=None
            instance=Asterisk(root/mode,args.image,proxy)
            try:
                report['engine_version']=await instance.start()
                await wait_for(lambda:front.records or backend.records)
                await asyncio.sleep(3)
                registrations=await instance.cli('pjsip show registrations')
                (instance.root/'registrations.txt').write_text(registrations)
                report[mode]={'front_requests':len(front.records),'backend_requests':len(backend.records),'authenticated':any(r['authenticated'] and r['expires']!='0' for r in backend.records),'registered_in_cli':'Registered' in registrations,'request_records':list(backend.records)}
                print(json.dumps({mode:report[mode]}),flush=True)
                if proxy:
                    if not report[mode]['registered_in_cli']:raise RuntimeError('Resolved proxy registration failed')
                    from media import evaluate_media
                    report['media']=await evaluate_media(backend,instance)
                    print(json.dumps({'media':report['media']}),flush=True)
            finally:await instance.stop()
    finally:
        front.close();backend.close()
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Registration evaluation complete.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default=IMAGE);parser.add_argument('--output',type=Path,default=Path('asterisk-lab-results'))
    asyncio.run(evaluate(parser.parse_args()))
