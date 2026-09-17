from ipaddress import ip_address

from django.conf import settings
from django.core.exceptions import SuspiciousOperation


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.TRUST_PROXY_CLIENT_IP:
            client_ip = request.META.get("HTTP_X_REAL_IP")
            # Internal readiness probes do not necessarily pass through the public edge.
            if client_ip is None and request.path_info == "/healthz/":
                return self.get_response(request)
            try:
                request.META["REMOTE_ADDR"] = str(ip_address(client_ip))
            except ValueError:
                raise SuspiciousOperation("Missing or invalid trusted client IP.") from None
        return self.get_response(request)
