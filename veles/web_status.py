#!/usr/bin/python3

"""
Created on Feb 10, 2014

Copyright (c) 2013 Samsung Electronics Co., Ltd.
"""


import argparse
from bson import json_util
from collections import defaultdict
import logging
import json
import motor
import os
from six import print_
import socket
import sys
import time

from tornado.escape import json_decode
import tornado.gen as gen
from tornado.ioloop import IOLoop
import tornado.web as web

from veles.config import root
from veles.error import AlreadyExistsError
import veles.external.daemon as daemon
from veles.logger import Logger

if (sys.version_info[0] + (sys.version_info[1] / 10.0)) < 3.3:
    PermissionError = IOError  # pylint: disable=W0622
    BrokenPipeError = OSError  # pylint: disable=W0622


debug_mode = True


class ServiceHandler(web.RequestHandler):
    def initialize(self, server):
        self.server = server

    @web.asynchronous
    @gen.coroutine
    def post(self):
        self.server.debug("service POST from %s: %s", self.request.remote_ip,
                          self.request.body)
        try:
            data = json_decode(self.request.body)
            yield self.server.receive_request(self, data)
        except:
            self.server.exception("service POST")
            self.clear()
            self.finish({"request": data["request"] if data else "",
                         "result": "error"})


class UpdateHandler(web.RequestHandler):
    def initialize(self, server):
        self.server = server

    def post(self):
        self.server.debug("update POST from %s: %s", self.request.remote_ip,
                          self.request.body)
        try:
            data = json_decode(self.request.body)
            self.server.receive_update(self, data)
        except:
            self.server.exception("update POST")


class LogsHandler(web.RequestHandler):
    def initialize(self, server):
        self.server = server

    def get(self):
        session = self.get_argument("session", None)
        if session is None:
            self.clear()
            self.set_status(400)
        else:
            self.render("logs.html", session=session)


class WebServer(Logger):
    """
    Operates a web server based on Tornado to show various runtime information.
    """

    GARBAGE_TIMEOUT = 60

    def __init__(self, **kwargs):
        super(WebServer, self).__init__()
        if not debug_mode:
            Logger.redirect_all_logging_to_file(
                root.common.web.log_file, backups=root.common.web.log_backups)
        self.application = web.Application([
            ("/service", ServiceHandler, {"server": self}),
            ("/update", UpdateHandler, {"server": self}),
            ("/logs.html?.*", LogsHandler, {"server": self}),
            (r"/(js/.*)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            (r"/(css/.*)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            (r"/(fonts/.*)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            (r"/(img/.*)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            (r"/(.+\.html)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            ("/(veles.png)",
             web.StaticFileHandler, {'path': root.common.web.root}),
            ("/", web.RedirectHandler, {"url": "/status.html",
                                        "permanent": True}),
            ("", web.RedirectHandler, {"url": "/status.html",
                                       "permanent": True})
        ], template_path=os.path.join(root.common.web.root, "templates"),
            gzip=not debug_mode)
        self._port = kwargs.get("port", root.common.web.port)
        self.application.listen(self._port)
        self.masters = {}
        self.motor = motor.MotorClient(
            "mongodb://" + kwargs.get("mongodb",
                                      root.common.mongodb_logging_address))
        self.db = self.motor.veles

    @property
    def port(self):
        return self._port

    @gen.coroutine
    def receive_request(self, handler, data):
        rtype = data["request"]
        if rtype == "workflows":
            ret = defaultdict(dict)
            garbage = []
            now = time.time()
            for mid, master in self.masters.items():
                if (now - master["last_update"] > WebServer.GARBAGE_TIMEOUT):
                    garbage.append(mid)
                    continue
                for item in data["args"]:
                    ret[mid][item] = master[item]
            for mid in garbage:
                self.info("Removing the garbage collected master %s", mid)
                del self.masters[mid]
            self.debug("Request %s: %s", rtype, ret)
            handler.finish({"request": rtype, "result": ret})
        elif rtype in ("logs", "events"):
            cursor = self.db[rtype].find(data["query"])
            handler.set_header("Content-Type",
                               "application/json; charset=UTF-8")
            handler.write("{\"request\": \"%s\", \"result\": [" % rtype)
            count = 0
            first = True
            while (yield cursor.fetch_next):
                if not first:
                    handler.write(",\n")
                else:
                    first = False
                json_raw = json.dumps(cursor.next_object(),
                                      default=json_util.default)
                handler.write(json_raw.replace("</", "<\\/"))
                count += 1
            handler.finish("]}")
            self.debug("Fetched %d %s", count, rtype)
        else:
            handler.finish({"request": rtype, "result": None})

    def receive_update(self, handler, data):
        mid = data["id"]
        self.debug("Master %s yielded %s", mid, data)
        self.masters[mid] = data
        self.masters[mid]["last_update"] = time.time()

    def run(self):
        self.info("HTTP server is running on %s:%s",
                  socket.gethostname(), self.port)
        IOLoop.instance().start()

    def stop(self):
        IOLoop.instance().stop()


def main():
    WebServer().run()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", default=False,
                        help="activates debugging mode (run in foreground, "
                        "DEBUG logging level)", action='store_true')
    args = parser.parse_args()
    debug_mode = args.debug
    if not debug_mode:
        pidfile = root.common.web.pidfile
        full_pidfile = pidfile + ".lock"
        if not os.access(os.path.dirname(full_pidfile), os.W_OK):
            raise PermissionError(pidfile)
        if os.path.exists(full_pidfile):
            real_pidfile = os.readlink(full_pidfile)
            pid = int(real_pidfile.split('.')[-1])
            try:
                os.kill(pid, 0)
            except OSError:
                os.remove(real_pidfile)
                os.remove(full_pidfile)
                print_("Detected a stale lock file %s" % real_pidfile,
                       file=sys.stderr)
            else:
                raise AlreadyExistsError(full_pidfile)
        print("Daemonizing, PID will be referenced by ", full_pidfile)
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            pass
        with daemon.DaemonContext(pidfile=pidfile, stderr=sys.stderr):
            log_file = root.common.web.log_file
            Logger.setup(level=logging.INFO)
            Logger.redirect_all_logging_to_file(log_file, backups=9)
            main()
    else:
        Logger.setup(level=logging.DEBUG)
        main()
