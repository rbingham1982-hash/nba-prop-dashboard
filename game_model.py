"""
game_model.py — win-probability model for whole games, and the honest test of it.

The point of this module is not the model. It is the grading.

A winner model is trivially easy to make look good. Home teams win 52.4% of MLB games
this season; picking the moneyline favourite wins about 57%. Either number reads like a
working model and neither carries a cent of edge. The prop side of this repo spent months
believing it had an edge that turned out to be four separate defects — an un-stripped
one-sided vig, a probability floor, a payout no book offered, and synthetic backtest rows
in the ROI — every one of which inflated the same direction, undetected, because nothing
was grading predictions against the market.

So this module reports three things together, always:

  accuracy   — how often the pick was right, the number that flatters
  log loss   — how good the PROBABILITY was, which accuracy hides
  vs market  — the only one that answers "is there edge"

and it refuses to present the first without the last two.

Elo is deliberately the starting point: few parameters, no feature pipeline to leak
through, and a walk-forward evaluation that is genuinely out of sample because a rating
only ever sees games already played. If plain Elo cannot beat the market, a heavier model
built on the same data is unlikely to, and would be far harder to prove wrong.
"""
from __future__ import annotations

import math
from collections import defaultdict

# Elo tuned to the sport's schedule length and score volatility. MLB games are close to
# coin flips — a great team wins ~60% — so K stays small or ratings thrash on noise.
SPORT_PARAMS = {
    "MLB":  {"k": 4.0,  "home_adv": 24.0, "regress": 0.25},
    "WNBA": {"k": 20.0, "home_adv": 80.0, "regress": 0.25},
    "NBA":  {"k": 20.0, "home_adv": 90.0, "regress": 0.25},
}
BASE_RATING = 1500.0


def expected_home(rating_home: float, rating_away: float, home_adv: float) -> float:
    """Standard Elo expectation, with home advantage added to the home rating."""
    return 1.0 / (1.0 + 10 ** (-((rating_home + home_adv) - rating_away) / 400.0))


def walk_forward(games: list, sport: str = "MLB") -> list:
    """
    Rate teams game by game in chronological order, predicting each game BEFORE
    applying its result.

    Every prediction therefore uses only prior games, which is what makes the accuracy
    below out-of-sample rather than a description of data the model already saw. Games
    are expected as dicts with home/away team keys, a date, and a final score.
    """
    p = SPORT_PARAMS.get(sport, SPORT_PARAMS["MLB"])
    ratings: dict = defaultdict(lambda: BASE_RATING)
    out = []
    for g in sorted(games, key=lambda x: (x["date"], str(x.get("game_id", "")))):
        h, a = g["home"], g["away"]
        exp = expected_home(ratings[h], ratings[a], p["home_adv"])
        home_won = 1.0 if g["home_score"] > g["away_score"] else 0.0
        out.append({**g, "pred_home": exp, "home_won": home_won,
                    "rating_home": ratings[h], "rating_away": ratings[a]})
        delta = p["k"] * (home_won - exp)
        ratings[h] += delta
        ratings[a] -= delta
    return out, dict(ratings)


def _metrics(preds: list, label: str) -> dict:
    """Accuracy, log loss and Brier over a list of (probability, outcome) predictions."""
    n = len(preds)
    if not n:
        return {}
    eps = 1e-12
    acc = sum(1 for p, y in preds if (p >= 0.5) == (y == 1.0)) / n
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in preds) / n
    brier = sum((p - y) ** 2 for p, y in preds) / n
    return {"label": label, "n": n, "accuracy": acc, "log_loss": ll, "brier": brier}


def evaluate(rated: list, burn_in: int = 200) -> list:
    """
    Score the walk-forward run against the baselines that matter.

    burn_in drops the opening games, where every rating is still 1500 and the model is
    just predicting home-field advantage. Including them flatters nothing but does make
    the numbers a description of the prior rather than of the model.

    "Always home" is the floor any winner model must clear to be worth running at all.
    A model can beat it on accuracy and still be worse on log loss, which is the tell
    that its probabilities are wrong even when its picks are right.
    """
    live = rated[burn_in:]
    model = [(g["pred_home"], g["home_won"]) for g in live]
    home_rate = sum(y for _, y in model) / len(model) if model else 0.5
    always_home = [(0.999, g["home_won"]) for g in live]        # the naive PICK
    base_rate = [(home_rate, g["home_won"]) for g in live]      # the naive PROBABILITY
    return [
        _metrics(model, "Elo (walk-forward)"),
        _metrics(always_home, "always pick home"),
        _metrics(base_rate, f"home base rate ({home_rate:.3f})"),
    ]


def compare_to_market(rated_today: list) -> dict:
    """
    The only test that answers "is there edge": model probability against the de-vigged
    closing moneyline, on the same games.

    Beating the market on log loss is the bar. Agreeing with it is not edge — it is an
    expensive way to reproduce a number already on the screen. Disagreeing with it is
    not edge either unless the disagreements are RIGHT, which is precisely what the
    prop model's own audit found they were not (legs it liked most hit least).
    """
    paired = [(g["pred_home"], g["market_home_prob"], g["home_won"])
              for g in rated_today
              if g.get("market_home_prob") is not None and g.get("home_won") is not None]
    if not paired:
        return {"n": 0}
    model = _metrics([(p, y) for p, _, y in paired], "model")
    market = _metrics([(m, y) for _, m, y in paired], "market")
    disagree = [(p, m, y) for p, m, y in paired if (p >= 0.5) != (m >= 0.5)]
    right = sum(1 for p, _, y in disagree if (p >= 0.5) == (y == 1.0))
    return {
        "n": len(paired),
        "model": model,
        "market": market,
        "log_loss_edge": market["log_loss"] - model["log_loss"],   # positive = model better
        "disagreements": len(disagree),
        "disagreements_model_right": right,
    }
