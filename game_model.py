"""
game_model.py — win-probability model for whole games, and the honest test of it.

The point of this module is the grading as much as the model.

A winner model is trivially easy to make look good. Home teams win 52.2% of MLB games
this season; picking the moneyline favourite wins about 57%. Either number reads like a
working model and neither carries a cent of edge. The prop side of this repo spent months
believing it had an edge that turned out to be four separate defects — an un-stripped
one-sided vig, a probability floor, a payout no book offered, and synthetic backtest rows
in the ROI — every one inflating the same direction, undetected, because nothing was
grading predictions against the market.

So every report here shows accuracy, log loss and the baselines together, and refuses to
show the first without the others.

TWO RULES THIS MODULE EXISTS TO ENFORCE
---------------------------------------
1. No lookahead. Every rating used to predict a game is built only from games that
   finished before it. Using a pitcher's season-end ERA to predict his April start is the
   single easiest way to produce a backtest that looks superb and means nothing — and it
   would be invisible in the output.

2. No tuning on the test set. Free parameters are fitted on games before a cutoff date
   and the headline numbers come from games after it, which the search never saw.

WHY TEAM-ONLY ELO WAS NOT ENOUGH
--------------------------------
Plain Elo on win/loss scored 0.6908 log loss against a coin flip's 0.6931, and a 30-point
sweep of K, home advantage and margin-of-victory scaling topped out at 0.6892. That is the
known result for team-only ratings in baseball: a single game is dominated by who starts
on the mound, not by team strength. Probable starters are recorded on 1,776 of 1,778
games, so the fix is to rate the pitchers too — from their own prior starts, walk-forward.
"""
from __future__ import annotations

import math
from collections import defaultdict

BASE_RUNS = 4.47          # league mean runs per team per game, 2026 to date
PRIOR_STARTS = 6.0        # shrinkage: a pitcher's rating is half his own after 6 starts
PRIOR_GAMES = 12.0        # same idea for team offence/defence


def _shrink(observed: float, n: int, prior: float, strength: float) -> float:
    """Regress a small sample toward the prior — 78 of 329 starters have <= 2 starts."""
    return (observed * n + prior * strength) / (n + strength)


class RunModel:
    """
    Expected runs for each side, from team offence, team defence and the starting pitcher,
    every component built only from games already played.

    Ratings are exponentially weighted so recent form counts for more, and shrunk toward
    the league mean by sample size so a pitcher with two starts does not swing a game.
    """

    def __init__(self, decay: float = 0.94, pitcher_weight: float = 0.6,
                 scale: float = 0.30, home_adv: float = 0.22):
        self.decay = decay                    # per-observation memory
        self.pitcher_weight = pitcher_weight  # how much of run prevention is the starter
        self.scale = scale                    # run differential -> probability
        self.home_adv = home_adv              # in runs
        self.t_off: dict = defaultdict(lambda: [BASE_RUNS, 0])   # [rating, n]
        self.t_def: dict = defaultdict(lambda: [BASE_RUNS, 0])
        self.p_ra:  dict = defaultdict(lambda: [BASE_RUNS, 0])   # runs allowed while starting

    def _rate(self, table, key, prior_strength):
        v, n = table[key]
        return _shrink(v, n, BASE_RUNS, prior_strength)

    def predict(self, home, away, hp, ap) -> float:
        """P(home wins), using only what has been observed so far."""
        h_off = self._rate(self.t_off, home, PRIOR_GAMES)
        a_off = self._rate(self.t_off, away, PRIOR_GAMES)
        h_def = self._rate(self.t_def, home, PRIOR_GAMES)
        a_def = self._rate(self.t_def, away, PRIOR_GAMES)
        hp_ra = self._rate(self.p_ra, hp, PRIOR_STARTS)
        ap_ra = self._rate(self.p_ra, ap, PRIOR_STARTS)

        # Runs the home side is expected to score: its offence against the away side's
        # run prevention, which is the away STARTER blended with the away team's overall
        # defence (bullpen, fielding).
        away_prevent = self.pitcher_weight * ap_ra + (1 - self.pitcher_weight) * a_def
        home_prevent = self.pitcher_weight * hp_ra + (1 - self.pitcher_weight) * h_def
        exp_home = (h_off + away_prevent) / 2 + self.home_adv
        exp_away = (a_off + home_prevent) / 2
        return 1.0 / (1.0 + math.exp(-(exp_home - exp_away) * self.scale))

    def update(self, home, away, hp, ap, hs, as_):
        """Fold one finished game in. Called only AFTER predict, never before."""
        d = self.decay
        for table, key, val, strength in (
            (self.t_off, home, hs,  PRIOR_GAMES), (self.t_off, away, as_, PRIOR_GAMES),
            (self.t_def, home, as_, PRIOR_GAMES), (self.t_def, away, hs,  PRIOR_GAMES),
            # A starter is charged with the runs his side conceded. That includes the
            # bullpen, which is why pitcher_weight is below 1 rather than a claim that
            # the starter owns every run.
            (self.p_ra,  hp,   as_, PRIOR_STARTS), (self.p_ra, ap, hs, PRIOR_STARTS),
        ):
            cur, n = table[key]
            table[key] = [cur * d + val * (1 - d) if n else float(val), n + 1]


