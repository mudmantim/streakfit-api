"""A tiny Redis (RESP2) server whose failure mode can be switched at runtime.

Test infrastructure for the D7 rate-limit backend tests. It implements only
what redis-py and `limits` send for fixed-window rate limiting -- PING,
CLIENT, SCRIPT LOAD, EVALSHA/EVAL of the incr-expire script, GET, TTL, DEL,
INCRBY, EXPIRE -- and nothing else.

Why a fake and not a container: pytest must run anywhere, and the property
under test is the APPLICATION's reaction to each failure shape, which needs
the shape switched at an exact moment (between the before-request hit and the
after-request deduction of one login, for example). The real-Valkey matrix
lives in the lab harness (e2e-campaign/evidence/d7), not here.

Modes (set `server.mode`):
  ok        answers normally
  refuse    closes every connection as soon as it speaks or connects
  hang      reads commands and never answers (a frozen backend)
  readonly  reads answer; writes get -READONLY (a replica after failover)
  oom       reads answer; writes get -OOM (maxmemory + noeviction)

`server.writes_allowed = n` lets n more writes succeed and then behaves like
`readonly` for writes until reset to None.
"""
import hashlib
import socket
import socketserver
import threading
import time

_WRITES = {b"EVALSHA", b"EVAL", b"INCRBY", b"INCR", b"EXPIRE", b"DEL", b"SET"}


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server.owner
        sock = self.request
        with srv._lock:
            srv.connections += 1
            srv._socks.add(sock)
        try:
            if srv.mode == "refuse":
                return
            buf = b""
            while True:
                cmd, buf = _read_command(sock, buf)
                if cmd is None:
                    return
                mode = srv.mode
                if mode == "refuse":
                    return
                if mode == "hang":
                    srv.record(cmd, "hung")
                    while srv.mode == "hang" and not srv._stopping:
                        time.sleep(0.02)
                    return          # drop the connection; the client timed out
                sock.sendall(srv.execute(cmd))
        except OSError:
            return
        finally:
            with srv._lock:
                srv._socks.discard(sock)
            try:
                sock.close()
            except OSError:
                pass


def _read_line(sock, buf):
    while b"\r\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            return None, buf
        buf += chunk
    line, _, rest = buf.partition(b"\r\n")
    return line, rest


def _read_command(sock, buf):
    line, buf = _read_line(sock, buf)
    if line is None:
        return None, buf
    if not line.startswith(b"*"):
        return line.split(), buf            # inline command
    n = int(line[1:])
    parts = []
    for _ in range(n):
        hdr, buf = _read_line(sock, buf)
        if hdr is None:
            return None, buf
        size = int(hdr[1:])
        while len(buf) < size + 2:
            chunk = sock.recv(65536)
            if not chunk:
                return None, buf
            buf += chunk
        parts.append(buf[:size])
        buf = buf[size + 2:]
    return parts, buf


def _bulk(v):
    if v is None:
        return b"$-1\r\n"
    v = v if isinstance(v, bytes) else str(v).encode()
    return b"$%d\r\n%s\r\n" % (len(v), v)


def _int(v):
    return b":%d\r\n" % v


