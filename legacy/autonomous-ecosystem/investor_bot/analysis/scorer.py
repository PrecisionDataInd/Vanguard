"""Investment opportunity scorer — scores candidates 0.0-1.0 by pool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from loguru import logger

from investor_bot.analysis.screener import OpportunityCandidate

# ---------------------------------------------------------------------------
# Score result
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    score: float          # 0.0 - 1.0
    conviction: str       # HIGH / MEDIUM / LOW
    factor_scores: dict   # individual factor scores
    pool: str
    signal_type: str


# ---------------------------------------------------------------------------
# Pool weight definitions
# ---------------------------------------------------------------------------

AGGRESSIVE_WEIGHTS = {
    "momentum_strength": 0.30,
    "volume_confirmation": 0.20,
    "risk_reward": 0.25,
    "trend_alignment": 0.15,
    "signal_quality": 0.10,
}

BALANCED_WEIGHTS = {
    "value_margin": 0.30,
    "earnings_quality": 0.25,
    "risk_reward": 0.20,
    "momentum": 0.15,
    "dividend_health": 0.10,
}

STEADY_WEIGHTS = {
    "dividend_reliability": 0.35,
    "value_score": 0.25,
    "downside_protection": 0.25,
    "earnings_stability": 0.15,
}

POOL_WEIGHTS = {
    "aggressive": AGGRESSIVE_WEIGHTS,
    "balanced": BALANCED_WEIGHTS,
    "steady": STEADY_WEIGHTS,
}


# ---------------------------------------------------------------------------
# Factor scoring functions (each returns 0.0-1.0)
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _score_momentum_strength(data: dict) -> float:
    """Score based on RSI level and trajectory."""
    rsi = data.get("rsi", 50)
    # RSI 55-75 is ideal for momentum entry
    if 55 <= rsi <= 75:
        return _clamp((rsi - 50) / 25)
    elif rsi > 75:
        return _clamp(1.0 - (rsi - 75) / 25)  # overbought penalty
    return _clamp(rsi / 55 * 0.5)


def _score_volume_confirmation(data: dict) -> float:
    """Score volume relative to average."""
    vol_ratio = data.get("volume_ratio", 1.0)
    # 1.5x+ is confirmation, 3x+ is strong
    if vol_ratio >= 3.0:
        return 1.0
    elif vol_ratio >= 1.5:
        return _clamp(0.5 + (vol_ratio - 1.5) / 3.0)
    return _clamp(vol_ratio / 3.0)


def _score_risk_reward(data: dict) -> float:
    """Score based on estimated risk/reward ratio."""
    rr = data.get("rr_ratio", 2.0)
    if rr >= 4.0:
        return 1.0
    elif rr >= 2.0:
        return _clamp(0.6 + (rr - 2.0) / 5.0)
    elif rr >= 1.0:
        return _clamp(0.3 * rr / 2.0)
    return 0.1


def _score_trend_alignment(data: dict) -> float:
    """Score EMA structure alignment."""
    ema20 = data.get("ema20", 0)
    ema50 = data.get("ema50", 0)
    price = data.get("price", 0)

    score = 0.0
    if price > 0 and ema20 > 0 and ema50 > 0:
        if price > ema20 > ema50:
            score = 1.0
        elif price > ema20:
            score = 0.7
        elif price > ema50:
            score = 0.4
        else:
            score = 0.1
    return score


def _score_signal_quality(data: dict) -> float:
    """Score how clean/textbook the setup is."""
    rsi = data.get("rsi", 50)
    vol_ratio = data.get("volume_ratio", 1.0)

    # Clean setup: RSI 55-65 on high volume
    rsi_quality = 1.0 - abs(rsi - 60) / 20 if 40 <= rsi <= 80 else 0.3
    vol_quality = min(vol_ratio / 2.0, 1.0) if vol_ratio > 0 else 0.0
    return _clamp((rsi_quality + vol_quality) / 2)


def _score_value_margin(data: dict) -> float:
    """Score discount to fair/historical value."""
    discount = data.get("pct_from_52w_high", 0) or data.get("discount_pct", 0) or data.get("sma200_discount_pct", 0)
    # 25-50% discount from high is value territory
    if discount >= 40:
        return 1.0
    elif discount >= 25:
        return _clamp(0.7 + (discount - 25) / 50)
    elif discount >= 10:
        return _clamp(discount / 25 * 0.7)
    return _clamp(discount / 25 * 0.3)


def _score_earnings_quality(data: dict) -> float:
    """Score earnings quality (positive, surprise)."""
    gap_pct = data.get("gap_pct", 0)
    total_move = data.get("total_move_pct", 0)

    if gap_pct > 5:
        return 0.9
    elif gap_pct > 2:
        return 0.7
    elif gap_pct > 0:
        return 0.5
    # For non-earnings signals, default moderate
    return 0.5


def _score_momentum(data: dict) -> float:
    """Score that the stock is not in freefall (for balanced/value plays)."""
    rsi = data.get("rsi", 50)
    if rsi >= 45:
        return _clamp(0.7 + (rsi - 45) / 100)
    elif rsi >= 35:
        return _clamp(0.4 + (rsi - 35) / 30)
    elif rsi >= 25:
        return _clamp((rsi - 20) / 25)
    return 0.1


def _score_dividend_health(data: dict) -> float:
    """Score dividend reliability for balanced pool."""
    # For non-dividend signals, neutral
    return 0.5


def _score_dividend_reliability(data: dict) -> float:
    """Score for steady pool: dividend streak and payout ratio."""
    pct_below = data.get("pct_below_52w_high", 0) or data.get("sma200_discount_pct", 0)
    # Stocks in steady watchlist are pre-screened dividend aristocrats
    # Score based on how attractive the entry is
    if pct_below >= 20:
        return 0.9
    elif pct_below >= 15:
        return 0.75
    elif pct_below >= 10:
        return 0.6
    return 0.4


def _score_value_score(data: dict) -> float:
    """Score value for steady pool."""
    return _score_value_margin(data)


def _score_downside_protection(data: dict) -> float:
    """Estimate downside protection."""
    rsi = data.get("rsi", 50)
    pullback = data.get("pullback_pct", 0) or data.get("pct_below_52w_high", 0)
    # Lower RSI + larger pullback = more downside already priced in
    if pullback >= 20 and rsi < 40:
        return 0.9
    elif pullback >= 10:
        return _clamp(0.5 + pullback / 40)
    return 0.4


def _score_earnings_stability(data: dict) -> float:
    """Score earnings consistency for steady pool."""
    # Pre-screened stocks in steady are stable earners
    return 0.6


# ---------------------------------------------------------------------------
# Factor dispatch
# ---------------------------------------------------------------------------

FACTOR_SCORERS = {
    "momentum_strength": _score_momentum_strength,
    "volume_confirmation": _score_volume_confirmation,
    "risk_reward": _score_risk_reward,
    "trend_alignment": _score_trend_alignment,
    "signal_quality": _score_signal_quality,
    "value_margin": _score_value_margin,
    "earnings_quality": _score_earnings_quality,
    "momentum": _score_momentum,
    "dividend_health": _score_dividend_health,
    "dividend_reliability": _score_dividend_reliability,
    "value_score": _score_value_score,
    "downside_protection": _score_downside_protection,
    "earnings_stability": _score_earnings_stability,
}


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def score(candidate: OpportunityCandidate, min_score: float = 0.0) -> ScoreResult | None:
    """Score an opportunity candidate.

    Args:
        candidate: The opportunity to score.
        min_score: Minimum score threshold (from pool config).

    Returns:
        ScoreResult if score >= min_score, else None.
    """
    pool = candidate.pool
    weights = POOL_WEIGHTS.get(pool, AGGRESSIVE_WEIGHTS)

    # Merge signal_data with price for scoring
    scoring_data = {**candidate.signal_data, "price": candidate.price}

    factor_scores = {}
    weighted_sum = 0.0

    for factor_name, weight in weights.items():
        scorer_fn = FACTOR_SCORERS.get(factor_name)
        if scorer_fn:
            try:
                factor_val = scorer_fn(scoring_data)
            except Exception as exc:
                logger.warning(f"Factor {factor_name} scoring failed: {exc}")
                factor_val = 0.5  # neutral on error
        else:
            factor_val = 0.5

        factor_scores[factor_name] = round(factor_val, 3)
        weighted_sum += factor_val * weight

    raw_score = _clamp(weighted_sum)

    # Determine conviction
    if raw_score >= 0.80:
        conviction = "HIGH"
    elif raw_score >= 0.70:
        conviction = "MEDIUM"
    else:
        conviction = "LOW"

    if raw_score < min_score:
        return None

    result = ScoreResult(
        score=round(raw_score, 3),
        conviction=conviction,
        factor_scores=factor_scores,
        pool=pool,
        signal_type=candidate.signal_type,
    )

    logger.debug(
        f"Scored {candidate.symbol} ({candidate.signal_type}): "
        f"{result.score:.3f} [{result.conviction}]"
    )

    return result
