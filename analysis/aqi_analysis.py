"""
台灣空氣品質歷史資料分析
資料來源：行政院環境部開放資料平台（2020–2025）
執行方式：python aqi_analysis.py
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Patch
import seaborn as sns
import glob
import os
import warnings
warnings.filterwarnings('ignore')

# ── 中文字體設定 ───────────────────────────────────────────────────────────────
plt.rcParams['font.family'] = 'Microsoft JhengHei'
plt.rcParams['axes.unicode_minus'] = False

# ── 1. 資料載入 ────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
files = sorted(glob.glob(os.path.join(DATA_DIR, '空氣品質指標(AQI)(歷史資料)*.csv')))
files = [f for f in files if any(f'({y}-' in f for y in range(2020, 2026))]
print(f'載入 {len(files)} 個檔案...')

dfs = []
for f in files:
    try:
        dfs.append(pd.read_csv(f, encoding='utf-8-sig'))
    except Exception as e:
        print(f'讀取失敗：{f} → {e}')

df = pd.concat(dfs, ignore_index=True)
print(f'合併後共 {len(df):,} 筆')

# ── 2. 資料清理 ────────────────────────────────────────────────────────────────
df['datacreationdate'] = pd.to_datetime(df['datacreationdate'], errors='coerce')
df['aqi']   = pd.to_numeric(df['aqi'],   errors='coerce')
df['pm2.5'] = pd.to_numeric(df['pm2.5'], errors='coerce')
df['pm10']  = pd.to_numeric(df['pm10'],  errors='coerce')
df = df[(df['aqi'] > 0) & (df['aqi'] <= 500)]

df['year']  = df['datacreationdate'].dt.year
df['month'] = df['datacreationdate'].dt.month
df['season'] = df['month'].map({
    12:'冬季', 1:'冬季', 2:'冬季',
    3:'春季',  4:'春季', 5:'春季',
    6:'夏季',  7:'夏季', 8:'夏季',
    9:'秋季', 10:'秋季', 11:'秋季'
})
df['county'] = df['county'].str.replace('臺', '台', regex=False)
print(f'清理後共 {len(df):,} 筆，時間範圍：{df["datacreationdate"].min()} ～ {df["datacreationdate"].max()}\n')

# ── 3. 全台 AQI 年度趨勢 ───────────────────────────────────────────────────────
print('▶ 繪製年度趨勢...')
yearly = df.groupby('year')['aqi'].mean().reset_index()

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(yearly['year'], yearly['aqi'], marker='o', color='#FF6B35', linewidth=2.5, markersize=8)
ax.fill_between(yearly['year'], yearly['aqi'], alpha=0.15, color='#FF6B35')
for _, row in yearly.iterrows():
    ax.annotate(f"{row['aqi']:.1f}", (row['year'], row['aqi']),
                textcoords='offset points', xytext=(0, 10), ha='center', fontsize=11)
ax.set_title('全台平均 AQI 年度趨勢（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_xlabel('年份', fontsize=13)
ax.set_ylabel('平均 AQI', fontsize=13)
ax.set_xticks(yearly['year'])
ax.grid(axis='y', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'annual_aqi_trend.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 4. 季節變化分析 ────────────────────────────────────────────────────────────
print('▶ 繪製季節分析...')
monthly = df.groupby('month')['aqi'].mean().reset_index()
month_labels = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']
season_colors = {
    1:'#5B9BD5', 2:'#5B9BD5', 3:'#70AD47', 4:'#70AD47', 5:'#70AD47',
    6:'#FF6B35', 7:'#FF6B35', 8:'#FF6B35', 9:'#FFC000', 10:'#FFC000',
    11:'#FFC000', 12:'#5B9BD5'
}
colors = [season_colors[m] for m in monthly['month']]

fig, ax = plt.subplots(figsize=(12, 5))
bars = ax.bar(month_labels, monthly['aqi'], color=colors, edgecolor='white')
for bar, val in zip(bars, monthly['aqi']):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
            f'{val:.1f}', ha='center', va='bottom', fontsize=10)
legend = [
    Patch(color='#5B9BD5', label='冬季（12–2月）'),
    Patch(color='#70AD47', label='春季（3–5月）'),
    Patch(color='#FF6B35', label='夏季（6–8月）'),
    Patch(color='#FFC000', label='秋季（9–11月）'),
]
ax.legend(handles=legend, loc='upper right', fontsize=10)
ax.set_title('各月份平均 AQI（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_ylabel('平均 AQI', fontsize=13)
ax.grid(axis='y', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'monthly_aqi_seasonal.png'), dpi=150, bbox_inches='tight')
plt.show()

# 四季 Boxplot
fig, ax = plt.subplots(figsize=(10, 6))
season_order = ['春季', '夏季', '秋季', '冬季']
season_palette = {'春季':'#70AD47', '夏季':'#FF6B35', '秋季':'#FFC000', '冬季':'#5B9BD5'}
sns.boxplot(data=df, x='season', y='aqi', order=season_order,
            palette=season_palette, width=0.5, ax=ax,
            flierprops=dict(marker='o', markersize=2, alpha=0.3))
ax.set_title('四季 AQI 分佈（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_xlabel('季節', fontsize=13)
ax.set_ylabel('AQI', fontsize=13)
ax.grid(axis='y', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'seasonal_aqi_boxplot.png'), dpi=150, bbox_inches='tight')
plt.show()

print('各季節平均 AQI：')
print(df.groupby('season')['aqi'].agg(['mean','median','std']).round(2), '\n')

# ── 5. 縣市空間分佈 ────────────────────────────────────────────────────────────
print('▶ 繪製縣市排名...')
county_aqi = df.groupby('county')['aqi'].mean().sort_values(ascending=True)

def aqi_color(val):
    if val <= 50:  return '#00B050'
    if val <= 100: return '#FFFF00'
    if val <= 150: return '#FF6600'
    return '#FF0000'

fig, ax = plt.subplots(figsize=(12, 8))
bars = ax.barh(county_aqi.index, county_aqi.values,
               color=[aqi_color(v) for v in county_aqi.values], edgecolor='white')
for bar, val in zip(bars, county_aqi.values):
    ax.text(val + 0.3, bar.get_y() + bar.get_height()/2,
            f'{val:.1f}', va='center', fontsize=10)
ax.axvline(x=50,  color='#00B050', linestyle='--', alpha=0.5, linewidth=1)
ax.axvline(x=100, color='#FFCC00', linestyle='--', alpha=0.5, linewidth=1)
ax.set_title('各縣市平均 AQI 排名（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_xlabel('平均 AQI', fontsize=13)
ax.grid(axis='x', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'county_aqi_ranking.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 6. PM2.5 長期趨勢 ──────────────────────────────────────────────────────────
print('▶ 繪製 PM2.5 趨勢...')
pm25_yearly = df.groupby('year')['pm2.5'].mean().reset_index()

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(pm25_yearly['year'], pm25_yearly['pm2.5'], marker='s',
        color='#8B4513', linewidth=2.5, markersize=8, label='全台平均')
ax.axhline(y=15, color='red', linestyle='--', alpha=0.7, label='WHO 準則值 15 µg/m³')
ax.fill_between(pm25_yearly['year'], pm25_yearly['pm2.5'], 15,
                where=pm25_yearly['pm2.5'] > 15, alpha=0.15, color='red', label='超標區間')
for _, row in pm25_yearly.iterrows():
    ax.annotate(f"{row['pm2.5']:.1f}", (row['year'], row['pm2.5']),
                textcoords='offset points', xytext=(0, 10), ha='center', fontsize=11)
ax.set_title('全台平均 PM2.5 年度趨勢（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_xlabel('年份', fontsize=13)
ax.set_ylabel('PM2.5 (µg/m³)', fontsize=13)
ax.set_xticks(pm25_yearly['year'])
ax.legend(fontsize=11)
ax.grid(axis='y', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'pm25_annual_trend.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 7. AQI 等級分佈 ────────────────────────────────────────────────────────────
print('▶ 繪製 AQI 等級分佈...')
def aqi_level(aqi):
    if aqi <= 50:  return '良好'
    if aqi <= 100: return '普通'
    if aqi <= 150: return '對敏感族群不健康'
    if aqi <= 200: return '對所有族群不健康'
    return '非常不健康/危害'

df['aqi_level'] = df['aqi'].apply(aqi_level)
level_order  = ['良好', '普通', '對敏感族群不健康', '對所有族群不健康', '非常不健康/危害']
level_colors = ['#00B050', '#FFFF00', '#FF6600', '#FF0000', '#7030A0']
level_pct = df['aqi_level'].value_counts().reindex(level_order, fill_value=0) / len(df) * 100

fig, ax = plt.subplots(figsize=(10, 5))
bars = ax.bar(level_order, level_pct.values, color=level_colors, edgecolor='white')
for bar, pct in zip(bars, level_pct.values):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
            f'{pct:.1f}%', ha='center', va='bottom', fontsize=11)
ax.set_title('AQI 等級分佈佔比（2020–2025）', fontsize=16, fontweight='bold', pad=15)
ax.set_ylabel('佔比 (%)', fontsize=13)
ax.set_xticklabels(level_order, rotation=15, ha='right')
ax.grid(axis='y', alpha=0.3)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'aqi_level_distribution.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 8. 南北差異 ────────────────────────────────────────────────────────────────
print('▶ 繪製南北比較...')
north = ['台北市', '新北市', '基隆市', '桃園市', '新竹市', '新竹縣']
south = ['台南市', '高雄市', '屏東縣', '嘉義市', '嘉義縣']
df_ns = df[df['county'].isin(north + south)].copy()
df_ns['region'] = df_ns['county'].apply(lambda c: '北部' if c in north else '南部')

fig, axes = plt.subplots(1, 2, figsize=(14, 6))
ns_yearly = df_ns.groupby(['year', 'region'])['aqi'].mean().reset_index()
for region, color in [('北部', '#5B9BD5'), ('南部', '#FF6B35')]:
    data = ns_yearly[ns_yearly['region'] == region]
    axes[0].plot(data['year'], data['aqi'], marker='o', label=region,
                 color=color, linewidth=2.5, markersize=8)
axes[0].set_title('北部 vs 南部 AQI 年度趨勢', fontsize=14, fontweight='bold')
axes[0].set_xlabel('年份', fontsize=12)
axes[0].set_ylabel('平均 AQI', fontsize=12)
axes[0].legend(fontsize=12)
axes[0].grid(alpha=0.3)
axes[0].spines['top'].set_visible(False)
axes[0].spines['right'].set_visible(False)

ns_season = df_ns.groupby(['season', 'region'])['aqi'].mean().reset_index()
x = range(len(season_order))
width = 0.35
for i, (region, color) in enumerate([('北部', '#5B9BD5'), ('南部', '#FF6B35')]):
    vals = [ns_season[(ns_season['region']==region) & (ns_season['season']==s)]['aqi'].values for s in season_order]
    vals = [v[0] if len(v) > 0 else 0 for v in vals]
    axes[1].bar([xi + (i - 0.5) * width for xi in x], vals, width, label=region, color=color, alpha=0.85)
axes[1].set_title('北部 vs 南部各季 AQI 比較', fontsize=14, fontweight='bold')
axes[1].set_xticks(x)
axes[1].set_xticklabels(season_order, fontsize=12)
axes[1].set_ylabel('平均 AQI', fontsize=12)
axes[1].legend(fontsize=12)
axes[1].grid(axis='y', alpha=0.3)
axes[1].spines['top'].set_visible(False)
axes[1].spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(os.path.dirname(__file__), 'north_south_comparison.png'), dpi=150, bbox_inches='tight')
plt.show()

# ── 9. 摘要 ────────────────────────────────────────────────────────────────────
print('\n' + '=' * 50)
print('📊 資料分析摘要')
print('=' * 50)
print(f'分析期間：{df["year"].min()} – {df["year"].max()} 年')
print(f'總筆數：{len(df):,} 筆')
print(f'涵蓋縣市：{df["county"].nunique()} 個 / 測站：{df["sitename"].nunique()} 個')
print(f'\n全台 AQI 平均：{df["aqi"].mean():.1f}')
print(f'全台 PM2.5 平均：{df["pm2.5"].mean():.1f} µg/m³')
print(f'\nAQI 最差 5 縣市：')
print(df.groupby('county')['aqi'].mean().sort_values(ascending=False).head(5).round(1).to_string())
print(f'\nAQI 最佳 5 縣市：')
print(df.groupby('county')['aqi'].mean().sort_values().head(5).round(1).to_string())
print(f'\n超標（AQI > 100）佔比：{(df["aqi"] > 100).sum() / len(df) * 100:.1f}%')
print('\n✅ 所有圖表已儲存至 analysis/ 資料夾')
