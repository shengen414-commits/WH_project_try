"""Standalone MPS4264 dashboard and importable acquisition API."""

from .controller import MPS4264Controller, MPSControllerError
from .protocol import FRAME_SIZE, FAST_GROUPS, decode_frame

__all__ = ["MPS4264Controller", "MPSControllerError", "FRAME_SIZE", "FAST_GROUPS", "decode_frame"]
