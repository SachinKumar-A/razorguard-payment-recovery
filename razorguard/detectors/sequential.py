"""Posterior-drop detector.

Two problems make naive per-slice testing useless here:

1. **Low volume.** A slice carrying 4 attempts a minute swings between 50% and
   100% on pure noise. A z-score on that is meaningless.
2. **Multiplicity.** We test hundreds of slices every minute. At any fixed
   per-test significance level, the absolute number of false alarms scales with
   the number of slices, and the report drowns.

Both are handled the same way: pool. Each slice's current success rate is
estimated as a Beta posterior whose prior is centred on its own method's pooled
rate, with a strength of `prior_strength` pseudo-observations. A low-volume
slice is therefore dragged toward its cohort and cannot alarm on three unlucky
failures; a high-volume slice overwhelms the prior and speaks for itself.

We alarm only when the posterior says the drop is both *real* and *large*:
P(p <= baseline - min_drop) >= confidence.
"""
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from scipy.stats import beta as beta_dist

from ..simulator import Observation
from .base import Alarm, Detector


class PosteriorDropDetector(Detector):
    def __init__(
        self,
        window: int = 5,
        baseline_window: int = 120,
        baseline_lag: int = 15,
        prior_strength: float = 60.0,
        min_drop_pp: float = 5.0,
        confidence: float = 0.995,
        min_baseline_attempts: int = 120,
        cooldown_min: int = 20,
    ):
        super().__init__()
        self.window = window
        self.baseline_window = baseline_window
        self.baseline_lag = baseline_lag
        self.prior_strength = prior_strength
        self.min_drop = min_drop_pp / 100.0
        self.confidence = confidence
        self.min_baseline_attempts = min_baseline_attempts
        self.cooldown_min = cooldown_min

        span = baseline_window + baseline_lag
        self._hist: Dict[str, Deque[Tuple[int, int]]] = defaultdict(
            lambda: deque(maxlen=span))
        # Pooled per-method rate, lagged by one minute so the current minute's
        # own degradation cannot inflate the prior it is being judged against.
        self._method_hist: Dict[str, Deque[Tuple[int, int]]] = defaultdict(
            lambda: deque(maxlen=baseline_window))
        self._cur_minute: Optional[int] = None
        self._cur_method: Dict[str, list] = defaultdict(lambda: [0, 0])

    @property
    def name(self) -> str:
        return (f"posterior_drop(win={self.window}, prior={self.prior_strength:g}, "
                f"drop>={self.min_drop*100:g}pp, conf={self.confidence})")

    def _roll_minute(self, minute: int) -> None:
        if self._cur_minute is None:
            self._cur_minute = minute
            return
        if minute == self._cur_minute:
            return
        for method, (att, suc) in self._cur_method.items():
            if att:
                self._method_hist[method].append((att, suc))
        self._cur_method.clear()
        self._cur_minute = minute

    def _method_prior_mean(self, method: str) -> Optional[float]:
        hist = self._method_hist[method]
        if len(hist) < self.window:
            return None
        att = sum(a for a, _ in hist)
        suc = sum(s for _, s in hist)
        return (suc / att) if att else None

    def _evaluate(self, obs: Observation) -> Optional[Alarm]:
        self._roll_minute(obs.minute)

        hist = self._hist[obs.slice_key]
        hist.append((obs.attempts, obs.successes))
        cur = self._cur_method[obs.method]
        cur[0] += obs.attempts
        cur[1] += obs.successes

        # Baseline: the slice's own history, skipping the most recent
        # `baseline_lag` minutes so an in-progress degradation does not quietly
        # become the new "normal" we compare against.
        if len(hist) < self.baseline_lag + self.window + 1:
            return None
        older = list(hist)[:-self.baseline_lag]
        base_att = sum(a for a, _ in older)
        base_suc = sum(s for _, s in older)
        if base_att < self.min_baseline_attempts:
            return None
        baseline_sr = base_suc / base_att

        recent = list(hist)[-self.window:]
        att = sum(a for a, _ in recent)
        suc = sum(s for _, s in recent)
        if att == 0:
            return None
        observed_sr = suc / att

        prior_mean = self._method_prior_mean(obs.method)
        if prior_mean is None:
            prior_mean = baseline_sr

        a = self.prior_strength * prior_mean + suc
        b = self.prior_strength * (1.0 - prior_mean) + (att - suc)

        threshold = baseline_sr - self.min_drop
        if threshold <= 0.0:
            return None

        # Cheap prefilter before the Beta CDF, which dominates runtime when
        # sweeping operating points. If the shrunk point estimate still sits
        # comfortably above the drop line, P(p <= threshold) cannot reach any
        # confidence above 0.5, so there is nothing to compute.
        posterior_mean = a / (a + b)
        if posterior_mean > threshold + 0.02:
            return None

        # P(true rate <= baseline - min_drop)
        p_dropped = float(beta_dist.cdf(threshold, a, b))
        if p_dropped < self.confidence:
            return None

        return Alarm(
            minute=obs.minute,
            slice_key=obs.slice_key,
            observed_sr=observed_sr,
            baseline_sr=baseline_sr,
            confidence=p_dropped,
            evidence={
                "window_attempts": float(att),
                "baseline_attempts": float(base_att),
                "prior_mean": prior_mean,
                "posterior_a": a,
                "posterior_b": b,
            },
        )
