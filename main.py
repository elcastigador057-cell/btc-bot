"""
╔══════════════════════════════════════════════════════════╗
║       CODIGO DE ORO — Bot BTC/USD v1.0 para Railway     ║
║  Fuente: Binance (gratis, sin API key)                  ║
║  Analisis: EMA, RSI, Impulso 5m/30m, tendencia lenta   ║
╚══════════════════════════════════════════════════════════╝
"""
import os
import time
import requests
from collections import deque
from datetime import datetime

# ══════════════════════════════════════════════════════════
# CONFIGURACION — variables de entorno en Railway
# ══════════════════════════════════════════════════════════
TOKEN       = os.environ.get("TELEGRAM_TOKEN", "8804236118:AAEsOWK0sk8ZAcUTXAD8ZYWiMm5OGPn07Xs")
CHAT_IDS    = [c.strip() for c in os.environ.get("CHAT_ID", "1842727203,5545360383").split(",") if c.strip()]
INTERVALO   = 5          # segundos — Binance es gratis, podemos ir rapido
HIST_MAX    = 360        # 360 registros x 5s = 30 minutos de historial

# Umbrales BTC (escala mayor que XAU)
UMBRAL_V5_FUERTE  = 150  # pts en 5 min para señal fuerte
UMBRAL_V30_LENTO  = 200  # pts en 30 min para tendencia lenta

# Cooldowns en segundos
CD = {
    "oportunidad_compra":  600,   # 10 min
    "oportunidad_venta":   600,
    "impulso_fuerte":      300,   # 5 min
    "tendencia_lenta_baj": 1200,  # 20 min
    "tendencia_lenta_alc": 1200,
    "soporte_roto":        300,
    "rebote_soporte":      300,
    "resumen":            1800,   # 30 min
}

# ══════════════════════════════════════════════════════════
# ESTADO GLOBAL
# ══════════════════════════════════════════════════════════
historial  = deque(maxlen=HIST_MAX)
cooldowns  = {}
soporte    = float(os.environ.get("SOPORTE_BTC", "0"))

# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def hora_txt():
    return datetime.now().strftime("%H:%M")

def en_cooldown(id_cd):
    ahora = time.time()
    cd_seg = CD.get(id_cd, 300)
    if id_cd in cooldowns and ahora - cooldowns[id_cd] < cd_seg:
        return True
    cooldowns[id_cd] = ahora
    return False

def telegram(msg):
    for cid in CHAT_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
            if r.status_code == 200:
                log(f"TG OK -> {cid}: {msg[:40].strip()}")
            else:
                log(f"TG error {r.status_code} -> {cid}")
        except Exception as e:
            log(f"TG excepcion -> {cid}: {e}")

# ══════════════════════════════════════════════════════════
# FETCH PRECIO BTC — Binance gratis
# ══════════════════════════════════════════════════════════
def get_precio():
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            timeout=10
        )
        data = r.json()
        if "price" in data:
            return float(data["price"])
        return None
    except Exception as e:
        log(f"Binance error: {e}")
        return None

def registrar(precio):
    if not precio or precio < 10000 or precio > 1000000:
        return False
    if historial:
        ultimo = historial[-1]["precio"]
        cambio_pct = abs(precio - ultimo) / ultimo * 100
        if cambio_pct > 2.0:
            log(f"Anomalia BTC: {ultimo} -> {precio} ({cambio_pct:.1f}%) — IGNORADO")
            historial.append({"precio": precio, "ts": time.time(), "anomalo": True})
            return False
    historial.append({"precio": precio, "ts": time.time(), "anomalo": False})
    return True

# ══════════════════════════════════════════════════════════
# INDICADORES
# ══════════════════════════════════════════════════════════
def precios_validos():
    return [r["precio"] for r in historial if not r.get("anomalo")]

def ema(precios, periodo):
    if len(precios) < periodo:
        return None
    k = 2 / (periodo + 1)
    val = sum(precios[:periodo]) / periodo
    for p in precios[periodo:]:
        val = p * k + val * (1 - k)
    return round(val, 2)

