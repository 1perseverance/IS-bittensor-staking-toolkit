 """
chutes_sn64_analysis.py
=======================
SN64 Chutes — Intelligence Market Hypothesis Test
Part of the Intelligence Sovereignty research suite by @im_perseverance

Tests whether Bittensor functions as a market for intelligence by comparing:
  Dataset 1: Real demand    — Chutes invocation exports (api.chutes.ai, public)
  Dataset 2: Recognition    — Validator weight allocations (Bittensor on-chain)
  Dataset 3: Capital flow   — Emission distribution (SN64 metagraph)

Join key: SS58 miner hotkey (present in both invocation CSV and metagraph)
Time window: 7-day rolling aggregate (matches incentive calculation window)

Usage:
    python chutes_sn64_analysis.py
    python chutes_sn64_analysis.py --root-threshold 1000 --alpha-threshold 1000
"""

import bittensor as bt
import requests
import csv
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────────
NETUID               = 64
CHUTES_API_BASE      = "https://api.chutes.ai"
INVOCATION_WINDOW    = 7
ROOT_TAO_THRESHOLD   = 1000.0
SN64_ALPHA_THRESHOLD = 1000.0
OUTPUT_DIR           = Path("chutes_analysis")

QUALITY_WEIGHT_COMPUTE    = 0.55
QUALITY_WEIGHT_INVOCATION = 0.25
QUALITY_WEIGHT_DIVERSITY  = 0.15
QUALITY_WEIGHT_EFFICIENCY = 0.05

SEPARATOR = "=" * 100
THIN_SEP  = "-" * 100


# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_pct(val):
    if val is None:
        return "  N/A  "
    return f"{val*100:.2f}%"

def fmt_delta(demand, emission):
    if demand is None or emission is None:
        return "  N/A  "
    delta = emission - demand
    sign  = "+" if delta >= 0 else ""
    return f"{sign}{delta*100:.2f}pp"

def fmt_large(val):
    if val is None: return "N/A"
    if val >= 1_000_000: return f"{val/1_000_000:.2f}M"
    if val >= 1_000:     return f"{val/1_000:.2f}K"
    return f"{val:.2f}"


# ── Alpha Pool ─────────────────────────────────────────────────────────────────

def fetch_alpha_pool_state(sub, netuid):
    result = {
        "alpha_price": None, "ema_price": None,
        "spot_ema_gap": None, "spot_ema_gap_pct": None,
        "tao_reserves": None, "alpha_outstanding": None,
        "alpha_in_pool": None, "market_cap_tao": None,
    }
    try:
        sn = sub.subnet(netuid)
        if sn is None:
            return result
        tao_in    = getattr(sn, 'tao_in', None)
        alpha_in  = getattr(sn, 'alpha_in', None)
        alpha_out = getattr(sn, 'alpha_out', None)
        mov_price = getattr(sn, 'moving_price', None)
        price     = getattr(sn, 'price', None)
        if tao_in is not None:    result["tao_reserves"]      = float(tao_in)
        if alpha_in is not None:  result["alpha_in_pool"]     = float(alpha_in)
        if alpha_out is not None: result["alpha_outstanding"] = float(alpha_out)
        if price is not None:     result["alpha_price"]       = float(price)
        elif tao_in and alpha_in and float(alpha_in) > 0:
            result["alpha_price"] = float(tao_in) / float(alpha_in)
        if mov_price is not None: result["ema_price"]         = float(mov_price)
        if result["alpha_price"] and result["alpha_outstanding"]:
            result["market_cap_tao"] = result["alpha_price"] * result["alpha_outstanding"]
        if result["alpha_price"] and result["ema_price"]:
            result["spot_ema_gap"]     = result["alpha_price"] - result["ema_price"]
            result["spot_ema_gap_pct"] = (result["spot_ema_gap"] / result["ema_price"]) * 100
        print(f"  Alpha pool state fetched")
    except Exception as e:
        print(f"  Alpha pool fetch failed: {e}")
    return result


# ── Invocation Exports ─────────────────────────────────────────────────────────

