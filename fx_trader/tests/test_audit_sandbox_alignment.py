# version: 261003
"""
tests/test_audit_sandbox_alignment.py

Method: build a small SYNTHETIC world in a temp SQLite file - six currencies
on a random walk, so every FX pair is exactly triangle-consistent, plus
crypto priced off the same latent FX - then inject known faults and assert
the audit finds exactly those: a holiday-window gap (all FX), an unscheduled
FX-wide gap, a pair-specific gap, a Binance-wide missing hour, a lone Kraken
hole, a +50 bps EUR_USD bar, a +100 bps BTC/GBP bar. Timing detection is
checked on constructed series (a one-hour FX shift must give loading ~1).
Real outputs are never read or named (hermetic); temp files only.

Run: python3 -m tests.test_audit_sandbox_alignment
"""

import json
import math
import random
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import audit_sandbox_alignment as au
from data.store import Candle, FxStore
from time_policy import epoch_to_stored_ts, fx_weekly_session

UTC = timezone.utc
HOUR = 3600
FX_PAIRS = ["GBP_USD", "EUR_GBP", "GBP_JPY", "GBP_CHF", "EUR_USD", "USD_JPY", "USD_CHF", "USD_CAD"]
CRYPTO = ["BTC/USDT", "ETH/USDT", "BTC/GBP", "ETH/GBP"]
INSTRUMENTS = [(n, "H1", None, 252 * 24) for n in FX_PAIRS] + [(n, "1h", None, 365 * 24) for n in CRYPTO]
HALF_SPREAD = 0.00005
CCY0 = {"USD": 0.0, "GBP": math.log(1.27), "EUR": math.log(1.10), "JPY": -math.log(150.0), "CHF": math.log(1.1), "CAD": -math.log(1.35)}


def _ep(y, m, d, h=0) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp())


def _candle(name, gran, epoch, bid, ask) -> Candle:
    return Candle(instrument=name, granularity=gran, timestamp=epoch_to_stored_ts(epoch),
                  bid_open=bid, bid_high=bid, bid_low=bid, bid_close=bid,
                  ask_open=ask, ask_high=ask, ask_low=ask, ask_close=ask, volume=1)


def _fx_hours() -> list:
    fridays = [date(2023, 12, 15), date(2023, 12, 22), date(2023, 12, 29)]
    hours = []
    for f in fridays:
        open_dt = fx_weekly_session(f)[1]
        last_dt = fx_weekly_session(f + timedelta(days=7))[0]
        hours += list(range(int(open_dt.timestamp()), int(last_dt.timestamp()) + 1, HOUR))
    return hours


def build_world(path: str) -> dict:
    rng = random.Random(11)
    fx_hours = _fx_hours()
    grid = list(range(fx_hours[0], fx_hours[-1] + 1, HOUR))
    latent, state = {}, dict(CCY0)
    for t in grid:
        state = {c: v + rng.gauss(0, 0.0004) for c, v in state.items()}
        latent[t] = dict(state)
    drop_all_fx = set(range(_ep(2023, 12, 25), _ep(2023, 12, 26), HOUR)) | {_ep(2024, 1, 4, 6), _ep(2024, 1, 4, 7)}
    drop_cad = {_ep(2023, 12, 28, 10), _ep(2023, 12, 28, 11), _ep(2023, 12, 28, 12)}
    inject_eur_usd = _ep(2023, 12, 27, 12)
    candles = []
    for t in fx_hours:
        for pair in FX_PAIRS:
            if t in drop_all_fx or (pair == "USD_CAD" and t in drop_cad):
                continue
            b, q = pair.split("_")
            m = math.exp(latent[t][b] - latent[t][q]) * (math.exp(0.005) if (pair == "EUR_USD" and t == inject_eur_usd) else 1.0)
            candles.append(_candle(pair, "H1", t, m * (1 - HALF_SPREAD), m * (1 + HALF_SPREAD)))
    p = {"BTC": 40000.0, "ETH": 2200.0}
    drop_usdt, drop_eth_gbp, inject_btc_gbp = _ep(2023, 12, 30, 13), _ep(2024, 1, 2, 3), _ep(2024, 1, 3, 9)
    for t in grid:
        gbpusd = math.exp(latent[t]["GBP"] - latent[t]["USD"])
        for asset in ("BTC", "ETH"):
            p[asset] *= math.exp(rng.gauss(0, 0.01))
            if t != drop_usdt:
                candles.append(_candle(f"{asset}/USDT", "1h", t, p[asset], p[asset]))
            gbp_price = p[asset] / gbpusd * (math.exp(0.01) if (asset == "BTC" and t == inject_btc_gbp) else 1.0)
            if not (asset == "ETH" and t == drop_eth_gbp):
                candles.append(_candle(f"{asset}/GBP", "1h", t, gbp_price, gbp_price))
    FxStore(database_url=f"sqlite:///{path}").upsert_candles(candles)
    return {"fx_hours": fx_hours, "grid": grid}