def rsi(precios, periodo=14):
    if len(precios) < periodo + 1:
        return None
    g = precios[-periodo:]
    deltas = [g[i] - g[i-1] for i in range(1, len(g))]
    avg_g = sum(x for x in deltas if x > 0) / periodo
    avg_p = sum(abs(x) for x in deltas if x < 0) / periodo
    if avg_p == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_p)), 1)

def get_velocidad(minutos):
    validos = [(r["precio"], r["ts"]) for r in historial if not r.get("anomalo")]
    if len(validos) < 2:
        return None
    ahora  = time.time()
    target = ahora - minutos * 60
    mejor  = min(validos, key=lambda r: abs(r[1] - target), default=None)
    margen = minutos * 60 * (2.0 if minutos >= 30 else 1.5)
    if not mejor or abs(mejor[1] - target) > margen:
        return None
    return round(validos[-1][0] - mejor[0], 2)

# ══════════════════════════════════════════════════════════
# CALCULAR SEÑAL
# ══════════════════════════════════════════════════════════
def calcular_senal(precio, v5, v30, ema9, ema21, ema20, ema50, rsi_val):
    score = 50
    razones = []

    if ema9 and ema21:
        if ema9 > ema21: score += 18; razones.append("EMA9 > EMA21 ✅")
        else:             score -= 18; razones.append("EMA9 < EMA21 ❌")

    if ema20 and ema50:
        if ema20 > ema50: score += 12; razones.append("Tendencia alcista ✅")
        else:              score -= 12; razones.append("Tendencia bajista ❌")

    if rsi_val is not None:
        if rsi_val < 30:        score += 15; razones.append(f"RSI {rsi_val} sobreventa ✅")
        elif rsi_val > 70:      score -= 15; razones.append(f"RSI {rsi_val} sobrecompra ❌")
        elif 45 < rsi_val < 65: score += 8;  razones.append(f"RSI {rsi_val} saludable ✅")
        else:                               razones.append(f"RSI {rsi_val} neutral")

    if v5 is not None:
        if v5 > 100:   score += 12; razones.append(f"Impulso +{v5:.0f} pts ✅")
        elif v5 < -100: score -= 12; razones.append(f"Impulso {v5:.0f} pts ❌")

    if v30 is not None:
        if v30 > 0 and v5 is not None and v5 > 0:   score += 8; razones.append("Tendencia 30m confirma ✅")
        elif v30 < 0 and v5 is not None and v5 < 0: score -= 8

    score = max(0, min(100, score))

    if score >= 68:   dir = "compra"; conf = score
    elif score <= 32: dir = "venta";  conf = 100 - score
    else:             dir = "esperar"; conf = 50

    # SL/TP dinamico
    spread  = max(50, abs(v5 or 50))
    sl_base = max(80, round(spread * 1.2, 0))
    rr      = 2.0 if conf >= 75 else 1.5

    if dir == "compra":
        entrada = round(precio + 5, 0)
        sl      = round(entrada - sl_base, 0)
        tp      = round(entrada + sl_base * rr, 0)
    elif dir == "venta":
        entrada = round(precio - 5, 0)
        sl      = round(entrada + sl_base, 0)
        tp      = round(entrada - sl_base * rr, 0)
    else:
        entrada = sl = tp = 0

    return {
        "dir": dir, "conf": conf, "score": score,
        "entrada": entrada, "sl": sl, "tp": tp,
        "sl_pts": sl_base, "tp_pts": round(sl_base * rr, 0), "rr": rr,
        "razones": razones, "rsi": rsi_val,
    }

