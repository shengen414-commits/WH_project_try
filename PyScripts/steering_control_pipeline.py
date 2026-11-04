"""Replaceable observations -> standalone proposals -> one output arbitrator.

Vehicle frame: X forward, Y left, Z up. IMU sensor frame [X forward,Y right,
Z down] maps with [1,-2,-3]. Camera offsets stay dimensionless IMAGE offsets;
image heading is not a measured vehicle yaw or lateral distance in metres.
Fusion consumes observations, not a sum/average of standalone PWM proposals.
"""
from dataclasses import dataclass, asdict
import math

IMU_MOUNT_AXES = [1, -2, -3]


@dataclass(frozen=True)
class SteeringObservation:
    source: str
    sequence: int
    observed_at: float  # Host monotonic time; device_time_ms is separate.
    valid: bool
    confidence: float
    reason: str
    image_offset_norm: float | None = None
    image_heading_deg: float | None = None
    relative_heading_deg: float | None = None
    yaw_rate_deg_s: float | None = None
    device_time_ms: int | None = None
    line_offset_norm: float | None = None


@dataclass(frozen=True)
class SteeringProposal:
    source: str
    sequence: int
    observed_at: float
    generated_at: float
    correction: float  # -1..1, positive = physical left, before PWM direction mapping.
    valid: bool
    reason: str
    lateral_us: float | None = None
    heading_us: float | None = None
    requested_us: float | None = None
    limited_us: float | None = None


def camera_observation(result, sequence, read_at, confidence_min=.65):
    values = (result.get('lookahead_error_norm'), result.get('image_angle_deg'))
    valid = bool(result.get('valid')) and result.get('confidence', 0) >= confidence_min
    valid = valid and all(type(v) in (float,int) and math.isfinite(v) for v in values)
    return SteeringObservation('camera', sequence, read_at, valid, float(result.get('confidence',0)),
        'ok' if valid else result.get('reason') if not result.get('valid') else 'low_confidence',
        image_offset_norm=values[0] if valid else None, image_heading_deg=values[1] if valid else None)


def line_observation(probes, observed_at):
    """Caller supplies the mapped measurement timestamp, not time of polling."""
    center=probes.get('center_channel')
    valid=bool(probes.get('valid') and probes.get('fresh')) and center is not None
    return SteeringObservation('line',probes.get('sequence',0),observed_at,valid,1. if valid else 0.,probes.get('reason','waiting'),
        device_time_ms=probes.get('device_ms'),line_offset_norm=(center-3.5)/3.5 if valid else None)


def standalone_proposal(observation, settings, now):
    """Pure PD/proportional test policies; no serial, GPIO, file, or camera I/O."""
    o=observation
    if not o.valid or not 0 <= now-o.observed_at <= .15:
        return SteeringProposal(o.source,o.sequence,o.observed_at,now,0.,False,'observation_stale' if o.valid else o.reason)
    lateral_us=heading_us=None
    if o.source=='imu':
        if o.relative_heading_deg is None or o.yaw_rate_deg_s is None:
            raise ValueError('IMU observation requires heading and yaw rate')
        heading=o.relative_heading_deg
        error=0. if abs(heading)<=settings['deadband'] else heading-math.copysign(settings['deadband'],heading)
        correction_us=-settings['kp']*error-settings['kd']*o.yaw_rate_deg_s
    elif o.source=='camera':
        if o.image_offset_norm is None or o.image_heading_deg is None:
            raise ValueError('Camera observation requires image offset and heading')
        offset=o.image_offset_norm
        error=0. if abs(offset)<=settings['deadband'] else offset-math.copysign(settings['deadband'],offset)
        lateral_us=-settings['kp']*error
        heading_us=-settings['kd']*o.image_heading_deg
        correction_us=lateral_us+heading_us
    else:
        raise ValueError('Standalone policy is available only for imu/camera; fusion needs its own policy')
    if not math.isfinite(correction_us):
        raise ValueError('Non-finite control output')
    limited_us=max(-settings['max_us'],min(settings['max_us'],correction_us))
    return SteeringProposal(o.source,o.sequence,o.observed_at,now,limited_us/settings['max_us'],True,'ok',
        lateral_us=lateral_us,heading_us=heading_us,requested_us=correction_us,limited_us=limited_us)


def arbitrate(selected_source, proposal, now):
    """Replace this selector with joint decision logic, keeping the gateway."""
    if proposal.source!=selected_source or not proposal.valid or not 0 <= now-proposal.observed_at <= .15 or not -1 <= proposal.correction <= 1:
        raise ValueError('Selected controller has no fresh valid proposal')
    return proposal.correction


def serialize(value):
    return asdict(value) if value is not None else None