def _cfg(path, checks=au.CHECKS, **extra) -> dict:
    return {"checks": tuple(checks), "display_tz": "Europe/London", "sandbox_path": path, "chat": 14,
            "write_manifest": None, "verify_manifest": None, **extra}


_WORLD = {}


def _world() -> str:
    if "path" not in _WORLD:
        _WORLD["path"] = tempfile.mktemp(suffix=".db")
        _WORLD.update(build_world(_WORLD["path"]))
    return _WORLD["path"]


def _by_class(report) -> dict:
    return {e["class"]: e for e in report["gaps"]["fx"]["unscheduled_events"]}


def test_format_check_clean_world():
    r = au.run_audit(_cfg(_world(), ("format",)), INSTRUMENTS)
    assert len(r["format"]) == len(INSTRUMENTS)
    for key, row in r["format"].items():
        assert row["distinct_ts_lengths"] == 1 and row["off_hour_rows"] == 0 and row["non_stored_format_rows"] == 0, (key, row)
        assert row["ask_below_bid_rows"] == 0 and row["ohlc_inconsistent_rows"] == 0, (key, row)
    assert r["format"]["BTC/USDT|1h"]["zero_spread_rows"] == r["format"]["BTC/USDT|1h"]["rows"], "crypto bars have bid == ask"
    assert r["format"]["GBP_USD|H1"]["zero_spread_rows"] == 0
    print("format check assertions passed.")


def test_fx_gap_taxonomy():
    r = au.run_audit(_cfg(_world(), ("gaps",)), INSTRUMENTS)
    fx = r["gaps"]["fx"]
    assert fx["scheduled_weekend_gaps_per_pair"] == {p: 2 for p in FX_PAIRS}, fx["scheduled_weekend_gaps_per_pair"]
    assert fx["class_counts"] == {"HOLIDAY_CANDIDATE": 1, "FX_WIDE_UNSCHEDULED": 1, "PAIR_SPECIFIC": 1}, fx["class_counts"]
    ev = _by_class(r)
    assert ev["HOLIDAY_CANDIDATE"]["gap_hours"] == 25 and ev["HOLIDAY_CANDIDATE"]["pairs_sharing"] == 8
    assert ev["HOLIDAY_CANDIDATE"]["reference_crypto_bars_inside"] == 24, "crypto traded through the FX holiday gap"
    wide = ev["FX_WIDE_UNSCHEDULED"]
    assert wide["gap_hours"] == 3 and wide["reference_crypto_bars_inside"] == 2 == wide["reference_crypto_bars_expected"]
    one = ev["PAIR_SPECIFIC"]
    assert one["pairs"] == ["USD_CAD"] and one["gap_hours"] == 4 and one["reference_crypto_bars_inside"] == 3
    print("FX gap taxonomy assertions passed.")


def test_crypto_missing_hours_by_venue():
    r = au.run_audit(_cfg(_world(), ("gaps",)), INSTRUMENTS)
    venues = r["gaps"]["crypto"]["venues"]
    assert venues["binance"]["missing_in_all"] == 1 and venues["binance"]["missing_union"] == 1
    assert venues["binance"]["missing_in_all_sample"][0]["utc"] == "2023-12-30T13:00:00.000000000Z"
    assert venues["kraken"]["missing_in_all"] == 0 and venues["kraken"]["missing_union"] == 1
    assert venues["kraken"]["histogram_by_n_instruments_missing"] == {"1": 1}
    per = r["gaps"]["crypto"]["per_instrument"]
    assert per["BTC/USDT"]["missing_hours"] == 1 and per["BTC/USDT"]["weekend_share"] == 1.0, "2023-12-30 is a Saturday"
    assert per["BTC/GBP"]["missing_hours"] == 0 and per["ETH/GBP"]["missing_hours"] == 1
    print("crypto venue missing-hour assertions passed.")


