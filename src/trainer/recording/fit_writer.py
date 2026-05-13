"""Write a Garmin-compatible FIT activity file from a recorded session."""
from __future__ import annotations

from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Event,
    EventType,
    FileType,
    Manufacturer,
    Sport,
    SubSport,
)

from ..workout.model import Workout
from .recorder import Record


def _ts_ms(start_unix: float, t_s: float) -> int:
    return int((start_unix + t_s) * 1000)


def write_fit(
    *,
    out_path: Path,
    workout: Workout,
    started_at_unix: float,
    records: list[Record],
) -> Path:
    """Build and write a FIT activity file with per-step laps."""
    builder = FitFileBuilder(auto_define=True)

    start_ms = int(started_at_unix * 1000)
    end_ms = _ts_ms(started_at_unix, records[-1].t_s) if records else start_ms

    # File ID
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.time_created = start_ms
    builder.add(fid)

    # Start event
    ev_start = EventMessage()
    ev_start.timestamp = start_ms
    ev_start.event = Event.TIMER
    ev_start.event_type = EventType.START
    builder.add(ev_start)

    # Records (1 Hz)
    for r in records:
        rec = RecordMessage()
        rec.timestamp = _ts_ms(started_at_unix, r.t_s)
        if r.power_w is not None:
            rec.power = max(0, int(r.power_w))
        if r.cadence_rpm is not None:
            rec.cadence = max(0, int(r.cadence_rpm))
        if r.hr_bpm is not None:
            rec.heart_rate = int(r.hr_bpm)
        if r.speed_kph is not None:
            rec.speed = float(r.speed_kph) * 1000.0 / 3600.0  # m/s
        rec.distance = float(r.distance_m)
        builder.add(rec)

    # Laps: one per workout step that actually had data
    if records:
        boundaries: list[tuple[int, int, int]] = []  # (step_idx, start_idx, end_idx)
        cur_step = records[0].step_idx
        seg_start = 0
        for i, r in enumerate(records):
            if r.step_idx != cur_step:
                boundaries.append((cur_step, seg_start, i - 1))
                cur_step = r.step_idx
                seg_start = i
        boundaries.append((cur_step, seg_start, len(records) - 1))

        for step_idx, a, b in boundaries:
            seg = records[a:b + 1]
            if not seg:
                continue
            lap = LapMessage()
            lap.timestamp = _ts_ms(started_at_unix, seg[-1].t_s)
            lap.start_time = _ts_ms(started_at_unix, seg[0].t_s)
            lap.total_elapsed_time = float(seg[-1].t_s - seg[0].t_s + 1)
            lap.total_timer_time = lap.total_elapsed_time
            lap.total_distance = float(seg[-1].distance_m - seg[0].distance_m)
            powers = [r.power_w for r in seg if r.power_w is not None]
            if powers:
                lap.avg_power = int(sum(powers) / len(powers))
                lap.max_power = int(max(powers))
            hrs = [r.hr_bpm for r in seg if r.hr_bpm is not None]
            if hrs:
                lap.avg_heart_rate = int(sum(hrs) / len(hrs))
                lap.max_heart_rate = int(max(hrs))
            cads = [r.cadence_rpm for r in seg if r.cadence_rpm is not None]
            if cads:
                lap.avg_cadence = int(sum(cads) / len(cads))
            lap.sport = Sport.CYCLING
            lap.sub_sport = SubSport.INDOOR_CYCLING
            try:
                if 0 <= step_idx < len(workout.steps):
                    lap.wkt_step_index = step_idx
            except Exception:  # noqa: BLE001
                pass
            builder.add(lap)

        # Session
        powers = [r.power_w for r in records if r.power_w is not None]
        hrs = [r.hr_bpm for r in records if r.hr_bpm is not None]
        cads = [r.cadence_rpm for r in records if r.cadence_rpm is not None]
        sess = SessionMessage()
        sess.timestamp = end_ms
        sess.start_time = start_ms
        sess.sport = Sport.CYCLING
        sess.sub_sport = SubSport.INDOOR_CYCLING
        sess.total_elapsed_time = float(records[-1].t_s + 1)
        sess.total_timer_time = sess.total_elapsed_time
        sess.total_distance = float(records[-1].distance_m)
        if powers:
            sess.avg_power = int(sum(powers) / len(powers))
            sess.max_power = int(max(powers))
        if hrs:
            sess.avg_heart_rate = int(sum(hrs) / len(hrs))
            sess.max_heart_rate = int(max(hrs))
        if cads:
            sess.avg_cadence = int(sum(cads) / len(cads))
        sess.first_lap_index = 0
        sess.num_laps = len(boundaries)
        builder.add(sess)

    # Stop event
    ev_stop = EventMessage()
    ev_stop.timestamp = end_ms
    ev_stop.event = Event.TIMER
    ev_stop.event_type = EventType.STOP_ALL
    builder.add(ev_stop)

    # Activity
    act = ActivityMessage()
    act.timestamp = end_ms
    act.total_timer_time = float(records[-1].t_s + 1) if records else 0.0
    act.num_sessions = 1
    builder.add(act)

    fit = builder.build()
    fit.to_file(str(out_path))
    return out_path