# ══════════════════════════════════════════════════════════
# MENSAJES
# ══════════════════════════════════════════════════════════
def msg_compra(precio, s, v5, v30):
    conf_txt = "Alta" if s["conf"] >= 80 else "Media-Alta"
    return (
        f"🟢 <b>OPORTUNIDAD COMPRA — BTC/USD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Precio: <b>${precio:,.0f}</b>\n"
        f"📈 5 min: {v5:+.0f} pts | 30 min: {v30:+.0f} pts\n"
        f"📊 RSI: {s['rsi'] or 'N/D'} | IT: {s['score']}/100\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Entrada: <b>${s['entrada']:,.0f}</b>\n"
        f"🎯 TP: ${s['tp']:,.0f} (+{s['tp_pts']:.0f} pts)\n"
        f"🛑 SL: ${s['sl']:,.0f} (-{s['sl_pts']:.0f} pts)\n"
        f"⚖️ R:R 1:{s['rr']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(f"  {r}" for r in s["razones"][:4]) + "\n"
        f"📶 Confianza: <b>{conf_txt} ({s['conf']}%)</b>\n"
        f"⏰ {hora_txt()} — Confirmar con siguiente vela"
    )

def msg_venta(precio, s, v5, v30):
    conf_txt = "Alta" if s["conf"] >= 80 else "Media-Alta"
    return (
        f"🔴 <b>OPORTUNIDAD VENTA — BTC/USD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Precio: <b>${precio:,.0f}</b>\n"
        f"📉 5 min: {v5:+.0f} pts | 30 min: {v30:+.0f} pts\n"
        f"📊 RSI: {s['rsi'] or 'N/D'} | IT: {s['score']}/100\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Entrada: <b>${s['entrada']:,.0f}</b>\n"
        f"🎯 TP: ${s['tp']:,.0f} (-{s['tp_pts']:.0f} pts)\n"
        f"🛑 SL: ${s['sl']:,.0f} (+{s['sl_pts']:.0f} pts)\n"
        f"⚖️ R:R 1:{s['rr']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(f"  {r}" for r in s["razones"][:4]) + "\n"
        f"📶 Confianza: <b>{conf_txt} ({s['conf']}%)</b>\n"
        f"⏰ {hora_txt()} — Confirmar con siguiente vela"
    )

def msg_tendencia_lenta(precio, v30, v5, rsi_val, dir):
    if dir == "baj":
        return (
            f"📉 <b>CAIDA GRADUAL — BTC/USD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Precio: <b>${precio:,.0f}</b>\n"
            f"Caida 30 min: <b>{v30:+.0f} pts</b>\n"
            f"Velocidad 5 min: {v5:+.0f} pts\n"
            f"RSI: {rsi_val or 'N/D'}\n"
            f"⚠️ Tendencia bajista sostenida\n"
            f"⏰ {hora_txt()}"
        )
    else:
        return (
            f"📈 <b>SUBIDA GRADUAL — BTC/USD</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Precio: <b>${precio:,.0f}</b>\n"
            f"Subida 30 min: <b>{v30:+.0f} pts</b>\n"
            f"Velocidad 5 min: {v5:+.0f} pts\n"
            f"RSI: {rsi_val or 'N/D'}\n"
            f"✅ Tendencia alcista sostenida\n"
            f"⏰ {hora_txt()}"
        )

def msg_impulso(precio, v5, dir):
    emoji = "📈" if dir == "alc" else "📉"
    return (
        f"{emoji} <b>Impulso {'alcista' if dir=='alc' else 'bajista'} BTC/USD</b>\n"
        f"Precio: <b>${precio:,.0f}</b>\n"
        f"5 min: <b>{v5:+.0f} pts</b>\n"
        f"⏰ {hora_txt()} — Revisar app para señal completa"
    )

def msg_resumen(precio, s, hist_len):
    it_txt = (
        "🔴 Venta fuerte" if s["score"] < 30 else
        "🟠 Presion bajista" if s["score"] < 45 else
        "🟡 Indeciso" if s["score"] < 60 else
        "🟢 Compra probable" if s["score"] < 80 else
        "🚀 Compra fuerte"
    )
    return (
        f"📋 <b>Resumen BTC/USD — {hora_txt()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Precio: <b>${precio:,.0f}</b>\n"
        f"📊 IT: {s['score']}/100 — {it_txt}\n"
        f"🗂 Historial: {hist_len}/{HIST_MAX} registros\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Estado: <b>{'🟢 COMPRA' if s['dir']=='compra' else '🔴 VENTA' if s['dir']=='venta' else '⏳ ESPERAR'}</b>"
    )

