import select
import socket
import socketserver


class Bridge(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.create_connection(('127.0.0.1', 1445), timeout=10) as upstream:
            peers = [self.request, upstream]
            while True:
                readable, _, _ = select.select(peers, [], [], 60)
                if not readable:
                    return
                for peer in readable:
                    data = peer.recv(65536)
                    if not data:
                        return
                    (upstream if peer is self.request else self.request).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


Server(('172.17.0.1', 1446), Bridge).serve_forever()
