"""Estimation simple marge / ROI / score pour les annonces du collector.

Le collector ne fait aucun calcul : il envoie prix, état et (optionnel) un prix
de revente estimé par recherche (collector/searches.json). Les règles sont ici,
sur le VPS, pour pouvoir les ajuster sans toucher au PC.
"""

from __future__ import annotations

from typing import Any

# Frais côté acheteur Vinted (protection acheteur) + envoi : estimation prudente.
BUYER_FEE_FIXED = 0.70
BUYER_FEE_RATE = 0.05
SHIPPING = 3.50
DEFAULT_RESALE_MULTIPLIER = 1.8

CONDITION_POINTS = {
    "neuf avec étiquette": 20,
    "neuf sans étiquette": 18,
    "très bon état": 15,
    "bon état": 10,
    "satisfaisant": 4,
}


def total_cost(price: float) -> float:
    return round(price + BUYER_FEE_FIXED + price * BUYER_FEE_RATE + SHIPPING, 2)


def condition_points(condition: str) -> int:
    c = (condition or "").strip().lower().replace("tres", "très").replace("etat", "état")
    return next((pts for label, pts in CONDITION_POINTS.items() if c.startswith(label)), 8)


def evaluate(price: float, resale_hint: float | None, condition: str) -> dict[str, Any]:
    """Renvoie resalePrice, profit (net de frais), roi (%) et score (0-100)."""
    resale = float(resale_hint) if resale_hint and resale_hint > 0 else price * DEFAULT_RESALE_MULTIPLIER
    cost = total_cost(price)
    profit = round(resale - cost, 2)
    roi = round(profit / cost * 100) if cost > 0 else 0
    score = (
        max(0.0, min(profit, 40.0)) / 40 * 45      # marge en € (45 pts, plafond 40 €)
        + max(0.0, min(roi, 150.0)) / 150 * 35     # ROI (35 pts, plafond 150 %)
        + condition_points(condition)              # état (20 pts)
    )
    return {"resalePrice": round(resale, 2), "profit": profit, "roi": roi,
            "score": max(0, min(100, round(score)))}
