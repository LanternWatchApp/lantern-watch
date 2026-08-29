#!/usr/bin/env python3
"""
Lantern Watch — blockserver.py

Tiny HTTPS server on :8444 that serves the branded block page for ANY request.

AdGuard answers a blocked lookup with the block-page virtual IP (see
adguard.BLOCK_PAGE_IP); iptables redirects that IP's :443 here. Because we can't
hold a valid TLS certificate for someone else's domain, the browser shows a
certificate warning first — on click-through the visitor lands on the block page
(with the Find Help link). HSTS-preloaded sites won't allow the click-through;
that's an unavoidable browser limitation, not a blocking failure — the site is
still blocked either way.

Plain-HTTP blocked sites are handled separately (iptables :80 -> 8081 -> the
dashboard's /blocked redirect); this module only covers the HTTPS side.
"""

import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DIR  = os.path.dirname(os.path.abspath(__file__))
_CRT  = os.path.join(_DIR, "blockpage.crt")
_KEY  = os.path.join(_DIR, "blockpage.key")
_PORT = 8444


def _ensure_cert():
    """Generate a long-lived self-signed cert once (openssl is present on the
    GL.iNet firmware). The cert name never matches the blocked domain — a warning
    is expected — so the subject is cosmetic. Needs a subjectAltName even so:
    browsers since ~2017 ignore a bare Common Name and can refuse to render
    even the click-through warning UI for a cert with no SAN extension at all.

    Self-heals an existing cert from before the SAN fix: any router updating
    from an older version already has blockpage.crt/.key on disk, which would
    otherwise never get regenerated. Checked once per boot, cheap either way."""
    if os.path.exists(_CRT) and os.path.exists(_KEY):
        try:
            r = subprocess.run(
                ["openssl", "x509", "-in", _CRT, "-noout", "-text"],
                capture_output=True, text=True, timeout=5,
            )
            if "Subject Alternative Name" in (r.stdout or ""):
                return
            print("[BlockServer] existing cert has no SAN, regenerating")
        except Exception:
            pass  # can't verify it — safest is to regenerate below
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", _KEY, "-out", _CRT, "-days", "3650",
         "-subj", "/CN=Lantern Watch",
         "-addext", "subjectAltName=DNS:lanternwatch.local,DNS:lanternwatch.lan"],
        capture_output=True,
    )


class _BlockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _serve(self):
        from pages import build_blocked_page
        body = build_blocked_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._serve()
        except Exception:
            pass

    def do_POST(self):
        try:
            self._serve()
        except Exception:
            pass

    def log_message(self, *args):
        pass  # stay quiet — one line per blocked hit would flood the log


class _QuietHTTPSServer(ThreadingHTTPServer):
    """Blocked HTTPS sites make the browser reject our self-signed cert
    (SSLV3_ALERT_CERTIFICATE_UNKNOWN) or abort the TLS handshake — that's the
    expected block-page flow, not a failure. Swallow those handshake errors
    instead of dumping a full traceback to the log on every blocked hit.

    The TLS handshake happens per-connection in finish_request(), NOT on the
    listening socket. ThreadingMixIn already runs finish_request() in its own
    thread per connection, but get_request()'s accept() call always runs on
    the single main server-loop thread — so wrapping the listening socket
    itself (the old approach) meant one slow or malformed client's handshake
    blocked accept() for every other blocked-site hit until it finished or
    timed out. Wrapping the accepted socket here instead keeps the listening
    socket plain, so a stuck handshake only ever stalls its own thread."""
    daemon_threads = True
    ssl_context = None  # set by start_block_server() before serve_forever()

    def finish_request(self, request, client_address):
        try:
            request = self.ssl_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # handshake aborted/rejected — same expected flow as before
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ssl.SSLError, ConnectionError, BrokenPipeError, OSError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def start_block_server():
    """Launch the HTTPS block-page server in a daemon thread. Best-effort: if it
    can't bind or the cert can't be made, the HTTP block page still works."""
    try:
        _ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=_CRT, keyfile=_KEY)
        srv = _QuietHTTPSServer(("0.0.0.0", _PORT), _BlockHandler)
        srv.ssl_context = ctx
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"[BlockServer] HTTPS block page serving on :{_PORT}")
    except Exception as e:
        print(f"[BlockServer] could not start (HTTP block page still works): {e}")