def fetch_invocation_exports(window_days):
    print(f"\nFetching Chutes invocation exports ({window_days}-day window)...")
    now  = datetime.now(timezone.utc)
    agg  = defaultdict(lambda: {"hotkey": None, "uid": None,
                                 "invocation_count": 0, "compute_seconds": 0.0,
                                 "chute_ids": set()})
    user_agg = defaultdict(lambda: {"invocation_count": 0, "compute_seconds": 0.0,
                                     "chute_ids": set()})
    total_rows    = 0
    hours_fetched = 0

    for days_back in range(window_days):
        target = now - timedelta(days=days_back)
        for hour in range(24):
            t   = target.replace(hour=hour, minute=0, second=0, microsecond=0)
            url = (f"{CHUTES_API_BASE}/invocations/exports/"
                   f"{t.year}/{t.month:02d}/{t.day:02d}/{t.hour:02d}.csv")
            try:
                r = requests.get(url, timeout=20)
                if r.status_code != 200 or not r.text.strip():
                    continue
                lines = r.text.split("\n")
                if len(lines) <= 1:
                    continue
                header = lines[0].rstrip("\r").split(",")

                # Detect fields
                hf = uf = cf = chf = usf = None
                for f in header:
                    fl = f.lower()
                    if fl == "miner_hotkey":   hf  = f
                    if fl == "miner_uid":      uf  = f
                    if fl in ("compute_time", "compute_seconds"): cf = f
                    if fl == "chute_id":       chf = f
                    if fl == "chute_user_id":  usf = f

                if cf is None and "started_at" in header and "completed_at" in header:
                    cf = "__derived__"

                hi  = header.index(hf)  if hf  and hf  in header else None
                ui  = header.index(uf)  if uf  and uf  in header else None
                ci  = header.index(cf)  if cf  and cf  in header and cf != "__derived__" else None
                chi = header.index(chf) if chf and chf in header else None
                usi = header.index(usf) if usf and usf in header else None
                sa_i = header.index("started_at")   if "started_at"   in header else None
                ca_i = header.index("completed_at") if "completed_at" in header else None

                if hi is None:
                    continue

                rows = 0
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    cols = line.rstrip("\r").split(",")
                    if hi >= len(cols):
                        continue
                    hk = cols[hi].strip()
                    if not hk or len(hk) < 10:
                        continue

                    rows += 1
                    agg[hk]["hotkey"] = hk
                    agg[hk]["invocation_count"] += 1
                    if ui is not None and ui < len(cols) and cols[ui]:
                        agg[hk]["uid"] = cols[ui]

                    compute_val = 0.0
                    if cf == "__derived__" and sa_i and ca_i and sa_i < len(cols) and ca_i < len(cols):
                        try:
                            t0 = datetime.fromisoformat(cols[sa_i].replace("Z", ""))
                            t1 = datetime.fromisoformat(cols[ca_i].replace("Z", ""))
                            compute_val = (t1 - t0).total_seconds()
                        except Exception:
                            pass
                    elif ci is not None and ci < len(cols) and cols[ci]:
                        try:
                            compute_val = float(cols[ci])
                        except (ValueError, TypeError):
                            pass

                    agg[hk]["compute_seconds"] += compute_val
                    if chi is not None and chi < len(cols) and cols[chi]:
                        agg[hk]["chute_ids"].add(cols[chi].strip())

                    if usi is not None and usi < len(cols) and cols[usi]:
                        user_id = cols[usi].strip()
                        if user_id:
                            user_agg[user_id]["invocation_count"] += 1
                            user_agg[user_id]["compute_seconds"]  += compute_val
                            if chi is not None and chi < len(cols) and cols[chi]:
                                user_agg[user_id]["chute_ids"].add(cols[chi].strip())

                if rows > 0:
                    total_rows    += rows
                    hours_fetched += 1

            except Exception:
                continue

    print(f"  Hours with data: {hours_fetched}")
    print(f"  Total rows processed: {total_rows:,}")
    print(f"  Unique miners seen: {len(agg)}")
    print(f"  Unique demand-side users seen: {len(user_agg)}")

    total_user_inv = sum(d["invocation_count"] for d in user_agg.values()) or 1
    total_user_cmp = sum(d["compute_seconds"]  for d in user_agg.values()) or 1
    user_final = {}
    for uid, d in user_agg.items():
        user_final[uid] = {
            "user_id":          uid,
            "invocation_count": d["invocation_count"],
            "compute_seconds":  round(d["compute_seconds"], 2),
            "chute_count":      len(d["chute_ids"]),
            "inv_share":        d["invocation_count"] / total_user_inv,
            "compute_share":    d["compute_seconds"]  / total_user_cmp,
        }

    return agg, user_final


def aggregate_by_miner(agg):
    result = {}
    for hk, data in agg.items():
        result[hk] = {
            "hotkey":           hk,
            "uid":              data["uid"] or "",
            "invocation_count": data["invocation_count"],
            "compute_seconds":  data["compute_seconds"],
            "chute_diversity":  len(data["chute_ids"]),
        }
    print(f"\n  Unique miners in aggregated demand data: {len(result)}")
    return result


# ── Quality Scoring ────────────────────────────────────────────────────────────

