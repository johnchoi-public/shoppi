"""Certification- and bay-aware scheduling engine.

Pure Python over a `Snapshot` (no database, no LLM), so the calendar, the intake
slot picker and the assistant all get the same answers, and the rules can be
unit-tested with plain fixtures.

    find_slots(snapshot, request)    -> ranked candidate plans, or why nothing fits
    validate_plan(snapshot, request, plan) -> violations of a specific plan

Hard constraints (a candidate never breaks these):
- every certification the step needs is valid ON THE DAY OF THE JOB;
- the bay fits the vehicle (class, weight, length) and has the needed capability;
- the bay is open (its own hours) and not closed for maintenance;
- the mechanic is on shift, not on time off, and not double-booked; same for the bay;
- a vehicle that must stay on the lift between steps holds the bay the whole time;
- parts are on hand or due in before the job starts;
- an apprentice has an on-shift supervisor who holds the certifications.

Soft preferences only affect ranking (see `score`).

All datetimes in a snapshot are timezone-aware UTC; `tz` is only used to work out
which local calendar day a time falls on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

UTC = timezone.utc
GRID_MINUTES = 15

DEFAULT_WEIGHTS = {
    "finish_hour": 1.0,  # per hour until the job is finished
    "not_preferred_mechanic": 6.0,
    "scarce_cert": 3.0,  # per rare certification "spent" on a job that doesn't need it
    "workload_hour": 0.5,  # per hour the mechanic already has booked that day
    "bay_oversize": 2.0,  # per vehicle class of unused bay capacity
    "bay_special": 2.0,  # per specialised capability the job doesn't use
    "mechanic_switch": 4.0,
    "bay_switch": 4.0,
    "outside_preferred_window": 8.0,
    "supervised": 4.0,  # an apprentice job also ties up a supervisor's attention
}


# ---- Model ------------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end

    def covers(self, start: datetime, end: datetime) -> bool:
        return self.start <= start and end <= self.end


@dataclass
class Mechanic:
    id: UUID
    name: str
    kind: str = "tech"  # tech | apprentice
    skill_level: int = 3
    can_supervise: bool = False
    certs: dict[UUID, date | None] = field(default_factory=dict)  # cert type -> expiry (None = never)
    shifts: list[Interval] = field(default_factory=list)
    busy: list[Interval] = field(default_factory=list)  # time off + existing jobs
    supervising: list[Interval] = field(default_factory=list)

    def holds(self, cert_id: UUID, on: date) -> bool:
        if cert_id not in self.certs:
            return False
        expiry = self.certs[cert_id]
        return expiry is None or expiry >= on

    def on_shift(self, start: datetime, end: datetime) -> bool:
        return any(s.covers(start, end) for s in self.shifts)

    def is_free(self, start: datetime, end: datetime) -> bool:
        return self.on_shift(start, end) and not any(b.overlaps(start, end) for b in self.busy)


@dataclass
class BayRes:
    id: UUID
    name: str
    capabilities: frozenset[str] = frozenset()
    max_gvwr_class: int = 3
    max_weight_lb: int | None = None
    max_length_ft: float | None = None
    open: list[Interval] = field(default_factory=list)  # when work may happen in it
    closures: list[Interval] = field(default_factory=list)
    busy: list[Interval] = field(default_factory=list)  # existing jobs and holds
    sort_order: int = 0

    def fits(self, v: "VehicleSpec") -> bool:
        if v.gvwr_class > self.max_gvwr_class:
            return False
        if v.weight_lb and self.max_weight_lb and v.weight_lb > self.max_weight_lb:
            return False
        if v.length_ft and self.max_length_ft and float(v.length_ft) > float(self.max_length_ft):
            return False
        return True

    def is_open(self, start: datetime, end: datetime) -> bool:
        return any(o.covers(start, end) for o in self.open)

    def is_clear(self, start: datetime, end: datetime) -> bool:
        """No booking, hold or closure in the way (the bay need not be 'open': a parked vehicle is fine)."""
        return not any(b.overlaps(start, end) for b in self.busy) and not any(c.overlaps(start, end) for c in self.closures)

    def is_free(self, start: datetime, end: datetime) -> bool:
        return self.is_open(start, end) and self.is_clear(start, end)


@dataclass(frozen=True)
class VehicleSpec:
    gvwr_class: int = 1
    weight_lb: int | None = None
    length_ft: float | None = None
    powertrain: str = "gas"


@dataclass
class StepReq:
    label: str
    minutes: int = 60
    hands_on: bool = True
    needs_bay: bool = True
    bay_capability: str | None = None
    holds_bay: bool = True
    same_mechanic_as_prev: bool = False
    # Each group is a set of alternatives; the mechanic needs one from every group.
    cert_groups: list[frozenset[UUID]] = field(default_factory=list)
    line_id: UUID | None = None
    step_id: UUID | None = None


@dataclass
class SlotRequest:
    steps: list[StepReq]
    vehicle: VehicleSpec
    earliest: datetime
    latest: datetime
    parts_ready_at: datetime | None = None  # None = parts are on hand
    parts_missing: list[str] = field(default_factory=list)  # needed, not in stock, no delivery known
    preferred_employee_id: UUID | None = None
    preferred_window: Interval | None = None
    # Relaxations (used for "would fit if..." suggestions and manager overrides)
    ignore_parts: bool = False
    ignore_optional_certs: bool = False


@dataclass
class Snapshot:
    tz: ZoneInfo
    now: datetime
    mechanics: list[Mechanic]
    bays: list[BayRes]
    # cert type id -> (code, name, legally_required)
    certs: dict[UUID, tuple[str, str, bool]] = field(default_factory=dict)
    supervision_cap: int = 2
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    def cert_label(self, cert_id: UUID) -> str:
        return self.certs.get(cert_id, ("?", "unknown certification", False))[1]

    def is_legal(self, cert_id: UUID) -> bool:
        return self.certs.get(cert_id, ("", "", False))[2]

    def local_date(self, dt: datetime) -> date:
        return dt.astimezone(self.tz).date()


@dataclass
class PlannedStep:
    step: StepReq
    start: datetime
    end: datetime
    employee_id: UUID | None = None
    bay_id: UUID | None = None
    supervisor_id: UUID | None = None


@dataclass
class Hold:
    bay_id: UUID
    start: datetime
    end: datetime


@dataclass
class Candidate:
    steps: list[PlannedStep]
    holds: list[Hold] = field(default_factory=list)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.steps[0].start

    @property
    def end(self) -> datetime:
        return self.steps[-1].end


@dataclass
class Blocker:
    code: str
    message: str


@dataclass
class Relaxation:
    code: str
    message: str
    start: datetime | None = None


@dataclass
class SlotSearchResult:
    candidates: list[Candidate] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    relaxations: list[Relaxation] = field(default_factory=list)


@dataclass
class Violation:
    code: str
    message: str
    # legal = a legally required certification is missing: nobody can override it.
    # conflict = a double booking: can't be overridden either (the database refuses it).
    overridable: bool = True


# ---- Eligibility ------------------------------------------------------------------


def _missing_groups(m: Mechanic, step: StepReq, on: date) -> list[frozenset[UUID]]:
    return [g for g in step.cert_groups if not any(m.holds(c, on) for c in g)]


def _supervisor_for(snap: Snapshot, m: Mechanic, step: StepReq, start: datetime, end: datetime) -> Mechanic | None:
    on = snap.local_date(start)
    best = None
    for s in snap.mechanics:
        if s.id == m.id or not s.can_supervise or s.kind == "apprentice":
            continue
        if _missing_groups(s, step, on) or not s.on_shift(start, end):
            continue
        if sum(1 for i in s.supervising if i.overlaps(start, end)) >= snap.supervision_cap:
            continue
        if best is None or s.skill_level > best.skill_level:
            best = s
    return best


def mechanic_problem(snap: Snapshot, m: Mechanic, step: StepReq, start: datetime, end: datetime,
                     req: SlotRequest) -> tuple[Violation | None, Mechanic | None]:
    """Why this mechanic can't do this step then (None if they can), plus the supervisor if one is needed."""
    on = snap.local_date(start)
    missing = _missing_groups(m, step, on)
    supervisor = None
    if m.kind == "apprentice":
        # An apprentice may work under someone who holds the certifications, but
        # must personally hold any that are legally required.
        legal_missing = [g for g in missing if any(snap.is_legal(c) for c in g)]
        if legal_missing:
            return Violation("CERT_REQUIRED_BY_LAW", f"{m.name} does not hold {_group_label(snap, legal_missing[0])}, which is legally required.", False), None
        supervisor = _supervisor_for(snap, m, step, start, end)
        if supervisor is None:
            return Violation("NO_SUPERVISOR", f"{m.name} is an apprentice and no qualified supervisor is on shift then."), None
        missing = []
    for group in missing:
        legal = any(snap.is_legal(c) for c in group)
        expired = [c for c in group if c in m.certs]
        if legal or not req.ignore_optional_certs:
            suffix = ", which is legally required and can't be overridden." if legal else "."
            if expired:
                return Violation("CERT_EXPIRED" if not legal else "CERT_REQUIRED_BY_LAW",
                                 f"{m.name}'s {_group_label(snap, group)} expires before {on.isoformat()}{suffix}", not legal), None
            return Violation("CERT_MISSING" if not legal else "CERT_REQUIRED_BY_LAW",
                             f"{m.name} does not hold {_group_label(snap, group)}{suffix}", not legal), None
    if not m.on_shift(start, end):
        return Violation("NOT_ON_SHIFT", f"{m.name} is not on shift for that whole time."), None
    if any(b.overlaps(start, end) for b in m.busy):
        return Violation("MECHANIC_BUSY", f"{m.name} is already booked or off at that time.", False), None
    return None, supervisor


