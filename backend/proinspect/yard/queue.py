"""Yard queue: how long do the buyer's trucks wait for a door, and do they back up into the street?

SimPy model, one replication per seed:
  * arrivals from the buyer's schedule:
      per_day N  -> N appointments evenly spaced over the operating hours (random phase per type), each truck early
                    or late by Normal(0, LATENESS_SD) clipped to +-45 min [U: no appointment data];
      per_hour   -> 24 counts; in hour h, floor(c) trucks plus one more with probability frac(c), uniform in the hour.
  * doors: one slot each (a FilterStore of free doors); a truck can only take a door its type fits (kinematics.fit).
  * yard: waiting trucks need room on the court; capacity is an AREA (m2) estimated from the paved yard left over
    after the swept paths, and each waiting truck takes its stall area. No room -> it waits on the street (spill-back).
  * service at a door = drive in from the gate + back-in maneuver + dock time (lognormal, CV 0.25, mean =
    equipment.yaml dock_turn_min or the buyer's override) + pull-out.
Outputs over R seeds: max in yard / on street (median and P90 of the per-run max), wait P50 / P90 (all trucks pooled),
door utilization, and the timeline + per-truck log of a representative run (the run with the median total wait).
"""
import math

import numpy as np
import simpy

LATENESS_SD_MIN = 15.0
DOCK_CV = 0.25
STALL_M2 = {"semi_53": 125.0, "box_truck_26": 65.0, "last_mile_van": 32.0}   # stall + share of aisle [U]
SAMPLE_S = 60


def arrivals(trucks, hours, rng):
    """[(t_s, type)] sorted, t in seconds since midnight."""
    t0, t1 = hours[0] * 3600.0, hours[1] * 3600.0
    out = []
    for tr in trucks:
        typ = tr["type"]
        if tr.get("per_hour"):
            for h, c in enumerate(tr["per_hour"][:24]):
                k = int(math.floor(c)) + (rng.random() < c - math.floor(c))
                out += [(h * 3600.0 + rng.random() * 3600.0, typ) for _ in range(k)]
        else:
            n = int(tr.get("per_day") or 0)
            if n <= 0:
                continue
            gap = (t1 - t0) / n
            phase = rng.random() * gap
            for i in range(n):
                late = float(np.clip(rng.normal(0, LATENESS_SD_MIN), -45, 45)) * 60
                out.append((max(0.0, t0 + phase + i * gap + late), typ))
    return sorted(out)