class FakeRedis:
    def __init__(self):
        self.mode = "ok"
        self.writes_allowed = None
        self.data = {}           # key -> int
        self.expires = {}        # key -> monotonic deadline
        self.scripts = {}        # sha -> script text
        self.log = []            # (monotonic, command name, outcome)
        self.connections = 0
        self._lock = threading.Lock()
        self._socks = set()
        self._stopping = False
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler,
                                                    bind_and_activate=False)
        self._srv.allow_reuse_address = True
        self._srv.daemon_threads = True
        self._srv.server_bind()
        self._srv.server_activate()
        self._srv.owner = self
        self.port = self._srv.server_address[1]
        self.uri = f"redis://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    def stop(self):
        self._stopping = True
        self._srv.shutdown()
        self._srv.server_close()
        with self._lock:
            for s in list(self._socks):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def set_mode(self, mode):
        self.mode = mode
        if mode == "refuse":
            with self._lock:
                for s in list(self._socks):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def flush(self):
        with self._lock:
            self.data.clear()
            self.expires.clear()

    def record(self, cmd, outcome):
        with self._lock:
            self.log.append((time.monotonic(), cmd[0].upper().decode(), outcome))

    def count(self, name=None, outcome=None):
        with self._lock:
            return sum(1 for _, n, o in self.log
                       if (name is None or n == name) and (outcome is None or o == outcome))

    # -- commands ---------------------------------------------------------

    def _live(self, key):
        dl = self.expires.get(key)
        if dl is not None and dl <= time.monotonic():
            self.data.pop(key, None)
            self.expires.pop(key, None)
        return self.data.get(key)

    def _incr_expire(self, key, expiry, amount):
        cur = (self._live(key) or 0) + amount
        self.data[key] = cur
        if cur == amount:
            self.expires[key] = time.monotonic() + expiry
        return cur

    def execute(self, cmd):
        name = cmd[0].upper()
        if name in _WRITES and not (name in (b"EVALSHA", b"EVAL") and self._is_read_script(cmd)):
            refusal = None
            if self.mode == "readonly":
                refusal = b"-READONLY You can't write against a read only replica.\r\n"
            elif self.mode == "oom":
                refusal = b"-OOM command not allowed when used memory > 'maxmemory'.\r\n"
            elif self.writes_allowed is not None:
                if self.writes_allowed <= 0:
                    refusal = b"-READONLY You can't write against a read only replica.\r\n"
                else:
                    self.writes_allowed -= 1
            if refusal:
                self.record(cmd, "refused")
                return refusal
        self.record(cmd, "ok")
        with self._lock:
            return self._dispatch(name, cmd)

    def _is_read_script(self, cmd):
        return False

    def _dispatch(self, name, cmd):
        if name == b"PING":
            return b"+PONG\r\n"
        if name in (b"CLIENT", b"SELECT", b"AUTH"):
            return b"+OK\r\n"
        if name == b"SCRIPT":
            if cmd[1].upper() == b"LOAD":
                sha = hashlib.sha1(cmd[2]).hexdigest().encode()
                self.scripts[sha] = cmd[2]
                return _bulk(sha)
            return b"+OK\r\n"
        if name in (b"EVALSHA", b"EVAL"):
            if name == b"EVAL":
                text = cmd[1]
                self.scripts[hashlib.sha1(text).hexdigest().encode()] = text
            else:
                text = self.scripts.get(cmd[1])
                if text is None:
                    return b"-NOSCRIPT No matching script. Please use EVAL.\r\n"
            nkeys = int(cmd[2])
            keys, args = cmd[3:3 + nkeys], cmd[3 + nkeys:]
            if b"incrby" in text and b"expire" in text:
                return _int(self._incr_expire(keys[0], int(args[0]), int(args[1])))
            return b"-ERR fake server: unsupported script\r\n"
        if name == b"GET":
            v = self._live(cmd[1])
            return _bulk(None if v is None else v)
        if name == b"TTL":
            if self._live(cmd[1]) is None:
                return _int(-2)
            dl = self.expires.get(cmd[1])
            return _int(-1 if dl is None else max(0, int(dl - time.monotonic())))
        if name == b"DEL":
            n = 0
            for k in cmd[1:]:
                n += 1 if self.data.pop(k, None) is not None else 0
                self.expires.pop(k, None)
            return _int(n)
        if name in (b"INCRBY", b"INCR"):
            amount = int(cmd[2]) if name == b"INCRBY" else 1
            cur = (self._live(cmd[1]) or 0) + amount
            self.data[cmd[1]] = cur
            return _int(cur)
        if name == b"EXPIRE":
            if self._live(cmd[1]) is None:
                return _int(0)
            self.expires[cmd[1]] = time.monotonic() + int(cmd[2])
            return _int(1)
        return b"-ERR fake server: unknown command '%s'\r\n" % name
