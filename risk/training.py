"""Train the dropout-risk model.

Honest limitation (stated in the TDD): the client has not shared any
history of who actually dropped out, so this trains on a synthetic
population generated from stated assumptions about how fee delays,
attendance and test trends relate to dropping out. The pipeline is real:
when the client exports past withdrawals, the same code retrains on them,
and counsellors' "false flag" marks become extra negative labels.
"""
import json
from datetime import date

import numpy as np

from risk.features import FEATURES

# Assumed generative weights (log-odds per unit of each feature).
TRUE_WEIGHTS = {"overdue_months": 1.1, "missed_instalments": 0.7, "attendance_gap": 5.0,
                "attendance_drop": 6.0, "score_drop": 7.0}
TRUE_INTERCEPT = -4.0


def synthetic_population(n=4000, seed=7):
    rng = np.random.default_rng(seed)
    struggling = rng.random(n) < 0.18
    overdue_days = np.where(struggling, rng.gamma(2.0, 25, n), rng.gamma(0.6, 8, n) * (rng.random(n) < 0.3))
    overdue_months = np.clip(overdue_days, 0, 120) / 30
    missed = np.where(overdue_days > 0, 1 + (overdue_days > 75) + (overdue_days > 110), 0).astype(float)
    att_recent = np.clip(np.where(struggling, rng.normal(0.70, 0.12, n), rng.normal(0.88, 0.07, n)), 0, 1)
    att_drop = np.clip(np.where(struggling, rng.normal(0.12, 0.08, n), rng.normal(0.01, 0.04, n)), 0, 1)
    score_drop = np.clip(np.where(struggling, rng.normal(0.06, 0.05, n), rng.normal(0.0, 0.03, n)), 0, 1)
    X = np.column_stack([overdue_months, missed, 1 - att_recent, att_drop, score_drop])
    w = np.array([TRUE_WEIGHTS[f] for f in FEATURES])
    logit = TRUE_INTERCEPT + X @ w + rng.normal(0, 0.6, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


def train(threshold=0.4, extra_X=None, extra_y=None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import train_test_split

    X, y = synthetic_population()
    if extra_X is not None and len(extra_X):
        X, y = np.vstack([X, extra_X]), np.concatenate([y, extra_y])
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=1, stratify=y)
    clf = LogisticRegression(C=1.0, max_iter=1000).fit(X_tr, y_tr)
    p = clf.predict_proba(X_te)[:, 1]
    flagged = p >= threshold
    table = []
    for t in (0.3, 0.4, 0.5, 0.6):
        f = p >= t
        table.append({"threshold": t, "flag_rate": round(float(f.mean()), 3),
                      "precision": round(float(precision_score(y_te, f, zero_division=0)), 3),
                      "recall": round(float(recall_score(y_te, f, zero_division=0)), 3)})
    return {
        "version": "v" + date.today().strftime("%Y%m%d"),
        "kind": "logistic_regression",
        "trained_on": "synthetic population (n=%d) — replace with real withdrawals" % len(X),
        "intercept": float(clf.intercept_[0]),
        "coefficients": {f: float(c) for f, c in zip(FEATURES, clf.coef_[0])},
        "feature_means": {f: float(m) for f, m in zip(FEATURES, X_tr.mean(axis=0))},
        "threshold": threshold,
        "metrics": {
            "test_auc": round(float(roc_auc_score(y_te, p)), 3),
            "precision_at_threshold": round(float(precision_score(y_te, flagged, zero_division=0)), 3),
            "recall_at_threshold": round(float(recall_score(y_te, flagged, zero_division=0)), 3),
            "base_rate": round(float(y.mean()), 3),
        },
        "threshold_table": table,
        "threshold_reason": ("A false flag costs one counsellor call; a missed dropout costs the rest of "
                             "the year's fee and a student. 0.4 trades some precision for recall."),
    }


def write(model, path):
    path.write_text(json.dumps(model, indent=2) + "\n")