def run_once(trucks, hours, doors, fits_ok, service, yard_m2, seed):
    """doors: [door_id]; fits_ok: {(door_id, type): bool}; service: {(door_id, type): (in_s, maneuver_s, out_s)};
    dock minutes in service['dock_min'][type]."""
    rng = np.random.default_rng(seed)
    env = simpy.Environment()
    free = simpy.FilterStore(env, capacity=len(doors))
    free.items = list(doors)
    yard = simpy.Container(env, capacity=max(yard_m2, 1e-6), init=max(yard_m2, 1e-6))
    state = {"in_yard": 0, "on_street": 0, "at_door": 0}
    log, busy = [], {d: 0.0 for d in doors}
    timeline = []
    arr = arrivals(trucks, hours, rng)
    dock_min = service["dock_min"]

    def truck(i, t, typ):
        yield env.timeout(max(0.0, t - env.now))
        rec = {"id": f"t{i + 1}", "type": typ, "arrive": env.now, "door": None, "street_s": 0.0, "yard_s": 0.0}
        log.append(rec)
        ok_doors = [d for d in doors if fits_ok.get((d, typ))]
        if not ok_doors:
            rec["unserved"] = "no tested door fits this truck"
            return
        want = lambda d: d in ok_doors  # noqa: E731
        immediate = any(want(d) for d in free.items) and not free.get_queue
        need = STALL_M2.get(typ, 100.0)
        if not immediate:
            state["on_street"] += 1
            t_s = env.now
            yield yard.get(min(need, yard.capacity)) if yard.capacity >= need else env.timeout(0)
            got_stall = yard.capacity >= need
            state["on_street"] -= 1
            rec["street_s"] = env.now - t_s
            if got_stall:
                state["in_yard"] += 1
                t_y = env.now
                d = yield free.get(want)
                state["in_yard"] -= 1
                yard.put(need)
                rec["yard_s"] = env.now - t_y
            else:              # no room at all on the court: the whole wait is on the street
                state["on_street"] += 1
                d = yield free.get(want)
                state["on_street"] -= 1
                rec["street_s"] = env.now - t_s
        else:
            d = yield free.get(want)
        rec["door"] = d
        rec["dock_start"] = env.now
        state["at_door"] += 1
        tin, man, tout = service[(d, typ)]
        mean = dock_min.get(typ, 30) * 60.0
        sig = math.sqrt(math.log(1 + DOCK_CV ** 2))
        dwell = float(rng.lognormal(math.log(mean) - sig ** 2 / 2, sig))
        rec.update(drive_in_s=tin, maneuver_s=man, dwell_s=dwell, pull_out_s=tout)
        yield env.timeout(tin + man + dwell + tout)
        busy[d] += tin + man + dwell + tout
        rec["leave"] = env.now
        state["at_door"] -= 1
        yield free.put(d)

    def sampler():
        while True:
            timeline.append([int(env.now), state["in_yard"], state["on_street"], state["at_door"]])
            yield env.timeout(SAMPLE_S)

    for i, (t, typ) in enumerate(arr):
        env.process(truck(i, t, typ))
    env.process(sampler())
    horizon = hours[1] * 3600.0 + 8 * 3600
    env.run(until=horizon)
    end = max([r.get("leave", 0) for r in log] + [hours[1] * 3600.0])
    tl = [row for row in timeline if hours[0] * 3600 - 1800 <= row[0] <= end + 600]
    waits = [(r["dock_start"] - r["arrive"]) / 60 for r in log if "dock_start" in r]
    span = (hours[1] - hours[0]) * 3600.0
    return {"seed": seed, "log": log, "timeline": tl, "waits_min": waits,
            "max_in_yard": max([r[1] for r in tl] or [0]), "max_on_street": max([r[2] for r in tl] or [0]),
            "unserved": sum(1 for r in log if r.get("unserved")),
            "unfinished": sum(1 for r in log if not r.get("unserved") and "leave" not in r),
            "door_util": {d: min(1.0, b / span) for d, b in busy.items()}, "end_s": end}


def simulate(trucks, hours, doors, fits_ok, service, yard_m2, runs=20):
    res = [run_once(trucks, hours, doors, fits_ok, service, yard_m2, seed) for seed in range(runs)]
    pooled = [w for r in res for w in r["waits_min"]]
    tot = [sum(r["waits_min"]) for r in res]
    rep = res[int(np.argsort(tot)[len(tot) // 2])]
    p = lambda a, q: round(float(np.percentile(a, q)), 1) if len(a) else None  # noqa: E731
    util = {d: round(float(np.mean([r["door_util"][d] for r in res])), 3) for d in doors}
    return {
        "runs": runs,
        "max_in_yard": int(np.median([r["max_in_yard"] for r in res])),
        "max_in_yard_p90": int(np.percentile([r["max_in_yard"] for r in res], 90)),
        "max_on_street": int(np.median([r["max_on_street"] for r in res])),
        "max_on_street_p90": int(np.percentile([r["max_on_street"] for r in res], 90)),
        "runs_with_street_spill": sum(r["max_on_street"] > 0 for r in res),
        "wait_p50_min": p(pooled, 50), "wait_p90_min": p(pooled, 90), "wait_max_min": p(pooled, 100),
        "door_utilization": round(float(np.mean(list(util.values()))), 3) if util else 0.0,
        "door_utilization_by_door": util,
        "unserved_per_day": round(float(np.mean([r["unserved"] for r in res])), 1),
        "trucks_per_day": round(float(np.mean([len(r["log"]) for r in res])), 1),
        "representative_seed": rep["seed"],
        "_rep": rep,
    }
