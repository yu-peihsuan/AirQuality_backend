"""
火災事件研究
分析1：火災日 vs 無火災日 AQI 比較
分析2：時序事件研究（報案前3小時 → 後6小時 PM2.5 變化曲線）
執行：python fire_event_study.py
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import glob
import os
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.family'] = 'Microsoft JhengHei'
plt.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, 'data')

# ── 載入火災資料 ───────────────────────────────────────────────────────────────
print('▶ 載入火災資料...')

def parse_roc_dt(s):
    try:
        date_part, time_part = str(s).strip().split(' ')
        y, m, d = date_part.split('-')
        return pd.Timestamp(f'{int(y)+1911}-{m}-{d} {time_part[:5]}')
    except Exception:
        return pd.NaT

def read_fire(path):
    with open(path, encoding='utf-8-sig') as fh:
        sep = '\t' if '\t' in fh.readline() else ','
    return pd.read_csv(path, encoding='utf-8-sig', sep=sep, on_bad_lines='skip')

fire_files = [os.path.join(DATA, f'{y}年火災事件資料.csv') for y in range(109, 114)]
fire = pd.concat([read_fire(f) for f in fire_files if os.path.exists(f)], ignore_index=True)

fire['report_dt'] = fire['報案時間'].apply(parse_roc_dt)
fire = fire.dropna(subset=['report_dt'])
fire['county']      = fire['縣市'].str.replace('臺', '台', regex=False)
fire['date']        = fire['report_dt'].dt.date
fire['report_hour'] = fire['report_dt'].dt.floor('h')
print(f'  有效事件：{len(fire):,} 筆')

# ── 載入 AQI 資料（小時級）────────────────────────────────────────────────────
print('▶ 載入 AQI 資料...')
aqi_files = sorted(glob.glob(os.path.join(DATA, '空氣品質指標(AQI)(歷史資料)*.csv')))
aqi_files = [f for f in aqi_files if any(f'({y}-' in f for y in range(2020, 2025))]
aqi = pd.concat(
    [pd.read_csv(f, encoding='utf-8-sig') for f in aqi_files],
    ignore_index=True
)
aqi['datacreationdate'] = pd.to_datetime(aqi['datacreationdate'], errors='coerce')
aqi['aqi']   = pd.to_numeric(aqi['aqi'],   errors='coerce')
aqi['pm25']  = pd.to_numeric(aqi['pm2.5'], errors='coerce')
aqi = aqi[(aqi['aqi'] > 0) & (aqi['aqi'] <= 500)]
aqi['county']    = aqi['county'].str.replace('臺', '台', regex=False)
aqi['date']      = aqi['datacreationdate'].dt.date
aqi['hour_floor'] = aqi['datacreationdate'].dt.floor('h')
print(f'  AQI 共 {len(aqi):,} 筆\n')

# ══════════════════════════════════════════════════════════════════════════════
# 分析1：火災日 vs 無火災日 AQI 比較
# ══════════════════════════════════════════════════════════════════════════════
print('▶ 分析1：火災日 vs 無火災日...')

# 縣市每日 AQI 均值
aqi_daily = aqi.groupby(['county', 'date'])['aqi'].mean().reset_index(name='avg_aqi')

# 標記是否有火災（任何類型）
fire_days_all = set(zip(fire['county'], fire['date']))
# 標記只有「燃燒雜草垃圾」的火災日
fire_grass = fire[fire['起火原因'].str.contains('雜草|垃圾', na=False)]
fire_days_grass = set(zip(fire_grass['county'], fire_grass['date']))

aqi_daily['fire_any']   = aqi_daily.apply(lambda r: (r['county'], r['date']) in fire_days_all,  axis=1)
aqi_daily['fire_grass'] = aqi_daily.apply(lambda r: (r['county'], r['date']) in fire_days_grass, axis=1)

# 統計
groups = {
    '無火災日':         aqi_daily[~aqi_daily['fire_any']]['avg_aqi'],
    '有火災日\n（任意原因）': aqi_daily[aqi_daily['fire_any']]['avg_aqi'],
    '有火災日\n（燃燒雜草垃圾）': aqi_daily[aqi_daily['fire_grass']]['avg_aqi'],
}
means  = {k: v.mean()   for k, v in groups.items()}
stds   = {k: v.std()    for k, v in groups.items()}
counts = {k: len(v)     for k, v in groups.items()}

print('  各類型日期 AQI 均值：')
for k in groups:
    print(f'    {k.replace(chr(10)," ")}：{means[k]:.2f}（n={counts[k]:,}）')

# 圖表
fig, axes = plt.subplots(1, 2, figsize=(14, 6))
colors = ['#5B9BD5', '#FF6B35', '#C00000']
labels = list(groups.keys())

# 左圖：長條圖
bars = axes[0].bar(labels, [means[k] for k in labels],
                   color=colors, edgecolor='white', width=0.5)
for bar, k in zip(bars, labels):
    axes[0].text(bar.get_x() + bar.get_width()/2,
                 bar.get_height() + 0.3,
                 f'{means[k]:.1f}', ha='center', fontsize=12)
axes[0].set_title('不同火災類型日期的平均 AQI 比較', fontsize=14, fontweight='bold')
axes[0].set_ylabel('平均 AQI', fontsize=12)
axes[0].set_ylim(0, max(means.values()) * 1.2)
axes[0].tick_params(axis='x', labelsize=11)
axes[0].grid(axis='y', alpha=0.3)
axes[0].spines['top'].set_visible(False)
axes[0].spines['right'].set_visible(False)

# 右圖：各縣市火災日 vs 無火災日差值
county_compare = aqi_daily.groupby(['county', 'fire_any'])['avg_aqi'].mean().unstack(fill_value=np.nan)
county_compare.columns = ['無火災日', '有火災日']
county_compare['差值'] = county_compare['有火災日'] - county_compare['無火災日']
county_compare = county_compare.dropna().sort_values('差值', ascending=True)

colors_bar = ['#FF6B35' if v > 0 else '#5B9BD5' for v in county_compare['差值']]
axes[1].barh(county_compare.index, county_compare['差值'], color=colors_bar, edgecolor='white')
axes[1].axvline(x=0, color='black', linewidth=0.8)
axes[1].set_title('各縣市「有火災日 − 無火災日」AQI 差值', fontsize=14, fontweight='bold')
axes[1].set_xlabel('AQI 差值（正值＝火災日較高）', fontsize=11)
axes[1].grid(axis='x', alpha=0.3)
axes[1].spines['top'].set_visible(False)
axes[1].spines['right'].set_visible(False)

plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_day_vs_nonfire.png'), dpi=150, bbox_inches='tight')
plt.show()

# ══════════════════════════════════════════════════════════════════════════════
# 分析2：時序事件研究（小時級）
# ══════════════════════════════════════════════════════════════════════════════
print('\n▶ 分析2：時序事件研究（-3h ~ +6h）...')

# 縣市每小時 PM2.5 均值（用於查表）
aqi_hourly = aqi.groupby(['county', 'hour_floor'])['pm25'].mean()

# 只取「燃燒雜草垃圾」火災（最直接影響大氣）
fire_study = fire[fire['起火原因'].str.contains('雜草|垃圾', na=False)].copy()
print(f'  燃燒雜草垃圾事件：{len(fire_study):,} 筆')

# 展開事件窗口（向量化）
OFFSETS = list(range(-3, 7))
fire_rep = fire_study[['county', 'report_hour']].loc[
    fire_study.index.repeat(len(OFFSETS))
].copy()
fire_rep['offset'] = OFFSETS * len(fire_study)
fire_rep['target_hour'] = fire_rep['report_hour'] + pd.to_timedelta(fire_rep['offset'], unit='h')

# 查表
fire_rep['pm25'] = fire_rep.apply(
    lambda r: aqi_hourly.get((r['county'], r['target_hour']), np.nan), axis=1
)
fire_rep = fire_rep.dropna(subset=['pm25'])
print(f'  成功匹配 {len(fire_rep):,} 個縣市-小時配對')

# 每個 offset 的均值與信賴區間
curve = fire_rep.groupby('offset')['pm25'].agg(['mean', 'std', 'count']).reset_index()
curve['se']    = curve['std'] / np.sqrt(curve['count'])
curve['ci95']  = curve['se'] * 1.96

# 以 offset=0 的值做標準化（看相對變化）
baseline = curve.loc[curve['offset'] == -1, 'mean'].values[0]
curve['delta'] = curve['mean'] - baseline

fig, axes = plt.subplots(1, 2, figsize=(14, 5))

# 左圖：絕對 PM2.5 值
axes[0].plot(curve['offset'], curve['mean'], color='#C00000', linewidth=2.5,
             marker='o', markersize=6, label='平均 PM2.5')
axes[0].fill_between(curve['offset'],
                     curve['mean'] - curve['ci95'],
                     curve['mean'] + curve['ci95'],
                     alpha=0.15, color='#C00000', label='95% 信賴區間')
axes[0].axvline(x=0, color='gray', linestyle='--', linewidth=1.5, label='報案時間')
axes[0].axvspan(-3, -0.5, alpha=0.04, color='blue')
axes[0].axvspan(0.5, 6, alpha=0.04, color='red')
axes[0].set_title('燃燒雜草垃圾事件前後 PM2.5 變化', fontsize=14, fontweight='bold')
axes[0].set_xlabel('相對報案時間（小時）', fontsize=12)
axes[0].set_ylabel('PM2.5 (µg/m³)', fontsize=12)
axes[0].set_xticks(OFFSETS)
axes[0].set_xticklabels([f'{h:+d}h' if h != 0 else '報案' for h in OFFSETS])
axes[0].legend(fontsize=10)
axes[0].grid(alpha=0.3)
axes[0].spines['top'].set_visible(False)
axes[0].spines['right'].set_visible(False)

# 右圖：相對基準線的差值
colors_delta = ['#FF6B35' if v > 0 else '#5B9BD5' for v in curve['delta']]
axes[1].bar(curve['offset'], curve['delta'], color=colors_delta, edgecolor='white')
axes[1].axhline(y=0, color='black', linewidth=0.8)
axes[1].axvline(x=0, color='gray', linestyle='--', linewidth=1.5)
axes[1].set_title('相對報案前1小時的 PM2.5 差值', fontsize=14, fontweight='bold')
axes[1].set_xlabel('相對報案時間（小時）', fontsize=12)
axes[1].set_ylabel('PM2.5 差值 (µg/m³)', fontsize=12)
axes[1].set_xticks(OFFSETS)
axes[1].set_xticklabels([f'{h:+d}h' if h != 0 else '報案' for h in OFFSETS])
axes[1].grid(axis='y', alpha=0.3)
axes[1].spines['top'].set_visible(False)
axes[1].spines['right'].set_visible(False)

plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_event_timeseries.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 摘要 ───────────────────────────────────────────────────────────────────────
print('\n' + '=' * 55)
print('📊 事件研究摘要')
print('=' * 55)
print(f'\n【分析1】平均 AQI：')
for k in groups:
    print(f'  {k.replace(chr(10)," ")}：{means[k]:.2f}')
diff_any   = means['有火災日\n（任意原因）']   - means['無火災日']
diff_grass = means['有火災日\n（燃燒雜草垃圾）'] - means['無火災日']
print(f'\n  有火災日比無火災日高：+{diff_any:.2f}（任意原因）')
print(f'  燃燒雜草垃圾日比無火災日高：+{diff_grass:.2f}')

peak_offset = curve.loc[curve['mean'].idxmax(), 'offset']
peak_val    = curve['mean'].max()
base_val    = curve.loc[curve['offset'] == -1, 'mean'].values[0]
print(f'\n【分析2】PM2.5 事件曲線：')
print(f'  基準值（報案前1小時）：{base_val:.1f} µg/m³')
print(f'  峰值出現於 {peak_offset:+d}h：{peak_val:.1f} µg/m³')
print(f'  峰值較基準上升：{peak_val - base_val:+.1f} µg/m³（{(peak_val-base_val)/base_val*100:+.1f}%）')
print('\n✅ 圖表已儲存至 analysis/ 資料夾')
