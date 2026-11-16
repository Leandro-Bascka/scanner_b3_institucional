# -*- coding: utf-8 -*-
"""
SCANNER B3 - INSTITUCIONAL QUANT

Universo dinâmico: as N_ATIVOS ações mais líquidas (Fundamentus, Liq.2meses).
Benchmark: BOVA11.

Score institucional (0-100), cinco dimensões independentes:
  Tendência e estrutura ......... 25
  Fluxo e volume ................ 25
  Momentum ...................... 20
  Força relativa ................ 15
  Volatilidade e rompimento ..... 15

Estados de sinal (separados do score):
  REVERSÃO INICIAL, REVERSÃO CONFIRMADA, TENDÊNCIA EM ANDAMENTO,
  CONTINUAÇÃO, ESTICADA.

O scanner não grava nenhum arquivo.
"""

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:  # servidor sem Tkinter (versão web)
    tk = ttk = messagebox = None
import threading
import queue
import warnings
from datetime import datetime
from io import StringIO

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import matplotlib

try:
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except Exception:  # sem Tkinter: usa backend sem janela
    matplotlib.use("Agg")
    FigureCanvasTkAgg = None

from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter


warnings.filterwarnings("ignore")


# ============================================================
# CONFIGURAÇÕES GERAIS
# ============================================================

N_ATIVOS = 80
BENCHMARK = "BOVA11"

PERIODO_HISTORICO = "2y"
INTERVALO = "1d"

DIAS_SINAL = 2                  # idade máxima (pregões) de um evento de sinal

ATR_PERIODO = 14
ATR_STOP = 1.8
RISCO_RETORNO_1 = 1.5
RISCO_RETORNO_2 = 2.5

VOLUME_RELATIVO_MIN = 1.5       # volume "institucional" (destaque / absorção)

# Preço esticado
ESTICADA_ATR = 3.0              # distância da EMA21 em ATRs
ESTICADA_PCT = 0.12             # distância da EMA21 em %
ESTICADA_RSI = 80

GAP_EPISODIO = 5                # sinais separados por até 5 pregões = mesmo episódio

# Durante o pregão a última barra do Yahoo é parcial; descarta-a.
DESCARTAR_BARRA_PARCIAL = True

# SMC (Smart Money Concepts) - aplicado aos estados abaixo
SMC_ESTADOS = ("REVERSÃO CONFIRMADA",)
SMC_K = 3                       # pivôs: 3 candles de cada lado
SMC_RECUO = 150                 # pregões analisados para zonas e níveis
SMC_ALCANCE_ATR = 10            # alvos até 10 ATRs acima do preço

# Suportes e resistências (histórico + volume por preço + SMC)
SR_RECUO = 250                  # pregões de histórico analisados
SR_ALCANCE_ATR = 12             # só níveis até 12 ATRs do preço
SR_SCORE_MIN = 50               # força mínima (0-100) para listar um nível
SR_QTD = 3                      # níveis listados de cada lado do preço
SR_MEIA_VIDA = 120              # meia-vida (pregões) do peso de pivôs/reações
SR_PROX_ATR = 0.75              # "no suporte/na resistência" = a até 0,75 ATR

URL_FUNDAMENTUS = "https://www.fundamentus.com.br/resultado.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

ESTADOS_COM_NIVEIS = (
    "REVERSÃO CONFIRMADA",
    "CONTINUAÇÃO",
    "REVERSÃO INICIAL",
    "TENDÊNCIA EM ANDAMENTO",
)

ORDEM_ESTADO = {
    "REVERSÃO CONFIRMADA": 5,
    "CONTINUAÇÃO": 4,
    "REVERSÃO INICIAL": 3,
    "TENDÊNCIA EM ANDAMENTO": 2,
    "ESTICADA": 1,
    "SEM SINAL": 0,
}

TAG_ESTADO = {
    "REVERSÃO CONFIRMADA": "confirmado",
    "CONTINUAÇÃO": "continuacao",
    "REVERSÃO INICIAL": "inicial",
    "TENDÊNCIA EM ANDAMENTO": "tendencia",
    "ESTICADA": "esticada",
}

TEMAS = {
    "escuro": {
        "bg": "#0e1116", "painel": "#161b22", "cab": "#21262d",
        "fg": "#e6edf3", "suave": "#8b949e", "grade": "#30363d",
        "alta": "#26a69a", "baixa": "#ef5350",
        "ema9": "#ffd54f", "ema21": "#4fc3f7",
        "ema50": "#ba68c8", "ema200": "#ff8a65",
        "vwap_fundo": "#00e5ff", "vwap_romp": "#ffab40",
        "bb": "#90a4ae", "vol_dest": "#fdd835",
        "sel_bg": "#1f4e79", "sel_fg": "#ffffff",
        "tag_conf": "#14532d", "tag_cont": "#0c4a6e",
        "tag_ini": "#713f12", "tag_tend": "#1c3d2e",
        "tag_esti": "#7c2d12",
        "stop": "#ef5350", "alvo": "#66bb6a", "entrada": "#90caf9",
        "smc": "#b388ff",
    },
    "claro": {
        "bg": "#f5f6f8", "painel": "#ffffff", "cab": "#e4e7eb",
        "fg": "#1f2933", "suave": "#616e7c", "grade": "#d0d5dc",
        "alta": "#2e9e6b", "baixa": "#d64545",
        "ema9": "#f2a900", "ema21": "#1e88e5",
        "ema50": "#8e24aa", "ema200": "#e64a19",
        "vwap_fundo": "#00838f", "vwap_romp": "#ef6c00",
        "bb": "#607d8b", "vol_dest": "#f9a825",
        "sel_bg": "#bbdefb", "sel_fg": "#000000",
        "tag_conf": "#b7e4c7", "tag_cont": "#bde0fe",
        "tag_ini": "#fff0b3", "tag_tend": "#dcefe3",
        "tag_esti": "#ffd6b0",
        "stop": "#d64545", "alvo": "#2e9e6b", "entrada": "#1e88e5",
        "smc": "#7e57c2",
    },
}


# ============================================================
# CONTROLE GLOBAL
# ============================================================

fila_resultados = queue.Queue()


# ============================================================
# IDENTIFICAÇÃO DINÂMICA DAS AÇÕES MAIS LÍQUIDAS
# ============================================================

def obter_acoes_mais_liquidas():

    print("\nConsultando Fundamentus...")

    try:
        resposta = requests.get(
            URL_FUNDAMENTUS,
            headers=HEADERS,
            timeout=30
        )
        resposta.raise_for_status()

        tabelas = pd.read_html(
            StringIO(resposta.text),
            decimal=",",
            thousands="."
        )

        if not tabelas:
            raise RuntimeError("Fundamentus não retornou nenhuma tabela.")

        tabela = None

        for tab in tabelas:
            colunas = [str(c).strip().lower() for c in tab.columns]
            if (
                any("papel" in c for c in colunas)
                and any("liq.2meses" in c for c in colunas)
            ):
                tabela = tab.copy()
                break

        if tabela is None:
            raise RuntimeError(
                "Não foi possível localizar a coluna Liq.2meses."
            )

        coluna_papel = next(
            c for c in tabela.columns if "papel" in str(c).lower()
        )
        coluna_liquidez = next(
            c for c in tabela.columns if "liq.2meses" in str(c).lower()
        )

        tabela = tabela[[coluna_papel, coluna_liquidez]].copy()
        tabela.columns = ["PAPEL", "LIQUIDEZ"]

        tabela["PAPEL"] = (
            tabela["PAPEL"].astype(str).str.strip().str.upper()
        )

        # read_html já converte para número quando consegue. Só trata
        # texto se a coluna ainda for texto (evita multiplicar valores).
        liquidez = tabela["LIQUIDEZ"]

        if liquidez.dtype == object:
            liquidez = (
                liquidez.astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
            )

        tabela["LIQUIDEZ"] = pd.to_numeric(liquidez, errors="coerce")

        tabela = tabela[
            tabela["PAPEL"].str.match(r"^[A-Z]{4}[0-9]{1,2}$", na=False)
        ]
        tabela = tabela.dropna(subset=["LIQUIDEZ"])
        tabela = tabela[tabela["LIQUIDEZ"] > 0]
        tabela = tabela.sort_values("LIQUIDEZ", ascending=False)
        tabela = tabela.drop_duplicates(subset=["PAPEL"])

        if len(tabela) < N_ATIVOS:
            raise RuntimeError(
                f"Fundamentus retornou somente {len(tabela)} ações "
                f"válidas. Não será utilizada lista estática."
            )

        ativos = tabela.head(N_ATIVOS)["PAPEL"].tolist()

        print(f"{len(ativos)} ações selecionadas pela liquidez.")

        return ativos

    except Exception as erro:
        raise RuntimeError(
            f"Falha ao obter as ações mais líquidas no Fundamentus: {erro}"
        )


# ============================================================
# INDICADORES BASE
# ============================================================

def calcular_rsi(serie, periodo=14):

    delta = serie.diff()

    ganhos = delta.clip(lower=0)
    perdas = -delta.clip(upper=0)

    media_ganhos = ganhos.ewm(
        alpha=1 / periodo, min_periods=periodo, adjust=False
    ).mean()

    media_perdas = perdas.ewm(
        alpha=1 / periodo, min_periods=periodo, adjust=False
    ).mean()

    rs = media_ganhos / media_perdas.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask((media_perdas == 0) & (media_ganhos > 0), 100)
    rsi = rsi.mask((media_perdas == 0) & (media_ganhos == 0), 50)

    return rsi


def calcular_adx(df, periodo=14):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0),
        index=df.index
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()

    plus_di = (
        100 * plus_dm.ewm(alpha=1 / periodo, adjust=False).mean()
        / atr.replace(0, np.nan)
    )
    minus_di = (
        100 * minus_dm.ewm(alpha=1 / periodo, adjust=False).mean()
        / atr.replace(0, np.nan)
    )

    dx = (
        100 * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    adx = dx.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()

    return adx, plus_di, minus_di


def recente(serie_bool, janela):
    """True se a condição ocorreu em algum dos últimos 'janela' pregões."""
    return serie_bool.astype(float).rolling(janela, min_periods=1).max() > 0


def vwap_ancorada(df, pos):
    """VWAP diária acumulada a partir da posição 'pos' (preço típico)."""

    tipico = (df["High"] + df["Low"] + df["Close"]) / 3
    volume = df["Volume"]

    pv = (tipico * volume).iloc[pos:].cumsum()
    v = volume.iloc[pos:].cumsum()

    saida = pd.Series(np.nan, index=df.index)
    saida.iloc[pos:] = (pv / v.replace(0, np.nan)).to_numpy()

    return saida


def inicio_episodio(flags, pos, gap=GAP_EPISODIO):
    """
    A partir da posição 'pos' (último True), volta enquanto houver
    outro True em até 'gap' pregões antes. Retorna a primeira aparição
    do episódio.
    """
    while True:
        ini = max(0, pos - gap)
        anteriores = np.flatnonzero(flags[ini:pos])

        if anteriores.size == 0:
            return pos

        pos = ini + int(anteriores[-1])


def detectar_divergencia_altista(low, rsi, janela=5, max_dist=60, validade=5):
    """
    Divergência altista real entre dois fundos (pivôs) de preço:
    preço faz fundo igual/menor e o RSI faz fundo maior.
    O pivô só é confirmado 'janela' pregões depois (sem olhar o futuro).
    O sinal fica válido por 'validade' pregões após a confirmação.
    """

    n = len(low)
    lows = low.to_numpy(dtype=float)
    rs = rsi.to_numpy(dtype=float)

    saida = np.zeros(n, dtype=bool)

    ultimo = None
    ultima_div = -10**9

    for t in range(2 * janela, n):

        c = t - janela
        trecho = lows[c - janela:t + 1]

        if (
            not np.isnan(trecho).any()
            and not np.isnan(rs[c])
            and lows[c] <= trecho.min()
        ):
            if (
                ultimo is not None
                and janela < c - ultimo[0] <= max_dist
                and lows[c] <= ultimo[1] * 1.01
                and rs[c] > ultimo[2] + 3
            ):
                ultima_div = t

            ultimo = (c, lows[c], rs[c])

        saida[t] = (t - ultima_div) <= validade

    return pd.Series(saida, index=low.index)


def calcular_indicadores(df):

    df = df.copy()

    open_ = df["Open"]
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # ---- Médias exponenciais
    for periodo in (9, 21, 50, 200):
        df[f"EMA{periodo}"] = close.ewm(span=periodo, adjust=False).mean()

    df["EMA21_INCL"] = df["EMA21"] / df["EMA21"].shift(5) - 1

    # ---- RSI
    df["RSI"] = calcular_rsi(close)

    # ---- MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

    # ---- ATR
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1 / ATR_PERIODO, adjust=False, min_periods=ATR_PERIODO
    ).mean()

    # ---- ADX / DI
    df["ADX"], df["PLUS_DI"], df["MINUS_DI"] = calcular_adx(df)

    # ---- Volume relativo
    df["VOL_MEDIA"] = volume.rolling(20).mean()
    df["VOL_REL"] = volume / df["VOL_MEDIA"].replace(0, np.nan)

    # ---- OBV
    direcao = np.sign(close.diff()).fillna(0)
    df["OBV"] = (direcao * volume).cumsum()
    df["OBV_MEDIA"] = df["OBV"].ewm(span=10, adjust=False).mean()

    # ---- CLV / A/D / CMF
    amplitude = (high - low).replace(0, np.nan)
    clv = (((close - low) - (high - close)) / amplitude).fillna(0)

    df["AD"] = (clv * volume).cumsum()
    df["AD_MEDIA"] = df["AD"].ewm(span=20, adjust=False).mean()

    df["CMF"] = (
        (clv * volume).rolling(20).sum()
        / volume.rolling(20).sum().replace(0, np.nan)
    )

    # ---- MFI
    tipico = (high + low + close) / 3
    fluxo = tipico * volume
    variacao = tipico.diff()

    positivo = fluxo.where(variacao > 0, 0).rolling(14).sum()
    negativo = fluxo.where(variacao < 0, 0).rolling(14).sum()

    razao = positivo / negativo.replace(0, np.nan)
    df["MFI"] = 100 - (100 / (1 + razao))
    df["MFI"] = df["MFI"].mask((negativo == 0) & (positivo > 0), 100)

    # ---- Force Index de Elder (EMA 13)
    df["FORCE13"] = (close.diff() * volume).ewm(span=13, adjust=False).mean()

    # ---- Bandas de Bollinger (20, 2)
    df["BB_MID"] = close.rolling(20).mean()
    desvio = close.rolling(20).std(ddof=0)
    df["BB_UP"] = df["BB_MID"] + 2 * desvio
    df["BB_LOW"] = df["BB_MID"] - 2 * desvio
    df["BB_LARG"] = (df["BB_UP"] - df["BB_LOW"]) / df["BB_MID"]

    # ---- Donchian 20 e 55 (máxima/mínima anteriores, sem o candle atual)
    df["DON20_ALTA"] = high.rolling(20).max().shift(1)
    df["DON20_BAIXA"] = low.rolling(20).min().shift(1)
    df["DON55_ALTA"] = high.rolling(55).max().shift(1)
    df["DON55_BAIXA"] = low.rolling(55).min().shift(1)

    # ---- Choppiness Index (14): alto = lateral, baixo = direcional
    soma_tr = tr.rolling(14).sum()
    faixa = high.rolling(14).max() - low.rolling(14).min()
    df["CHOP"] = (
        100 * np.log10(soma_tr / faixa.replace(0, np.nan)) / np.log10(14)
    )

    # ---- Posição do fechamento no candle
    df["POSICAO_CANDLE"] = (close - low) / amplitude

    # ---- Retornos
    df["RETORNO_20"] = close.pct_change(20)
    df["RETORNO_60"] = close.pct_change(60)

    # ---- Estrutura de topos e fundos
    max20 = high.rolling(20).max()
    min20 = low.rolling(20).min()
    min10 = low.rolling(10).min()

    df["EST_ALTA"] = (max20 > max20.shift(20)) & (min20 > min20.shift(20))
    df["HL10"] = min10 > min10.shift(10)

    # ---- Queda recente (contexto para reversão)
    df["DD60"] = close / high.rolling(60).max() - 1

    return df


