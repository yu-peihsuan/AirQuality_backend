"""
火災事件 vs 空氣品質對比分析
資料：消防署火災事件資料（109–113年）× 環境部 AQI 歷史資料（2020–2025）
執行：python fire_aqi_analysis.py
"""

import pandas as pd
import matplotlib.pyplot as plt
import glob
import os
import warnings
warnings.filterwarnings('ignore')

plt.rcParams['font.family'] = 'Microsoft JhengHei'
plt.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, 'data')

# ── 1. 載入火災資料（109–113年 = 2020–2024）─────────────────────────────────
print('▶ 載入火災資料...')
fire_files = [os.path.join(DATA, f'{y}年火災事件資料.csv') for y in range(109, 114)]
fire_files = [f for f in fire_files if os.path.exists(f)]

def _read_fire(path):
    with open(path, encoding='utf-8-sig') as fh:
        sep = '\t' if '\t' in fh.readline() else ','
    return pd.read_csv(path, encoding='utf-8-sig', sep=sep, on_bad_lines='skip')

fire_dfs = []
for f in fire_files:
    try:
        fire_dfs.append(_read_fire(f))
    except Exception as e:
        print(f'  讀取失敗：{f} → {e}')

fire = pd.concat(fire_dfs, ignore_index=True)
print(f'  火災事件共 {len(fire):,} 筆')

# 民國年 → 西元年（格式：109-01-01 00:02）
def roc_to_ad(s):
    try:
        parts = str(s).split('-')
        year = int(parts[0]) + 1911
        return pd.Timestamp(f'{year}-{parts[1]}-{parts[2][:2]}')
    except Exception:
        return pd.NaT

fire['date'] = fire['報案時間'].apply(roc_to_ad)
fire = fire.dropna(subset=['date'])
fire['year']  = fire['date'].dt.year
fire['month'] = fire['date'].dt.month
fire['county'] = fire['縣市'].str.replace('臺', '台', regex=False)

print(f'  有效事件：{len(fire):,} 筆，期間：{fire["date"].min().date()} ～ {fire["date"].max().date()}')

# ── 2. 載入 AQI 資料（2020–2025）────────────────────────────────────────────
print('▶ 載入 AQI 資料...')
aqi_files = sorted(glob.glob(os.path.join(DATA, '空氣品質指標(AQI)(歷史資料)*.csv')))
aqi_files = [f for f in aqi_files if any(f'({y}-' in f for y in range(2020, 2025))]

aqi_dfs = []
for f in aqi_files:
    try:
        aqi_dfs.append(pd.read_csv(f, encoding='utf-8-sig'))
    except Exception:
        pass

aqi = pd.concat(aqi_dfs, ignore_index=True)
aqi['datacreationdate'] = pd.to_datetime(aqi['datacreationdate'], errors='coerce')
aqi['aqi']   = pd.to_numeric(aqi['aqi'],   errors='coerce')
aqi['pm2.5'] = pd.to_numeric(aqi['pm2.5'], errors='coerce')
aqi = aqi[(aqi['aqi'] > 0) & (aqi['aqi'] <= 500)]
aqi['year']   = aqi['datacreationdate'].dt.year
aqi['month']  = aqi['datacreationdate'].dt.month
aqi['county'] = aqi['county'].str.replace('臺', '台', regex=False)
print(f'  AQI 共 {len(aqi):,} 筆')

# ── 3. 彙整：縣市 × 年月 ─────────────────────────────────────────────────────
fire_monthly = fire.groupby(['county', 'year', 'month']).size().reset_index(name='fire_count')
aqi_monthly  = aqi.groupby(['county',  'year', 'month'])['aqi'].mean().reset_index(name='avg_aqi')
merged = pd.merge(fire_monthly, aqi_monthly, on=['county', 'year', 'month'], how='inner')
print(f'  合併後：{len(merged):,} 個縣市-月份配對\n')

