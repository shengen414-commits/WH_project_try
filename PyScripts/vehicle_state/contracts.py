"""Fusion boundary: SI units, host monotonic seconds, left-positive vehicle frame.

Reference point is the rear axle midpoint. These records can also be supplied by
replay/simulation. No record contains a hardware handle or an actuator command.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ImuObservation:
    time_s: float
    sequence: int
    yaw_rate_rad_s: float
    calibrated: bool


@dataclass(frozen=True)
class SpeedObservation:
    time_s: float
    sequence: int
    speed_m_s: float


@dataclass(frozen=True)
class LineObservation:
    time_s: float
    sequence: int
    offset_left_m: float
    sensor_x_m: float
    sensor_y_m: float
    variance_m2: float


@dataclass(frozen=True)
class VisionObservation:
    time_s: float
    sequence: int
    lateral_error_m: float
    heading_error_rad: float
    # covariance of [lateral error, heading error], row-major 2x2.
    covariance: tuple
    confidence: float