def test_triangles_enumerated_and_injected_fault_found():
    r = au.run_audit(_cfg(_world(), ("cycles",)), INSTRUMENTS)
    cycles = r["cycles"]["cycles"]
    assert r["cycles"]["n_cycles"] == 3, "8 pairs over 6 currencies have exactly 3 triangles (USD_CAD has none)"
    hit = [c for c in cycles if "EUR_USD" in c["cycle"]]
    assert len(hit) == 1
    c = hit[0]
    assert abs(c["abs_bps"]["100"] - 50.0) < 0.01, c["abs_bps"]
    assert c["tiers"].get("suspect") == 1 and c["tiers"].get("clean") == c["n"] - 1, c["tiers"]
    assert 40 < c["exec_violation_bps"]["100"] < 50, "50 bps injected minus ~3 bps of spread"
    assert c["worst_events"][0]["at"]["utc"] == "2023-12-27T12:00:00.000000000Z"
    for other in cycles:
        if other is not c:
            assert other["abs_bps"]["100"] < 1e-6 and other["exec_violation_share"] == 0.0, other["cycle"]
            assert other["tiers"] == {"clean": other["n"]}
    print(f"triangle assertions passed (3 cycles; injected 50 bps found, executable violation {c['exec_violation_bps']['100']:.1f} bps).")


def test_basis_cycles_and_injection():
    r = au.run_audit(_cfg(_world(), ("basis",)), INSTRUMENTS)
    by_label = {c["cycle"].split(":")[0]: c for c in r["basis"]["cycles"]}
    assert set(by_label) == {"BTC via USDT/GBP", "ETH via USDT/GBP"}, set(by_label)
    assert abs(by_label["BTC via USDT/GBP"]["abs_bps"]["100"] - 100.0) < 0.01
    assert by_label["ETH via USDT/GBP"]["abs_bps"]["100"] < 1e-6
    for c in r["basis"]["cycles"]:
        assert c["rated"] is False and c["tiers"] is None and c["exec_violation_share"] is None, "basis must not be tiered (crypto bars have no spread)"
    print("basis-cycle assertions passed (injected 100 bps found).")


def test_lag_and_loading_on_world():
    r = au.run_audit(_cfg(_world(), ("lag",)), INSTRUMENTS)
    btc = r["lag"]["BTC"]
    corr = btc["binance_vs_kraken_return_corr_by_lag"]
    assert corr["0"] > 0.99 and abs(corr["1"]) < 0.3 and abs(corr["-1"]) < 0.3, corr
    assert abs(btc["basis_change_loading_on_fx_return_by_lag"]["0"]) < 0.2, btc
    print("lag assertions passed:", {k: round(v, 3) for k, v in corr.items()})


def test_loading_detects_a_one_hour_fx_shift():
    rng = random.Random(5)
    n = 600
    g = {0: 1.27}
    u = {0: 40000.0}
    for i in range(1, n):
        g[i * HOUR] = g[(i - 1) * HOUR] * math.exp(rng.gauss(0, 0.0004))
        u[i * HOUR] = u[(i - 1) * HOUR] * math.exp(rng.gauss(0, 0.01))
    fx = {t: (v, v) for t, v in g.items()}
    usdt = {t: (v, v) for t, v in u.items()}
    aligned = {t: (u[t] / g[t],) * 2 for t in g}
    shifted = {t: (u[t] / g[t - HOUR],) * 2 for t in g if t - HOUR in g}
    ok = au.basis_loading(aligned, usdt, fx)
    bad = au.basis_loading(shifted, {t: usdt[t] for t in shifted}, fx)
    assert abs(ok["0"]) < 1e-6, ok
    assert 0.8 < bad["0"] < 1.2, bad
    print(f"timing detection assertions passed (aligned loading {ok['0']:.2e}, one-hour-shift loading {bad['0']:.2f}).")


def test_ppy_check():
    n = 8766 * 2
    p = au.ppy_check("X", [i * HOUR for i in range(n)], 8760)
    expected = n / ((n - 1) * HOUR / au.YEAR_SECONDS)
    assert abs(p["observed_bars_per_year"] - expected) < 1e-6 and not p["warn"]
    q = au.ppy_check("FX", [i * HOUR for i in range(n)], 6048)
    assert q["warn"] and abs(q["sharpe_scale_if_rebased"] - math.sqrt(q["ratio"])) < 1e-12
    print("ppy assertions passed.")


