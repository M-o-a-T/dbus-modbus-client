import logging
import struct
import time

import client
import utils

log = logging.getLogger(__name__)

device_types = []

def probe(mlist, pr_cb=None, pr_interval=10, timeout=None, filt=None):
    num_probed = 0
    found = []
    failed = []

    for m in mlist:
        try:
            modbus = client.make_client(m)
            unit = m.unit
        except Exception:
            log.exception("Connect to client %s",m)
            continue

        if not modbus:
            continue

        d = None

        for t in device_types:
            if t.methods and m.method not in t.methods:
                continue
            log.debug("Probe for %s", next(iter(t.models.values()))["handler"].__name__)

            units = [unit] if unit > 0 else t.units

            try:
                for u in units:
                    mm = m._replace(unit=u)

                    if filt and not filt(mm):
                        continue

                    t0 = time.time()
                    d = t.probe(mm, modbus, timeout)
                    t1 = time.time()
                    if d:
                        break
            except Exception:
                log.exception("Reading from client %s",m)
                break

            if d:
                d.log.info('Found %s: %s %s',
                           d.device_type, d.vendor_name, d.model)
                d.latency = t1 - t0
                d.timeout = max(d.min_timeout, d.latency * 4)
                found.append(d)
                break

        if not d:
            log.debug("... not found.")
            failed.append(m)

        modbus.put()
        num_probed += 1

        if pr_cb:
            if d or num_probed == pr_interval:
                pr_cb(num_probed, d)
                num_probed = 0

    if pr_cb and num_probed:
        pr_cb(num_probed, None)

    return found, failed

def add_handler(devtype):
    if devtype not in device_types:
        device_types.append(devtype)

def get_attrs(attr, method):
    a = []

    for t in device_types:
        if method in t.methods:
            a += getattr(t, attr, [])

    return set(a)

def get_units(method):
    return get_attrs('units', method)

def get_rates(method):
    return get_attrs('rates', method)

class ModelRegister:
    def __init__(self, reg, models, **args):
        self.reg = reg
        self.models = models
        self.timeout = args.get('timeout', 1)
        self.methods = args.get('methods', [])
        self.units = args.get('units', [])
        self.rates = args.get('rates', [])

        if reg.access:
            self.access = [reg.access]
        else:
            self.access = {m['handler'].default_access for m in models.values()}

    def probe(self, spec, modbus, timeout=None):
        with modbus, utils.timeout(modbus, timeout or self.timeout):
            if not modbus.connect():
                raise Exception('connection error')

            for acs in self.access:
                if acs == "holding":
                    rf = modbus.read_holding_registers
                elif acs == "input":
                    rf = modbus.read_input_registers
                rr = rf(address=self.reg.base, count=self.reg.count, slave=spec.unit)
                if not rr.isError():
                    break

        if rr.isError():
            log.debug('%s: %s', modbus, rr)
            return None

        try:
            self.reg.decode(rr.registers)
            m = self.models[self.reg.value]
            return m['handler'](spec, modbus, m['model'])
        except KeyError:
            return None
        except Exception:
            log.exception("Decoding %s: %s",self.reg,rr)
            return None

    def get_models(self):
        m = []
        for v in self.models.values():
            h = v['handler']
            m.append((h.vendor_name, h.device_type, v['model']))

        return m