# ============================================================
# ANÁLISE: SCORES, EVENTOS E ESTADOS
# ============================================================

def classificar_score(valor):

    if valor >= 85:
        return "Tendência muito forte"
    if valor >= 70:
        return "Tendência confirmada"
    if valor >= 55:
        return "Tendência em formação"
    if valor >= 40:
        return "Sem confirmação suficiente"
    return "Tendência fraca"


def analisar_ativo(df, benchmark, score_minimo):

    df = calcular_indicadores(df)

    # ---- Benchmark
    df = df.join(benchmark, how="left")
    colunas_bench = list(benchmark.columns)
    df[colunas_bench] = df[colunas_bench].ffill()

    df["FR20"] = df["RETORNO_20"] - df["BOVA_RET20"]
    df["FR60"] = df["RETORNO_60"] - df["BOVA_RET60"]
    df["RS"] = df["Close"] / df["BOVA_CLOSE"]
    df["RS_EMA21"] = df["RS"].ewm(span=21, adjust=False).mean()

    n = len(df)
    close = df["Close"]

    # ---- Rompimentos
    df["ROMP20"] = close > df["DON20_ALTA"]
    df["ROMP55"] = close > df["DON55_ALTA"]
    romp20_rec = recente(df["ROMP20"], 3)
    romp55_rec = recente(df["ROMP55"], 10)

    # Rompimento curto (máxima dos 10 pregões anteriores): é o gatilho
    # da reversão; o Donchian 20 entra como pontuação extra.
    df["ROMP10"] = close > df["High"].shift(1).rolling(10).max()
    df["ROMP10_REC"] = recente(df["ROMP10"], 3)

    # ---- VWAP ancorada: no fundo dos últimos 60 pregões
    janela = min(60, n)
    pos_fundo = n - janela + int(np.argmin(df["Low"].to_numpy()[-janela:]))
    df["VWAP_FUNDO"] = vwap_ancorada(df, pos_fundo)

    # ---- VWAP ancorada: no último rompimento (Donchian 20)
    df["VWAP_ROMP"] = np.nan
    romp = np.flatnonzero(df["ROMP20"].to_numpy()[-janela:])

    if romp.size:
        ultimo_romp = n - janela + int(romp[-1])
        ini_romp = inicio_episodio(
            df["ROMP20"].to_numpy(dtype=bool), ultimo_romp
        )
        df["VWAP_ROMP"] = vwap_ancorada(df, ini_romp)

    # ---- Divergência altista real do RSI
    df["DIV_RSI"] = detectar_divergencia_altista(df["Low"], df["RSI"])

    # ---- Componentes de reversão
    df["ABSORCAO"] = (
        (df["VOL_REL"] >= VOLUME_RELATIVO_MIN)
        & (df["POSICAO_CANDLE"] >= 0.65)
        & (df["CMF"] > 0)
    )

    df["ENFRAQUECIMENTO"] = (
        (df["MINUS_DI"] < df["MINUS_DI"].shift(2))
        & (df["RSI"] > df["RSI"].shift(2))
    )

    df["MOMENTUM"] = (
        (df["MACD_HIST"] > df["MACD_HIST"].shift(1))
        & (df["RSI"] > df["RSI"].shift(1))
    )

    # ========================================================
    # SCORE INSTITUCIONAL (0-100)
    # ========================================================

    df["SC_TEND"] = (
        (close > df["EMA21"]) * 4
        + (df["EMA21"] > df["EMA50"]) * 4
        + (df["EMA50"] > df["EMA200"]) * 4
        + (df["EMA21_INCL"] > 0) * 3
        + ((df["ADX"] >= 20) & (df["PLUS_DI"] > df["MINUS_DI"])) * 6
        + df["EST_ALTA"] * 4
    )

    df["SC_FLUXO"] = (
        ((df["OBV"] > df["OBV_MEDIA"])
         & (df["OBV_MEDIA"] > df["OBV_MEDIA"].shift(5))) * 5
        + (df["CMF"] > 0) * 5
        + (df["AD"] > df["AD_MEDIA"]) * 4
        + ((df["MFI"] > 50) & (df["MFI"] >= df["MFI"].shift(3))) * 3
        + (df["FORCE13"] > 0) * 4
        + ((df["VOL_REL"] >= 1.2) & (close > df["Open"])) * 4
    )

    df["SC_MOM"] = (
        (df["RSI"] > 50) * 6
        + (df["MACD"] > df["MACD_SIGNAL"]) * 4
        + (df["MACD_HIST"] > df["MACD_HIST"].shift(1)) * 4
        + (df["MACD"] > 0) * 3
        + df["DIV_RSI"] * 3
    )

    df["SC_FR"] = (
        (df["FR20"] > 0) * 5
        + (df["FR60"] > 0) * 5
        + (df["RS"] > df["RS_EMA21"]) * 5
    )

    df["SC_VOL"] = (
        romp20_rec * 5
        + romp55_rec * 4
        + ((df["BB_LARG"] > df["BB_LARG"].shift(3)) & (close > df["BB_MID"])) * 3
        + (df["CHOP"] < 50) * 3
    )

    df["SCORE_INST"] = (
        df["SC_TEND"] + df["SC_FLUXO"] + df["SC_MOM"]
        + df["SC_FR"] + df["SC_VOL"]
    ).astype(int)

    # ========================================================
    # SCORE DE REVERSÃO (0-100)
    # ========================================================

    df["SCORE_REV"] = (
        df["ABSORCAO"] * 20
        + df["DIV_RSI"] * 20
        + df["HL10"] * 10
        + (df["CMF"] > 0) * 10
        + (df["OBV"] > df["OBV_MEDIA"]) * 10
        + df["ENFRAQUECIMENTO"] * 10
        + (df["MFI"] > df["MFI"].shift(1)) * 5
        + (df["FR20"] > 0) * 5
        + (close > df["VWAP_FUNDO"]) * 10
    ).astype(int)

    # ========================================================
    # CONTEXTO E EVENTOS
    # ========================================================

    queda_previa = (df["DD60"] <= -0.06) | recente(close < df["EMA50"], 15)
    queda_rec = recente(queda_previa, 20)
    df["QUEDA_REC"] = queda_rec

    # ---- Score próprio da reversão confirmada (0-100).
    # O score institucional é de TENDÊNCIA: numa reversão recente as
    # médias ainda estão desalinhadas e ele nunca chega a 70. Por isso
    # a confirmação usa componentes de virada, não de tendência madura.
    df["SCORE_CONF"] = (
        (close > df["EMA21"]) * 10
        + (close > df["EMA50"]) * 10
        + (df["EMA9"] > df["EMA21"]) * 5
        + (df["MACD_HIST"] > 0) * 10
        + (df["MACD_HIST"] > df["MACD_HIST"].shift(1)) * 5
        + (df["PLUS_DI"] > df["MINUS_DI"]) * 10
        + (df["CMF"] > 0) * 10
        + (df["OBV"] > df["OBV_MEDIA"]) * 5
        + (df["VOL_REL"] >= 1.2) * 10
        + df["ROMP10_REC"] * 10
        + romp20_rec * 5
        + (df["FR20"] > 0) * 5
        + (df["HL10"] | (close > df["VWAP_FUNDO"])) * 5
    ).astype(int)

    tendencia_alinhada = (
        (df["EMA21"] > df["EMA50"])
        & (df["EMA50"] > df["EMA200"])
        & (close > df["EMA21"])
        & (df["ADX"] >= 20)
    )

    score_ok = df["SCORE_INST"] >= score_minimo

    # Reversão confirmada: rompimento (10 pregões) com volume, após queda
    df["EVT_CONFIRMADA"] = (
        queda_rec
        & df["ROMP10_REC"]
        & (close > df["EMA21"])
        & (df["VOL_REL"] >= 1.0)
        & (df["SCORE_CONF"] >= score_minimo)
    )

    # Reversão inicial: estrutura + fluxo + momentum, ainda sem rompimento
    df["EVT_INICIAL"] = (
        (df["SCORE_REV"] >= score_minimo)
        & (df["ABSORCAO"] | df["DIV_RSI"] | df["HL10"])
        & df["MOMENTUM"]
        & queda_previa
        & ~df["EVT_CONFIRMADA"]
    )

    # Continuação: tendência alinhada, recuo até a EMA21 e retomada
    pullback = recente(df["Low"] <= df["EMA21"] * 1.01, 10)
    retomada = close > df["High"].shift(1).rolling(5).max()

    df["EVT_CONTINUACAO"] = (
        tendencia_alinhada
        & pullback
        & retomada
        & (df["VOL_REL"] >= 1.2)
        & score_ok
        & ~df["EVT_CONFIRMADA"]
    )

    # Tendência em andamento (estado, não evento)
    df["COND_TENDENCIA"] = (
        tendencia_alinhada
        & (df["PLUS_DI"] > df["MINUS_DI"])
        & (df["FR20"] > 0)
        & score_ok
    )

    # Preço esticado
    df["DIST_EMA21"] = (close - df["EMA21"]) / df["ATR"].replace(0, np.nan)

    df["ESTICADA"] = (
        (df["DIST_EMA21"] > ESTICADA_ATR)
        | (close / df["EMA21"] - 1 > ESTICADA_PCT)
        | (df["RSI"] > ESTICADA_RSI)
    )

    return df


def classificar_estado(df):
    """
    Retorna (estado, posição da primeira aparição do episódio).
    Eventos (reversão/continuação) só valem se ocorreram nos últimos
    DIAS_SINAL pregões; a data devolvida é a PRIMEIRA aparição do
    episódio, para não tratar continuação antiga como sinal novo.
    """

    n = len(df)
    ultimo = df.iloc[-1]

    def posicao_evento(coluna):
        flags = df[coluna].to_numpy(dtype=bool)
        recentes = np.flatnonzero(flags[-DIAS_SINAL:])

        if recentes.size == 0:
            return None

        pos_ultimo = n - DIAS_SINAL + int(recentes[-1])

        return inicio_episodio(flags, pos_ultimo)

    estado = "SEM SINAL"
    pos = None

    for coluna, nome in (
        ("EVT_CONFIRMADA", "REVERSÃO CONFIRMADA"),
        ("EVT_CONTINUACAO", "CONTINUAÇÃO"),
        ("EVT_INICIAL", "REVERSÃO INICIAL"),
    ):
        pos_evento = posicao_evento(coluna)

        if pos_evento is not None:
            estado = nome
            pos = pos_evento
            break

    if estado == "SEM SINAL" and bool(ultimo["COND_TENDENCIA"]):
        estado = "TENDÊNCIA EM ANDAMENTO"
        pos = inicio_episodio(
            df["COND_TENDENCIA"].to_numpy(dtype=bool), n - 1, gap=3
        )

    if bool(ultimo["ESTICADA"]) and estado in (
        "REVERSÃO CONFIRMADA",
        "CONTINUAÇÃO",
        "TENDÊNCIA EM ANDAMENTO",
    ):
        estado = "ESTICADA"

    return estado, pos


def motivos_confirmacao(df, score_minimo):
    """Lista o que impede a REVERSÃO CONFIRMADA no último pregão."""

    u = df.iloc[-1]
    faltas = []

    if not bool(u["QUEDA_REC"]):
        faltas.append("sem queda prévia")

    if not bool(u["ROMP10_REC"]):
        max10 = df["High"].iloc[-11:-1].max()
        faltas.append(
            f"sem rompimento 10d ({(u['Close'] / max10 - 1) * 100:+.1f}% da máx)"
        )

    if not u["Close"] > u["EMA21"]:
        faltas.append("abaixo da EMA21")

    if not u["VOL_REL"] >= 1.0:
        faltas.append(f"volume {u['VOL_REL']:.1f}x")

    if u["SCORE_CONF"] < score_minimo:
        faltas.append(f"score {int(u['SCORE_CONF'])}<{score_minimo}")

    return "; ".join(faltas) if faltas else "-"


# ============================================================
# DOWNLOAD E ORGANIZAÇÃO DOS DADOS
# ============================================================

def baixar_dados(ativos):

    tickers = [ativo + ".SA" for ativo in ativos]
    tickers.append(BENCHMARK + ".SA")

    print("\nBaixando histórico de mercado...")

    dados = yf.download(
        tickers=tickers,
        period=PERIODO_HISTORICO,
        interval=INTERVALO,
        auto_adjust=True,
        group_by="ticker",
        progress=False,
        threads=True
    )

    if dados is None or dados.empty:
        raise RuntimeError("Yahoo Finance não retornou dados.")

    return dados