def compute_quality_scores(merged):
    active = [m for m in merged if m["invocation_count"] > 0]
    if not active:
        return merged

    max_div = max(m["chute_diversity"] for m in active) or 1

    for m in active:
        m["_inv_per_sec"] = (m["invocation_count"] / m["compute_seconds"]
                             if m["compute_seconds"] > 0 else 0)
        m["seconds_per_invocation"] = (m["compute_seconds"] / m["invocation_count"]
                                       if m["invocation_count"] > 0 else 0)

    active_with_compute = [m for m in active if m["seconds_per_invocation"] > 0]
    if active_with_compute:
        sorted_by_speed = sorted(active_with_compute, key=lambda m: m["seconds_per_invocation"])
        n = len(sorted_by_speed)
        for i, m in enumerate(sorted_by_speed):
            if i < n // 3:       m["efficiency_tier"] = "FAST"
            elif i < 2 * n // 3: m["efficiency_tier"] = "MID"
            else:                 m["efficiency_tier"] = "SLOW"
    for m in active:
        if "efficiency_tier" not in m:
            m["efficiency_tier"] = "N/A"

    max_ips  = max(m["_inv_per_sec"] for m in active) or 1
    total_qs = 0.0

    for m in active:
        div_norm = m["chute_diversity"] / max_div
        eff_norm = m["_inv_per_sec"] / max_ips
        qs = (QUALITY_WEIGHT_COMPUTE    * m["compute_share"] +
              QUALITY_WEIGHT_INVOCATION * m["invocation_share"] +
              QUALITY_WEIGHT_DIVERSITY  * div_norm +
              QUALITY_WEIGHT_EFFICIENCY * eff_norm)
        m["_raw_quality_score"] = qs
        total_qs += qs

    for m in active:
        m["quality_score"]        = m["_raw_quality_score"] / total_qs if total_qs > 0 else 0
        m["delta_quality_weight"] = m["weight_share"] - m["quality_score"]
        m["delta_quality_emit"]   = m["emission_share"] - m["quality_score"]

    active_keys = {m["hotkey"] for m in active}
    for m in merged:
        if m["hotkey"] not in active_keys:
            m["quality_score"]          = 0.0
            m["delta_quality_weight"]   = 0.0
            m["delta_quality_emit"]     = 0.0
            m["efficiency_tier"]        = "N/A"
            m["seconds_per_invocation"] = 0.0
        m.pop("_raw_quality_score", None)
        m.pop("_inv_per_sec", None)

    return merged


# ── Previous Snapshot ──────────────────────────────────────────────────────────

def load_previous_snapshot(output_dir, current_date):
    csvs = sorted([
        f for f in output_dir.glob("chutes_sn64_analysis_*.csv")
        if current_date not in f.name
    ], reverse=True)
    if not csvs:
        print("  No previous snapshot found — trend delta will be N/A")
        return {}
    prev_file = csvs[0]
    print(f"  Previous snapshot: {prev_file.name}")
    prev = {}
    try:
        with open(prev_file, newline="") as f:
            for row in csv.DictReader(f):
                hk = row.get("hotkey", "").strip()
                if hk:
                    prev[hk] = {
                        "emission_share":   float(row.get("emission_share", 0) or 0),
                        "invocation_share": float(row.get("invocation_share", 0) or 0),
                    }
    except Exception as e:
        print(f"  Could not load previous snapshot: {e}")
        return {}
    print(f"  Loaded {len(prev)} miners from previous snapshot")
    return prev


# ── Core Analysis ──────────────────────────────────────────────────────────────

