import numpy as np
import pandas as pd

from src.compute import calibrate as cal


def _overlap(country, years, ratio, days_per_year=365, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for y in years:
        dates = pd.date_range(f"{y}-01-01", periods=days_per_year, freq="D")
        modis = rng.integers(10, 100, size=len(dates))
        rows.append(pd.DataFrame({
            "country": country,
            "date": dates,
            "modis_harm": modis,
            "viirs_harm": (modis * ratio).round().astype(int),
        }))
    return pd.concat(rows, ignore_index=True)


def test_fit_recovers_known_ratio():
    ov = _overlap("India", range(2012, 2020), ratio=5.0)
    f = cal.fit_factor(ov, "India")
    assert f["sufficient"]
    assert abs(f["factor"] - 5.0) < 0.01


def test_insufficient_overlap_is_not_calibrated():
    ov = _overlap("Maldives", [2012], ratio=0.9, days_per_year=30)
    res = cal.fit_all(ov, ["Maldives", "All South Asia"])
    assert res["Maldives"]["status"] == "not_calibrated"
    assert res["Maldives"]["factor_used"] is None


def test_holdout_error_zero_for_perfect_factor():
    ov = _overlap("India", range(2020, 2026), ratio=4.0)
    h = cal.holdout_error(ov, "India", 4.0)
    assert h["mean_abs_rel_error"] < 0.01


def test_apply_scales_only_modis_era():
    daily = pd.DataFrame({
        "country": ["India"] * 4,
        "year": [2010, 2011, 2012, 2013],
        "harmonised_count": [10, 20, 100, 200],
    })
    out = cal.apply_calibration(daily, {"India": 5.0})
    assert out["harmonised_calibrated"].tolist() == [50.0, 100.0, 100.0, 200.0]


def test_uncalibrated_country_left_unchanged():
    daily = pd.DataFrame({
        "country": ["Maldives"] * 2,
        "year": [2010, 2013],
        "harmonised_count": [3, 4],
    })
    out = cal.apply_calibration(daily, {"Maldives": None})
    assert out["harmonised_calibrated"].tolist() == [3.0, 4.0]


def test_confidence_tiers():
    assert cal.confidence_tier(0.05, "fitted") == "high"
    assert cal.confidence_tier(0.15, "fitted") == "medium"
    assert cal.confidence_tier(0.30, "fitted") == "low"
    assert cal.confidence_tier(0.05, "not_calibrated") == "none"