# ══════════════════════════════════════════════════════════════════════════════
# 圖1：全台每月火災事件數 vs 平均 AQI（雙軸折線）
# ══════════════════════════════════════════════════════════════════════════════
print('▶ 圖1：全台月趨勢...')
national = merged.groupby(['year', 'month']).agg(
    fire_count=('fire_count', 'sum'),
    avg_aqi=('avg_aqi', 'mean')
).reset_index()
national['ym'] = pd.to_datetime(national[['year', 'month']].assign(day=1))
national = national.sort_values('ym')

fig, ax1 = plt.subplots(figsize=(14, 5))
ax2 = ax1.twinx()
ax1.bar(range(len(national)), national['fire_count'], color='#FF6B35', alpha=0.6, label='火災件數')
ax2.plot(range(len(national)), national['avg_aqi'],   color='#1F77B4', linewidth=2, marker='o', markersize=3, label='平均 AQI')

tick_idx   = [i for i, r in national.iterrows() if r['month'] == 1]
tick_labels = [str(int(national.iloc[i]['year'])) for i in tick_idx]
ax1.set_xticks(tick_idx)
ax1.set_xticklabels(tick_labels, fontsize=11)

ax1.set_xlabel('年份', fontsize=12)
ax1.set_ylabel('火災件數', color='#FF6B35', fontsize=12)
ax2.set_ylabel('平均 AQI', color='#1F77B4', fontsize=12)
ax1.tick_params(axis='y', labelcolor='#FF6B35')
ax2.tick_params(axis='y', labelcolor='#1F77B4')

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right', fontsize=11)
ax1.set_title('全台每月火災件數 vs 平均 AQI（2020–2024）', fontsize=15, fontweight='bold', pad=12)
ax1.spines['top'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_aqi_monthly_trend.png'), dpi=150, bbox_inches='tight')
plt.show()

# ══════════════════════════════════════════════════════════════════════════════
# 圖2：季節分佈 — 火災件數 vs AQI
# ══════════════════════════════════════════════════════════════════════════════
print('▶ 圖2：季節比較...')
season_map = {12:'冬季',1:'冬季',2:'冬季',3:'春季',4:'春季',5:'春季',
              6:'夏季',7:'夏季',8:'夏季',9:'秋季',10:'秋季',11:'秋季'}
merged['season'] = merged['month'].map(season_map)
season_order  = ['春季','夏季','秋季','冬季']
season_colors = {'春季':'#70AD47','夏季':'#FF6B35','秋季':'#FFC000','冬季':'#5B9BD5'}

seasonal = merged.groupby('season').agg(
    fire_count=('fire_count', 'sum'),
    avg_aqi=('avg_aqi', 'mean')
).reindex(season_order)

fig, axes = plt.subplots(1, 2, figsize=(13, 5))
colors = [season_colors[s] for s in season_order]

bars = axes[0].bar(season_order, seasonal['fire_count'], color=colors, edgecolor='white')
for bar, v in zip(bars, seasonal['fire_count']):
    axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 200,
                 f'{int(v):,}', ha='center', fontsize=11)
axes[0].set_title('各季節火災總件數', fontsize=14, fontweight='bold')
axes[0].set_ylabel('火災件數', fontsize=12)
axes[0].grid(axis='y', alpha=0.3)
axes[0].spines['top'].set_visible(False)
axes[0].spines['right'].set_visible(False)

bars2 = axes[1].bar(season_order, seasonal['avg_aqi'], color=colors, edgecolor='white')
for bar, v in zip(bars2, seasonal['avg_aqi']):
    axes[1].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                 f'{v:.1f}', ha='center', fontsize=11)
axes[1].set_title('各季節平均 AQI', fontsize=14, fontweight='bold')
axes[1].set_ylabel('平均 AQI', fontsize=12)
axes[1].grid(axis='y', alpha=0.3)
axes[1].spines['top'].set_visible(False)
axes[1].spines['right'].set_visible(False)

