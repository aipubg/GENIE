"""Test fixture: crashes the host process on demand."""
import os


class Adapter:
    def __init__(self, manifest, workspace=None):
        self.manifest = manifest

    def initialize(self):
        return {"ok": True}

    def health(self):
        return {"ok": True, "available": True, "detail": "crash fixture ready"}

    def invoke(self, capability, params):
        name = capability.rsplit(".", 1)[-1]
        if name == "die":
            os._exit(7)                       # hard process death, no cleanup
        if name == "raise":
            raise RuntimeError("fixture exception from inside the plugin")
        return {"ok": True, "detail": "fixture ok"}
