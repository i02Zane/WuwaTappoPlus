"""Minimal RFC 6455 WebSocket client.

Only what CDP needs: text frames over ws:// with no Origin header.
Deliberately dependency-free so the project runs on a stock Python.
"""

import base64
import hashlib
import os
import socket
import struct
from urllib.parse import urlparse

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WSError(Exception):
    pass


def _recv_exact(sock, n):
    chunks = []
    need = n
    while need > 0:
        chunk = sock.recv(need)
        if not chunk:
            raise WSError("connection closed while reading")
        chunks.append(chunk)
        need -= len(chunk)
    return b"".join(chunks)


class WebSocket:
    def __init__(self, url, timeout=20.0):
        u = urlparse(url)
        if u.scheme != "ws":
            raise WSError("only ws:// is supported, got %r" % u.scheme)
        host = u.hostname or "127.0.0.1"
        port = u.port or 80
        path = u.path or "/"
        if u.query:
            path += "?" + u.query

        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake(host, port, path)

    def _handshake(self, host, port, path):
        key = base64.b64encode(os.urandom(16)).decode()
        # No Origin header: CDP rejects requests that carry one.
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n" % (path, host, port, key)
        )
        self.sock.sendall(req.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("handshake closed")
            buf += chunk
            if len(buf) > 65536:
                raise WSError("handshake response too large")

        head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        status = head.split("\r\n", 1)[0]
        if "101" not in status:
            raise WSError("handshake failed: %s" % status)

        expect = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()
        ).decode()
        if ("Sec-WebSocket-Accept: " + expect).lower() not in head.lower():
            raise WSError("bad Sec-WebSocket-Accept")

    def _send_frame(self, opcode, payload):
        header = bytearray()
        header.append(0x80 | opcode)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n <= 0xFFFF:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send(self, text):
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def recv(self):
        parts = []
        while True:
            b1, b2 = _recv_exact(self.sock, 2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F

            if length == 126:
                length = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack(">Q", _recv_exact(self.sock, 8))[0]

            mask = _recv_exact(self.sock, 4) if masked else None
            data = _recv_exact(self.sock, length) if length else b""
            if mask:
                data = bytes(c ^ mask[i & 3] for i, c in enumerate(data))

            if opcode == OP_PING:
                self._send_frame(OP_PONG, data)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise WSError("closed by peer")
            parts.append(data)

            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(OP_CLOSE, b"\x03\xe8")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