def bay_problem(bay: BayRes, step: StepReq, vehicle: VehicleSpec, start: datetime, end: datetime) -> Violation | None:
    if not bay.fits(vehicle):
        return Violation("BAY_TOO_SMALL", f"{bay.name} can't take this vehicle (class {vehicle.gvwr_class}).")
    if step.bay_capability and step.bay_capability not in bay.capabilities:
        return Violation("BAY_LACKS_CAPABILITY", f"{bay.name} has no {step.bay_capability.replace('_', ' ')}.")
    if any(c.overlaps(start, end) for c in bay.closures):
        return Violation("BAY_CLOSED", f"{bay.name} is closed then.")
    if step.hands_on and not bay.is_open(start, end):
        return Violation("BAY_CLOSED", f"{bay.name} is not open for that whole time.")
    if any(b.overlaps(start, end) for b in bay.busy):
        return Violation("BAY_BUSY", f"{bay.name} is already booked at that time.", False)
    return None


def _group_label(snap: Snapshot, group: frozenset[UUID]) -> str:
    return " or ".join(sorted(snap.cert_label(c) for c in group))


# ---- Search -----------------------------------------------------------------------


def _ceil_grid(dt: datetime) -> datetime:
    dt = dt.astimezone(UTC)
    extra = (dt.minute % GRID_MINUTES, dt.second, dt.microsecond)
    if any(extra):
        dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=GRID_MINUTES - dt.minute % GRID_MINUTES)
    return dt