def test_manifest_write_verify_and_tamper():
    path = _world()
    out = Path(tempfile.mkdtemp()) / "manifest.json"
    r1 = au.run_audit(_cfg(path, ("manifest",), write_manifest=str(out)), INSTRUMENTS)
    assert out.exists() and r1["manifest"]["instruments"] == len(INSTRUMENTS)
    r2 = au.run_audit(_cfg(path, ("manifest",), verify_manifest=str(out)), INSTRUMENTS)
    assert r2["manifest"]["verified_ok"] is True and r2["manifest"]["differences"] == []
    copy = tempfile.mktemp(suffix=".db")
    src, dst = sqlite3.connect(path), sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.execute("UPDATE candles SET bid_close = bid_close + 0.0001 WHERE instrument='GBP_USD' AND rowid = (SELECT MIN(rowid) FROM candles WHERE instrument='GBP_USD')")
    dst.execute("DELETE FROM candles WHERE instrument='BTC/GBP'")
    dst.commit()
    dst.close()
    r3 = au.run_audit(_cfg(copy, ("manifest",), verify_manifest=str(out)), INSTRUMENTS)
    diffs = r3["manifest"]["differences"]
    assert r3["manifest"]["verified_ok"] is False
    assert any(d.startswith("GBP_USD|H1") for d in diffs) and any(d.startswith("BTC/GBP|1h: in manifest but missing") for d in diffs), diffs
    try:
        au.write_manifest_file(str(out), {"x": {}}, path)
        raise RuntimeError("expected refusal to overwrite an existing manifest")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    print("manifest write / verify / tamper assertions passed.")


def test_config_rejections_and_report_name():
    path = _world()
    out_dir = tempfile.mkdtemp()

    def args(*extra):
        return au.parse_args(["--chat", "14", "--sandbox-db", path, "--out-dir", out_dir, *extra])

    cfg = au.build_config(args("--tag", "T1"), "000101")
    assert Path(cfg["report_path"]).name == "audit_231217to240105_chat14_000101_T1.json", cfg["report_path"]
    cases = [
        (lambda: au.build_config(args("--tag", "bad tag!"), "000101"), "tag"),
        (lambda: au.build_config(args("--checks", "gaps,nonsense"), "000101"), "unknown"),
        (lambda: au.build_config(args("--display-tz", "Not/AZone"), "000101"), "display-tz"),
        (lambda: au.build_config(au.parse_args(["--chat", "14", "--sandbox-db", "/nonexistent/x.db"]), "000101"), "not found"),
        (lambda: au.build_config(args("--verify-manifest", "/nonexistent/m.json"), "000101"), "not found"),
    ]
    for call, expected in cases:
        try:
            call()
            raise RuntimeError(f"expected rejection containing {expected!r}")
        except AssertionError as exc:
            assert expected in str(exc), str(exc)
    existing = Path(cfg["report_path"])
    existing.write_text("{}")
    try:
        au.build_config(args("--tag", "T1"), "000101")
        raise RuntimeError("expected refusal to reuse an existing report path")
    except AssertionError as exc:
        assert "already exists" in str(exc)
    finally:
        existing.unlink()
    print("config rejection and report-name assertions passed.")


def test_text_render_and_json_roundtrip():
    r = au.run_audit(_cfg(_world()), INSTRUMENTS)
    text = au.render_text(r)
    assert "HOLIDAY_CANDIDATE" in text and "LIMITATIONS" in text
    assert json.loads(json.dumps(r))["tool"] == "audit_sandbox_alignment", "report must be JSON-serialisable"
    print("render / JSON assertions passed.")


if __name__ == "__main__":
    test_format_check_clean_world()
    test_fx_gap_taxonomy()
    test_crypto_missing_hours_by_venue()
    test_triangles_enumerated_and_injected_fault_found()
    test_basis_cycles_and_injection()
    test_lag_and_loading_on_world()
    test_loading_detects_a_one_hour_fx_shift()
    test_ppy_check()
    test_manifest_write_verify_and_tamper()
    test_config_rejections_and_report_name()
    test_text_render_and_json_roundtrip()
    print("\nAll audit_sandbox_alignment tests passed.")
