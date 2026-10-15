"""
OrderBlock Analysis: Derivative Buildup Classifier
File: backend/engines/buildup_engine.py
Infers derivative buildup classifications:
- LONG BUILD-UP: Price ↑ + OI ↑
- SHORT BUILD-UP: Price ↓ + OI ↑
- SHORT COVERING: Price ↑ + OI ↓
- LONG UNWINDING: Price ↓ + OI ↓
"""

from backend.core.schemas import DerivativeBuildup


class DerivativeBuildupClassifier:
    """Classifies derivative positioning from intraday price delta and Open Interest delta."""

    @staticmethod
    def classify(price_delta: float, oi_delta: float) -> DerivativeBuildup:
        if price_delta > 0:
            if oi_delta >= 0:
                return DerivativeBuildup.LONG_BUILDUP
            else:
                return DerivativeBuildup.SHORT_COVERING
        elif price_delta < 0:
            if oi_delta >= 0:
                return DerivativeBuildup.SHORT_BUILDUP
            else:
                return DerivativeBuildup.LONG_UNWINDING
        return DerivativeBuildup.NEUTRAL
