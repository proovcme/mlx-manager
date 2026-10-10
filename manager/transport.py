"""Expose the local streaming socket so cancellation can interrupt prefill too."""
import http.client
import urllib.request


def open_stream(request, on_socket, timeout=300, redirects=True):
    class Connection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            on_socket(self.sock)

    class Handler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(Connection, request)

    class SecureConnection(http.client.HTTPSConnection):
        def connect(self):
            super().connect()
            on_socket(self.sock)

    class SecureHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(SecureConnection, request, context=self._context)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    handlers = [Handler(), SecureHandler()]
    if not redirects:
        handlers.extend([NoRedirect(), urllib.request.ProxyHandler({})])
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)