def run_analysis(root_tao_threshold, sn64_alpha_threshold):

    print(f"\n{SEPARATOR}")
    print(f"  SN64 CHUTES — INTELLIGENCE MARKET HYPOTHESIS TEST")
    print(f"  Intelligence Sovereignty Research Suite | @im_perseverance")
    print(SEPARATOR)

    print("\nConnecting to Bittensor network...")
    sub   = bt.Subtensor(network="finney")
    block = sub.get_current_block()
    now   = datetime.now(timezone.utc)
    ts    = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    date  = now.strftime("%Y-%m-%d")

    print(f"  Snapshot block : {block:,}")
    print(f"  Snapshot time  : {ts}")
    print(f"  Analysis window: {INVOCATION_WINDOW} days")

    print(f"\nLoading SN{NETUID} metagraph...")
    meta = sub.metagraph(NETUID)

    n_uids          = len(meta.uids)
    total_stake     = sum(float(s) for s in meta.stake)
    vali_uids       = [i for i, vp in enumerate(meta.validator_permit) if vp]
    total_incentive = sum(float(meta.incentive[i]) for i in range(n_uids))
    total_dividends = sum(float(meta.dividends[i]) for i in range(n_uids))

    onchain = {}
    for uid in range(n_uids):
        hk        = meta.hotkeys[uid]
        stake     = float(meta.stake[uid])
        tao_equiv = float(meta.tao_stake[uid]) if hasattr(meta, 'tao_stake') else stake
        incentive = float(meta.incentive[uid])
        dividends = float(meta.dividends[uid])
        tv        = float(meta.validator_trust[uid]) if hasattr(meta, 'validator_trust') else 0.0
        is_val    = uid in vali_uids
        reg_block = int(meta.block_at_registration[uid]) if hasattr(meta, 'block_at_registration') else 0
        last_upd  = int(meta.last_update[uid]) if hasattr(meta, 'last_update') else 0

        onchain[hk] = {
            "uid":                 uid,
            "hotkey":              hk,
            "stake":               stake,
            "tao_equiv":           tao_equiv,
            "stake_pct":           stake / total_stake if total_stake > 0 else 0,
            "incentive_pct":       incentive / total_incentive if total_incentive > 0 else 0,
            "dividends_pct":       dividends / total_dividends if total_dividends > 0 else 0,
            "tv":                  tv,
            "is_validator":        is_val,
            "age_blocks":          block - reg_block if reg_block > 0 else 0,
            "blocks_since_update": block - last_upd  if last_upd  > 0 else 0,
        }

    print(f"  SN64 UIDs: {n_uids} | Validators: {len(vali_uids)}")

    print(f"\nFetching SN{NETUID} alpha pool state...")
    pool = fetch_alpha_pool_state(sub, NETUID)

    print(f"\nFetching validator weight submissions for SN{NETUID}...")
    weight_totals = defaultdict(float)
    weight_counts = 0
    for val_uid in vali_uids:
        try:
            weights = sub.get_neuron_for_pubkey_and_subnet(meta.hotkeys[val_uid], NETUID)
            if weights and hasattr(weights, 'weights') and weights.weights:
                weight_counts += 1
                for miner_uid, w in weights.weights:
                    weight_totals[miner_uid] += float(w)
        except Exception:
            continue
    total_weight = sum(weight_totals.values())
    print(f"  Validators with weight data: {weight_counts}/{len(vali_uids)}")

    print("\nLoading Root metagraph (netuid=0)...")
    root_meta   = sub.metagraph(0)
    root_stakes = {}
    for uid in range(len(root_meta.hotkeys)):
        stake = float(root_meta.stake[uid])
        if stake >= root_tao_threshold:
            root_stakes[root_meta.hotkeys[uid]] = stake
    print(f"  Root validators with {root_tao_threshold:,.0f}+ TAO: {len(root_stakes)}")

    sn64_validators = []
    for hk, data in onchain.items():
        if data["is_validator"] and data["tao_equiv"] >= sn64_alpha_threshold:
            sn64_validators.append({
                "hotkey":        hk,
                "hotkey_short":  hk[:8] + "...",
                "uid":           data["uid"],
                "sn64_alpha":    data["stake"],
                "tao_equiv":     data["tao_equiv"],
                "tv":            data["tv"],
                "dividends_pct": data["dividends_pct"],
            })
    sn64_validators.sort(key=lambda x: x["sn64_alpha"], reverse=True)
    print(f"\n  SN64 validators with {sn64_alpha_threshold:,.0f}+ alpha: {len(sn64_validators)}")

    overlap = []
    for hk, root_tao in root_stakes.items():
        if hk in onchain and onchain[hk]["is_validator"] and onchain[hk]["tao_equiv"] >= sn64_alpha_threshold:
            overlap.append({
                "hotkey":        hk,
                "hotkey_short":  hk[:8] + "...",
                "uid":           onchain[hk]["uid"],
                "root_tao":      root_tao,
                "sn64_alpha":    onchain[hk]["stake"],
                "tv":            onchain[hk]["tv"],
                "dividends_pct": onchain[hk]["dividends_pct"],
            })
    overlap.sort(key=lambda x: x["root_tao"], reverse=True)
    print(f"  Root x SN64 overlap: {len(overlap)} validators")

    raw_agg, user_agg = fetch_invocation_exports(INVOCATION_WINDOW)
    miner_demand      = aggregate_by_miner(raw_agg)

    total_invocations = sum(m["invocation_count"] for m in miner_demand.values())
    total_compute     = sum(m["compute_seconds"]  for m in miner_demand.values())

    print("\nLoading previous snapshot for trend analysis...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prev_snapshot = load_previous_snapshot(OUTPUT_DIR, date)

    print(f"\nMerging datasets on hotkey join key...")
    merged  = []
    matched = 0

    for hk in set(onchain.keys()) | set(miner_demand.keys()):
        chain_data  = onchain.get(hk)
        demand_data = miner_demand.get(hk)
        if chain_data is None:
            continue

        inv_count     = demand_data["invocation_count"] if demand_data else 0
        compute_sec   = demand_data["compute_seconds"]  if demand_data else 0.0
        chute_div     = demand_data["chute_diversity"]  if demand_data else 0
        inv_share     = inv_count / total_invocations   if total_invocations > 0 else 0
        compute_share = compute_sec / total_compute     if total_compute > 0 else 0
        uid           = chain_data["uid"]
        weight_share  = weight_totals.get(uid, 0) / total_weight if total_weight > 0 else 0
        emission_share = chain_data["incentive_pct"]

        if demand_data:
            matched += 1

        prev       = prev_snapshot.get(hk, {})
        emit_trend = emission_share - prev.get("emission_share", emission_share) if prev else None
        inv_trend  = inv_share - prev.get("invocation_share", inv_share) if prev else None

        merged.append({
            "hotkey":               hk,
            "hotkey_short":         hk[:8] + "...",
            "uid":                  uid,
            "is_validator":         chain_data["is_validator"],
            "tv":                   chain_data["tv"],
            "age_blocks":           chain_data["age_blocks"],
            "blocks_since_update":  chain_data["blocks_since_update"],
            "invocation_count":     inv_count,
            "compute_seconds":      round(compute_sec, 2),
            "chute_diversity":      chute_div,
            "seconds_per_invocation": 0.0,
            "efficiency_tier":      "N/A",
            "invocation_share":     inv_share,
            "compute_share":        compute_share,
            "weight_share":         weight_share,
            "emission_share":       emission_share,
            "dividends_share":      chain_data["dividends_pct"],
            "stake_pct":            chain_data["stake_pct"],
            "emission_trend":       round(emit_trend, 6) if emit_trend is not None else None,
            "invocation_trend":     round(inv_trend, 6)  if inv_trend  is not None else None,
            "delta_inv_emit":       emission_share - inv_share,
            "delta_inv_weight":     weight_share - inv_share,
            "quality_score":        0.0,
            "delta_quality_weight": 0.0,
            "delta_quality_emit":   0.0,
        })

    print(f"  Matched (hotkey in both datasets): {matched}")
    print(f"  On-chain only (no invocations): {len(merged) - matched}")

    merged = compute_quality_scores(merged)

    miners_only = [m for m in merged if not m["is_validator"] and m["invocation_count"] > 0]

    def pearson(x, y):
        n = len(x)
        if n < 2: return None
        mx, my = sum(x)/n, sum(y)/n
        num = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
        dx  = sum((xi-mx)**2 for xi in x) ** 0.5
        dy  = sum((yi-my)**2 for yi in y) ** 0.5
        return num / (dx * dy) if dx * dy > 0 else None

    def spearman(x, y):
        n = len(x)
        if n < 2: return None
        rank_x = sorted(range(n), key=lambda i: x[i])
        rank_y = sorted(range(n), key=lambda i: y[i])
        rx, ry = [0]*n, [0]*n
        for rank, idx in enumerate(rank_x): rx[idx] = rank
        for rank, idx in enumerate(rank_y): ry[idx] = rank
        d_sq = sum((rx[i]-ry[i])**2 for i in range(n))
        return 1 - (6*d_sq) / (n*(n**2-1))

    r_inv_emit = r_inv_wt = rs_inv_emit = rs_inv_wt = None
    r_qs_wt = r_qs_emi = rs_qs_wt = rs_qs_emi = None

    if len(miners_only) >= 2:
        inv_shares  = [m["invocation_share"] for m in miners_only]
        emit_shares = [m["emission_share"]    for m in miners_only]
        wt_shares   = [m["weight_share"]      for m in miners_only]
        r_inv_emit  = pearson(inv_shares, emit_shares)
        r_inv_wt    = pearson(inv_shares, wt_shares)
        rs_inv_emit = spearman(inv_shares, emit_shares)
        rs_inv_wt   = spearman(inv_shares, wt_shares)

    quality_miners = [m for m in merged if m["invocation_count"] > 0]
    if len(quality_miners) >= 2:
        qs_shares  = [m["quality_score"]  for m in quality_miners]
        wt_shares  = [m["weight_share"]   for m in quality_miners]
        emi_shares = [m["emission_share"] for m in quality_miners]
        r_qs_wt    = pearson(qs_shares, wt_shares)
        r_qs_emi   = pearson(qs_shares, emi_shares)
        rs_qs_wt   = spearman(qs_shares, wt_shares)
        rs_qs_emi  = spearman(qs_shares, emi_shares)

    # ── Print Report ───────────────────────────────────────────────────────────

    print(f"\n\n{SEPARATOR}")
    print(f"  SNAPSHOT METADATA")
    print(THIN_SEP)
    print(f"  Block          : {block:,}")
    print(f"  Timestamp      : {ts}")
    print(f"  Analysis window: {INVOCATION_WINDOW} days")
    print(f"  Data sources   : api.chutes.ai (invocations) + Bittensor finney (metagraph)")

    print(f"\n{SEPARATOR}")
    print(f"  ALPHA TOKEN POOL — SN64 (aCHUTES)")
    print(THIN_SEP)
    if pool["alpha_price"] is None:
        print(f"  Pool state unavailable.")
    else:
        print(f"  Spot price (alpha -> TAO)  : {pool['alpha_price']:.4f} TAO")
        if pool["ema_price"]:
            print(f"  EMA price (protocol)      : {pool['ema_price']:.4f} TAO")
        if pool["spot_ema_gap_pct"] is not None:
            gap_pct = pool["spot_ema_gap_pct"]
            sign    = "+" if gap_pct >= 0 else ""
            label   = "spot ABOVE ema" if gap_pct >= 0 else "spot BELOW ema"
            print(f"  Spot vs EMA gap           : {sign}{gap_pct:.1f}%  [{label}]")
        if pool["tao_reserves"]:
            print(f"  TAO reserves (pool depth) : {fmt_large(pool['tao_reserves'])} TAO")
        if pool["market_cap_tao"]:
            print(f"  Implied market cap        : {fmt_large(pool['market_cap_tao'])} TAO")

    print(f"\n{SEPARATOR}")
    print(f"  DEMAND SIGNAL — TOP 20 MINERS BY INVOCATION SHARE (7d)")
    print(THIN_SEP)
    print(f"  {'Rank':>4}  {'UID':>4}  {'Hotkey':10}  {'Invocations':>12}  {'Inv Share':>10}  {'Compute Share':>13}  {'Chute Div':>9}  {'Emission Share':>14}")
    print(THIN_SEP)
    for rank, m in enumerate(sorted(merged, key=lambda x: x["invocation_share"], reverse=True)[:20], 1):
        print(f"  {rank:>4}  {m['uid']:>4}  {m['hotkey_short']:10}  "
              f"{m['invocation_count']:>12,}  {fmt_pct(m['invocation_share']):>10}  "
              f"{fmt_pct(m['compute_share']):>13}  {m['chute_diversity']:>9}  "
              f"{fmt_pct(m['emission_share']):>14}")

    print(f"\n{SEPARATOR}")
    print(f"  RECOGNITION — TOP 20 MINERS BY EMISSION SHARE")
    print(THIN_SEP)
    print(f"  {'Rank':>4}  {'UID':>4}  {'Hotkey':10}  {'Emission Share':>14}  {'Inv Share':>10}  {'Weight Share':>12}  {'Delta (E-D)':>12}")
    print(THIN_SEP)
    for rank, m in enumerate(sorted(merged, key=lambda x: x["emission_share"], reverse=True)[:20], 1):
        print(f"  {rank:>4}  {m['uid']:>4}  {m['hotkey_short']:10}  "
              f"{fmt_pct(m['emission_share']):>14}  {fmt_pct(m['invocation_share']):>10}  "
              f"{fmt_pct(m['weight_share']):>12}  "
              f"{fmt_delta(m['invocation_share'], m['emission_share']):>12}")

    print(f"\n{SEPARATOR}")
    print(f"  DIVERGENCE TABLE — TOP 20 LARGEST DEMAND vs EMISSION GAP")
    print(THIN_SEP)
    print(f"  {'Rank':>4}  {'UID':>4}  {'Hotkey':10}  {'Inv Share':>10}  {'Emission Share':>14}  {'Delta (pp)':>12}  {'Weight Share':>12}")
    print(THIN_SEP)
    for rank, m in enumerate(sorted([m for m in merged if m["invocation_count"] > 0],
                                     key=lambda x: abs(x["delta_inv_emit"]), reverse=True)[:20], 1):
        print(f"  {rank:>4}  {m['uid']:>4}  {m['hotkey_short']:10}  "
              f"{fmt_pct(m['invocation_share']):>10}  {fmt_pct(m['emission_share']):>14}  "
              f"{fmt_delta(m['invocation_share'], m['emission_share']):>12}  "
              f"{fmt_pct(m['weight_share']):>12}")

    print(f"\n{SEPARATOR}")
    print(f"  CORRELATION ANALYSIS — DEMAND vs RECOGNITION")
    print(THIN_SEP)
    print(f"  Miners with invocation data : {len(miners_only)}")
    if r_inv_emit is not None:
        strength_p  = "STRONG" if abs(r_inv_emit)  > 0.7 else "MODERATE" if abs(r_inv_emit)  > 0.4 else "WEAK"
        strength_rs = "STRONG" if abs(rs_inv_emit) > 0.7 else "MODERATE" if abs(rs_inv_emit) > 0.4 else "WEAK"
        print(f"  Pearson  r  (inv -> emission) : {r_inv_emit:+.4f}  [{strength_p}]")
        print(f"  Spearman rho (inv -> emission) : {rs_inv_emit:+.4f}  [{strength_rs}]")
        if r_inv_wt:  print(f"  Pearson  r  (inv -> weight)   : {r_inv_wt:+.4f}")
        if rs_inv_wt: print(f"  Spearman rho (inv -> weight)   : {rs_inv_wt:+.4f}")
        lead = rs_inv_emit if rs_inv_emit is not None else r_inv_emit
        print()
        if lead > 0.7:
            print(f"  -> STRONG correlation. Validators reward miners proportional to real demand.")
            print(f"     Evidence supports: Bittensor functions as a market for intelligence on SN64.")
        elif lead > 0.4:
            print(f"  -> MODERATE correlation. General alignment exists but divergences are material.")
        else:
            print(f"  -> WEAK correlation. Emission distribution diverges from invocation demand.")
        if r_inv_emit and rs_inv_emit and abs(r_inv_emit - rs_inv_emit) > 0.15:
            print(f"\n  Pearson/Spearman gap suggests power-law distribution.")
            print(f"  Spearman is the more reliable measure for this dataset.")

    print(f"\n{SEPARATOR}")
    print(f"  QUALITY CROSSCHECK — INDEPENDENT SCORE vs VALIDATOR WEIGHTS")
    print(f"  Our score = {QUALITY_WEIGHT_COMPUTE*100:.0f}% compute + {QUALITY_WEIGHT_INVOCATION*100:.0f}% invocations + {QUALITY_WEIGHT_DIVERSITY*100:.0f}% diversity + {QUALITY_WEIGHT_EFFICIENCY*100:.0f}% efficiency")
    print(THIN_SEP)
    if r_qs_wt is not None:
        print(f"  Pearson  r  (quality -> weight)   : {r_qs_wt:+.4f}")
        print(f"  Spearman rho (quality -> weight)   : {rs_qs_wt:+.4f}")
        print(f"  Pearson  r  (quality -> emission) : {r_qs_emi:+.4f}")
        print(f"  Spearman rho (quality -> emission) : {rs_qs_emi:+.4f}")

    print(f"\n{SEPARATOR}")
    print(f"  DEMAND-SIDE USER ANALYSIS")
    print(THIN_SEP)
    if user_agg:
        users_sorted = sorted(user_agg.values(), key=lambda u: u["invocation_count"], reverse=True)
        top1_share   = users_sorted[0]["inv_share"] if users_sorted else 0
        top3_share   = sum(u["inv_share"] for u in users_sorted[:3])
        top5_share   = sum(u["inv_share"] for u in users_sorted[:5])
        print(f"  Total unique users (7d)      : {len(users_sorted):,}")
        print(f"  Top-1  user invocation share : {top1_share*100:.2f}%")
        print(f"  Top-3  user invocation share : {top3_share*100:.2f}%")
        print(f"  Top-5  user invocation share : {top5_share*100:.2f}%")
        print(f"\n  {'Rank':>4}  {'User ID (truncated)':35}  {'Invocations':>12}  {'Inv Share':>10}  {'Unique Chutes':>14}")
        print(THIN_SEP)
        for rank, u in enumerate(users_sorted[:20], 1):
            uid_display = u["user_id"][:33] + ".." if len(u["user_id"]) > 35 else u["user_id"]
            print(f"  {rank:>4}  {uid_display:35}  {u['invocation_count']:>12,}  "
                  f"{fmt_pct(u['inv_share']):>10}  {u['chute_count']:>14,}")

    print(f"\n{SEPARATOR}")
    print(f"  SN64 VALIDATORS (TAO-equivalent stake >= {sn64_alpha_threshold:,.0f})")
    print(THIN_SEP)
    print(f"  {'Rank':>4}  {'Hotkey':10}  {'UID':>4}  {'Alpha Stake':>12}  {'TAO Equiv':>10}  {'Tv':>6}  {'Div Share':>10}")
    print(THIN_SEP)
    for rank, v in enumerate(sn64_validators, 1):
        print(f"  {rank:>4}  {v['hotkey_short']:10}  {v['uid']:>4}  "
              f"{v['sn64_alpha']:>12,.0f}  {v['tao_equiv']:>10,.0f}  "
              f"{v['tv']:>6.4f}  {fmt_pct(v['dividends_pct']):>10}")

    print(f"\n{SEPARATOR}")
    print(f"  ROOT x SN64 VALIDATOR OVERLAP")
    print(f"  Root stake >= {root_tao_threshold:,.0f} TAO  AND  SN64 stake >= {sn64_alpha_threshold:,.0f} alpha")
    print(THIN_SEP)
    for rank, v in enumerate(overlap, 1):
        print(f"  {rank:>4}  {v['hotkey_short']:10}  UID {v['uid']:>4}  "
              f"Root: {v['root_tao']:>10,.0f} TAO  |  SN64: {v['sn64_alpha']:>10,.0f} alpha  |  "
              f"Tv: {v['tv']:.4f}  |  Div: {fmt_pct(v['dividends_pct'])}")

    print(f"\n{SEPARATOR}")
    print(f"  MINER INCENTIVE CONCENTRATION")
    print(THIN_SEP)
    miners_sorted = sorted([m for m in merged if not m["is_validator"]],
                           key=lambda x: x["emission_share"], reverse=True)
    top1  = miners_sorted[0]["emission_share"] if miners_sorted else 0
    top3  = sum(m["emission_share"] for m in miners_sorted[:3])
    top5  = sum(m["emission_share"] for m in miners_sorted[:5])
    top10 = sum(m["emission_share"] for m in miners_sorted[:10])

    def gini(values):
        vals = sorted([v for v in values if v > 0])
        n    = len(vals)
        if n == 0: return None
        total = sum(vals)
        if total == 0: return None
        return (2 * sum((i+1)*v for i, v in enumerate(vals))) / (n*total) - (n+1)/n

    gini_active = gini([m["emission_share"] for m in miners_sorted if m["emission_share"] > 0])
    print(f"  Top-1  miner emission share : {top1*100:.2f}%")
    print(f"  Top-3  miner emission share : {top3*100:.2f}%")
    print(f"  Top-5  miner emission share : {top5*100:.2f}%")
    print(f"  Top-10 miner emission share : {top10*100:.2f}%")
    if gini_active: print(f"  Gini (active miners only)   : {gini_active:.4f}")

    print(f"\n{SEPARATOR}\n")

    # ── Save Outputs ───────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    merged_file = OUTPUT_DIR / f"chutes_sn64_analysis_{date}.csv"
    fieldnames  = [
        "hotkey", "uid", "is_validator", "tv", "age_blocks", "blocks_since_update",
        "invocation_count", "compute_seconds", "chute_diversity",
        "seconds_per_invocation", "efficiency_tier",
        "invocation_share", "compute_share", "weight_share",
        "emission_share", "dividends_share", "stake_pct",
        "emission_trend", "invocation_trend",
        "quality_score", "delta_inv_emit", "delta_inv_weight",
        "delta_quality_weight", "delta_quality_emit"
    ]
    with open(merged_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    div_file = OUTPUT_DIR / f"chutes_sn64_divergence_{date}.csv"
    with open(div_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["hotkey","uid","invocation_share","emission_share","delta_inv_emit","weight_share","chute_diversity"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(merged, key=lambda x: abs(x["delta_inv_emit"]), reverse=True))

    meta_file = OUTPUT_DIR / f"chutes_sn64_metadata_{date}.json"
    with open(meta_file, "w") as f:
        json.dump({
            "block": block, "timestamp": ts, "netuid": NETUID,
            "window_days": INVOCATION_WINDOW,
            "total_invocations": total_invocations,
            "total_compute_sec": total_compute,
            "n_miners_demand": len(miner_demand),
            "n_matched": matched,
            "n_overlap": len(overlap),
            "n_sn64_validators": len(sn64_validators),
            "n_demand_users": len(user_agg),
            "r_inv_emit": r_inv_emit, "r_inv_weight": r_inv_wt,
            "rs_inv_emit": rs_inv_emit, "rs_inv_weight": rs_inv_wt,
            "r_quality_weight": r_qs_wt, "r_quality_emit": r_qs_emi,
            "rs_quality_weight": rs_qs_wt, "rs_quality_emit": rs_qs_emi,
            "gini_active_miners": gini_active,
            "alpha_price": pool["alpha_price"], "ema_price": pool["ema_price"],
            "tao_reserves": pool["tao_reserves"], "market_cap_tao": pool["market_cap_tao"],
        }, f, indent=2)

    user_file = OUTPUT_DIR / f"chutes_sn64_demand_users_{date}.csv"
    with open(user_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id","invocation_count","compute_seconds","chute_count","inv_share","compute_share"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(user_agg.values(), key=lambda u: u["invocation_count"], reverse=True))

    print(f"Outputs saved to {OUTPUT_DIR}/")
    print(f"  {merged_file.name}")
    print(f"  {div_file.name}")
    print(f"  {meta_file.name}")
    print(f"  {user_file.name}")


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SN64 Chutes Intelligence Market Analysis")
    parser.add_argument("--root-threshold",  type=float, default=ROOT_TAO_THRESHOLD)
    parser.add_argument("--alpha-threshold", type=float, default=SN64_ALPHA_THRESHOLD)
    args = parser.parse_args()
    run_analysis(args.root_threshold, args.alpha_threshold)

if __name__ == "__main__":
    main()
