import logging
import socket
import struct
import threading

from dnslib.dns import *

import devspec

log = logging.getLogger(__name__)

MDNS_IP = '224.0.0.251'
MDNS_PORT = 5353

services = []

def add_service(svc):
    services.append(svc + '.local.')

def mreqn(maddr):
    return struct.pack("4sii", socket.inet_aton(maddr), socket.INADDR_ANY, 0)

class MDNS:
    def __init__(self):
        self.lock = threading.Lock()
        self.found = set()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', MDNS_PORT))
        self.mcast = False

    def close(self):
        if self.mcast:
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,
                                   mreqn(MDNS_IP))
        self.socket.close()

    def recv(self):
        return self.socket.recv(65536)

    def send(self, buf):
        return self.socket.sendto(buf, (MDNS_IP, MDNS_PORT))

    def req(self):
        if not services:
            return

        if not self.mcast:
            try:
                self.socket.setsockopt(socket.IPPROTO_IP,
                                       socket.IP_ADD_MEMBERSHIP,
                                       mreqn(MDNS_IP))
                self.mcast = True
            except Exception:
                log.exception("MDNS problem")
                return

        try:
            q = DNSRecord()
            for svc in services:
                q.add_question(DNSQuestion(svc, QTYPE.PTR))
            self.send(q.pack())
        except Exception as e:
            log.error('Error sending MDNS request: %s', e)

    def get_devices(self):
        with self.lock:
            ret = self.found.copy()
            self.found.clear()
            return ret

    def parse_record(self, rec):
        ptr = set()
        srv = {}
        ips = {}

        for rr in rec.auth + rec.rr + rec.ar:
            rname = str(rr.rname)

            if rr.rtype == QTYPE.PTR:
                if rname in services:
                    ptr.add(str(rr.rdata.label))

            if rr.rtype == QTYPE.SRV:
                if len(rr.rname.label) < 3:
                    continue

                proto = str(rr.rname.label[-2], encoding='ascii').lstrip('_')

                if proto not in ['tcp', 'udp']:
                    continue

                srv[rname] = devspec.create(
                    method=proto,
                    target=str(rr.rdata.target),
                    port=rr.rdata.port
                )

            if rr.rtype == QTYPE.A:
                ips[rname] = rr.rdata

        for k, v in srv.items():
            t = v.target
            if t in ips:
                srv[k] = v._replace(target=str(ips[t]))

        with self.lock:
            for p in ptr & srv.keys():
                self.found.add(srv[p])

    def run(self):
        while True:
            try:
                pkt = self.recv()
                rec = DNSRecord.parse(pkt)
                log.debug('--- BEGIN RECORD ---')
                log.debug(rec)
                log.debug('--- END RECORD ---')
                self.parse_record(rec)
            except DNSError:
                continue
            except Exception:
                log.exception('Exception parsing record')

    def start(self):
        t = threading.Thread(target=self.run)
        t.daemon = True
        t.start()

if __name__ == '__main__':
    import sys
    import time

    argv = sys.argv[1:]
    level = logging.INFO

    if argv and argv[0] == '-d':
        level = logging.DEBUG
        argv.pop(0)

    logging.basicConfig(format='%(message)s', level=level)

    for s in argv:
        add_service(s)

    mdns = MDNS()
    mdns.start()
    mdns.req()

    while True:
        devices = mdns.get_devices()
        for d in devices:
            log.info('%s %d', d.target, d.port)
        time.sleep(1)
