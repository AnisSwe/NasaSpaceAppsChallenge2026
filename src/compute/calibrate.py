# Copyright 2026 Space Apps Challenge Team
# Licensed under the Apache License, Version 2.0
"""
CALIBRATION LAYER (deterministic, no LLM):
Scales MODIS-era daily counts (years before the VIIRS break) onto the VIIRS
count scale. Per-country factors are fitted on MODIS/VIIRS overlap days in
FIT_YEARS and checked on HOLDOUT_YEARS, so the fit can be audited.
Outputs:
  demo_fixtures/daily_series.parquet  (adds column harmonised_calibrated)
  demo_fixtures/calibration.json      (factors, status, holdout error, diagnostics)
"""

import argparse
import json
import os

import numpy as np
import pandas as pd

SENSOR_BREAK_YEAR = 2012          # first year with VIIRS S-NPP detections
FIT_YEARS = (2012, 2019)          # years used to estimate the factor
HOLDOUT_YEARS = (2020, 2025)      # years held out to check the factor
MIN_OVERLAP_DAYS = 365            # below this, fall back to the pooled factor
POOLED = "All South Asia"
CAL_COL = "harmonised_calibrated"


def _overlap_slice(overlap: pd.DataFrame, country: str, years: tuple) -> pd.DataFrame:
    m = overlap["country"].astype(str).eq(country) & overlap["date"].dt.year.between(years[0], years[1])
    return overlap.loc[m]


def fit_factor(overlap: pd.DataFrame, country: str, years: tuple = FIT_YEARS,
               min_days: int = MIN_OVERLAP_DAYS) -> dict:
    """Ratio of VIIRS to MODIS counts over overlap days: sum(viirs) / sum(modis)."""
    o = _overlap_slice(overlap, country, years)
    modis = float(o["modis_harm"].sum())
    viirs = float(o["viirs_harm"].sum())
    sufficient = bool(len(o) >= min_days and modis > 0)
    return {
        "country": country,
        "fit_years": list(years),
        "n_days": int(len(o)),
        "modis_sum": modis,
        "viirs_sum": viirs,
        "factor": (viirs / modis) if modis > 0 else None,
        "sufficient": sufficient,
    }


def fit_all(overlap: pd.DataFrame, countries: list) -> dict:
    pooled = fit_factor(overlap, POOLED, min_days=0)
    results = {}
    for c in countries:
        if c == POOLED:
            continue
        f = fit_factor(overlap, c)
        if f["sufficient"]:
            f["status"] = "fitted"
            f["factor_used"] = f["factor"]
        else:
            # Too little overlap to trust a country factor (e.g. Maldives): leave counts unscaled.
            f["status"] = "not_calibrated"
            f["factor_used"] = None
        results[c] = f
    pooled["status"] = "pooled"
    pooled["factor_used"] = pooled["factor"]
    results[POOLED] = pooled
    return results


def holdout_error(overlap: pd.DataFrame, country: str, factor: float | None,
                  years: tuple = HOLDOUT_YEARS) -> dict:
    """Relative error of factor * MODIS vs VIIRS, per held-out year."""
    o = _overlap_slice(overlap, country, years)
    if o.empty or factor is None:
        return {"yearly_rel_error": {}, "mean_abs_rel_error": None}
    rows = {}
    for year, g in o.groupby(o["date"].dt.year):
        pred = float(g["modis_harm"].sum()) * factor
        act = float(g["viirs_harm"].sum())
        rows[int(year)] = round((pred - act) / act, 4) if act > 0 else None
    vals = [abs(v) for v in rows.values() if v is not None]
    return {
        "yearly_rel_error": rows,
        "mean_abs_rel_error": round(float(np.mean(vals)), 4) if vals else None,
    }


def confidence_tier(mean_abs_rel_error, status: str) -> str:
    if status != "fitted" or mean_abs_rel_error is None:
        return "none"
    if mean_abs_rel_error <= 0.10:
        return "high"
    if mean_abs_rel_error <= 0.20:
        return "medium"
    return "low"


def apply_calibration(daily: pd.DataFrame, factor_by_country: dict) -> pd.DataFrame:
    """Add harmonised_calibrated. Only MODIS-era rows (year < SENSOR_BREAK_YEAR) are scaled."""
    d = daily.copy()
    d[CAL_COL] = d["harmonised_count"].astype("float64")
    pre = d["year"] < SENSOR_BREAK_YEAR
    country = d["country"].astype(str)
    for c, f in factor_by_country.items():
        if f is None:
            continue
        m = pre & country.eq(c)
        d.loc[m, CAL_COL] = d.loc[m, "harmonised_count"].astype("float64") * f
    return d


def step_diagnostic(daily: pd.DataFrame, country: str, col: str) -> float | None:
    """Mean of the 3 MODIS-era years before the break over the first 2 VIIRS years.
    A ratio near 1.0 means the level step has been removed (seasonality is not controlled)."""
    d = daily[daily["country"].astype(str).eq(country)]
    before = d[d["year"].between(SENSOR_BREAK_YEAR - 3, SENSOR_BREAK_YEAR - 1)][col].mean()
    after = d[d["year"].between(SENSOR_BREAK_YEAR, SENSOR_BREAK_YEAR + 1)]["harmonised_count"].mean()
    if not after or np.isnan(before):
        return None
    return round(float(before / after), 3)


def run(data_dir: str = "demo_fixtures") -> dict:
    daily = pd.read_parquet(os.path.join(data_dir, "daily_series.parquet"))
    overlap = pd.read_parquet(os.path.join(data_dir, "overlap_daily.parquet"))
    daily["date"] = pd.to_datetime(daily["date"])
    overlap["date"] = pd.to_datetime(overlap["date"])

    countries = sorted(daily["country"].astype(str).unique().tolist())
    fits = fit_all(overlap, countries)
    for c, f in fits.items():
        f["holdout"] = holdout_error(overlap, c, f["factor_used"])
        f["confidence"] = confidence_tier(f["holdout"]["mean_abs_rel_error"], f["status"])

    factor_by_country = {c: f["factor_used"] for c, f in fits.items()}
    calibrated = apply_calibration(daily, factor_by_country)

    diagnostics = {
        c: {
            "step_ratio_raw": step_diagnostic(daily, c, "harmonised_count"),
            "step_ratio_calibrated": step_diagnostic(calibrated, c, CAL_COL),
        }
        for c in countries
    }

    out = {
        "sensor_break_year": SENSOR_BREAK_YEAR,
        "assumptions": [
            "daily_series is VIIRS-referenced from the break year onward; MODIS-only before it",
            "factor = sum(VIIRS) / sum(MODIS) on overlap days in fit_years",
            "2026 excluded from fit and holdout: partial year, ratio of 8.37 not yet explained",
            "countries with fewer than MIN_OVERLAP_DAYS overlap days are not calibrated (counts left as-is)",
        ],
        "fit_years": list(FIT_YEARS),
        "holdout_years": list(HOLDOUT_YEARS),
        "factors": fits,
        "diagnostics": diagnostics,
    }

    calibrated.to_parquet(os.path.join(data_dir, "daily_series.parquet"), index=False)
    with open(os.path.join(data_dir, "calibration.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="demo_fixtures")
    args = parser.parse_args()
    res = run(args.data_dir)
    for c, f in res["factors"].items():
        h = f["holdout"]["mean_abs_rel_error"]
        fu = f["factor_used"]
        print(f"{c:15s} {f['status']:15s} factor={'-' if fu is None else round(fu,3)}  holdout_mae={h}")
