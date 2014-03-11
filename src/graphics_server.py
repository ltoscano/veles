"""
Created on Mar 7, 2014

@author: Vadim Markovtsev <v.markovtsev@samsung.com>
"""


import array
import fcntl
import os
from six.moves import cPickle as pickle, zip
import socket
import struct
import subprocess
import sys
from tempfile import mkdtemp
from twisted.internet import reactor
from txzmq import ZmqConnection, ZmqEndpoint
import zmq

import config
from logger import Logger
import graphics_client


class ZmqPublisher(ZmqConnection):
    socketType = zmq.constants.PUB


class GraphicsServer(Logger):
    """
    Graphics server which uses ZeroMQ PUB socket to publish updates.
    """
    _instance = None
    _pair_fds = {}

    def __new__(cls):
        if not cls._instance:
            cls._instance = super(GraphicsServer, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return
        self.initialized = True
        super(GraphicsServer, self).__init__()
        zmq_endpoints = [ZmqEndpoint("bind", "inproc://veles-plots"),
                         ZmqEndpoint("bind", "rndipc://veles-ipc-plots-:")]
        interfaces = []
        for iface, _ in self.interfaces():
            interfaces.append(iface)
            zmq_endpoints.append(ZmqEndpoint(
                "bind", "rndepgm://%s;%s:1024:65535:1" %
                        (iface, config.graphics_multicast_address)))
        self.zmq_connection = ZmqPublisher(zmq_endpoints)
        tmpfn, *ports = self.zmq_connection.rnd_vals
        self.endpoints = {"inproc": "inproc://veles-plots",
                          "ipc": "ipc://" + tmpfn,
                          "epgm": []}
        for port, iface in zip(ports, interfaces):
            self.endpoints["epgm"].append("epgm://%s;%s:%d" % \
                (iface, config.graphics_multicast_address, port))
        self.info("Publishing to %s", "; ".join([self.endpoints["inproc"],
                                                 self.endpoints["ipc"]] +
                                                self.endpoints["epgm"]))

    def interfaces(self):
        max_possible = 128
        max_bytes = max_possible * 32
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            names = array.array('B', b'\0' * max_bytes)
            outbytes = struct.unpack('iL', fcntl.ioctl(
                sock.fileno(),
                0x8912,  # SIOCGIFCONF
                struct.pack('iL', max_bytes, names.buffer_info()[0])
            ))[0]
        namestr = names.tobytes()
        for i in range(0, outbytes, 40):
            name = namestr[i:i + 16].split(b'\0', 1)[0]
            if name == b'lo':
                continue
            ip = namestr[i + 20:i + 24]
            yield (name.decode(), ip)

    def enqueue(self, obj):
        data = pickle.dumps(obj)
        self.debug("Broadcasting %d bytes" % len(data))
        self.zmq_connection.send(data)

    def shutdown(self):
        self.debug("Broadcasting None")
        self.enqueue(None)

    @staticmethod
    def launch_pair(webagg_callback=None):
        if not config.plotters_disabled:
            server = GraphicsServer()
            args = ["env", "python3", graphics_client.__file__,
                    config.matplotlib_backend,
                    server.endpoints["ipc"]]
            if config.matplotlib_backend == "WebAgg" and \
               webagg_callback is not None:
                tmpdir = mkdtemp(prefix="veles-graphics")
                tmpfn = os.path.join(tmpdir, "comm")
                os.mkfifo(tmpfn)
                fifo = os.open(tmpfn, os.O_RDONLY | os.O_NONBLOCK)
                reactor.callLater(0, GraphicsServer._read_webagg_port,
                                  fifo, tmpfn, tmpdir, webagg_callback)
                args.append(tmpfn)
            client = subprocess.Popen(args, stdout=sys.stdout,
                                      stderr=sys.stderr)
            return server, client

    @staticmethod
    def _read_webagg_port(fifo, tmpfn, tmpdir, webagg_callback):
        output = os.read(fifo, 8)
        if not output:
            reactor.callLater(0, GraphicsServer._read_webagg_port,
                              fifo, tmpfn, tmpdir, webagg_callback)
        else:
            os.close(fifo)
            os.unlink(tmpfn)
            os.rmdir(tmpdir)
            if webagg_callback is not None:
                webagg_callback(int(output))
