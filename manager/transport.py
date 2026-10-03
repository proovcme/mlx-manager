"""Expose the local streaming socket so cancellation can interrupt prefill too."""
import http.client
import urllib.request


def open_stream(request, on_socket, timeout=300):
    class Connection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            on_socket(self.sock)

    class Handler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(Connection, request)

    return urllib.request.build_opener(Handler()).open(request, timeout=timeout)