def _start_times(snap: Snapshot, req: SlotRequest, start_from: datetime) -> list[datetime]:
    """Grid times at which step one could begin: inside some mechanic's shift (or, for a
    hands-off first step, a bay's open hours)."""
    windows: list[Interval] = []
    first = req.steps[0]
    if first.hands_on:
        for m in snap.mechanics:
            windows.extend(m.shifts)
    else:
        for b in snap.bays:
            windows.extend(b.open)
    times: set[datetime] = set()
    dur = timedelta(minutes=first.minutes)
    for w in windows:
        t = _ceil_grid(max(w.start, start_from))
        while t + dur <= w.end and t + dur <= req.latest:
            times.add(t)
            t += timedelta(minutes=GRID_MINUTES)
    return sorted(times)


def _eligible_bays(snap: Snapshot, step: StepReq, vehicle: VehicleSpec) -> list[BayRes]:
    bays = [b for b in snap.bays if b.fits(vehicle) and (not step.bay_capability or step.bay_capability in b.capabilities)]
    # Smallest bay that fits first: keeps the heavy-duty bay free for trucks.
    return sorted(bays, key=lambda b: (b.max_gvwr_class, len(b.capabilities), b.sort_order))


def _place_step(snap: Snapshot, req: SlotRequest, step: StepReq, not_before: datetime, *,
                mechanic: Mechanic | None, bay: BayRes | None, taken: list[tuple[UUID, datetime, datetime]],
                exact: bool) -> PlannedStep | None:
    """Earliest placement of one step at/after `not_before` with a fixed mechanic and bay.
    `exact` = it must start exactly at `not_before` (used for step one)."""
    dur = timedelta(minutes=step.minutes)
    limit = not_before if exact else min(req.latest - dur, not_before + timedelta(days=5))
    t = _ceil_grid(not_before)
    while t <= limit:
        end = t + dur
        ok, supervisor = True, None
        if any(rid in (getattr(mechanic, "id", None), getattr(bay, "id", None)) and s < end and t < e for rid, s, e in taken):
            ok = False
        if ok and step.hands_on and mechanic is not None:
            problem, supervisor = mechanic_problem(snap, mechanic, step, t, end, req)
            ok = problem is None
        if ok and bay is not None:
            ok = bay_problem(bay, step, req.vehicle, t, end) is None
        if ok:
            return PlannedStep(step, t, end, mechanic.id if (mechanic and step.hands_on) else None,
                               bay.id if bay else None, supervisor.id if supervisor else None)
        if exact:
            return None
        t += timedelta(minutes=GRID_MINUTES)
    return None


