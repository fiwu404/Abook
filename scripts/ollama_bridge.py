"""Expose a loopback-only Ollama to Docker through the host's Docker gateway.

The listener binds only to the docker0 address. It does not change Ollama's own
listener or require Docker host networking.
"""

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen


MAX_BODY = 50 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                self.send_error(413, "Request body too large")
                return
            body = self.rfile.read(length) if length else None
            headers = {name: value for name, value in self.headers.items()
                       if name.lower() not in HOP_HEADERS}
            headers["Host"] = "127.0.0.1:11434"
            upstream = http.client.HTTPConnection("127.0.0.1", 11434, timeout=360)
            try:
                upstream.request(self.command, self.path, body=body, headers=headers)
                response = upstream.getresponse()
                content = response.read()
                self.send_response(response.status, response.reason)
                for name, value in response.getheaders():
                    if name.lower() not in HOP_HEADERS:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            finally:
                upstream.close()
        except (OSError, ValueError) as exc:
            self.send_error(502, f"Ollama bridge error: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Forward Docker gateway requests to local Ollama")
    parser.add_argument("--bind", default="172.17.0.1", help="docker0 gateway address")
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args()
    with urlopen("http://127.0.0.1:11434/", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("Ollama is not responding on 127.0.0.1:11434")
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    print(f"Ollama bridge listening on {args.bind}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
