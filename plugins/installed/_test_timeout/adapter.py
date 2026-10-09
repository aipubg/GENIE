"""Test fixture: hangs forever on demand."""
import time


class Adapter:
    def __init__(self, manifest, workspace=None):
        self.manifest = manifest

    def initialize(self):
        return {"ok": True}

    def health(self):
        return {"ok": True, "available": True, "detail": "timeout fixture ready"}

    def invoke(self, capability, params):
        while True:                            # the client must time out and kill us
            time.sleep(1)