# ══════════════════════════════════════════════════════════
# EVALUACION
# ══════════════════════════════════════════════════════════
def evaluar(precio):
    ps    = precios_validos()
    v5    = get_velocidad(5)
    v30   = get_velocidad(30)
    e9    = ema(ps, 9)
    e21   = ema(ps, 21)
    e20   = ema(ps, 20)
    e50   = ema(ps, 50)
    rsi_v = rsi(ps, 14)

    log(f"BTC=${precio:,.0f}  v5={v5:+.0f if v5 else 'N/D'}  v30={v30:+.0f if v30 else 'N/D'}  RSI={rsi_v}")

    # Esperar historial completo antes de evaluar
    if len(ps) < 180:
        log(f"Acumulando: {len(ps)}/180 validos")
        return

    s = calcular_senal(precio, v5, v30, e9, e21, e20, e50, rsi_v)
    log(f"Señal: {s['dir']} | IT={s['score']} | Conf={s['conf']}%")

    # 1. Soporte roto
    if soporte > 0 and precio < soporte - 10:
        if not en_cooldown("soporte_roto"):
            telegram(
                f"🚨 <b>SOPORTE ROTO — BTC/USD</b>\n"
                f"Soporte: <b>${soporte:,.0f}</b> → Precio: <b>${precio:,.0f}</b>\n"
                f"⚠️ No comprar hasta confirmar rebote\n"
                f"⏰ {hora_txt()}"
            )
        return

    # 2. Señal de compra
    if s["dir"] == "compra" and s["conf"] >= 68:
        if not en_cooldown("oportunidad_compra"):
            telegram(msg_compra(precio, s, v5 or 0, v30 or 0))
        return

    # 3. Señal de venta
    if s["dir"] == "venta" and s["conf"] >= 68:
        if not en_cooldown("oportunidad_venta"):
            telegram(msg_venta(precio, s, v5 or 0, v30 or 0))
        return

    # 4. Impulso fuerte 5 min
    if v5 is not None and abs(v5) >= UMBRAL_V5_FUERTE:
        dir_imp = "alc" if v5 > 0 else "baj"
        if not en_cooldown(f"impulso_{dir_imp}"):
            telegram(msg_impulso(precio, v5, dir_imp))
        return

    # 5. Tendencia lenta 30 min
    if v30 is not None:
        if v30 <= -UMBRAL_V30_LENTO and not en_cooldown("tendencia_lenta_baj"):
            telegram(msg_tendencia_lenta(precio, v30, v5 or 0, rsi_v, "baj"))
        elif v30 >= UMBRAL_V30_LENTO and not en_cooldown("tendencia_lenta_alc"):
            telegram(msg_tendencia_lenta(precio, v30, v5 or 0, rsi_v, "alc"))

    # 6. Resumen periodico
    if not en_cooldown("resumen"):
        telegram(msg_resumen(precio, s, len(ps)))

# ══════════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════
def main():
    log("═══ Codigo de Oro BTC Bot v1.0 arrancando ═══")
    telegram(
        "✅ <b>Bot BTC/USD activo</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔍 Monitoreando BTC/USD 24/7\n"
        "📊 Fuente: Binance (gratis)\n"
        "📊 Analisis: EMA, RSI, Impulso 5m/30m\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Intervalo: cada {INTERVALO} segundos\n"
        f"🗂 Buffer: {HIST_MAX} registros (30 min)\n"
        f"🛡 Filtro anomalias: >2% entre ticks = ignorado"
    )

    while True:
        try:
            precio = get_precio()
            if precio:
                valido = registrar(precio)
                if valido:
                    evaluar(precio)
            time.sleep(INTERVALO)
        except KeyboardInterrupt:
            log("Bot detenido.")
            break
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
