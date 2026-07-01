import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak
import plotly.graph_objects as go
from datetime import datetime, timedelta
import urllib.request
import time
import random
import warnings

warnings.filterwarnings('ignore')


def get_stock_name(symbol):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for attempt in range(3):
        try:
            if symbol.startswith('6'): market = 'sh'
            elif symbol.startswith('0') or symbol.startswith('3'): market = 'sz'
            elif symbol.startswith('8') or symbol.startswith('4'): market = 'bj'
            else: market = 'sh'
            url = f"http://qt.gtimg.cn/q={market}{symbol}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                data = response.read().decode('gbk')
                if '~' in data:
                    parts = data.split('~')
                    if len(parts) > 1 and parts[1]:
                        return parts[1]
        except Exception:
            if attempt < 2: time.sleep(1)
    return None


def fetch_kline_data(symbol, start_date, end_date):
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            if df is not None and not df.empty:
                df.columns = [str(c).lower() for c in df.columns]
                df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low"}, inplace=True)
                return df
        except Exception:
            if attempt < 2:
                time.sleep(random.uniform(1.5, 3.5))
    try:
        sina_symbol = f"sh{symbol}" if symbol.startswith('6') else f"sz{symbol}"
        df = ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date, end_date=end_date, adjust="qfq")
        if df is not None and not df.empty:
            df.columns = [str(c).lower() for c in df.columns]
            df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low"}, inplace=True)
            return df
    except Exception:
        pass
    raise ValueError("所有数据源均无法获取K线，可能是网络限制或接口维护，请稍后再试。")


def calculate_signals(df):
    df['ma18'] = df['close'].rolling(window=18).mean()
    df['ma65'] = df['close'].rolling(window=65).mean()
    df['ma18_prev'] = df['ma18'].shift(1)
    df['close_prev'] = df['close'].shift(1)

    cross_close_ma18 = (df['close'] > df['ma18']) & (df['close_prev'] <= df['ma18_prev'])
    cross_ma18_close = (df['ma18'] > df['close']) & (df['ma18_prev'] <= df['close_prev'])

    bb_cond1 = (df['close'] > df['ma18']) & (df['close'] / df['ma18'] > 1.003) & (df['ma18'] > df['ma18_prev']) & (df['ma18'] - df['ma18_prev'] > 0.003)
    bb_cond2 = cross_close_ma18 & (df['close'] / df['close_prev'] >= 1.0994)
    df['BB'] = bb_cond1 | bb_cond2

    ss_cond1 = (df['close'] < df['ma18']) & (df['close'] <= df['open']) & (df['ma18'] < df['ma18_prev']) & (df['ma18_prev'] - df['ma18'] > 0.006)
    ss_cond2 = cross_ma18_close & (df['ma18'] - df['close'] > 0.01) & ((df['close_prev'] - df['close']) / df['close_prev'] >= 0.0994)
    df['SS'] = ss_cond1 | ss_cond2

    position_state = 0
    states = []
    for i in range(len(df)):
        if df.iloc[i]['BB']: position_state = 1
        elif df.iloc[i]['SS']: position_state = 0
        states.append(position_state)
    df['pos_state'] = states
    return df


