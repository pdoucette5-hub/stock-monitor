"""Small helpers shared across backend modules."""

from __future__ import annotations


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()
