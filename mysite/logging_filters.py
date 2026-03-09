import logging


class Exclude404Filter(logging.Filter):
    """Exclude 404 Not Found logs from django.request (scanner/bot probes)."""

    def filter(self, record):
        if getattr(record, "status_code", None) == 404:
            return False
        return True
