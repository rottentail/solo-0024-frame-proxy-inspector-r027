from __future__ import annotations

import mmap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# Read-only reuse of the production media adapters. The investigator never
# writes through these modules and never imports the production HTTP/service
# layers, so the running service is unaffected.
from frame_proxy.media.avi import AviDescriptor, probe_avi
from frame_proxy.media.frames import Frame, decode_frame_at


@dataclass(frozen=True, slots=True)
class MediaHandle:
    descriptor: AviDescriptor
    path: Path
    digest: str
    byte_size: int


def open_media(path: str | Path) -> MediaHandle:
    """Probe an AVI file without retaining its decoded frames."""
    media_path = Path(path)
    payload = media_path.read_bytes()
    descriptor = probe_avi(payload)
    return MediaHandle(
        descriptor=descriptor,
        path=media_path,
        digest=_sha256_file(media_path),
        byte_size=media_path.stat().st_size,
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_decoded_frames(handle: MediaHandle) -> Iterator[Frame]:
    """Decode frames one at a time via a read-only memory map.

    Only one decoded frame is resident per iteration. The mmap object speaks
    the buffer protocol (``len`` and slicing) that the production decoder
    expects, so no whole-file copy is made and a whole library is never held
    decoded in memory.
    """
    descriptor = handle.descriptor
    with handle.path.open("rb") as file_handle:
        with mmap.mmap(
            file_handle.fileno(),
            length=0,
            access=mmap.ACCESS_READ,
        ) as mapped:
            for frame_index in range(descriptor.frame_count):
                yield decode_frame_at(mapped, descriptor, frame_index)


def decode_one(handle: MediaHandle, frame_index: int) -> Frame:
    descriptor = handle.descriptor
    with handle.path.open("rb") as file_handle:
        with mmap.mmap(
            file_handle.fileno(),
            length=0,
            access=mmap.ACCESS_READ,
        ) as mapped:
            return decode_frame_at(mapped, descriptor, frame_index)