def agora_brasil():
    """Hora atual em Brasília (servidores na nuvem costumam usar UTC)."""

    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        return datetime.now()


def remover_barra_parcial(df):
    """Descarta o candle do dia se o pregão ainda não encerrou."""

    if not DESCARTAR_BARRA_PARCIAL or df.empty:
        return df

    agora = agora_brasil()

    if (
        agora.weekday() < 5
        and agora.hour < 18
        and df.index[-1].date() == agora.date()
    ):
        return df.iloc[:-1]

    return df


def extrair_ativo(dados, ticker):

    if isinstance(dados.columns, pd.MultiIndex):

        nivel0 = dados.columns.get_level_values(0)
        nivel1 = dados.columns.get_level_values(1)

        if ticker in nivel0:
            df = dados[ticker].copy()
        elif ticker in nivel1:
            df = dados.xs(ticker, axis=1, level=1).copy()
        else:
            return None
    else:
        df = dados.copy()

    colunas = ["Open", "High", "Low", "Close", "Volume"]

    if not all(c in df.columns for c in colunas):
        return None

    df = df[colunas].copy()
    df = df.apply(pd.to_numeric, errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna()

    df = df[(df["Close"] > 0) & (df["Volume"] > 0)]

    indice = pd.to_datetime(df.index)

    if getattr(indice, "tz", None) is not None:
        indice = indice.tz_localize(None)

    df.index = indice.normalize()

    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    df = remover_barra_parcial(df)

    if len(df) < 220:
        return None

    return df


# ============================================================
# PROCESSAMENTO COMPLETO
# ============================================================

def executar_analise(score_minimo, callback=None):
    """
    Roda a varredura completa. Com 'callback(i, total, ativo, estado)'
    informa o progresso e devolve o resultado (uso web). Sem callback,
    publica na fila do Tkinter (uso desktop).
    """

    inicio = datetime.now()

    ativos = obter_acoes_mais_liquidas()
    dados = baixar_dados(ativos)

    base = extrair_ativo(dados, BENCHMARK + ".SA")

    if base is None:
        raise RuntimeError("Não foi possível obter o BOVA11.")

    benchmark = pd.DataFrame({
        "BOVA_CLOSE": base["Close"],
        "BOVA_RET20": base["Close"].pct_change(20),
        "BOVA_RET60": base["Close"].pct_change(60),
    })

    resultados = []
    graficos = {}
    smc_map = {}
    niveis_map = {}
    erros = []

    total = len(ativos)

    for i, ativo in enumerate(ativos, start=1):

        estado_progresso = "descartado"

        try:

            df = extrair_ativo(dados, ativo + ".SA")

            if df is None:
                erros.append(ativo)
                continue

            df = analisar_ativo(df, benchmark, score_minimo)

            atual = df.iloc[-1]

            preco = float(atual["Close"])
            atr = float(atual["ATR"])

            if not np.isfinite(preco) or not np.isfinite(atr) or atr <= 0:
                erros.append(ativo)
                continue

            estado, pos = classificar_estado(df)

            data_1a = "-"
            idade = np.nan

            if pos is not None:
                data_1a = df.index[pos].strftime("%d/%m/%Y")
                idade = len(df) - 1 - pos

            entrada = stop = alvo1 = alvo2 = risco_pct = np.nan

            if estado in ESTADOS_COM_NIVEIS:

                entrada = preco
                stop = entrada - ATR_STOP * atr

                if stop > 0:
                    risco = entrada - stop
                    alvo1 = entrada + risco * RISCO_RETORNO_1
                    alvo2 = entrada + risco * RISCO_RETORNO_2
                    risco_pct = risco / entrada * 100
                else:
                    entrada = stop = np.nan

            sr = None

            try:
                sr = analisar_niveis(df)
            except Exception as erro_sr:
                print(f"Suportes/resistências falharam em {ativo}: {erro_sr}")

            if sr is not None:
                niveis_map[ativo] = sr

            smc = None

            if estado in SMC_ESTADOS:
                try:
                    smc = analisar_smc(df, sr)
                except Exception as erro_smc:
                    print(f"SMC falhou em {ativo}: {erro_smc}")

            if smc is not None:
                smc_map[ativo] = smc

            alvos_smc = smc["alvos"] if smc else []

            def alvo_smc(posicao):
                if len(alvos_smc) > posicao:
                    return alvos_smc[posicao]["preco"]
                return np.nan

            resultados.append({
                "ATIVO": ativo,
                "PRECO": preco,
                "ESTADO": estado,
                "CLASSE": classificar_score(int(atual["SCORE_INST"])),
                "DATA_1A": data_1a,
                "IDADE": idade,
                "SC_INST": int(atual["SCORE_INST"]),
                "SC_REV": int(atual["SCORE_REV"]),
                "SC_CONF": int(atual["SCORE_CONF"]),
                "FALTA": (
                    "-" if estado == "REVERSÃO CONFIRMADA"
                    else motivos_confirmacao(df, score_minimo)
                ),
                "RSI": float(atual["RSI"]),
                "ADX": float(atual["ADX"]),
                "CMF": float(atual["CMF"]),
                "MFI": float(atual["MFI"]),
                "VOL_REL": float(atual["VOL_REL"]),
                "FR20": float(atual["FR20"]) * 100,
                "RET60": float(atual["RETORNO_60"]),
                "DIST_EMA21": float(atual["DIST_EMA21"]),
                "ENTRADA": entrada,
                "STOP": stop,
                "ALVO1": alvo1,
                "ALVO2": alvo2,
                "RISCO": risco_pct,
                "SR_POS": sr["posicao"] if sr else "-",
                "SR_S1": sr["S1"]["nivel"] if sr and sr["S1"] else np.nan,
                "SR_S1F": sr["S1"]["score"] if sr and sr["S1"] else np.nan,
                "SR_R1": sr["R1"]["nivel"] if sr and sr["R1"] else np.nan,
                "SR_R1F": sr["R1"]["score"] if sr and sr["R1"] else np.nan,
                "SR_ESP": sr["espaco_r1"] if sr else np.nan,
                "SR_RR": sr["rr"] if sr else np.nan,
                "SMC_CONF": f'{smc["confluencia"]}/5' if smc else "-",
                "SMC_ESTRUT": smc["estrutura"] if smc else "-",
                "SMC_LIQ": smc["liquidez"] if smc else "-",
                "SMC_ZONA": smc["zona"] if smc else "-",
                "SMC_T1": alvo_smc(0),
                "SMC_T2": alvo_smc(1),
                "SMC_T3": alvo_smc(2),
                "SMC_UP": smc["upside"] if smc else np.nan,
                "SMC_STOP": smc["stop"] if smc else np.nan,
                "SMC_RR": smc["rr1"] if smc else np.nan,
            })

            graficos[ativo] = df
            estado_progresso = estado

        except Exception as erro:
            erros.append(ativo)
            print(f"Erro em {ativo}: {erro}")

        finally:
            if callback is not None:
                callback(i, total, ativo, estado_progresso)
            else:
                fila_resultados.put(
                    ("progresso", i, total, ativo, estado_progresso)
                )

    if not resultados:
        raise RuntimeError("Nenhum ativo foi analisado com sucesso.")

    tabela = pd.DataFrame(resultados)

    # Ranking de força relativa dentro do universo (0-100)
    tabela["RANK_FR"] = tabela["RET60"].rank(pct=True) * 100

    tabela["ORDEM"] = tabela["ESTADO"].map(ORDEM_ESTADO).fillna(0)

    tabela = tabela.sort_values(
        by=["ORDEM", "SC_INST", "SC_REV"],
        ascending=False
    ).reset_index(drop=True)

    tempo = datetime.now() - inicio

    if callback is None:
        fila_resultados.put(
            ("finalizado", tabela, graficos, erros, tempo, smc_map,
             niveis_map)
        )

    return tabela, graficos, erros, tempo, smc_map, niveis_map


# ============================================================
# GRÁFICO
# ============================================================

def br(valor, casas=2):
    """Número no formato brasileiro (vírgula decimal)."""
    return f"{valor:.{casas}f}".replace(".", ",")


# ------------------------------------------------------------
# SMART MONEY CONCEPTS (candles diários)
# ------------------------------------------------------------

def detectar_pivos(h, l, k=SMC_K):
    """Pivôs (fractais): índice do candle; confirmados k candles depois."""

    altas, baixas = [], []

    for i in range(k, len(h) - k):

        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            altas.append(i)

        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            baixas.append(i)

    return altas, baixas


def mapear_estrutura(h, l, c, altas, baixas, k=SMC_K):
    """
    BOS: fechamento rompe o último pivô a favor da tendência.
    CHoCH: fechamento rompe o último pivô contra a tendência vigente.
    Só usa pivôs já confirmados (sem olhar o futuro).
    """

    conf_h = {i + k: i for i in altas}
    conf_l = {i + k: i for i in baixas}

    nivel_h = nivel_l = None
    tendencia = 0
    eventos = []

    for t in range(len(c)):

        if t in conf_h:
            nivel_h = (conf_h[t], h[conf_h[t]])

        if t in conf_l:
            nivel_l = (conf_l[t], l[conf_l[t]])

        if nivel_h is not None and c[t] > nivel_h[1]:

            eventos.append({
                "t": t,
                "tipo": "CHoCH" if tendencia == -1 else "BOS",
                "dir": "alta",
                "nivel": float(nivel_h[1]),
                "idx_nivel": nivel_h[0],
            })
            tendencia = 1
            nivel_h = None

        elif nivel_l is not None and c[t] < nivel_l[1]:

            eventos.append({
                "t": t,
                "tipo": "CHoCH" if tendencia == 1 else "BOS",
                "dir": "baixa",
                "nivel": float(nivel_l[1]),
                "idx_nivel": nivel_l[0],
            })
            tendencia = -1
            nivel_l = None

    return eventos, tendencia


def detectar_varreduras(h, l, c, baixas, k=SMC_K, janela=30):
    """
    Varredura de liquidez de venda (SSL): a mínima perfura um fundo
    anterior ainda intacto e o candle fecha de volta acima dele.
    """

    n = len(c)
    resultado = []

    for t in range(max(k + 1, n - janela), n):

        candidatos = [i for i in baixas if i + k < t and t - i <= 80]

        for i in reversed(candidatos[-4:]):

            nivel = l[i]
            fecho_min = c[i + 1:t].min() if t > i + 1 else np.inf

            if l[t] < nivel and c[t] > nivel and fecho_min > nivel:
                resultado.append({
                    "t": t,
                    "nivel": float(nivel),
                    "minima": float(l[t]),
                    "idx_nivel": i,
                })
                break

    return resultado


def detectar_desequilibrios(h, l, atr, ini):
    """FVG de 3 candles e gaps reais ainda não totalmente preenchidos."""

    n = len(h)
    zonas = []
    vistos = set()

    for i in range(max(2, ini), n):

        candidatos = []

        if l[i] > h[i - 2]:
            candidatos.append(("alta", h[i - 2], l[i], False))
        elif h[i] < l[i - 2]:
            candidatos.append(("baixa", h[i], l[i - 2], False))

        if l[i] > h[i - 1]:
            candidatos.append(("alta", h[i - 1], l[i], True))
        elif h[i] < l[i - 1]:
            candidatos.append(("baixa", h[i], l[i - 1], True))

        for tipo, base, topo, gap in candidatos:

            if not np.isfinite(atr[i]) or topo - base < 0.25 * atr[i]:
                continue

            if tipo == "alta":
                depois = l[i + 1:]
                minimo = depois.min() if depois.size else np.inf

                if minimo <= base:
                    continue

                base_ef, topo_ef = base, min(topo, minimo)

            else:
                depois = h[i + 1:]
                maximo = depois.max() if depois.size else -np.inf

                if maximo >= topo:
                    continue

                base_ef, topo_ef = max(base, maximo), topo

            chave = (tipo, round(base_ef, 2), round(topo_ef, 2))

            if chave in vistos:
                continue

            vistos.add(chave)

            zonas.append({
                "tipo": tipo,
                "base": float(base_ef),
                "topo": float(topo_ef),
                "idx": i - 1,
                "gap": gap,
            })

    return zonas


def detectar_order_blocks(o, h, l, c, eventos, altas, baixas, k, ini):
    """
    OB de baixa (oferta): último candle de alta antes da perna que
    rompeu a estrutura para baixo. OB de alta (demanda): o inverso.
    Descarta OBs invalidados por fechamento além da zona.
    """

    obs = []
    usados = set()

    for e in eventos:

        if e["t"] < ini:
            continue

        if e["dir"] == "baixa":

            pivos = [i for i in altas if i + k <= e["t"]]

            if not pivos:
                continue

            p = pivos[-1]
            cand = [j for j in range(p, max(p - 4, -1), -1) if c[j] > o[j]]

            if not cand or (cand[0], "baixa") in usados:
                continue

            j = cand[0]
            usados.add((j, "baixa"))

            if c[j + 1:].size and c[j + 1:].max() > h[j]:
                continue

            tocado = bool(h[j + 1:].size and h[j + 1:].max() >= l[j])

            obs.append({
                "tipo": "baixa", "base": float(l[j]), "topo": float(h[j]),
                "idx": j, "tocado": tocado,
            })

        else:

            pivos = [i for i in baixas if i + k <= e["t"]]

            if not pivos:
                continue

            p = pivos[-1]
            cand = [j for j in range(p, max(p - 4, -1), -1) if c[j] < o[j]]

            if not cand or (cand[0], "alta") in usados:
                continue

            j = cand[0]
            usados.add((j, "alta"))

            if c[j + 1:].size and c[j + 1:].min() < l[j]:
                continue

            tocado = bool(l[j + 1:].size and l[j + 1:].min() <= h[j])

            obs.append({
                "tipo": "alta", "base": float(l[j]), "topo": float(h[j]),
                "idx": j, "tocado": tocado,
            })

    return obs


def agrupar_alvos(candidatos, tolerancia):
    """Une níveis próximos; a força é a soma dos pesos (confluência)."""

    candidatos = sorted(candidatos, key=lambda x: x["preco"])
    grupos = []

    for item in candidatos:
        if grupos and item["preco"] - grupos[-1][-1]["preco"] <= tolerancia:
            grupos[-1].append(item)
        else:
            grupos.append([item])

    saida = []

    for g in grupos:
        principal = max(g, key=lambda x: (x["peso"], -x["preco"]))
        saida.append({
            "preco": float(principal["preco"]),
            "forca": float(sum(x["peso"] for x in g)),
            "tags": list(dict.fromkeys(x["tag"] for x in g)),
        })

    return saida


def analisar_smc(df, sr=None):
    """
    Lê estrutura, liquidez, desequilíbrios e zonas de oferta/demanda e
    projeta onde o preço tende a encontrar liquidez acima.
    São zonas de reação prováveis, não garantias.
    """

    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    atr = df["ATR"].to_numpy(dtype=float)

    n = len(df)
    preco = c[-1]
    atr_u = atr[-1]

    if n < 80 or not np.isfinite(atr_u) or atr_u <= 0:
        return None

    datas = df.index
    k = SMC_K
    ini = max(0, n - SMC_RECUO)

    def dt(i):
        return datas[i].strftime("%d/%m")

    altas, baixas = detectar_pivos(h, l, k)
    eventos, tendencia = mapear_estrutura(h, l, c, altas, baixas, k)
    varreduras = detectar_varreduras(h, l, c, baixas, k)
    zonas = detectar_desequilibrios(h, l, atr, ini)
    obs = detectar_order_blocks(o, h, l, c, eventos, altas, baixas, k, ini)

    # ---- Estrutura
    ultimo_ev = eventos[-1] if eventos else None

    if ultimo_ev is not None:
        seta = "↑" if ultimo_ev["dir"] == "alta" else "↓"
        estrutura_txt = f'{ultimo_ev["tipo"]} {seta} {dt(ultimo_ev["t"])}'
        idade_ev = n - 1 - ultimo_ev["t"]
    else:
        estrutura_txt = "-"
        idade_ev = None

    ev_alta_ok = (
        ultimo_ev is not None
        and ultimo_ev["dir"] == "alta"
        and idade_ev <= 15
    )

    # ---- Varredura de liquidez de venda
    varr = varreduras[-1] if varreduras else None
    idade_varr = (n - 1 - varr["t"]) if varr else None

    # ---- Range da perna de baixa (premium / discount)
    janela = min(60, n)
    il = n - janela + int(np.argmin(l[-janela:]))
    ini_perna = max(0, il - 80)
    ih = ini_perna + int(np.argmax(h[ini_perna:il + 1]))

    baixo_ref, alto_ref = float(l[il]), float(h[ih])
    rng = None

    if alto_ref - baixo_ref >= 2 * atr_u and ih < il:
        amp = alto_ref - baixo_ref
        rng = {
            "baixo": baixo_ref, "alto": alto_ref,
            "eq": baixo_ref + 0.5 * amp,
            "pct": (preco - baixo_ref) / amp,
            "idx_baixo": il, "idx_alto": ih,
        }

    if rng is None:
        zona_txt = "-"
    else:
        p = rng["pct"]
        rotulo = (
            "DISCOUNT" if p < 0.45
            else "EQUILÍBRIO" if p <= 0.55
            else "PREMIUM" if p <= 1.0
            else "ACIMA DO RANGE"
        )
        zona_txt = f"{rotulo} {p * 100:.0f}%"

    # ---- Liquidez de compra acima (BSL / EQH)
    topos = []

    for i in altas:
        if i < ini:
            continue
        p = h[i]
        if p > preco * 1.003 and (i + 1 >= n or h[i + 1:].max() <= p):
            topos.append((i, float(p)))

    tol_eq = max(0.25 * atr_u, 0.0015 * preco)
    topos.sort(key=lambda x: x[1])

    grupos_topos = []

    for i, p in topos:
        if grupos_topos and p - grupos_topos[-1][-1][1] <= tol_eq:
            grupos_topos[-1].append((i, p))
        else:
            grupos_topos.append([(i, p)])

    bsl = []
    candidatos = []

    for g in grupos_topos:
        preco_g = max(p for _, p in g)
        idx_g = min(i for i, _ in g)
        tag = "EQH" if len(g) >= 2 else "BSL"
        bsl.append({"idx": idx_g, "preco": preco_g, "tag": tag})
        candidatos.append({
            "preco": preco_g, "tag": tag, "peso": 4 if tag == "EQH" else 3
        })

    max60 = float(h[-60:].max())

    if max60 > preco * 1.003:
        candidatos.append({"preco": max60, "tag": "Máx 60d", "peso": 2})

    # ---- Gaps / FVG de baixa acima (ímãs) e de alta abaixo (suporte)
    zonas_vis = []

    for z in zonas:
        if z["tipo"] == "baixa":
            if preco < z["base"] <= preco + SMC_ALCANCE_ATR * atr_u:
                zonas_vis.append(z)
                candidatos.append({
                    "preco": z["base"],
                    "tag": "Gap↓" if z["gap"] else "FVG↓",
                    "peso": 2,
                })
        else:
            if z["base"] <= preco and preco - z["topo"] <= 6 * atr_u:
                zonas_vis.append(z)

    # ---- Order blocks
    obs_vis = []

    for b in obs:
        if b["tipo"] == "baixa":
            if preco < b["base"] <= preco + SMC_ALCANCE_ATR * atr_u:
                obs_vis.append(b)
                candidatos.append({
                    "preco": b["base"], "tag": "OB↓",
                    "peso": 2 if b["tocado"] else 3,
                })
        else:
            if b["base"] <= preco and preco - b["topo"] <= 6 * atr_u:
                obs_vis.append(b)

    # ---- Retração da perna de baixa e projeção 1:1
    if rng is not None:
        amp = rng["alto"] - rng["baixo"]

        for f, peso in ((0.5, 2), (0.618, 2), (0.786, 2), (1.0, 3)):
            candidatos.append({
                "preco": rng["baixo"] + f * amp,
                "tag": "Topo do range" if f == 1.0 else f"Fib {f:g}",
                "peso": peso,
            })

        evs_alta = [e for e in eventos if e["dir"] == "alta"]

        if evs_alta:
            e = evs_alta[-1]
            candidatos.append({
                "preco": e["nivel"] + (e["nivel"] - rng["baixo"]),
                "tag": "Proj 1:1", "peso": 1,
            })

    # ---- Resistências históricas (força >= 55) também são candidatas a alvo
    if sr:
        for r in sr["resistencias"]:
            if r["score"] >= 55:
                candidatos.append({
                    "preco": r["lo"], "tag": "Res.hist.",
                    "peso": 3 if r["score"] >= 70 else 2,
                })

    candidatos = [
        x for x in candidatos
        if preco * 1.005 <= x["preco"] <= preco + SMC_ALCANCE_ATR * atr_u
    ]

    grupos_alvo = agrupar_alvos(candidatos, 0.5 * atr_u)

    alvos = []
    fraco = False

    for g in [g for g in grupos_alvo if g["forca"] >= 3]:
        if not alvos or g["preco"] - alvos[-1]["preco"] >= 0.8 * atr_u:
            alvos.append(g)
        if len(alvos) == 3:
            break

    if not alvos and grupos_alvo:
        alvos = [grupos_alvo[0]]
        fraco = True

    # ---- Suportes e invalidação (stop SMC)
    obs_alta = [b for b in obs if b["tipo"] == "alta" and b["base"] < preco]
    zonas_alta = [z for z in zonas if z["tipo"] == "alta" and z["base"] < preco]

    suportes = []

    for b in obs_alta:
        if preco - b["topo"] <= 3 * atr_u:
            suportes.append(f'OB {br(b["base"])}–{br(b["topo"])}')

    for z in zonas_alta:
        if preco - z["topo"] <= 3 * atr_u:
            suportes.append(
                f'{"Gap" if z["gap"] else "FVG"} {br(z["base"])}–{br(z["topo"])}'
            )

    stop_smc = None
    origem_stop = ""

    if varr is not None and idade_varr <= 20:
        stop_smc = varr["minima"] - 0.2 * atr_u
        origem_stop = "abaixo da mínima da varredura"
    elif obs_alta:
        ob_ref = max(obs_alta, key=lambda b: b["idx"])
        stop_smc = ob_ref["base"] - 0.2 * atr_u
        origem_stop = "abaixo do order block de alta"
    else:
        fundos_abaixo = [l[i] for i in baixas if i >= ini and l[i] < preco]
        if fundos_abaixo:
            stop_smc = float(fundos_abaixo[-1]) - 0.2 * atr_u
            origem_stop = "abaixo do último fundo"

    if stop_smc is None or stop_smc >= preco or preco - stop_smc > 4 * atr_u:
        stop_smc = preco - ATR_STOP * atr_u
        origem_stop = "stop por ATR (estrutura distante ou ausente)"

    risco = preco - stop_smc

    for a in alvos:
        a["pct"] = (a["preco"] / preco - 1) * 100
        a["rr"] = (a["preco"] - preco) / risco if risco > 0 else np.nan

    # ---- Confluência SMC (5 verificações)
    verificacoes = [
        (ev_alta_ok, "estrutura virou para alta (BOS/CHoCH ≤ 15 pregões)"),
        (idade_varr is not None and idade_varr <= 25,
         "liquidez de venda varrida (≤ 25 pregões)"),
        (rng is not None and rng["pct"] <= 0.62,
         "preço em discount/equilíbrio do range"),
        (len(suportes) > 0, "OB/FVG de alta como suporte logo abaixo"),
        (bool(alvos) and np.isfinite(alvos[0]["rr"]) and alvos[0]["rr"] >= 1.5,
         "R:R ≥ 1,5 até o primeiro alvo"),
    ]

    confluencia = sum(1 for ok, _ in verificacoes if ok)

    # ---- Texto explicativo
    linhas = []

    if ultimo_ev is not None:
        sentido = "altista" if ultimo_ev["dir"] == "alta" else "baixista"
        linhas.append(
            f'Estrutura: {ultimo_ev["tipo"]} {sentido} em {dt(ultimo_ev["t"])} '
            f'(fechou além de {br(ultimo_ev["nivel"])}); '
            f'tendência atual: {"ALTA" if tendencia == 1 else "BAIXA"}.'
        )
    else:
        linhas.append("Estrutura: nenhum rompimento de pivô identificado.")

    if varr is not None:
        linhas.append(
            f'Liquidez de venda: varrida em {dt(varr["t"])} — mínima '
            f'{br(varr["minima"])} perfurou o fundo de {br(varr["nivel"])} e '
            f'o candle fechou acima (captura de stops).'
        )
    else:
        linhas.append("Liquidez de venda: sem varredura recente.")

    if bsl:
        niveis = ", ".join(f'{br(x["preco"])} ({x["tag"]})' for x in bsl[:3])
        linhas.append(f"Liquidez de compra acima: {niveis}.")

    zb = [z for z in zonas_vis if z["tipo"] == "baixa"][:2]

    if zb:
        linhas.append(
            "Gaps/FVG de baixa a preencher: "
            + " | ".join(f'{br(z["base"])}–{br(z["topo"])}' for z in zb) + "."
        )

    if rng is not None:
        linhas.append(
            f'Zona: {zona_txt} do range {br(rng["baixo"])}–{br(rng["alto"])} '
            f'(equilíbrio em {br(rng["eq"])}).'
        )

    if suportes:
        linhas.append("Suporte (demanda): " + " | ".join(suportes[:3]) + ".")

    if alvos:
        partes = [
            f'T{i} {br(a["preco"])} ({a["pct"]:+.1f}%, R {br(a["rr"], 1)}; '
            f'{"+".join(a["tags"])})'
            for i, a in enumerate(alvos, start=1)
        ]
        linhas.append(
            "Alvos prováveis: " + " → ".join(partes)
            + (" [confluência fraca]" if fraco else "") + "."
        )
        linhas.append(
            f'Alcance provável: até {br(alvos[-1]["preco"])} '
            f'({alvos[-1]["pct"]:+.1f}%). Sem liquidez mapeada além disso '
            f'em {SMC_ALCANCE_ATR} ATRs.'
        )
    else:
        linhas.append("Alvos: nenhuma liquidez mapeada acima do preço.")

    linhas.append(
        f"Invalidação: {br(stop_smc)} ({origem_stop}); "
        f"risco {risco / preco * 100:.1f}%."
    )

    linhas.append(
        f"Confluência SMC {confluencia}/5: "
        + "; ".join(
            ("✓ " if ok else "✗ ") + texto for ok, texto in verificacoes
        ) + "."
    )

    return {
        "estrutura": estrutura_txt,
        "liquidez": (
            (f"SSL varrida {dt(varr['t'])}" if varr else "sem varredura")
            + (f" → {bsl[0]['tag']} {br(bsl[0]['preco'])}" if bsl else "")
        ),
        "zona": zona_txt,
        "confluencia": confluencia,
        "alvos": alvos,
        "stop": float(stop_smc),
        "rr1": alvos[0]["rr"] if alvos else np.nan,
        "upside": alvos[-1]["pct"] if alvos else np.nan,
        "eventos": [e for e in eventos if e["t"] >= n - 120],
        "varredura": varr,
        "range": rng,
        "zonas": zonas_vis,
        "obs": obs_vis,
        "bsl": bsl,
        "linhas": linhas,
    }


# ------------------------------------------------------------
# SUPORTES E RESISTÊNCIAS (histórico + volume por preço + SMC)
# ------------------------------------------------------------

SMC_PTS = {
    "OB": 12, "OBt": 8, "FVG": 8, "Gap": 9,
    "EQH": 10, "EQL": 10, "BSL": 4, "SSL": 4,
    "BOS": 7, "CHoCH": 8,
}


def peso_idade(idade, meia_vida=SR_MEIA_VIDA):
    """Peso que decai com a idade (meia-vida em pregões); mínimo 0,25."""
    return max(0.25, 0.5 ** (max(idade, 0) / meia_vida))


def passo_redondo(preco):
    """Espaçamento dos números redondos (níveis psicológicos)."""

    if preco < 5:
        return 0.5
    if preco < 20:
        return 1.0
    if preco < 50:
        return 5.0
    if preco < 200:
        return 10.0
    return 50.0


def classe_nivel(score):

    if score >= 70:
        return "FORTE"
    if score >= 55:
        return "MÉDIO"
    return "FRACO"


def perfil_volume(h, l, v, n_bins=60):
    """
    Volume por faixa de preço: o volume de cada candle é distribuído de
    forma uniforme entre a mínima e a máxima. Devolve (bordas, densidade
    relativa à média) ou None.
    """

    baixo, alto = float(np.nanmin(l)), float(np.nanmax(h))

    if not alto > baixo:
        return None

    bordas = np.linspace(baixo, alto, n_bins + 1)
    vol = np.zeros(n_bins)

    ia = np.clip(np.searchsorted(bordas, l, side="right") - 1, 0, n_bins - 1)
    ib = np.clip(np.searchsorted(bordas, h, side="right") - 1, 0, n_bins - 1)

    for a, b, vv in zip(ia, ib, v):
        b = max(a, b)
        vol[a:b + 1] += vv / (b - a + 1)

    suave = np.convolve(vol, [0.25, 0.5, 0.25], mode="same")
    media = suave.mean()

    if not media > 0:
        return None

    return bordas, suave / media


def densidade_zona(perfil, lo, hi):
    """Densidade de volume média (1,0 = média do período) na zona."""

    if perfil is None:
        return 1.0

    bordas, dens = perfil
    passo = bordas[1] - bordas[0]
    centros = (bordas[:-1] + bordas[1:]) / 2

    dentro = (centros + passo / 2 >= lo) & (centros - passo / 2 <= hi)

    return float(dens[dentro].mean()) if dentro.any() else 0.0


def nos_de_volume(perfil, area=0.70):
    """POC, limites da área de valor (VAL/VAH) e nós de alto volume (HVN)."""

    bordas, dens = perfil
    centros = (bordas[:-1] + bordas[1:]) / 2

    poc = int(np.argmax(dens))
    total = dens.sum()
    a = b = poc
    soma = dens[poc]

    while soma < area * total and (a > 0 or b < len(dens) - 1):

        esq = dens[a - 1] if a > 0 else -1.0
        dir_ = dens[b + 1] if b < len(dens) - 1 else -1.0

        if esq >= dir_:
            a -= 1
            soma += dens[a]
        else:
            b += 1
            soma += dens[b]

    nos = [
        (float(centros[poc]), "POC"),
        (float(bordas[a]), "VAL"),
        (float(bordas[b + 1]), "VAH"),
    ]

    for i in range(1, len(dens) - 1):
        if (
            dens[i] >= 1.3
            and dens[i] > dens[i - 1]
            and dens[i] >= dens[i + 1]
            and abs(i - poc) > 2
        ):
            nos.append((float(centros[i]), "HVN"))

    return nos


def agrupar_niveis(cands, tol, largura_max):
    """Une candidatos próximos em zonas (largura limitada)."""

    cands = sorted(cands, key=lambda x: (x["lo"] + x["hi"]) / 2)
    grupos = []

    for c in cands:

        larg = c["hi"] - c["lo"]

        if grupos:

            g = grupos[-1]
            novo_lo = min(g["lo"], c["lo"])
            novo_hi = max(g["hi"], c["hi"])
            limite = max(largura_max, g["larg_max"], larg)

            if c["lo"] - g["hi"] <= tol and novo_hi - novo_lo <= limite:
                g["itens"].append(c)
                g["lo"], g["hi"] = novo_lo, novo_hi
                g["larg_max"] = max(g["larg_max"], larg)
                continue

        grupos.append({
            "lo": c["lo"], "hi": c["hi"], "larg_max": larg, "itens": [c]
        })

    return grupos


def medir_reacoes(h, l, c, atr, lo, hi, ini):
    """
    Testes históricos da zona [lo, hi]. Cada vez que o preço entra na
    zona (com folga de 0,25 ATR) conta um teste:
      - reagiu: afastou-se pelo menos 1 ATR da zona em até 5 pregões,
        sem fechar além dela por mais de 0,3 ATR;
      - quebrou: fechou além da zona por mais de 0,3 ATR.
    Testes mais recentes pesam mais.
    Devolve (R ponderado, B ponderado, testes, reações, quebras, índice
    do último teste).
    """

    n = len(c)
    pad = 0.25 * atr
    dentro = (h >= lo - pad) & (l <= hi + pad)

    R = B = 0.0
    testes = reacoes = quebras = 0
    ultimo = None

    t = max(ini, 1)

    while t < n - 1:

        if not dentro[t] or dentro[t - 1]:
            t += 1
            continue

        t0 = e = t

        while e + 1 < n and dentro[e + 1]:
            e += 1

        t = e + 1
        a = atr[t0]

        # episódio em curso ou sem pregões suficientes depois dele
        if e > n - 3 or not np.isfinite(a):
            continue

        fim = min(n - 1, e + 5)

        if c[t0 - 1] > hi:      # veio de cima: teste de suporte
            quebrou = bool((c[t0:fim + 1] < lo - 0.3 * a).any())
            reagiu = (not quebrou) and h[e + 1:fim + 1].max() >= hi + a
        else:                   # veio de baixo: teste de resistência
            quebrou = bool((c[t0:fim + 1] > hi + 0.3 * a).any())
            reagiu = (not quebrou) and l[e + 1:fim + 1].min() <= lo - a

        peso = peso_idade(n - 1 - t0)
        testes += 1
        ultimo = t0

        if reagiu:
            R += peso
            reacoes += 1
        elif quebrou:
            B += peso
            quebras += 1

    return R, B, testes, reacoes, quebras, ultimo


def analisar_niveis(df):
    """
    Suportes e resistências por confluência de:
      - histórico: pivôs (toques) e reações medidas no próprio preço;
      - volume por preço: POC, área de valor e nós de alto volume;
      - SMC: order blocks, FVG/gaps, EQH/EQL, liquidez e BOS/CHoCH;
      - extras: EMA 50/200, VWAP ancorada, máx/mín de 52 semanas e
        números redondos.
    Cada zona recebe uma força de 0 a 100: pivôs 25 + reações medidas 25
    + volume 15 + SMC 25 + confluências 10. Usa só dados até o último
    candle (pivôs só valem depois de confirmados).
    """

    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    v = np.nan_to_num(df["Volume"].to_numpy(dtype=float))
    atr = df["ATR"].to_numpy(dtype=float)

    n = len(df)
    preco = float(c[-1])
    atr_u = float(atr[-1])

    if n < 120 or not np.isfinite(atr_u) or atr_u <= 0:
        return None

    datas = df.index
    ini = max(1, n - SR_RECUO)
    alcance = SR_ALCANCE_ATR * atr_u
    tol_eq = max(0.25 * atr_u, 0.0015 * preco)

    cands = []

    def add(lo, hi, cat, tag, w=0.0, pts=0.0, idx=None):
        if np.isfinite(lo) and np.isfinite(hi) and hi >= lo:
            cands.append({
                "lo": float(lo), "hi": float(hi), "cat": cat,
                "tag": tag, "w": w, "pts": pts, "idx": idx,
            })

    # ---- Pivôs menores (k=3) e maiores (k=8): toques históricos
    altas3, baixas3 = detectar_pivos(h, l, SMC_K)
    altas8, baixas8 = detectar_pivos(h, l, 8)

    for altas, baixas, peso in (
        (altas3, baixas3, 1.0), (altas8, baixas8, 2.0)
    ):
        for i in altas:
            if i >= ini:
                add(h[i], h[i], "pivo", "pivô",
                    w=peso * peso_idade(n - 1 - i), idx=i)
        for i in baixas:
            if i >= ini:
                add(l[i], l[i], "pivo", "pivô",
                    w=peso * peso_idade(n - 1 - i), idx=i)

    # ---- SMC: estrutura, order blocks, FVG/gaps, liquidez
    eventos, _ = mapear_estrutura(h, l, c, altas3, baixas3, SMC_K)
    zonas = detectar_desequilibrios(h, l, atr, ini)
    obs = detectar_order_blocks(
        o, h, l, c, eventos, altas3, baixas3, SMC_K, ini
    )

    for b in obs:
        add(b["base"], b["topo"], "smc",
            "OB↑" if b["tipo"] == "alta" else "OB↓",
            pts=SMC_PTS["OBt"] if b["tocado"] else SMC_PTS["OB"])

    for z in zonas:
        rotulo = "Gap" if z["gap"] else "FVG"
        add(z["base"], z["topo"], "smc",
            rotulo + ("↑" if z["tipo"] == "alta" else "↓"),
            pts=SMC_PTS[rotulo])

    for e in eventos:
        if e["t"] >= ini:
            add(e["nivel"], e["nivel"], "smc",
                e["tipo"] + ("↑" if e["dir"] == "alta" else "↓"),
                pts=SMC_PTS[e["tipo"]])

    def grupos_iguais(indices, serie):
        ordenados = sorted(indices, key=lambda i: serie[i])
        grupos, atual = [], []
        for i in ordenados:
            if atual and serie[i] - serie[atual[-1]] > tol_eq:
                grupos.append(atual)
                atual = []
            atual.append(i)
        if atual:
            grupos.append(atual)
        return grupos

    for tag_eq, tag_li, indices, serie, topos in (
        ("EQH", "BSL", [i for i in altas3 if i >= ini], h, True),
        ("EQL", "SSL", [i for i in baixas3 if i >= ini], l, False),
    ):
        for g in grupos_iguais(indices, serie):

            precos = [float(serie[i]) for i in g]
            ultimo = max(g)

            if ultimo + 1 < n:
                varrido = (
                    h[ultimo + 1:].max() > max(precos) + 0.1 * atr_u
                    if topos else
                    l[ultimo + 1:].min() < min(precos) - 0.1 * atr_u
                )
            else:
                varrido = False

            if len(g) >= 2:
                add(min(precos), max(precos), "smc", tag_eq,
                    pts=SMC_PTS[tag_eq] * (0.6 if varrido else 1.0))
            elif not varrido:
                add(precos[0], precos[0], "smc", tag_li,
                    pts=SMC_PTS[tag_li])

    # ---- Volume por preço
    perfil = perfil_volume(h[ini:], l[ini:], v[ini:])

    if perfil is not None:
        for centro, rotulo in nos_de_volume(perfil):
            add(centro, centro, "vol", rotulo)

    # ---- Confluências extras (só reforçam zonas existentes)
    extras = [
        (float(df["EMA200"].iloc[-1]), "EMA200", 4),
        (float(df["EMA50"].iloc[-1]), "EMA50", 2),
        (float(h[-252:].max()), "Máx 52s", 3),
        (float(l[-252:].min()), "Mín 52s", 3),
    ]

    for coluna in ("VWAP_FUNDO", "VWAP_ROMP"):
        if coluna in df.columns and np.isfinite(df[coluna].iloc[-1]):
            extras.append((float(df[coluna].iloc[-1]), "VWAP anc.", 2))

    passo = passo_redondo(preco)
    redondo = np.floor((preco - alcance) / passo) * passo

    while redondo <= preco + alcance:
        if redondo > 0:
            extras.append((float(redondo), "Redondo", 2))
        redondo += passo

    # ---- Agrupamento e pontuação
    grupos = agrupar_niveis(cands, 0.5 * atr_u, 1.5 * atr_u)

    niveis = []

    for g in grupos:

        lo, hi = g["lo"], g["hi"]

        if hi < preco - alcance or lo > preco + alcance:
            continue

        itens = g["itens"]

        conf = {}
        for valor, tag, pts in extras:
            if lo - 0.3 * atr_u <= valor <= hi + 0.3 * atr_u:
                conf[tag] = max(conf.get(tag, 0), pts)

        pivos = [i for i in itens if i["cat"] == "pivo"]
        toques = len({i["idx"] for i in pivos})
        T = sum(i["w"] for i in pivos)

        R, B, testes, reacoes, quebras, ult = medir_reacoes(
            h, l, c, atr, lo, hi, ini
        )

        smc_pts = {}
        for i in itens:
            if i["cat"] == "smc":
                smc_pts[i["tag"]] = max(smc_pts.get(i["tag"], 0), i["pts"])

        dens = densidade_zona(perfil, lo, hi)

        partes = {
            "pivos": 25 * (1 - np.exp(-T / 2.2)),
            "reacoes": (
                25 * (1 - np.exp(-R / 1.6)) * (R + 0.5) / (R + B + 1)
            ),
            "volume": 15 * min(1.0, max(0.0, (dens - 1.0) / 1.2)),
            "smc": min(25.0, sum(smc_pts.values())),
            "confluencia": min(10.0, sum(conf.values())),
        }

        score = int(round(min(100.0, sum(partes.values()))))

        fontes = []

        for cat in ("smc", "vol"):
            for i in itens:
                if i["cat"] == cat and i["tag"] not in fontes:
                    fontes.append(i["tag"])

        if toques:
            fontes.append(f'{toques} pivô{"s" if toques > 1 else ""}')

        fontes += [t for t in conf if t not in fontes]

        niveis.append({
            "lo": float(lo), "hi": float(hi),
            "score": score, "classe": classe_nivel(score),
            "fontes": fontes,
            "toques": toques, "testes": testes,
            "reacoes": reacoes, "quebras": quebras,
            "ultimo": (
                datas[ult].strftime("%d/%m/%y") if ult is not None else "-"
            ),
            "partes": {k: round(float(x), 1) for k, x in partes.items()},
            "chave": False,
        })

    validos = [x for x in niveis if x["score"] >= SR_SCORE_MIN]

    dentro = [x for x in validos if x["lo"] <= preco <= x["hi"]]
    dentro = max(dentro, key=lambda x: x["score"]) if dentro else None

    def escolher(lista, distancia):

        escolhidos = []

        for x in sorted(lista, key=distancia):
            if (
                escolhidos
                and distancia(x) - distancia(escolhidos[-1]) < 0.6 * atr_u
            ):
                if x["score"] > escolhidos[-1]["score"]:
                    escolhidos[-1] = x
                continue
            escolhidos.append(x)

        escolhidos = escolhidos[:SR_QTD]

        if escolhidos:
            max(escolhidos, key=lambda x: x["score"])["chave"] = True

        return escolhidos

    suportes = escolher(
        [x for x in validos if x["hi"] < preco], lambda x: preco - x["hi"]
    )
    resistencias = escolher(
        [x for x in validos if x["lo"] > preco], lambda x: x["lo"] - preco
    )

    for x in suportes:
        x["lado"] = "S"
        x["nivel"] = x["hi"]
        x["dist_pct"] = (x["hi"] / preco - 1) * 100
        x["dist_atr"] = (preco - x["hi"]) / atr_u

    for x in resistencias:
        x["lado"] = "R"
        x["nivel"] = x["lo"]
        x["dist_pct"] = (x["lo"] / preco - 1) * 100
        x["dist_atr"] = (x["lo"] - preco) / atr_u

    s1 = suportes[0] if suportes else None
    r1 = resistencias[0] if resistencias else None

    espaco_r1 = r1["dist_pct"] if r1 else np.nan
    stop_sr = (s1["lo"] - 0.2 * atr_u) if s1 else np.nan
    risco = (preco - stop_sr) if s1 else np.nan

    rr = (
        (r1["lo"] - preco) / risco
        if r1 and s1 and risco > 0 else np.nan
    )

    # ---- Onde o preço está em relação aos níveis
    # (a pergunta que abre qualquer decisão: "preço no suporte? na
    # resistência? ou no meio do caminho?")
    candidatos_pos = []

    if dentro is not None:
        origem = float(c[max(0, len(c) - 6)])
        meio = (dentro["lo"] + dentro["hi"]) / 2

        if origem >= dentro["hi"]:
            papel = "NO SUPORTE"          # veio de cima: testa como suporte
        elif origem <= dentro["lo"]:
            papel = "NA RESISTÊNCIA"      # veio de baixo: testa como resistência
        else:
            papel = "NO SUPORTE" if preco >= meio else "NA RESISTÊNCIA"

        candidatos_pos.append((0.0, papel))

    if s1:
        candidatos_pos.append(((preco - s1["hi"]) / atr_u, "NO SUPORTE"))

    if r1:
        candidatos_pos.append(((r1["lo"] - preco) / atr_u, "NA RESISTÊNCIA"))

    candidatos_pos = [x for x in candidatos_pos if x[0] <= SR_PROX_ATR]

    if candidatos_pos:
        posicao = min(candidatos_pos, key=lambda x: x[0])[1]
    elif s1 or r1:
        posicao = "ENTRE NÍVEIS"
    else:
        posicao = "SEM NÍVEL"

    # ---- Texto
    def pct(valor):
        return f"{valor:+.1f}".replace(".", ",") + "%"

    def zona_txt(x):
        if x["hi"] - x["lo"] < 0.005:
            return br(x["lo"])
        return f'{br(x["lo"])}–{br(x["hi"])}'

    def linha_nivel(rotulo, x):
        reacao = (
            f'reagiu em {x["reacoes"]} de {x["testes"]} testes'
            if x["testes"] else "sem testes no período"
        )
        return (
            f'  {rotulo}{"★" if x["chave"] else ""} {zona_txt(x)} · '
            f'força {x["score"]} ({x["classe"]}) · {pct(x["dist_pct"])} '
            f'({br(x["dist_atr"], 1)} ATR) · {reacao} · '
            + " + ".join(x["fontes"][:5])
        )

    linhas = [
        f"Suportes e resistências (histórico + volume + SMC) | "
        f"preço {br(preco)} | ATR {br(atr_u)}"
    ]

    linhas.append(
        f"Posição do preço: {posicao} (considera até "
        f"{br(SR_PROX_ATR, 2)} ATR de distância de uma zona)."
    )

    if dentro is not None:
        linhas.append(
            f'Preço dentro da zona {zona_txt(dentro)} (força '
            f'{dentro["score"]}, {dentro["classe"]}): teste em andamento.'
        )

    linhas.append("Resistências (acima, da mais próxima à mais distante):")

    linhas += (
        [linha_nivel(f"R{i}", x) for i, x in enumerate(resistencias, 1)]
        or [f"  nenhuma zona com força ≥ {SR_SCORE_MIN} em "
            f"{SR_ALCANCE_ATR} ATRs"]
    )

    linhas.append("Suportes (abaixo, do mais próximo ao mais distante):")

    linhas += (
        [linha_nivel(f"S{i}", x) for i, x in enumerate(suportes, 1)]
        or [f"  nenhuma zona com força ≥ {SR_SCORE_MIN} em "
            f"{SR_ALCANCE_ATR} ATRs"]
    )

    if r1 and s1:
        linhas.append(
            f"Espaço até R1: {pct(espaco_r1)} · risco até abaixo de S1 "
            f"({br(stop_sr)}): {br(risco / preco * 100, 1)}% · R:R "
            f"{br(rr, 1)}"
        )

    linhas.append(
        "Força 0–100 = pivôs/toques 25 + reações medidas 25 + volume por "
        "preço 15 + SMC 25 + confluências 10. ★ = mais forte do lado."
    )

    return {
        "preco": preco,
        "atr": atr_u,
        "suportes": suportes,
        "resistencias": resistencias,
        "dentro": dentro,
        "posicao": posicao,
        "todos": validos,
        "S1": s1,
        "R1": r1,
        "espaco_r1": espaco_r1,
        "stop_sr": stop_sr,
        "rr": rr,
        "linhas": linhas,
    }


def desenhar_niveis(ax, sr, n, t, ylim=None):
    """
    Zonas de suporte (verde) e resistência (vermelho) no gráfico.

    Feito para funcionar com zoom e arraste (estilo TradingView): as
    zonas e linhas atravessam toda a largura visível, e os rótulos ficam
    presos à borda esquerda da janela (x em coordenadas do eixo, y em
    preço), então continuam à vista ao ampliar ou deslocar o gráfico.
    Níveis fora da faixa vertical atual são desenhados mesmo assim (e
    cortados pela moldura), para aparecerem quando o usuário rolar.
    """

    atr = sr["atr"]
    ancora = ax.get_yaxis_transform()   # x: fração do eixo | y: preço

    for lado, lista, cor in (
        ("S", sr["suportes"], t["alta"]),
        ("R", sr["resistencias"], t["baixa"]),
    ):
        for i, x in enumerate(lista, start=1):

            ax.axhspan(
                x["lo"] - 0.08 * atr, x["hi"] + 0.08 * atr,
                color=cor, alpha=0.07 + 0.12 * x["score"] / 100,
                linewidth=0, zorder=0
            )
            ax.axhline(
                x["nivel"], color=cor, linestyle=(0, (6, 3)),
                linewidth=1.4 if x["score"] >= 70 else 0.9,
                alpha=0.9, zorder=2
            )
            ax.text(
                0.006, x["nivel"],
                f'{lado}{i}{"★" if x["chave"] else ""} '
                f'{br(x["nivel"])} · {x["score"]}',
                transform=ancora, clip_on=True,
                color=cor, fontsize=7.5, ha="left",
                va="bottom" if lado == "R" else "top",
                fontweight="bold", zorder=6,
                bbox=dict(
                    boxstyle="round,pad=0.15", fc=t["painel"],
                    ec="none", alpha=0.75
                )
            )


def validar_niveis(df, passo=10, horizonte=10, minimo=260, semente=0):
    """
    Teste walk-forward dos níveis: em cada data (passo em passo) calcula
    os níveis só com dados até ali e observa os próximos 'horizonte'
    pregões. No primeiro toque, "reagiu" = afastou-se 1 ATR em até 5
    pregões sem fechar além da zona por 0,3 ATR; "quebrou" = fechou além.
    Compara FORTE/MÉDIO/FRACO com níveis de CONTROLE sorteados à mesma
    distância. Se os fortes não reagirem mais que o controle, os pesos
    do score não têm valor preditivo.
    """

    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    atr = df["ATR"].to_numpy(dtype=float)

    n = len(df)
    rng = np.random.default_rng(semente)

    estat = {
        g: {"niveis": 0, "tocou": 0, "reagiu": 0, "quebrou": 0}
        for g in ("FORTE", "MÉDIO", "FRACO", "CONTROLE")
    }

    def avaliar(lo, hi, lado, t):

        a = atr[t]
        fim = min(n - 1, t + horizonte)

        for j in range(t + 1, fim + 1):

            if (lado == "R" and h[j] >= lo) or (lado == "S" and l[j] <= hi):

                f2 = min(n - 1, j + 5)

                if lado == "R":
                    quebrou = bool((c[j:f2 + 1] > hi + 0.3 * a).any())
                    reagiu = (not quebrou) and l[j + 1:f2 + 1].min() <= lo - a
                else:
                    quebrou = bool((c[j:f2 + 1] < lo - 0.3 * a).any())
                    reagiu = (not quebrou) and h[j + 1:f2 + 1].max() >= hi + a

                return "reagiu" if reagiu else (
                    "quebrou" if quebrou else "neutro"
                )

        return None

    def registrar(grupo, resultado):
        e = estat[grupo]
        e["niveis"] += 1
        if resultado is not None:
            e["tocou"] += 1
            if resultado in ("reagiu", "quebrou"):
                e[resultado] += 1

    for t in range(minimo, n - horizonte - 6, passo):

        sr = analisar_niveis(df.iloc[:t + 1])

        if sr is None:
            continue

        for lado, lista in (
            ("S", sr["suportes"]), ("R", sr["resistencias"])
        ):
            for x in lista:
                registrar(x["classe"], avaliar(x["lo"], x["hi"], lado, t))

        a = atr[t]

        # Controle: mesma largura de zona dos níveis reais (zonas largas
        # são mais difíceis de "quebrar", e isso não pode favorecer o
        # score) e distância sorteada entre 0,5 e 8 ATRs.
        larguras = [x["hi"] - x["lo"] for x in sr["todos"]] or [0.4 * a]

        for lado in ("S", "R"):
            for _ in range(3):
                dist = rng.uniform(0.5, 8.0) * a
                larg = float(rng.choice(larguras))
                centro = c[t] - dist if lado == "S" else c[t] + dist
                registrar(
                    "CONTROLE",
                    avaliar(centro - larg / 2, centro + larg / 2, lado, t)
                )

    return estat


def resumir_validacao(estat):
    """Linhas de texto com as taxas de cada grupo."""

    linhas = [
        f'{"grupo":<10}{"níveis":>8}{"tocados":>9}'
        f'{"% reação (±95%)":>20}{"% quebra":>11}'
    ]

    for g in ("FORTE", "MÉDIO", "FRACO", "CONTROLE"):

        e = estat[g]
        toc = e["tocou"]

        if toc:
            p = e["reagiu"] / toc
            erro = 1.96 * np.sqrt(p * (1 - p) / toc)
            taxa_r = f'{p * 100:.0f}% ±{erro * 100:.0f}'
            taxa_q = f'{e["quebrou"] / toc * 100:.0f}%'
        else:
            taxa_r = taxa_q = "-"

        linhas.append(
            f'{g:<10}{e["niveis"]:>8}{toc:>9}{taxa_r:>20}{taxa_q:>11}'
        )

    return linhas


def desenhar_smc(ax, smc, n, deslocamento, t):
    """Sobrepõe os conceitos SMC ao gráfico de preço."""

    cor = t["smc"]

    def x(i):
        return i - deslocamento

    r = smc["range"]

    if r is not None:
        ax.axhspan(r["eq"], r["alto"], color=t["baixa"], alpha=0.04, zorder=0)
        ax.axhspan(r["baixo"], r["eq"], color=t["alta"], alpha=0.04, zorder=0)
        ax.hlines(
            r["eq"], 0, n + 1, colors=cor, linestyles="-.",
            linewidth=0.8, alpha=0.7, zorder=2
        )
        ax.text(0.5, r["eq"], "EQ 50%", color=cor, fontsize=7, va="bottom")

    for z in smc["zonas"]:
        x0 = max(0, x(z["idx"]))
        if x0 >= n:
            continue
        cor_z = t["alta"] if z["tipo"] == "alta" else t["baixa"]
        ax.fill_between(
            [x0, n + 1], [z["base"]] * 2, [z["topo"]] * 2,
            color=cor_z, alpha=0.16, zorder=1
        )
        ax.text(
            x0 + 0.3, z["topo"], "Gap" if z["gap"] else "FVG",
            color=cor_z, fontsize=6.5, va="top"
        )

    for b in smc["obs"]:
        x0 = max(0, x(b["idx"]))
        if x0 >= n:
            continue
        cor_b = t["alta"] if b["tipo"] == "alta" else t["baixa"]
        ax.fill_between(
            [x0, n + 1], [b["base"]] * 2, [b["topo"]] * 2,
            color=cor_b, alpha=0.24, zorder=1
        )
        ax.text(
            x0 + 0.3, b["base"], "OB↑" if b["tipo"] == "alta" else "OB↓",
            color=cor_b, fontsize=6.5, va="bottom", fontweight="bold"
        )

    for nv in smc["bsl"]:
        x0 = max(0, x(nv["idx"]))
        if x0 >= n:
            continue
        ax.hlines(
            nv["preco"], x0, n + 1, colors=cor, linestyles="--",
            linewidth=0.9, zorder=2
        )
        ax.text(
            x0, nv["preco"], f'{nv["tag"]} {br(nv["preco"])}',
            color=cor, fontsize=6.5, va="bottom"
        )

    for e in smc["eventos"]:
        xe = x(e["t"])
        if xe < 1:
            continue
        x_ini = max(0, x(e["idx_nivel"]))
        cor_e = t["alta"] if e["dir"] == "alta" else t["baixa"]
        ax.hlines(
            e["nivel"], x_ini, xe, colors=cor_e,
            linestyles=":", linewidth=1.0, zorder=3
        )
        ax.text(
            (x_ini + xe) / 2, e["nivel"],
            f'{e["tipo"]}{"↑" if e["dir"] == "alta" else "↓"}',
            color=cor_e, fontsize=7, ha="center",
            va="bottom" if e["dir"] == "alta" else "top",
            fontweight="bold"
        )

    v = smc["varredura"]

    if v is not None and x(v["t"]) >= 0:
        ax.scatter(
            [x(v["t"])], [v["minima"]], marker="X", s=60,
            color=t["vol_dest"], edgecolors=t["fg"], linewidths=0.5,
            zorder=7
        )
        ax.text(
            x(v["t"]), v["minima"], " SSL sweep", color=t["vol_dest"],
            fontsize=7, va="top"
        )

    for i, a in enumerate(smc["alvos"], start=1):
        ax.hlines(
            a["preco"], max(0, n - 25), n + 1, colors=cor,
            linestyles="-", linewidth=1.3, zorder=5
        )
        ax.text(
            max(0, n - 25), a["preco"],
            f'SMC T{i} {br(a["preco"])} ({a["pct"]:+.1f}%)',
            color=cor, fontsize=7.5, va="bottom", fontweight="bold"
        )

    ax.hlines(
        smc["stop"], max(0, n - 25), n + 1, colors=cor,
        linestyles="-", linewidth=1.0, alpha=0.8, zorder=5
    )
    ax.text(
        max(0, n - 25), smc["stop"], f'SMC stop {br(smc["stop"])}',
        color=cor, fontsize=7.5, va="top"
    )


# ============================================================
# GRÁFICO
# ============================================================

def desenhar_grafico(figura, df_completo, linha, tema, periodo, smc=None,
                     sr=None):

    t = TEMAS[tema]

    df = df_completo.tail(periodo)
    n = len(df)
    x = np.arange(n)

    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)

    figura.clear()
    figura.patch.set_facecolor(t["bg"])

    eixos = figura.subplots(
        6, 1, sharex=True,
        gridspec_kw={
            "height_ratios": [5, 1.1, 1.1, 1.1, 1.1, 1.1],
            "hspace": 0.06
        }
    )

    figura.subplots_adjust(left=0.05, right=0.985, top=0.94, bottom=0.07)

    ax_p, ax_v, ax_rsi, ax_macd, ax_adx, ax_fl = eixos

    for ax in eixos:
        ax.set_facecolor(t["painel"])
        ax.tick_params(colors=t["fg"], labelsize=8)
        ax.grid(True, color=t["grade"], alpha=0.5, linewidth=0.5)
        for borda in ax.spines.values():
            borda.set_color(t["grade"])

    # ---- Candles
    cores = np.where(c >= o, t["alta"], t["baixa"])
    corpo = np.maximum(np.abs(c - o), np.nanmean(c) * 0.0005)

    ax_p.vlines(x, l, h, colors=cores, linewidth=0.8, zorder=2)
    ax_p.bar(
        x, corpo, bottom=np.minimum(o, c),
        width=0.6, color=cores, zorder=3
    )

    # ---- Bollinger (sombra discreta), EMAs e VWAPs ancoradas
    ax_p.fill_between(
        x, df["BB_LOW"].to_numpy(dtype=float),
        df["BB_UP"].to_numpy(dtype=float),
        color=t["bb"], alpha=0.08, zorder=1
    )

    for coluna, cor, rotulo in (
        ("EMA9", t["ema9"], "EMA 9"),
        ("EMA21", t["ema21"], "EMA 21"),
        ("EMA50", t["ema50"], "EMA 50"),
        ("EMA200", t["ema200"], "EMA 200"),
    ):
        ax_p.plot(
            x, df[coluna].to_numpy(dtype=float),
            color=cor, linewidth=1.0, label=rotulo, zorder=4
        )

    if df["VWAP_FUNDO"].notna().any():
        ax_p.plot(
            x, df["VWAP_FUNDO"].to_numpy(dtype=float),
            color=t["vwap_fundo"], linewidth=1.3, linestyle="--",
            label="VWAP anc. (fundo)", zorder=4
        )

    if df["VWAP_ROMP"].notna().any():
        ax_p.plot(
            x, df["VWAP_ROMP"].to_numpy(dtype=float),
            color=t["vwap_romp"], linewidth=1.3, linestyle="--",
            label="VWAP anc. (rompimento)", zorder=4
        )

    # ---- Marcadores históricos de sinais
    marcadores = (
        ("EVT_CONFIRMADA", t["alta"], "^", "Confirmada"),
        ("EVT_CONTINUACAO", t["ema21"], "^", "Continuação"),
        ("EVT_INICIAL", t["vol_dest"], "^", "Inicial"),
    )

    faixa = np.nanmax(h) - np.nanmin(l)

    for coluna, cor, marca, rotulo in marcadores:
        idx = np.flatnonzero(df[coluna].to_numpy(dtype=bool))
        if idx.size:
            ax_p.scatter(
                idx, l[idx] - faixa * 0.03, marker=marca, s=55,
                color=cor, edgecolors=t["fg"], linewidths=0.5,
                label=rotulo, zorder=6
            )

    # ---- Entrada, stop e alvos
    if linha is not None:
        niveis = (
            ("ENTRADA", t["entrada"], "ENTRADA"),
            ("STOP", t["stop"], "STOP"),
            ("ALVO1", t["alvo"], "ALVO 1"),
            ("ALVO2", t["alvo"], "ALVO 2"),
        )

        for chave, cor, rotulo in niveis:
            valor = linha.get(chave)

            if valor is not None and np.isfinite(valor):
                ax_p.hlines(
                    valor, max(0, n - 25), n + 1,
                    colors=cor, linestyles=":", linewidth=1.2, zorder=5
                )
                ax_p.text(
                    n + 1.3, valor, f"{rotulo} {valor:.2f}",
                    color=cor, fontsize=7.5, va="center"
                )

    if smc:
        desenhar_smc(ax_p, smc, n, len(df_completo) - n, t)

    # Suportes/resistências não alteram a escala vertical do gráfico:
    # só aparecem as zonas dentro da faixa de preço já visível.
    if sr:
        ylim_antes = ax_p.get_ylim()
        desenhar_niveis(ax_p, sr, n, t, ylim_antes)
        ax_p.set_ylim(ylim_antes)

    ax_p.set_xlim(-1, n + 9)
    ax_p.set_ylabel("Preço (R$)", color=t["fg"], fontsize=8)

    ax_p.legend(
        loc="upper left", fontsize=7, ncol=5,
        facecolor=t["painel"], edgecolor=t["grade"], labelcolor=t["fg"]
    )

    if linha is not None:
        titulo = (
            f'{linha["ATIVO"]}  |  {linha["ESTADO"]}  |  '
            f'Score {linha["SC_INST"]} ({linha["CLASSE"]})  |  '
            f'Reversão {linha["SC_REV"]}'
        )
    else:
        titulo = ""

    ax_p.set_title(titulo, color=t["fg"], fontsize=11, loc="left")

    # ---- Volume (destaque acima da média)
    vol = df["Volume"].to_numpy(dtype=float)
    vol_rel = df["VOL_REL"].to_numpy(dtype=float)
    destaque = np.nan_to_num(vol_rel) >= VOLUME_RELATIVO_MIN

    cores_vol = [
        to_rgba(cor, 1.0 if d else 0.45)
        for cor, d in zip(cores, destaque)
    ]
    bordas = [t["vol_dest"] if d else "none" for d in destaque]

    ax_v.bar(
        x, vol, width=0.7, color=cores_vol,
        edgecolor=bordas, linewidth=0.8
    )
    ax_v.plot(
        x, df["VOL_MEDIA"].to_numpy(dtype=float),
        color=t["fg"], linewidth=0.9
    )
    ax_v.yaxis.set_major_formatter(
        FuncFormatter(lambda valor, _: f"{valor / 1e6:.0f}M")
    )
    ax_v.set_ylabel("Volume", color=t["fg"], fontsize=8)

    # ---- RSI
    ax_rsi.plot(
        x, df["RSI"].to_numpy(dtype=float), color=t["ema21"], linewidth=1.0
    )
    for nivel in (30, 70):
        ax_rsi.axhline(nivel, color=t["suave"], linewidth=0.6, linestyle="--")
    ax_rsi.axhline(50, color=t["grade"], linewidth=0.6)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", color=t["fg"], fontsize=8)

    # ---- MACD
    hist = df["MACD_HIST"].to_numpy(dtype=float)
    cores_hist = np.where(hist >= 0, t["alta"], t["baixa"])

    ax_macd.bar(x, hist, width=0.7, color=cores_hist, alpha=0.6)
    ax_macd.plot(
        x, df["MACD"].to_numpy(dtype=float), color=t["ema21"], linewidth=1.0
    )
    ax_macd.plot(
        x, df["MACD_SIGNAL"].to_numpy(dtype=float),
        color=t["ema9"], linewidth=1.0
    )
    ax_macd.set_ylabel("MACD", color=t["fg"], fontsize=8)

    # ---- ADX / DI
    ax_adx.plot(
        x, df["ADX"].to_numpy(dtype=float), color=t["fg"], linewidth=1.2
    )
    ax_adx.plot(
        x, df["PLUS_DI"].to_numpy(dtype=float), color=t["alta"], linewidth=0.9
    )
    ax_adx.plot(
        x, df["MINUS_DI"].to_numpy(dtype=float), color=t["baixa"], linewidth=0.9
    )
    ax_adx.axhline(20, color=t["suave"], linewidth=0.6, linestyle="--")
    ax_adx.set_ylabel("ADX/DI", color=t["fg"], fontsize=8)

    # ---- Fluxo de volume: CMF (barras) e OBV normalizado (linha)
    cmf = df["CMF"].to_numpy(dtype=float)
    ax_fl.bar(
        x, cmf, width=0.7,
        color=np.where(np.nan_to_num(cmf) >= 0, t["alta"], t["baixa"]),
        alpha=0.6
    )

    obv = df["OBV"].to_numpy(dtype=float)
    amplitude_obv = np.nanmax(obv) - np.nanmin(obv)

    if amplitude_obv > 0:
        obv_norm = (obv - np.nanmin(obv)) / amplitude_obv * 0.8 - 0.4
        ax_fl.plot(x, obv_norm, color=t["ema9"], linewidth=1.0)

    ax_fl.axhline(0, color=t["suave"], linewidth=0.6)
    ax_fl.set_ylabel("CMF/OBV", color=t["fg"], fontsize=8)

    # ---- Eixo X com datas
    passo = max(1, n // 10)
    ticks = np.arange(0, n, passo)

    ax_fl.set_xticks(ticks)
    ax_fl.set_xticklabels(
        [df.index[i].strftime("%d/%m/%y") for i in ticks]
    )


# ============================================================
# INTERFACE GRÁFICA
# ============================================================

COLUNAS = [
    "ATIVO", "PRECO", "ESTADO", "CLASSE", "DATA_1A", "IDADE",
    "SC_INST", "SC_REV", "SC_CONF", "RSI", "ADX", "CMF", "MFI", "VOL_REL",
    "FR20", "RANK_FR", "DIST_EMA21",
    "ENTRADA", "STOP", "ALVO1", "ALVO2", "RISCO",
    "SR_POS", "SR_S1", "SR_S1F", "SR_R1", "SR_R1F", "SR_ESP", "SR_RR",
    "SMC_CONF", "SMC_ESTRUT", "SMC_LIQ", "SMC_ZONA",
    "SMC_T1", "SMC_T2", "SMC_T3", "SMC_UP", "SMC_STOP", "SMC_RR",
    "FALTA"
]

TITULOS = {
    "ATIVO": "ATIVO", "PRECO": "PREÇO", "ESTADO": "ESTADO",
    "CLASSE": "CLASSE DO SCORE", "DATA_1A": "1ª APARIÇÃO",
    "IDADE": "IDADE (pregões)", "SC_INST": "SCORE INST.",
    "SC_REV": "SCORE REV.", "RSI": "RSI", "ADX": "ADX", "CMF": "CMF",
    "MFI": "MFI", "VOL_REL": "VOL REL", "FR20": "FR 20d (%)",
    "RANK_FR": "RANK FR", "DIST_EMA21": "DIST EMA21 (ATR)",
    "ENTRADA": "ENTRADA", "STOP": "STOP", "ALVO1": "ALVO 1",
    "ALVO2": "ALVO 2", "RISCO": "RISCO %",
    "SC_CONF": "SCORE CONF.", "FALTA": "FALTA P/ CONFIRMAR REVERSÃO",
    "SMC_CONF": "SMC CONFL.", "SMC_ESTRUT": "SMC ESTRUTURA",
    "SMC_LIQ": "SMC LIQUIDEZ", "SMC_ZONA": "SMC ZONA",
    "SMC_T1": "SMC ALVO 1", "SMC_T2": "SMC ALVO 2", "SMC_T3": "SMC ALVO 3",
    "SMC_UP": "SMC ALCANCE %", "SMC_STOP": "SMC STOP", "SMC_RR": "SMC R:R T1",
    "SR_POS": "PREÇO vs S/R",
    "SR_S1": "SUPORTE 1", "SR_S1F": "FORÇA S1",
    "SR_R1": "RESISTÊNCIA 1", "SR_R1F": "FORÇA R1",
    "SR_ESP": "ESPAÇO ATÉ R1 %", "SR_RR": "R:R S/R",
}

CASAS = {
    "PRECO": 2, "IDADE": 0, "SC_INST": 0, "SC_REV": 0, "SC_CONF": 0,
    "RSI": 1,
    "ADX": 1, "CMF": 3, "MFI": 1, "VOL_REL": 2, "FR20": 1,
    "RANK_FR": 0, "DIST_EMA21": 2, "ENTRADA": 2, "STOP": 2,
    "ALVO1": 2, "ALVO2": 2, "RISCO": 1,
    "SMC_T1": 2, "SMC_T2": 2, "SMC_T3": 2, "SMC_UP": 1,
    "SMC_STOP": 2, "SMC_RR": 1,
    "SR_S1": 2, "SR_S1F": 0, "SR_R1": 2, "SR_R1F": 0,
    "SR_ESP": 1, "SR_RR": 1,
}


def formatar(valor, casas):

    if valor is None:
        return "-"

    try:
        if not np.isfinite(valor):
            return "-"
    except TypeError:
        return str(valor)

    return f"{valor:.{casas}f}"


class ScannerB3:

    def __init__(self, root):

        self.root = root
        self.root.title("SCANNER B3 - INSTITUCIONAL QUANT")
        self.root.geometry("1600x980")
        self.root.minsize(1150, 760)

        self.resultados = None
        self.linhas = {}
        self.graficos = {}
        self.smc = {}
        self.niveis = {}
        self.ativo_atual = None
        self.thread_ativa = False
        self.tema = "escuro"
        self.ordem_reversa = {}

        self.estilo = ttk.Style()
        self.estilo.theme_use("clam")

        self.criar_interface()
        self.aplicar_tema()
        self.verificar_fila()

    # --------------------------------------------------------
    # CONSTRUÇÃO DA INTERFACE
    # --------------------------------------------------------

    def criar_interface(self):

        topo = ttk.Frame(self.root, padding=(10, 8))
        topo.pack(fill="x")

        ttk.Label(
            topo, text="SCANNER B3 - INSTITUCIONAL QUANT",
            font=("Arial", 17, "bold")
        ).pack(side="left", padx=10)

        ttk.Label(topo, text="Score mínimo:").pack(side="left", padx=(25, 5))

        self.score_var = tk.StringVar(value="70")
        ttk.Combobox(
            topo, textvariable=self.score_var, values=["70", "85"],
            width=5, state="readonly"
        ).pack(side="left")

        ttk.Label(topo, text="Gráfico (pregões):").pack(
            side="left", padx=(20, 5)
        )

        self.periodo_var = tk.StringVar(value="120")
        combo_periodo = ttk.Combobox(
            topo, textvariable=self.periodo_var,
            values=["60", "120", "250"], width=5, state="readonly"
        )
        combo_periodo.pack(side="left")
        combo_periodo.bind(
            "<<ComboboxSelected>>", lambda e: self.desenhar()
        )

        self.somente_sinais = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            topo, text="Somente com sinal",
            variable=self.somente_sinais,
            command=self.exibir_resultados
        ).pack(side="left", padx=(20, 5))

        self.mostrar_smc = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            topo, text="SMC no gráfico",
            variable=self.mostrar_smc,
            command=self.desenhar
        ).pack(side="left", padx=(5, 5))

        self.mostrar_sr = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            topo, text="Suportes/resistências no gráfico",
            variable=self.mostrar_sr,
            command=self.desenhar
        ).pack(side="left", padx=(5, 20))

        self.botao = ttk.Button(
            topo, text="VARRER A B3", command=self.iniciar_scanner
        )
        self.botao.pack(side="left", padx=5)

        self.botao_tema = ttk.Button(
            topo, text="Tema claro", command=self.alternar_tema
        )
        self.botao_tema.pack(side="left", padx=5)

        linha_status = ttk.Frame(self.root, padding=(15, 0))
        linha_status.pack(fill="x")

        self.status = tk.StringVar(value="Aguardando início da análise.")
        ttk.Label(linha_status, textvariable=self.status).pack(side="left")

        self.progresso = ttk.Progressbar(self.root, mode="determinate")
        self.progresso.pack(fill="x", padx=15, pady=5)

        principal = ttk.PanedWindow(self.root, orient="vertical")
        principal.pack(fill="both", expand=True, padx=10, pady=5)

        quadro_tabela = ttk.Frame(principal)
        quadro_grafico = ttk.Frame(principal)

        principal.add(quadro_tabela, weight=2)
        principal.add(quadro_grafico, weight=3)

        # ---- Tabela
        self.tabela = ttk.Treeview(
            quadro_tabela, columns=COLUNAS, show="headings", height=10
        )

        for coluna in COLUNAS:
            self.tabela.heading(
                coluna, text=TITULOS[coluna],
                command=lambda c=coluna: self.ordenar_coluna(c)
            )
            self.tabela.column(coluna, width=82, anchor="center")

        self.tabela.column("ATIVO", width=70)
        self.tabela.column("ESTADO", width=190)
        self.tabela.column("CLASSE", width=170)
        self.tabela.column("DATA_1A", width=95)
        self.tabela.column("IDADE", width=105)
        self.tabela.column("DIST_EMA21", width=120)
        self.tabela.column("FALTA", width=380, anchor="w")
        self.tabela.column("SMC_ESTRUT", width=120)
        self.tabela.column("SMC_LIQ", width=230, anchor="w")
        self.tabela.column("SMC_ZONA", width=130)

        scroll_y = ttk.Scrollbar(
            quadro_tabela, orient="vertical", command=self.tabela.yview
        )
        scroll_x = ttk.Scrollbar(
            quadro_tabela, orient="horizontal", command=self.tabela.xview
        )

        self.tabela.configure(
            yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set
        )

        scroll_x.pack(side="bottom", fill="x")
        scroll_y.pack(side="right", fill="y")
        self.tabela.pack(side="left", fill="both", expand=True)

        self.tabela.bind("<<TreeviewSelect>>", self.selecionar_ativo)

        # ---- Painel de leitura SMC (texto do ativo selecionado)
        quadro_texto = ttk.Frame(quadro_grafico)
        quadro_texto.pack(side="top", fill="x")

        self.texto_smc = tk.Text(
            quadro_texto, height=11, wrap="word", relief="flat",
            font=("Consolas", 9), padx=10, pady=6, state="disabled"
        )

        barra_texto = ttk.Scrollbar(
            quadro_texto, orient="vertical", command=self.texto_smc.yview
        )
        self.texto_smc.configure(yscrollcommand=barra_texto.set)

        barra_texto.pack(side="right", fill="y")
        self.texto_smc.pack(side="left", fill="both", expand=True)

        # ---- Gráfico
        self.figura = Figure(figsize=(12, 6), dpi=100)

        self.canvas = FigureCanvasTkAgg(self.figura, master=quadro_grafico)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ---- Rodapé
        rodape = ttk.Frame(self.root, padding=8)
        rodape.pack(fill="x")

        self.resumo = tk.StringVar(value="Nenhuma varredura realizada.")
        ttk.Label(
            rodape, textvariable=self.resumo, font=("Arial", 10, "bold")
        ).pack(side="left")

    # --------------------------------------------------------
    # TEMA
    # --------------------------------------------------------

    def aplicar_tema(self):

        t = TEMAS[self.tema]
        st = self.estilo

        self.root.configure(bg=t["bg"])

        st.configure(
            ".", background=t["bg"], foreground=t["fg"],
            fieldbackground=t["painel"], bordercolor=t["grade"]
        )
        st.configure("TFrame", background=t["bg"])
        st.configure("TLabel", background=t["bg"], foreground=t["fg"])
        st.configure(
            "TButton", background=t["cab"], foreground=t["fg"],
            bordercolor=t["grade"], padding=5
        )
        st.map(
            "TButton",
            background=[("active", t["sel_bg"]), ("disabled", t["bg"])],
            foreground=[("disabled", t["suave"])]
        )
        st.configure(
            "TCheckbutton", background=t["bg"], foreground=t["fg"]
        )
        st.map("TCheckbutton", background=[("active", t["bg"])])
        st.configure(
            "TCombobox", fieldbackground=t["painel"],
            background=t["cab"], foreground=t["fg"],
            arrowcolor=t["fg"], bordercolor=t["grade"]
        )
        st.map(
            "TCombobox",
            fieldbackground=[("readonly", t["painel"])],
            foreground=[("readonly", t["fg"])],
            selectbackground=[("readonly", t["painel"])],
            selectforeground=[("readonly", t["fg"])]
        )

        self.root.option_add("*TCombobox*Listbox.background", t["painel"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["sel_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["sel_fg"])

        st.configure(
            "Treeview", background=t["painel"], fieldbackground=t["painel"],
            foreground=t["fg"], rowheight=22, bordercolor=t["grade"]
        )
        st.configure(
            "Treeview.Heading", background=t["cab"], foreground=t["fg"],
            relief="flat", font=("Arial", 9, "bold")
        )
        st.map("Treeview.Heading", background=[("active", t["sel_bg"])])
        st.map(
            "Treeview",
            background=[("selected", t["sel_bg"])],
            foreground=[("selected", t["sel_fg"])]
        )

        st.configure(
            "Horizontal.TProgressbar", troughcolor=t["painel"],
            background=t["alta"], bordercolor=t["grade"]
        )
        for orientacao in ("Vertical", "Horizontal"):
            st.configure(
                f"{orientacao}.TScrollbar", background=t["cab"],
                troughcolor=t["painel"], arrowcolor=t["fg"],
                bordercolor=t["grade"]
            )
        st.configure("TPanedwindow", background=t["bg"])

        for tag, chave in (
            ("confirmado", "tag_conf"),
            ("continuacao", "tag_cont"),
            ("inicial", "tag_ini"),
            ("tendencia", "tag_tend"),
            ("esticada", "tag_esti"),
        ):
            self.tabela.tag_configure(
                tag, background=t[chave], foreground=t["fg"]
            )

        self.texto_smc.configure(
            bg=t["painel"], fg=t["fg"], insertbackground=t["fg"],
            highlightthickness=1, highlightbackground=t["grade"]
        )

        self.botao_tema.configure(
            text="Tema claro" if self.tema == "escuro" else "Tema escuro"
        )

        self.desenhar()

    def alternar_tema(self):

        self.tema = "claro" if self.tema == "escuro" else "escuro"
        self.aplicar_tema()

    # --------------------------------------------------------
    # INICIAR SCANNER
    # --------------------------------------------------------

    def iniciar_scanner(self):

        if self.thread_ativa:
            return

        try:
            score = int(self.score_var.get())
        except ValueError:
            messagebox.showerror("Erro", "Selecione um score válido.")
            return

        self.thread_ativa = True
        self.botao.configure(state="disabled")

        self.progresso["value"] = 0
        self.progresso["maximum"] = N_ATIVOS

        self.status.set("Consultando liquidez e baixando dados...")

        self.tabela.delete(*self.tabela.get_children())

        threading.Thread(
            target=self.executar_thread, args=(score,), daemon=True
        ).start()

    def executar_thread(self, score):

        try:
            executar_analise(score)
        except Exception as erro:
            fila_resultados.put(("erro", str(erro)))

    # --------------------------------------------------------
    # ATUALIZAÇÃO DA INTERFACE (fila entre thread e Tkinter)
    # --------------------------------------------------------

    def verificar_fila(self):

        try:
            while True:

                mensagem = fila_resultados.get_nowait()
                tipo = mensagem[0]

                if tipo == "progresso":

                    _, atual, total, ativo, estado = mensagem

                    self.progresso["maximum"] = total
                    self.progresso["value"] = atual
                    self.status.set(
                        f"Analisando {ativo} ({atual}/{total}) - {estado}"
                    )

                elif tipo == "finalizado":

                    (
                        _, tabela, graficos, erros, tempo, smc, niveis
                    ) = mensagem

                    self.resultados = tabela
                    self.linhas = tabela.set_index("ATIVO").assign(
                        ATIVO=lambda d: d.index
                    ).to_dict("index")
                    self.graficos = graficos
                    self.smc = smc
                    self.niveis = niveis

                    self.exibir_resultados()

                    contagem = tabela["ESTADO"].value_counts()

                    def qtd(nome):
                        return int(contagem.get(nome, 0))

                    self.resumo.set(
                        f"Analisadas: {len(tabela)} | "
                        f"Confirmadas: {qtd('REVERSÃO CONFIRMADA')} | "
                        f"Continuações: {qtd('CONTINUAÇÃO')} | "
                        f"Iniciais: {qtd('REVERSÃO INICIAL')} | "
                        f"Tendência: {qtd('TENDÊNCIA EM ANDAMENTO')} | "
                        f"Esticadas: {qtd('ESTICADA')} | "
                        f"Falhas: {len(erros)} | "
                        f"Tempo: {str(tempo).split('.')[0]}"
                    )

                    self.progresso["value"] = self.progresso["maximum"]
                    self.status.set("Análise concluída.")
                    self.thread_ativa = False
                    self.botao.configure(state="normal")

                elif tipo == "erro":

                    self.status.set("Erro na análise.")
                    self.thread_ativa = False
                    self.botao.configure(state="normal")

                    messagebox.showerror("Erro no Scanner", mensagem[1])

        except queue.Empty:
            pass

        self.root.after(200, self.verificar_fila)

    # --------------------------------------------------------
    # TABELA
    # --------------------------------------------------------

    def exibir_resultados(self):

        self.tabela.delete(*self.tabela.get_children())

        if self.resultados is None:
            return

        tabela = self.resultados

        if self.somente_sinais.get():
            tabela = tabela[tabela["ESTADO"] != "SEM SINAL"]

        for _, linha in tabela.iterrows():

            valores = []

            for coluna in COLUNAS:

                valor = linha[coluna]

                if coluna in CASAS:
                    valores.append(formatar(valor, CASAS[coluna]))
                else:
                    valores.append(valor)

            self.tabela.insert(
                "", "end", values=valores,
                tags=(TAG_ESTADO.get(linha["ESTADO"], ""),)
            )

    def ordenar_coluna(self, coluna):

        reverso = not self.ordem_reversa.get(coluna, False)
        self.ordem_reversa[coluna] = reverso

        validos = []
        vazios = []

        for item in self.tabela.get_children(""):

            texto = self.tabela.set(item, coluna)

            if texto in ("-", ""):
                vazios.append(item)
                continue

            try:
                chave = (0, float(texto))
            except ValueError:
                try:
                    chave = (
                        0, datetime.strptime(texto, "%d/%m/%Y").timestamp()
                    )
                except ValueError:
                    chave = (1, texto.lower())

            validos.append((chave, item))

        validos.sort(key=lambda par: par[0], reverse=reverso)

        for posicao, item in enumerate(
            [item for _, item in validos] + vazios
        ):
            self.tabela.move(item, "", posicao)

    # --------------------------------------------------------
    # GRÁFICO
    # --------------------------------------------------------

    def selecionar_ativo(self, event=None):

        selecionado = self.tabela.selection()

        if not selecionado:
            return

        ativo = self.tabela.item(selecionado[0], "values")[0]

        if ativo not in self.graficos:
            return

        self.ativo_atual = ativo
        self.atualizar_texto_smc()
        self.desenhar()

    def atualizar_texto_smc(self):

        ativo = self.ativo_atual

        if ativo is None:
            linhas = ["Selecione um ativo na tabela."]
        else:
            sr = self.niveis.get(ativo)
            smc = self.smc.get(ativo)

            if sr:
                linhas = [f"{ativo} | {sr['linhas'][0]}"] + sr["linhas"][1:]
            else:
                linhas = [
                    f"{ativo} | suportes/resistências indisponíveis."
                ]

            linhas.append("")

            if smc:
                linhas += [f"SMC | {ativo}"] + smc["linhas"]
            else:
                linhas.append(
                    "Alvos SMC: disponíveis apenas para ações com "
                    "REVERSÃO CONFIRMADA."
                )

            linhas.append(
                "Leitura em candles diários: níveis são zonas de reação "
                "prováveis, não garantias, e não constituem recomendação."
            )

        self.texto_smc.configure(state="normal")
        self.texto_smc.delete("1.0", "end")
        self.texto_smc.insert("1.0", "\n".join(linhas))
        self.texto_smc.configure(state="disabled")

    def desenhar(self):

        if self.ativo_atual is None or self.ativo_atual not in self.graficos:

            t = TEMAS[self.tema]
            self.figura.clear()
            self.figura.patch.set_facecolor(t["bg"])
            self.figura.text(
                0.5, 0.5, "Selecione um ativo para visualizar o gráfico",
                ha="center", va="center", color=t["fg"], fontsize=13
            )
            self.canvas.draw_idle()
            return

        desenhar_grafico(
            self.figura,
            self.graficos[self.ativo_atual],
            self.linhas.get(self.ativo_atual),
            self.tema,
            int(self.periodo_var.get()),
            self.smc.get(self.ativo_atual) if self.mostrar_smc.get() else None,
            self.niveis.get(self.ativo_atual)
            if self.mostrar_sr.get() else None
        )

        self.canvas.draw_idle()


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":

    janela = tk.Tk()
    app = ScannerB3(janela)
    janela.mainloop()
