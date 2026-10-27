"""Request-scoped middleware."""

import uuid

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Attach a request id to every request/response for log correlation.

    A caller-supplied ``X-Request-ID`` is honoured (trimmed to 64 chars);
    otherwise one is generated. The id also appears in every error body.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()[:64]
        request.request_id = incoming or uuid.uuid4().hex
        response = self.get_response(request)
        response[REQUEST_ID_HEADER] = request.request_id
        return response