def _build_chain(snap: Snapshot, req: SlotRequest, start: datetime, mechanic: Mechanic | None, bay: BayRes | None) -> Candidate | None:
    steps: list[PlannedStep] = []
    holds: list[Hold] = []
    taken: list[tuple[UUID, datetime, datetime]] = []
    cur_mech, cur_bay, t = mechanic, bay, start
    for i, step in enumerate(req.steps):
        if i > 0:
            prev = req.steps[i - 1]
            keep_bay = prev.holds_bay and step.needs_bay and cur_bay is not None \
                and bay_static_ok(cur_bay, step, req.vehicle)
            mech_options = [cur_mech] if (step.same_mechanic_as_prev and cur_mech) else \
                ([cur_mech] if cur_mech else []) + [m for m in snap.mechanics if m is not cur_mech]
            bay_options = [cur_bay] if keep_bay else ([cur_bay] if cur_bay and bay_static_ok(cur_bay, step, req.vehicle) else []) \
                + [b for b in _eligible_bays(snap, step, req.vehicle) if b is not cur_bay]
        else:
            mech_options, bay_options = [mechanic], [bay]
        if not step.hands_on:
            mech_options = [None]
        if not step.needs_bay:
            bay_options = [None]

        best: PlannedStep | None = None
        best_pair = (None, None)
        for m in mech_options:
            if step.hands_on and m is None:
                continue
            for b in bay_options:
                if step.needs_bay and b is None:
                    continue
                placed = _place_step(snap, req, step, t, mechanic=m, bay=b, taken=taken, exact=(i == 0))
                if placed and (best is None or placed.end < best.end):
                    best, best_pair = placed, (m, b)
            if best and i > 0 and best.start == _ceil_grid(t):
                break  # can't do better than "immediately"
        if best is None:
            return None

        # The vehicle stays on the lift between steps: the bay must be clear in the gap.
        if i > 0 and steps[-1].bay_id and req.steps[i - 1].holds_bay and best.start > steps[-1].end:
            held = next(b for b in snap.bays if b.id == steps[-1].bay_id)
            if not held.is_clear(steps[-1].end, best.start):
                return None
            if best.bay_id == held.id or best.bay_id is None:
                holds.append(Hold(held.id, steps[-1].end, best.start))
                taken.append((held.id, steps[-1].end, best.start))
        steps.append(best)
        if best.employee_id:
            taken.append((best.employee_id, best.start, best.end))
            cur_mech = best_pair[0]
        if best.bay_id:
            taken.append((best.bay_id, best.start, best.end))
            cur_bay = best_pair[1]
        t = best.end
    if steps[-1].end > req.latest:
        return None
    return Candidate(steps=steps, holds=holds)