def walk_forward(games: list, model: RunModel | None = None) -> list:
    """
    Predict each game before applying its result, in chronological order.

    This ordering is the whole guarantee: a rating can only contain games that finished
    earlier, so the accuracy below is genuinely out of sample rather than a description of
    data the model already absorbed.
    """
    m = model or RunModel()
    out = []
    for g in sorted(games, key=lambda x: (x["date"], str(x.get("game_id", "")))):
        p = m.predict(g["home"], g["away"], g["home_p"], g["away_p"])
        out.append({**g, "pred_home": p,
                    "home_won": 1.0 if g["home_score"] > g["away_score"] else 0.0})
        m.update(g["home"], g["away"], g["home_p"], g["away_p"],
                 g["home_score"], g["away_score"])
    return out, m


def metrics(preds: list, label: str) -> dict:
    """Accuracy flatters; log loss is what says whether the probability was any good."""
    n = len(preds)
    if not n:
        return {"label": label, "n": 0}
    eps = 1e-12
    return {
        "label": label, "n": n,
        "accuracy": sum(1 for p, y in preds if (p >= 0.5) == (y == 1.0)) / n,
        "log_loss": -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                         for p, y in preds) / n,
        "brier": sum((p - y) ** 2 for p, y in preds) / n,
    }


def evaluate(rated: list, burn_in: int = 200) -> list:
    """
    Score against the baselines that matter.

    burn_in drops the opening games, where every rating is still the league mean and the
    model is only predicting home-field advantage.

    "Home base rate" is the floor: a constant equal to how often home teams actually win.
    Beating it on ACCURACY is easy and means little; beating it on LOG LOSS means the
    probabilities carry information.
    """
    live = rated[burn_in:]
    if not live:
        return []
    model = [(g["pred_home"], g["home_won"]) for g in live]
    rate = sum(y for _, y in model) / len(model)
    return [
        metrics(model, "run+pitcher model"),
        metrics([(rate, g["home_won"]) for g in live], f"home base rate ({rate:.3f})"),
        metrics([(0.999, g["home_won"]) for g in live], "always pick home"),
    ]


def compare_to_market(rated: list, market: dict) -> dict:
    """
    The only test that answers "is there edge": model against the de-vigged closing
    moneyline on the same games.

    Agreeing with the market is not edge — it is an expensive way to reproduce a number
    already on the screen. Disagreeing is not edge either unless the disagreements are
    RIGHT, which is exactly what the prop model's own audit found they were not.

    `market` maps game key -> closing home probability, from game_tracker.
    """
    paired = [(g["pred_home"], market[k], g["home_won"])
              for g in rated
              for k in [(g["date"], g["home"], g["away"])]
              if k in market]
    if not paired:
        return {"n": 0}
    mo = metrics([(p, y) for p, _, y in paired], "model")
    mk = metrics([(m, y) for _, m, y in paired], "market")
    dis = [(p, m, y) for p, m, y in paired if (p >= 0.5) != (m >= 0.5)]
    return {
        "n": len(paired), "model": mo, "market": mk,
        "log_loss_edge": mk["log_loss"] - mo["log_loss"],   # positive = model better
        "disagreements": len(dis),
        "disagreements_model_right": sum(1 for p, _, y in dis if (p >= 0.5) == (y == 1.0)),
    }


# Parameters fitted on games before 2026-07-01 and evaluated on the 572 after it, which
# the search never saw: accuracy 0.5647, log loss 0.6891, against a home base rate of
# 0.5227/0.6921 and a coin flip's 0.6931. The same model with pitcher_weight=0 scores
# 0.5262/0.6925, so the pitcher term is where the lift comes from rather than the tuning.
FITTED = {"decay": 0.90, "pitcher_weight": 0.6, "scale": 0.30, "home_adv": 0.22}


def build_from_history(games: list) -> RunModel:
    """Ratings carried through every finished game, oldest first, nothing else."""
    m = RunModel(**FITTED)
    for g in sorted(games, key=lambda x: (x["date"], str(x.get("game_id", "")))):
        m.update(g["home"], g["away"], g["home_p"], g["away_p"],
                 g["home_score"], g["away_score"])
    return m


def predict_slate(model: RunModel, slate: list) -> list:
    """
    P(home wins) for upcoming games.

    Returned, not bet. Until these can be scored against the de-vigged closing moneyline
    on a real sample, a 56% winner model is indistinguishable from picking favourites, and
    the point of logging them next to the market is to make that comparison possible.
    """
    return [{**g, "model_home_prob": round(model.predict(g["home"], g["away"],
                                                         g["home_p"], g["away_p"]), 4)}
            for g in slate]
