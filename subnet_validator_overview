import bittensor as bt

POOL_CUTOFF = 1000
BLOCKS_PER_DAY = 7200

my_stake = float(input("Enter your stake amount in TAO: "))
netuid = int(input("Enter subnet ID: "))

print("\nConnecting to Bittensor network...")
sub = bt.Subtensor()

all_s = sub.all_subnets()
subnet = next((s for s in all_s if s.netuid == netuid), None)
if not subnet:
    print(f"SN{netuid} not found")
    exit()

tao_per_block = float(subnet.tao_in_emission)
current_price = float(subnet.price)
moving_price = float(subnet.moving_price)
name = subnet.subnet_name or f"SN{netuid}"

price_momentum = (current_price - moving_price) / moving_price if moving_price > 0 else 0
price_apy = price_momentum * (365 / 30) * 100

print(f"\nSN{netuid} ({name})")
print(f"TAO/block: {tao_per_block:.6f} | Alpha price: {current_price:.6f} | Moving price: {moving_price:.6f}")
print(f"Price momentum (vs EMA): {price_momentum:+.2%} | Annualized price APY est: {price_apy:+.2f}%\n")

print(f"Loading SN{netuid} metagraph...")
metagraph = sub.metagraph(netuid=netuid)

print("Loading root metagraph for take rates...")
root_meta = sub.metagraph(netuid=0)

take_rates = {}
for uid in range(len(root_meta.hotkeys)):
    hotkey = root_meta.hotkeys[uid]
    try:
        delegate_info = sub.get_delegate_by_hotkey(hotkey)
        take_rates[hotkey] = delegate_info.take if delegate_info else 0.18
    except:
        take_rates[hotkey] = 0.18

def get_concentration(sub, hotkey, netuid, price):
    try:
        delegate = sub.get_delegate_by_hotkey(hotkey)
        if not delegate or not delegate.nominators:
            return None, None, "NO DATA"
        stakes = []
        for coldkey, subnet_stakes in delegate.nominators.items():
            alpha = float(subnet_stakes.get(netuid, 0))
            tao_equiv = alpha * price
            if tao_equiv > 0:
                stakes.append(tao_equiv)
        if not stakes:
            return None, None, "NO DATA"
        stakes.sort(reverse=True)
        total = sum(stakes)
        top1_pct = stakes[0] / total
        top3_pct = sum(stakes[:3]) / total
        if top1_pct > 0.50:
            flag = "🔴 HIGH"
        elif top1_pct > 0.25:
            flag = "🟡 MODERATE"
        else:
            flag = "🟢 DISTRIBUTED"
        return top1_pct, top3_pct, flag
    except:
        return None, None, "NO DATA"

# Filter: dividend > 0 AND emission > 0 AND Tv > 0.5
div_uids = [
    (uid, float(metagraph.D[uid]), float(metagraph.E[uid]), float(metagraph.S[uid]), float(metagraph.Tv[uid]), metagraph.hotkeys[uid])
    for uid in range(len(metagraph.hotkeys))
    if float(metagraph.D[uid]) > 0
    and float(metagraph.E[uid]) > 0
    and float(metagraph.Tv[uid]) > 0.5
]

if not div_uids:
    print(f"SN{netuid} has no active dividend-earning validators.")
    print(f"Price APY estimate: {price_apy:+.2f}%")
    exit()

total_E_validators = sum(e for _, _, e, _, _, _ in div_uids)

print(f"{'UID':<6} {'Stake':<16} {'Dividend':<10} {'E Share':<10} {'Take':<7} {'Tv':<8} {'Emission APY':<14} {'Price APY':<12} {'Combined APY':<14} {'Top1':<8} {'Top3':<8} {'Concentration':<16} {'Hotkey'}")
print("-" * 210)

results = []
for uid, div, e_val, stake, tv, hotkey in div_uids:
    take = take_rates.get(hotkey, 0.18)
    e_share = e_val / total_E_validators
    validator_tao_per_block = tao_per_block * e_share
    pool_total = stake + my_stake
    your_share = my_stake / pool_total
    your_tao_per_day = validator_tao_per_block * (1 - take) * your_share * BLOCKS_PER_DAY
    emission_apy = (your_tao_per_day * 365 / my_stake) * 100 if my_stake > 0 else 0
    combined_apy = emission_apy + price_apy
    status = "Large Pool" if stake >= POOL_CUTOFF else "Small Pool"

    top1_pct, top3_pct, conc_flag = get_concentration(sub, hotkey, netuid, current_price)
    top1_str = f"{top1_pct:.1%}" if top1_pct is not None else "N/A"
    top3_str = f"{top3_pct:.1%}" if top3_pct is not None else "N/A"

    results.append((uid, stake, div, e_share, take, tv, your_tao_per_day, emission_apy, price_apy, combined_apy, top1_str, top3_str, conc_flag, status, hotkey))

results.sort(key=lambda x: x[9], reverse=True)
for uid, stake, div, e_share, take, tv, tao_day, emission_apy, p_apy, combined_apy, top1_str, top3_str, conc_flag, status, hotkey in results:
    print(f"{uid:<6} {stake:<16,.0f} {div:<10.6f} {e_share:<10.4f} {take:<7.1%} {tv:<8.4f} {emission_apy:<14.4f} {p_apy:<+12.4f} {combined_apy:<14.4f} {top1_str:<8} {top3_str:<8} {conc_flag:<16} {hotkey}")

print(f"\nTop validator (combined): {results[0][9]:.4f}% APY (emission: {results[0][7]:.4f}% + price: {results[0][8]:+.4f}%)")
print(f"Concentration: {results[0][12]}")
print(f"\nNote: Price APY is estimated from current vs 30d EMA momentum. Past momentum does not guarantee future appreciation.")