def bay_static_ok(bay: BayRes, step: StepReq, vehicle: VehicleSpec) -> bool:
    return bay.fits(vehicle) and (not step.bay_capability or step.bay_capability in bay.capabilities)


def score(snap: Snapshot, req: SlotRequest, cand: Candidate) -> None:
    w = snap.weights
    parts: dict[str, float] = {}
    reasons: list[str] = []
    mech = {m.id: m for m in snap.mechanics}
    bays = {b.id: b for b in snap.bays}

    parts["finish"] = w["finish_hour"] * max(0.0, (cand.end - req.earliest).total_seconds() / 3600)

    used = [s.employee_id for s in cand.steps if s.employee_id]
    if req.preferred_employee_id:
        if req.preferred_employee_id in used:
            reasons.append("Preferred mechanic")
        else:
            parts["not_preferred_mechanic"] = w["not_preferred_mechanic"]

    # Don't spend a rare certification on a job that doesn't need it.
    holders: dict[UUID, int] = {}
    for m in snap.mechanics:
        for c in m.certs:
            holders[c] = holders.get(c, 0) + 1
    scarce = 0
    for s in cand.steps:
        if not s.employee_id:
            continue
        needed = set().union(*s.step.cert_groups) if s.step.cert_groups else set()
        scarce += sum(1 for c in mech[s.employee_id].certs if holders.get(c, 0) <= 1 and c not in needed)
    if scarce:
        parts["scarce_cert"] = w["scarce_cert"] * scarce

    day = snap.local_date(cand.start)
    load = 0.0
    for eid in set(used):
        load += sum((b.end - b.start).total_seconds() / 3600 for b in mech[eid].busy if snap.local_date(b.start) == day)
    if load:
        parts["workload"] = w["workload_hour"] * load

    oversize = special = 0.0
    for s in cand.steps:
        if s.bay_id:
            b = bays[s.bay_id]
            # Classes 1-3 are all "light duty" bays: only count capacity beyond that as wasted.
            oversize += max(0, b.max_gvwr_class - max(req.vehicle.gvwr_class, 3))
            special += len(b.capabilities - ({s.step.bay_capability} if s.step.bay_capability else set()))
    n = max(1, len(cand.steps))
    if oversize:
        parts["bay_oversize"] = w["bay_oversize"] * oversize / n
    if special:
        parts["bay_special"] = w["bay_special"] * special / n

    switches = sum(1 for a, b in zip(used, used[1:]) if a != b)
    bay_ids = [s.bay_id for s in cand.steps if s.bay_id]
    bay_switches = sum(1 for a, b in zip(bay_ids, bay_ids[1:]) if a != b)
    if switches:
        parts["mechanic_switch"] = w["mechanic_switch"] * switches
    if bay_switches:
        parts["bay_switch"] = w["bay_switch"] * bay_switches
    if req.preferred_window and not req.preferred_window.overlaps(cand.start, cand.end):
        parts["outside_preferred_window"] = w["outside_preferred_window"]
    elif req.preferred_window:
        reasons.append("In the customer's preferred time")

    if not oversize and bay_ids:
        reasons.append("Right-sized bay")
    if not scarce and used:
        reasons.append("Keeps specialist mechanics free")
    supervised = sum(1 for s in cand.steps if s.supervisor_id)
    if supervised:
        parts["supervised"] = w["supervised"] * supervised
        reasons.append("Apprentice with supervisor")
    cand.score = round(sum(parts.values()), 2)
    cand.score_breakdown = {k: round(v, 2) for k, v in parts.items()}
    cand.reasons = reasons


