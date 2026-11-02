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
    p = 1 / (1 + math.exp(-z))
    # Convert log-odds impacts into approximate probability points for display.
    slope = p * (1 - p)
    for c in contributions:
        c["points"] = round(c["impact"] * slope, 2)
        c["label"] = describe(c["feature"], row.context) if c["value"] is not None else None
    contributions.sort(key=lambda c: c["impact"], reverse=True)
    return p, contributions


def describe(feature, ctx):
    try:
        return LABELS[feature].format(**ctx)
    except KeyError:
        return feature.replace("_", " ")


def threshold():
    return load()["threshold"]


def version():
    return load()["version"]
