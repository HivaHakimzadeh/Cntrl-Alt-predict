"""Feature builder for the predictive layer.

Per tool call:  rule signals (points)  +  action one-hot  +  a few numeric features  +  a binary
bag-of-words/bigrams over the UNTRUSTED source_context (so paraphrased injections that regexes miss can still
be learned). The vocabulary is stored as plain JSON next to the model, so nothing needs pickling.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import CountVectorizer

from . import rules
from .schema import ToolCallRequest
from .tool_registry import EXTRA_ACTIONS

SIGNAL_NAMES = [
    "action_type", "external_destination", "unknown_account", "amount_mismatch", "large_amount",
    "sizeable_amount", "sensitive_data", "write_access", "action_not_requested", "target_not_requested",
    "injection_phrase", "target_from_untrusted",
]
ACTIONS = ["read_file", "send_email", "share_document", "delete_file", "wire_transfer"] + list(EXTRA_ACTIONS)
DENSE_NAMES = (
    [f"sig:{n}" for n in SIGNAL_NAMES] + ["rule_score"] + [f"act:{a}" for a in ACTIONS]
    + ["act:unknown", "amount_log10", "ctx_len_log10", "untrusted_unknown_targets"]
)
NGRAM = (1, 2)


def untrusted_unknown_targets(req: ToolCallRequest) -> int:
    """Targets the user never named that DO appear in the untrusted content (independent of any regex)."""
    n = 0
    for k, v in rules._targets(req):
        if req.action == "wire_transfer" and k == "to" and v in rules.KNOWN_PAYEES:
            continue
        d = rules._domain(v)
        if d and rules._is_internal(d):
            continue
        if rules._mentioned(v, req.source_context) and not rules._mentioned(v, req.user_request):
            n += 1
    return n


def dense_vector(req: ToolCallRequest, signals: list, rule: int) -> List[float]:
    pts: Dict[str, int] = {s.name: s.points for s in signals}
    amount = rules._num(req.arguments.get("amount")) or 0.0
    return (
        [float(pts.get(n, 0)) for n in SIGNAL_NAMES] + [float(rule)]
        + [1.0 if req.action == a else 0.0 for a in ACTIONS]
        + [1.0 if req.action not in ACTIONS else 0.0, math.log10(amount + 1),
           math.log10(len(req.source_context) + 1), float(untrusted_unknown_targets(req))]
    )


def make_vectorizer(vocabulary: Optional[dict] = None) -> CountVectorizer:
    if vocabulary is not None:  # inference: fixed vocabulary, nothing to fit
        return CountVectorizer(vocabulary=vocabulary, binary=True, ngram_range=NGRAM)
    return CountVectorizer(binary=True, ngram_range=NGRAM, min_df=5, max_features=300)


def featurize(reqs: Sequence[ToolCallRequest], signals: Sequence[list], rule_scores: Sequence[int],
              vec: CountVectorizer):
    dense = np.array([dense_vector(r, s, rs) for r, s, rs in zip(reqs, signals, rule_scores)], dtype=np.float32)
    text = vec.transform([r.source_context.lower() for r in reqs]).astype(np.float32)
    return hstack([csr_matrix(dense), text], format="csr")


def feature_names(vec: CountVectorizer) -> List[str]:
    return DENSE_NAMES + ["txt:" + t for t in vec.get_feature_names_out()]