def find_slots(snap: Snapshot, req: SlotRequest, limit: int = 8, *, _explain: bool = True) -> SlotSearchResult:
    result = SlotSearchResult()
    if not req.steps:
        result.blockers.append(Blocker("NOTHING_TO_SCHEDULE", "This work order has no services to schedule."))
        return result

    static = _static_blockers(snap, req)
    start_from = max(req.earliest, snap.now)
    parts_blocked = False
    if not req.ignore_parts:
        if req.parts_missing:
            parts_blocked = True
        elif req.parts_ready_at and req.parts_ready_at > start_from:
            start_from = req.parts_ready_at

    candidates: list[Candidate] = []
    if not static and not parts_blocked and start_from < req.latest:
        first = req.steps[0]
        mechs = snap.mechanics if first.hands_on else [None]
        bays = _eligible_bays(snap, first, req.vehicle) if first.needs_bay else [None]
        seen: set[tuple] = set()
        first_found: datetime | None = None
        for t in _start_times(snap, req, start_from):
            # Enough choice: stop once we have plenty and are two days past the first fit.
            if first_found and len(candidates) >= limit * 4 and t > first_found + timedelta(days=2):
                break
            for m in mechs:
                if m is not None and not m.on_shift(t, t + timedelta(minutes=first.minutes)):
                    continue
                for b in bays:
                    cand = _build_chain(snap, req, t, m, b)
                    if cand is None:
                        continue
                    key = tuple((s.employee_id, s.bay_id, s.start) for s in cand.steps)
                    if key in seen:
                        continue
                    seen.add(key)
                    score(snap, req, cand)
                    candidates.append(cand)
                    first_found = first_found or t
                    break  # bays are ordered best-first: one bay per (time, mechanic) is enough

    candidates.sort(key=lambda c: (c.score, c.start))
    # Variety: at most two options per mechanic-and-day so the list isn't 8 near-identical slots.
    picked: list[Candidate] = []
    per_key: dict[tuple, int] = {}
    for c in candidates:
        k = (c.steps[0].employee_id, snap.local_date(c.start))
        if per_key.get(k, 0) >= 2:
            continue
        per_key[k] = per_key.get(k, 0) + 1
        picked.append(c)
        if len(picked) >= limit:
            break
    result.candidates = picked

    if not picked and _explain:
        result.blockers = static or _dynamic_blockers(snap, req, parts_blocked)
        result.relaxations = _relaxations(snap, req)
    return result