def run_backtest(symbol, initial_capital=100000.0):
    stock_name = get_stock_name(symbol)
    if not stock_name:
        try:
            spot_df = ak.stock_zh_a_spot_em()
            code_col = '代码' if '代码' in spot_df.columns else 'code'
            name_col = '名称' if '名称' in spot_df.columns else 'name'
            match = spot_df[spot_df[code_col] == symbol]
            if not match.empty:
                stock_name = str(match.iloc[0][name_col])
        except Exception:
            pass
    if not stock_name:
        stock_name = "未知"

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=450)).strftime("%Y%m%d")

    df = fetch_kline_data(symbol, start_date, end_date)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    df = calculate_signals(df)

    one_year_ago = datetime.now() - timedelta(days=365)
    future_df = df[df['date'] >= one_year_ago]
    if future_df.empty:
        raise ValueError("最近一年的交易数据不足。")

    bt_start_idx = future_df.index[0]
    initial_position = df.iloc[bt_start_idx - 1]['pos_state'] if bt_start_idx > 0 else 0
    bt_df = df.iloc[bt_start_idx:].copy().reset_index(drop=True)

    if len(bt_df) < 10:
        raise ValueError("最近一年的交易数据不足。")

    bt_df['buy_signal'] = bt_df['BB'].shift(1)
    bt_df['sell_signal'] = bt_df['SS'].shift(1)

    capital = initial_capital
    shares = 0
    position = initial_position
    trades = []
    strategy_equity = []
    last_buy_price = 0.0

    if position == 1:
        first_open = bt_df.iloc[0]['open']
        last_buy_price = first_open
        buy_shares = int(capital / first_open / 100) * 100
        if buy_shares > 0:
            capital -= buy_shares * first_open
            shares = buy_shares
            trades.append({'date': bt_df.iloc[0]['date'].strftime('%Y-%m-%d'), 'action': '期初建仓', 'price': first_open, 'shares': shares, 'single_return': None})

    bh_first_open = bt_df.iloc[0]['open']
    bh_shares = int(initial_capital / bh_first_open / 100) * 100
    bh_remain_cash = initial_capital - (bh_shares * bh_first_open)
    bh_equity = []

    for i in range(len(bt_df)):
        row = bt_df.iloc[i]
        date = row['date']
        if i == 0 and position == 1:
            pass
        elif position == 0 and row['buy_signal']:
            buy_price = row['low']
            last_buy_price = buy_price
            buy_shares = int(capital / buy_price / 100) * 100
            if buy_shares > 0:
                capital -= buy_shares * buy_price
                shares = buy_shares
                position = 1
                trades.append({'date': date.strftime('%Y-%m-%d'), 'action': '买入', 'price': buy_price, 'shares': shares, 'single_return': None})
        elif position == 1 and row['sell_signal']:
            sell_price = row['high']
            single_return = ((sell_price - last_buy_price) / last_buy_price) * 100 if last_buy_price > 0 else 0.0
            capital += shares * sell_price
            trades.append({'date': date.strftime('%Y-%m-%d'), 'action': '卖出', 'price': sell_price, 'shares': shares, 'single_return': single_return})
            shares = 0
            position = 0
        strategy_equity.append(capital + shares * row['close'])
        bh_equity.append(bh_remain_cash + bh_shares * row['close'])

    if position == 1:
        last_close = bt_df.iloc[-1]['close']
        single_return = ((last_close - last_buy_price) / last_buy_price) * 100 if last_buy_price > 0 else 0.0
        capital += shares * last_close
        trades.append({'date': bt_df.iloc[-1]['date'].strftime('%Y-%m-%d'), 'action': '期末平仓', 'price': last_close, 'shares': shares, 'single_return': single_return})

    strategy_returns = [(eq / initial_capital - 1) * 100 for eq in strategy_equity]
    bh_returns = [(eq / initial_capital - 1) * 100 for eq in bh_equity]

    strategy_return = strategy_returns[-1]
    bh_return = bh_returns[-1]

    eq_series = pd.Series(strategy_equity)
    strategy_max_dd = ((eq_series - eq_series.cummax()) / eq_series.cummax()).min() * 100

    bh_series = pd.Series(bh_equity)
    bh_max_dd = ((bh_series - bh_series.cummax()) / bh_series.cummax()).min() * 100

    return {
        'stock_name': stock_name,
        'bt_df': bt_df,
        'strategy_returns': strategy_returns,
        'bh_returns': bh_returns,
        'trades': trades,
        'strategy_return': strategy_return,
        'bh_return': bh_return,
        'strategy_max_dd': strategy_max_dd,
        'bh_max_dd': bh_max_dd,
        'initial_capital': initial_capital,
    }


# ==================== Streamlit UI ====================