plt.suptitle('火災事件與 AQI 的季節分佈比較', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_aqi_seasonal.png'), dpi=150, bbox_inches='tight')
plt.show()

# ══════════════════════════════════════════════════════════════════════════════
# 圖3：縣市散點圖 — 年均火災件數 vs 年均 AQI
# ══════════════════════════════════════════════════════════════════════════════
print('▶ 圖3：縣市散點圖...')
county_stats = merged.groupby('county').agg(
    avg_fire=('fire_count', 'mean'),
    avg_aqi=('avg_aqi', 'mean')
).reset_index()

fig, ax = plt.subplots(figsize=(11, 7))
ax.scatter(county_stats['avg_fire'], county_stats['avg_aqi'],
           s=80, color='#FF6B35', alpha=0.75, edgecolors='white', linewidth=0.8)

for _, row in county_stats.iterrows():
    ax.annotate(row['county'], (row['avg_fire'], row['avg_aqi']),
                textcoords='offset points', xytext=(5, 3), fontsize=9, color='#444')

# 趨勢線
import numpy as np
z = np.polyfit(county_stats['avg_fire'], county_stats['avg_aqi'], 1)
p = np.poly1d(z)
x_line = np.linspace(county_stats['avg_fire'].min(), county_stats['avg_fire'].max(), 100)
ax.plot(x_line, p(x_line), '--', color='#1F77B4', linewidth=1.5, alpha=0.7, label='趨勢線')

corr = county_stats['avg_fire'].corr(county_stats['avg_aqi'])
ax.text(0.05, 0.93, f'相關係數 r = {corr:.3f}', transform=ax.transAxes,
        fontsize=12, color='#1F77B4',
        bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#1F77B4', alpha=0.8))

ax.set_title('各縣市年均火災件數 vs 年均 AQI', fontsize=15, fontweight='bold', pad=12)
ax.set_xlabel('月均火災件數', fontsize=12)
ax.set_ylabel('平均 AQI', fontsize=12)
ax.legend(fontsize=11)
ax.grid(alpha=0.2)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_aqi_county_scatter.png'), dpi=150, bbox_inches='tight')
plt.show()

# ══════════════════════════════════════════════════════════════════════════════
# 圖4：起火原因分析
# ══════════════════════════════════════════════════════════════════════════════
print('▶ 圖4：起火原因...')
cause_counts = fire['起火原因'].value_counts().head(12)

fig, ax = plt.subplots(figsize=(12, 6))
bars = ax.barh(cause_counts.index[::-1], cause_counts.values[::-1],
               color='#FF6B35', alpha=0.8, edgecolor='white')
for bar, v in zip(bars, cause_counts.values[::-1]):
    ax.text(bar.get_width() + 200, bar.get_y() + bar.get_height()/2,
            f'{int(v):,}', va='center', fontsize=10)
ax.set_title('火災起火原因排名（前 12 名，2020–2024）', fontsize=15, fontweight='bold', pad=12)
ax.set_xlabel('事件件數', fontsize=12)
ax.grid(axis='x', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'fire_cause_ranking.png'), dpi=150, bbox_inches='tight')
plt.show()

# ══════════════════════════════════════════════════════════════════════════════
# 摘要
# ══════════════════════════════════════════════════════════════════════════════
print('\n' + '=' * 55)
print('🔥 火災 × AQI 分析摘要')
print('=' * 55)
print(f'火災資料期間：{fire["date"].min().date()} ～ {fire["date"].max().date()}')
print(f'總件數：{len(fire):,} 件')

print(f'\n各季節火災件數：')
for s in season_order:
    cnt = fire[fire['month'].map(season_map) == s].shape[0]
    print(f'  {s}：{cnt:,} 件')

print(f'\n火災最多縣市 Top 5：')
print(fire['county'].value_counts().head(5).to_string())

print(f'\n最常見起火原因 Top 5：')
print(fire['起火原因'].value_counts().head(5).to_string())

print(f'\n縣市火災件數 vs AQI 相關係數：{corr:.3f}')
if abs(corr) > 0.5:
    print('  → 中度以上相關，火災頻繁的縣市空氣品質也較差')
elif abs(corr) > 0.3:
    print('  → 低度相關，有一定關聯但不顯著')
else:
    print('  → 相關性低，縣市火災頻率與 AQI 無明顯線性關係')

print('\n✅ 所有圖表已儲存至 analysis/ 資料夾')
