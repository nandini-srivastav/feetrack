"""Inference for the dropout-risk model: a logistic regression whose
coefficients are stored in model.json. Scoring is plain Python, so the
running app does not need scikit-learn and every number can be explained.
"""
import json
import math
from functools import lru_cache
from pathlib import Path

from risk.features import FEATURES, LABELS

MODEL_PATH = Path(__file__).with_name("model.json")


@lru_cache(maxsize=1)
def load():
    return json.loads(MODEL_PATH.read_text())


def score(row):
    """Return (probability, contributions). Missing inputs are filled with
    the training average, so they neither raise nor lower the score."""
    m = load()
    z = m["intercept"]
    contributions = []
    for name in FEATURES:
        coef = m["coefficients"][name]
        mean = m["feature_means"][name]
        value = row.values[name]
        filled = mean if value is None else value
        z += coef * filled
        impact = coef * (filled - mean)  # vs. an average student
        contributions.append({"feature": name, "value": value, "impact": round(impact, 3)})
    p = sigmoid(z)
    # Share the gap between this student's score and a typical student's
    # score across the factors, in proportion to each factor's log-odds.
    # Unlike a local slope (p*(1-p), which shrinks to ~0 near p=1), these
    # points always add up to what the counsellor sees on screen.
    base = baseline()
    total_impact = sum(c["impact"] for c in contributions)
    for c in contributions:
        share = c["impact"] / total_impact if abs(total_impact) > 1e-9 else 0.0
        c["points"] = round((p - base) * share, 3)
        c["label"] = describe(c["feature"], row.context) if c["value"] is not None else None
    contributions.sort(key=lambda c: c["impact"], reverse=True)
    return p, contributions


def sigmoid(z):
    return 1 / (1 + math.exp(-z))


def baseline():
    """Score of a student whose every input equals the training average."""
    m = load()
    return sigmoid(m["intercept"] + sum(m["coefficients"][f] * m["feature_means"][f] for f in FEATURES))


def describe(feature, ctx):
    try:
        return LABELS[feature].format(**ctx)
    except KeyError:
        return feature.replace("_", " ")


def threshold():
    return load()["threshold"]


def version():
    return load()["version"]