st.set_page_config(page_title="贝塔系统回测", layout="wide")
st.title("📈 贝塔系统回测")

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    symbol = st.text_input("股票代码", value="600519", max_chars=6, help="输入6位纯数字股票代码")
with col2:
    initial_capital = st.number_input("初始资金(元)", value=100000, min_value=10000, step=10000)
with col3:
    st.write("")  # 占位
    st.write("")
    run_btn = st.button("🚀 开始回测", type="primary", use_container_width=True)

if run_btn:
    if not symbol.isdigit() or len(symbol) != 6:
        st.warning("请输入正确的6位纯数字股票代码！")
    else:
        with st.spinner(f"正在获取 {symbol} 数据并回测，请稍候..."):
            try:
                result = run_backtest(symbol, initial_capital)
                st.session_state['result'] = result
            except Exception as e:
                st.error(f"回测失败: {e}\n\n建议: 可能是网络限制或数据源维护，请稍后再试。")

if 'result' in st.session_state:
    r = st.session_state['result']
    st.subheader(f"{symbol} {r['stock_name']} 回测统计 (最近一年)")

    # 统计卡片
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("贝塔系统收益率", f"{r['strategy_return']:+.2f}%")
    with col2:
        st.metric("贝塔系统最大回撤", f"{r['strategy_max_dd']:.2f}%")
    with col3:
        st.metric("买入持有收益率", f"{r['bh_return']:+.2f}%")
    with col4:
        st.metric("买入持有最大回撤", f"{r['bh_max_dd']:.2f}%")
    with col5:
        trade_count = len([t for t in r['trades'] if '买' in t['action'] or '卖' in t['action']])
        st.metric("交易次数", f"{trade_count} 次")

    # 收益率曲线
    st.subheader("收益率曲线对比")
    bt_df = r['bt_df']
    dates = bt_df['date']

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=r['strategy_returns'], name='贝塔系统收益率', line=dict(color='red', width=2)))
    fig.add_trace(go.Scatter(x=dates, y=r['bh_returns'], name='买入并持有收益率', line=dict(color='blue', width=2, dash='dash')))
    fig.add_hline(y=0, line_dash="dot", line_color="black", line_width=1)

    # 买卖标记
    for t in r['trades']:
        idx = bt_df[bt_df['date'] == pd.to_datetime(t['date'])].index
        if len(idx) > 0:
            y_val = r['strategy_returns'][idx[0]]
            if '买' in t['action']:
                fig.add_trace(go.Scatter(
                    x=[pd.to_datetime(t['date'])], y=[y_val],
                    mode='markers', marker=dict(symbol='triangle-up', color='red', size=12),
                    name=f"买入 {t['date']}", showlegend=False,
                    hovertext=f"买入 {t['date']} 价格:{t['price']:.2f}"
                ))
            elif '卖' in t['action'] or '平仓' in t['action']:
                ret_str = f" ({t['single_return']:+.2f}%)" if t.get('single_return') is not None else ""
                fig.add_trace(go.Scatter(
                    x=[pd.to_datetime(t['date'])], y=[y_val],
                    mode='markers', marker=dict(symbol='triangle-down', color='green', size=12),
                    name=f"卖出 {t['date']}", showlegend=False,
                    hovertext=f"卖出 {t['date']} 价格:{t['price']:.2f}{ret_str}"
                ))

    fig.update_layout(
        xaxis_title="日期", yaxis_title="收益率 (%)",
        xaxis=dict(tickformat="%Y.%m"),
        hovermode='x unified', height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

    # 交易明细
    st.subheader("交易明细")
    trade_rows = []
    for t in r['trades']:
        ret_str = f"{t['single_return']:+.2f}%" if t.get('single_return') is not None else "-"
        trade_rows.append({
            '日期': t['date'], '操作': t['action'],
            '价格': f"{t['price']:.2f}", '数量(股)': t['shares'], '单次收益': ret_str
        })
    if trade_rows:
        st.dataframe(trade_rows, use_container_width=True, hide_index=True)

    st.caption(f"初始资金: {r['initial_capital']:,.2f} 元 | 红三角: 买入 | 绿三角: 卖出")
