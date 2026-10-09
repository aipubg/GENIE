"""Negotiate formats on the selected device, without choosing another device."""
import audioop
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFormat:
    device: int
    rate: int
    channels: int


def select_format(sd, device, kind, wire_rate):
    index = sd.default.device[0 if kind == "input" else 1] if device is None else device
    try:
        info = sd.query_devices(index, kind)
        if index < 0:
            raise ValueError("No default device")
        rates = list(dict.fromkeys([wire_rate, int(info["default_samplerate"])]))
        channels = [c for c in (1, 2) if c <= info["max_" + kind + "_channels"]]
        errors = []
        for rate in rates:
            for count in channels:
                try:
                    getattr(sd, "check_" + kind + "_settings")(
                        device=index, channels=count, dtype="int16", samplerate=rate)
                    return AudioFormat(index, rate, count)
                except Exception as exc:
                    errors.append(str(exc))
        raise ValueError("; ".join(errors) or "No usable channels")
    except Exception as exc:
        raise RuntimeError(f"Selected {kind} audio device {index} is unavailable: {exc}. "
                           "Select a working device in GENIE voice settings.") from exc


class PcmConverter:
    def __init__(self, source_rate, target_rate, source_channels=1, target_channels=1):
        self.source_rate, self.target_rate = source_rate, target_rate
        self.source_channels, self.target_channels = source_channels, target_channels
        self.state = None

    def convert(self, data):
        if not data:
            return b""
        if self.source_channels == 2:
            data = audioop.tomono(data, 2, 0.5, 0.5)
        if self.source_rate != self.target_rate:
            data, self.state = audioop.ratecv(data, 2, 1, self.source_rate,
                                            self.target_rate, self.state)
        if self.target_channels == 2:
            data = audioop.tostereo(data, 2, 1, 1)
        return data