def _static_blockers(snap: Snapshot, req: SlotRequest) -> list[Blocker]:
    """Problems no amount of waiting inside the window will fix."""
    out: list[Blocker] = []
    last_day = snap.local_date(req.latest)
    first_day = snap.local_date(max(req.earliest, snap.now))
    for step in req.steps:
        if step.needs_bay and not _eligible_bays(snap, step, req.vehicle):
            if not any(b.fits(req.vehicle) for b in snap.bays):
                out.append(Blocker("NO_BAY_FOR_CLASS", f"No bay can take this vehicle (class {req.vehicle.gvwr_class}"
                                   + (f", {req.vehicle.weight_lb:,} lb" if req.vehicle.weight_lb else "") + ")."))
            else:
                out.append(Blocker("NO_BAY_WITH_CAPABILITY", f"No bay that fits this vehicle has {str(step.bay_capability).replace('_', ' ')} for “{step.label}”."))
        if not step.hands_on:
            continue
        for group in step.cert_groups:
            legal = any(snap.is_legal(c) for c in group)
            if req.ignore_optional_certs and not legal:
                continue
            pool = [m for m in snap.mechanics if legal or m.kind != "apprentice" or True]
            if any(any(m.holds(c, last_day) or m.holds(c, first_day) for c in group) for m in pool):
                continue
            ever = [m for m in snap.mechanics if any(c in m.certs for c in group)]
            label = _group_label(snap, group)
            if ever:
                names = ", ".join(m.name for m in ever)
                out.append(Blocker("CERT_EXPIRES_BEFORE", f"“{step.label}” needs {label}; {names}'s certification has expired."))
            else:
                out.append(Blocker("NO_CERTIFIED_MECHANIC", f"Nobody on the team holds {label}, needed for “{step.label}”."))
        longest_shift = max(((s.end - s.start).total_seconds() / 60 for m in snap.mechanics for s in m.shifts), default=0)
        if longest_shift and step.minutes > longest_shift:
            out.append(Blocker("STEP_LONGER_THAN_SHIFT", f"“{step.label}” takes {step.minutes} min, longer than any shift. Split it into steps."))
    seen, unique = set(), []
    for b in out:
        if (b.code, b.message) not in seen:
            seen.add((b.code, b.message))
            unique.append(b)
    return unique


def _dynamic_blockers(snap: Snapshot, req: SlotRequest, parts_blocked: bool) -> list[Blocker]:
    out: list[Blocker] = []
    if parts_blocked:
        out.append(Blocker("PARTS_NOT_AVAILABLE", "Parts are not in stock and no delivery is expected: " + ", ".join(req.parts_missing) + "."))
        return out
    if req.parts_ready_at and req.parts_ready_at >= req.latest and not req.ignore_parts:
        when = req.parts_ready_at.astimezone(snap.tz).strftime("%a %b %-d")
        out.append(Blocker("PARTS_UNAVAILABLE_UNTIL", f"Parts are not expected until {when}, after the end of the search window."))
        return out
    start, end = max(req.earliest, snap.now), req.latest
    hands_on = [s for s in req.steps if s.hands_on]
    if hands_on and not any(sh.overlaps(start, end) for m in snap.mechanics for sh in m.shifts):
        out.append(Blocker("NO_SHIFT_COVERAGE", "No mechanic has a shift scheduled in that period."))
        return out
    first = req.steps[0]
    if first.needs_bay:
        fitting = _eligible_bays(snap, first, req.vehicle)
        if fitting and not any(o.overlaps(start, end) for b in fitting for o in b.open):
            out.append(Blocker("BAY_CLOSED", "No suitable bay is open in that period."))
            return out
        if fitting and all(any(c.covers(start, end) for c in b.closures) for b in fitting):
            out.append(Blocker("BAY_CLOSED", "Every suitable bay is closed for the whole period."))
            return out
    # Qualified people exist and are on shift, but never together with a free bay.
    qualified = [m for m in snap.mechanics if hands_on and not _missing_groups(m, hands_on[0], snap.local_date(start))]
    if hands_on and qualified and not any(sh.overlaps(start, end) for m in qualified for sh in m.shifts):
        names = ", ".join(m.name for m in qualified)
        out.append(Blocker("NO_SHIFT_COVERAGE", f"Only {names} can do “{hands_on[0].label}”, and they have no shift in that period."))
        return out
    out.append(Blocker("FULLY_BOOKED", "Qualified mechanics and suitable bays are fully booked in that period."))
    return out


