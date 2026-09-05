"""The congestion model must bite only when the router causes it to."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from razorguard.capacity import CapacityModel, capacities
from razorguard.config import GATEWAY_SHARE, ISSUERS, METHODS, WorldConfig
from razorguard.control_plane import ControlPlane
from razorguard.detectors import default_detector
from razorguard.policy import PolicyConfig, PolicyEngine
from razorguard.routing import RoutingTable
from razorguard.scenarios import default_incident_plan
from razorguard.world import World

M = CapacityModel()


def test_below_the_knee_extra_traffic_is_free():
    for u in (0.0, 0.2, 0.5, M.knee):
        assert M.factor(u * 100, 100) == 1.0


def test_degradation_is_monotonic_past_the_knee():
    prev = 1.0
    for u in (0.75, 0.9, 1.0, 1.25, 1.5, 2.0):
        f = M.factor(u * 100, 100)
        assert f <= prev
        prev = f


def test_never_falls_below_the_floor():
    assert M.factor(100_000, 100) == M.floor


def test_zero_capacity_does_not_divide_by_zero():
    assert M.factor(50, 0) == M.floor


def test_capacity_is_sized_from_each_gateway_own_share():
    """A small gateway is provisioned small, so absorbing a large one hurts."""
    cap = capacities(900.0, GATEWAY_SHARE, 1.52, M)
    assert cap["gw_alpha"] > cap["gw_beta"] > cap["gw_gamma"]
    for g, share in GATEWAY_SHARE.items():
        assert cap[g] == 900.0 * share * 1.52 * M.headroom


def test_baseline_traffic_does_not_sit_past_the_knee():
    """Congestion must be something the router causes, not a standing tax.

    If normal operation were already congested, the control arm would be
    degraded too and the recovery comparison would be measuring the
    simulator's headroom rather than the policy.
    """
    cfg = WorldConfig()
    cap = capacities(cfg.total_txn_per_min, GATEWAY_SHARE, max(cfg.diurnal), M)
    for g, share in GATEWAY_SHARE.items():
        peak_load = cfg.total_txn_per_min * share * max(cfg.diurnal)
        assert M.utilisation(peak_load, cap[g]) < M.knee


def test_congestion_costs_the_router_more_than_the_control_arm():
    """Routing loads destinations; standing still does not."""
    def run(routing):
        world = World(WorldConfig(seed=7), default_incident_plan(1))
        cp = ControlPlane(world, default_detector(),
                          policy=PolicyEngine(PolicyConfig()),
                          enable_routing=routing)
        cp.run(1440)
        return world

    control = run(False)
    treat = run(True)
    assert (treat.success_lost_to_congestion
            > control.success_lost_to_congestion)


def test_diverting_raises_utilisation_on_the_destination():
    world = World(WorldConfig(seed=7), default_incident_plan(1))
    routing = RoutingTable.default(METHODS, ISSUERS)

    def alpha_load():
        return sum(o.attempts for o in world.step(600, routing)
                   if o.slice_key.startswith("gw_alpha|"))

    before = alpha_load()
    for method, issuer in list(routing.current):
        routing.shift_away(method, issuer, "gw_gamma", "gw_alpha", 0.9)
        routing.shift_away(method, issuer, "gw_beta", "gw_alpha", 0.9)
    assert alpha_load() > before
