#!/usr/bin/env python3
"""极简反向代理：80(http) + 443(https, 自签证书) -> ad-unlock-portal:5000"""
import http.server
import socketserver
import http.client
import ssl
import threading

APP_HOST = "ad-unlock-portal"
APP_PORT = 5000
CERT = "/etc/proxy/certs/server.crt"
KEY = "/etc/proxy/certs/server.key"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self):
        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length > 0 else None

        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ("host", "connection", "x-forwarded-for",
                                 "x-forwarded-proto", "accept-encoding"):
                headers[k] = v
        headers["X-Forwarded-For"] = self.client_address[0]
        headers["X-Forwarded-Proto"] = "https" if isinstance(self.request, ssl.SSLSocket) else "http"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")

        conn = http.client.HTTPConnection(APP_HOST, APP_PORT, timeout=15)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "content-encoding"):
                    self.send_header(k, v)
            self.end_headers()
            data = resp.read()
            if self.command != "HEAD":
                self.wfile.write(data)
        except Exception as e:
            try:
                self.send_error(502, f"Bad Gateway: {e}")
            except Exception:
                pass
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = _proxy

    def log_message(self, fmt, *args):
        pass


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_http():
    with ReusableServer(("0.0.0.0", 80), ProxyHandler) as httpd:
        httpd.serve_forever()


def run_https():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    with ReusableServer(("0.0.0.0", 443), ProxyHandler) as httpd:
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        httpd.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_http, daemon=True).start()
    threading.Thread(target=run_https, daemon=True).start()
    print(f"proxy ready: http://0.0.0.0:80  and  https://0.0.0.0:443  ->  {APP_HOST}:{APP_PORT}", flush=True)
    threading.Event().wait()