def _relaxations(snap: Snapshot, req: SlotRequest) -> list[Relaxation]:
    """Re-run with one constraint relaxed at a time to say what would make it fit.
    A legally required certification is never relaxed."""
    from dataclasses import replace

    out: list[Relaxation] = []

    def try_(code: str, message: str, **changes) -> None:
        r = find_slots(snap, replace(req, **changes), limit=1, _explain=False)
        if r.candidates:
            when = r.candidates[0].start.astimezone(snap.tz).strftime("%a %b %-d, %-I:%M %p")
            out.append(Relaxation(code, message.format(when=when), r.candidates[0].start))

    span = req.latest - req.earliest
    try_("LATER_DATE", "The earliest fit is {when} if you can wait longer.", latest=req.latest + max(span, timedelta(days=14)) * 2)
    if (req.parts_missing or req.parts_ready_at) and not req.ignore_parts:
        try_("PARTS", "It would fit on {when} if the parts were on hand.", ignore_parts=True)
    if not req.ignore_optional_certs and any(s.cert_groups for s in req.steps):
        try_("CERT_OVERRIDE", "It would fit on {when} with a manager override of a non-legal certification requirement.",
             ignore_optional_certs=True)
    return out


# ---- Validation of a specific plan ------------------------------------------------


def validate_plan(snap: Snapshot, req: SlotRequest, plan: list[PlannedStep], holds: list[Hold] | None = None) -> list[Violation]:
    """Check a concrete plan (from a candidate, a drag-and-drop, or the assistant).
    The snapshot must not include the plan's own existing rows."""
    out: list[Violation] = []
    mech = {m.id: m for m in snap.mechanics}
    bays = {b.id: b for b in snap.bays}
    for i, p in enumerate(plan):
        if p.end <= p.start:
            out.append(Violation("BAD_TIME", "End must be after start.", False))
            continue
        if p.step.hands_on:
            m = mech.get(p.employee_id) if p.employee_id else None
            if m is None:
                out.append(Violation("NO_MECHANIC", f"“{p.step.label}” needs a mechanic.", False))
            else:
                problem, supervisor = mechanic_problem(snap, m, p.step, p.start, p.end, req)
                if problem:
                    out.append(problem)
                elif supervisor and not p.supervisor_id:
                    p.supervisor_id = supervisor.id
        if p.step.needs_bay:
            b = bays.get(p.bay_id) if p.bay_id else None
            if b is None:
                out.append(Violation("NO_BAY", f"“{p.step.label}” needs a bay.", False))
            else:
                problem = bay_problem(b, p.step, req.vehicle, p.start, p.end)
                if problem:
                    out.append(problem)
        # Overlaps inside the plan itself
        for q in plan[:i]:
            if q.start < p.end and p.start < q.end:
                if p.employee_id and p.employee_id == q.employee_id:
                    out.append(Violation("MECHANIC_BUSY", "The same mechanic is on two steps at once.", False))
                if p.bay_id and p.bay_id == q.bay_id:
                    out.append(Violation("BAY_BUSY", "Two steps overlap in the same bay.", False))
    for h in holds or []:
        b = bays.get(h.bay_id)
        if b and not b.is_clear(h.start, h.end):
            out.append(Violation("BAY_HELD", f"{b.name} can't be held between steps: it is booked or closed in the gap.", False))
    if not req.ignore_parts:
        if req.parts_missing:
            out.append(Violation("PARTS_NOT_AVAILABLE", "Parts are not in stock and none are on order: " + ", ".join(req.parts_missing) + "."))
        elif req.parts_ready_at and plan and plan[0].start < req.parts_ready_at:
            when = req.parts_ready_at.astimezone(snap.tz).strftime("%a %b %-d, %-I:%M %p")
            out.append(Violation("PARTS_UNAVAILABLE_UNTIL", f"Parts are not expected until {when}."))
    return out
