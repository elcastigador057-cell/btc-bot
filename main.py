"""
╔══════════════════════════════════════════════════════════╗
║       CODIGO DE ORO — Bot BTC/USD v2.0 para Railway     ║
║  Filosofia: pocas alertas, todas de calidad             ║
║  Solo avisa cuando hay contexto claro de entrada        ║
╚══════════════════════════════════════════════════════════╝
"""
import os
import time
import requests
from collections import deque
from datetime import datetime

# ══════════════════════════════════════════════════════════
# CONFIGURACION
# ══════════════════════════════════════════════════════════
TOKEN     = os.environ.get("TELEGRAM_TOKEN", "8804236118:AAEsOWK0sk8ZAcUTXAD8ZYWiMm5OGPn07Xs")
CHAT_IDS  = [c.strip() for c in os.environ.get("CHAT_ID", "1842727203,5545360383").split(",") if c.strip()]
INTERVALO = 5       # segundos entre ticks
HIST_MAX  = 720     # 720 x 5s = 60 minutos de historial

# Cooldowns — tiempo minimo entre alertas del mismo tipo
CD = {
    "entrada_compra":  1800,   # 30 min entre señales de compra
    "entrada_venta":   1800,   # 30 min entre señales de venta
    "soporte_roto":     600,   # 10 min
    "rebote_soporte":   600,
    "resumen":         3600,   # resumen cada 1 hora (solo informativo)
}

# ══════════════════════════════════════════════════════════
# ESTADO GLOBAL
# ══════════════════════════════════════════════════════════
historial = deque(maxlen=HIST_MAX)
cooldowns = {}
soporte   = float(os.environ.get("SOPORTE_BTC", "0"))

# Estado de tendencia — para detectar cuando lleva bajando y da vuelta
_tendencia_previa    = "neutral"   # "alc", "baj", "neutral"
_precio_tendencia    = 0.0         # precio cuando inicio la tendencia actual
_contador_tendencia  = 0           # cuantos ticks consecutivos confirman la tendencia

# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def hora_txt():
    return datetime.now().strftime("%H:%M")

def en_cooldown(id_cd):
    ahora  = time.time()
    cd_seg = CD.get(id_cd, 600)
    if id_cd in cooldowns and ahora - cooldowns[id_cd] < cd_seg:
        return True
    cooldowns[id_cd] = ahora
    return False

def peek_cooldown(id_cd):
    """Consulta si esta en cooldown SIN activarlo."""
    ahora  = time.time()
    cd_seg = CD.get(id_cd, 600)
    return id_cd in cooldowns and ahora - cooldowns[id_cd] < cd_seg

def telegram(msg):
    for cid in CHAT_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
            if r.status_code == 200:
                log(f"TG OK -> {cid}: {msg[:50].strip()}")
            else:
                log(f"TG error {r.status_code} -> {cid}")
        except Exception as e:
            log(f"TG excepcion -> {cid}: {e}")

# ══════════════════════════════════════════════════════════
# FETCH PRECIO — Binance gratis
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
        ultimo     = historial[-1]["precio"]
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
    k   = 2 / (periodo + 1)
    val = sum(precios[:periodo]) / periodo
    for p in precios[periodo:]:
        val = p * k + val * (1 - k)
    return round(val, 2)

def rsi(precios, periodo=14):
    if len(precios) < periodo + 1:
        return None
    g      = precios[-(periodo + 1):]
    deltas = [g[i] - g[i-1] for i in range(1, len(g))]
    avg_g  = sum(x for x in deltas if x > 0) / periodo
    avg_p  = sum(abs(x) for x in deltas if x < 0) / periodo
    if avg_p == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_p)), 1)

def get_velocidad(minutos):
    validos = [(r["precio"], r["ts"]) for r in historial if not r.get("anomalo")]
    if len(validos) < 2:
        return None
    ahora  = time.time()
    target = ahora - minutos * 60
    mejor  = min(validos, key=lambda r: abs(r[1] - target))
    margen = minutos * 60 * (2.0 if minutos >= 30 else 1.5)
    if abs(mejor[1] - target) > margen:
        return None
    return round(validos[-1][0] - mejor[0], 2)

# ══════════════════════════════════════════════════════════
# LOGICA CENTRAL — detectar contexto de entrada real
#
# Para COMPRA se requiere TODO esto junto:
#   1. BTC lleva bajando (v30 negativo) — "viene de abajo"
#   2. El movimiento reciente de 5 min se invierte (v5 positivo)
#   3. EMA9 cruza o esta a punto de cruzar EMA21 al alza
#   4. RSI no esta en sobrecompra (< 70)
#   5. EMA20 > EMA50 O el precio reboto desde soporte
#
# Para VENTA se requiere TODO esto junto:
#   1. BTC lleva subiendo (v30 positivo) — "viene de arriba"
#   2. El movimiento de 5 min empieza a caer (v5 negativo)
#   3. EMA9 cruza o esta a punto de cruzar EMA21 a la baja
#   4. RSI no esta en sobreventa (> 30)
#   5. EMA20 < EMA50 O el precio cayo desde resistencia
# ══════════════════════════════════════════════════════════
def analizar(precio, v5, v30, e9, e21, e20, e50, rsi_v):
    """
    Retorna: ("compra"|"venta"|"esperar", score 0-100, lista razones, sl, tp, rr)
    Solo retorna compra/venta cuando el contexto completo esta alineado.
    """
    if e9 is None or e21 is None or e20 is None or e50 is None:
        return "esperar", 0, [], 0, 0, 0
    if v5 is None or v30 is None:
        return "esperar", 0, [], 0, 0, 0

    razones_c = []   # razones a favor de compra
    razones_v = []   # razones a favor de venta
    score_c   = 0
    score_v   = 0

    # ── CONTEXTO: lleva bajando / subiendo en 30 min ──
    # Este es el filtro mas importante: solo buscamos compra
    # cuando el precio lleva un rato bajando (posible suelo)
    # y solo buscamos venta cuando lleva subiendo (posible techo)
    if v30 < -30:
        score_c += 30
        razones_c.append(f"Lleva bajando {v30:+.0f} pts en 30m — posible suelo")
    elif v30 > 30:
        score_v += 30
        razones_v.append(f"Lleva subiendo {v30:+.0f} pts en 30m — posible techo")
    else:
        # Sin tendencia clara en 30m — no hay contexto de entrada
        return "esperar", 0, [], 0, 0, 0

    # ── CAMBIO DE DIRECCION en 5 min ──
    # Para compra: v30 negativo pero v5 ya positivo (giro al alza)
    # Para venta:  v30 positivo pero v5 ya negativo (giro a la baja)
    if v5 > 10:
        score_c += 25
        razones_c.append(f"Giro alcista en 5m: +{v5:.0f} pts")
    elif v5 < -10:
        score_v += 25
        razones_v.append(f"Giro bajista en 5m: {v5:.0f} pts")
    else:
        # Sin giro confirmado — no hay entrada todavia
        return "esperar", 0, [], 0, 0, 0

    # ── EMA9 vs EMA21 ──
    diff_ema = e9 - e21
    if diff_ema > 0:
        score_c += 20
        razones_c.append(f"EMA9 sobre EMA21 (+{diff_ema:.0f}) ✅")
    elif diff_ema > -100:
        # Muy cerca del cruce — señal de cambio inminente
        score_c += 10
        razones_c.append(f"EMA9 acercandose a EMA21 ({diff_ema:.0f}) — cruce proximo")
    else:
        score_v += 20
        razones_v.append(f"EMA9 bajo EMA21 ({diff_ema:.0f}) ✅")

    # ── TENDENCIA ESTRUCTURAL EMA20 vs EMA50 ──
    if e20 > e50:
        score_c += 15
        razones_c.append("EMA20 > EMA50 — estructura alcista ✅")
    else:
        score_v += 15
        razones_v.append("EMA20 < EMA50 — estructura bajista ✅")

    # ── RSI ──
    if rsi_v is not None:
        if rsi_v < 35:
            score_c += 20
            razones_c.append(f"RSI {rsi_v} — zona sobreventa, rebote probable ✅")
        elif rsi_v < 55:
            score_c += 10
            razones_c.append(f"RSI {rsi_v} — zona neutra/alcista ✅")
        elif rsi_v > 65:
            score_v += 20
            razones_v.append(f"RSI {rsi_v} — zona sobrecompra, caida probable ✅")
        elif rsi_v > 45:
            score_v += 10
            razones_v.append(f"RSI {rsi_v} — zona neutra/bajista ✅")

    # ── DECISION FINAL ──
    # Se necesita score >= 70 para generar señal
    # Ademas el contexto debe ser coherente:
    # compra requiere v30 negativo + v5 positivo
    # venta requiere v30 positivo + v5 negativo
    coherente_compra = v30 < 0 and v5 > 0
    coherente_venta  = v30 > 0 and v5 < 0

    if coherente_compra and score_c >= 65 and (rsi_v is None or rsi_v < 70):
        sl_base = max(80, round(abs(v5) * 1.5, 0))
        rr      = 2.0 if score_c >= 80 else 1.5
        entrada = round(precio + 5, 0)
        sl      = round(entrada - sl_base, 0)
        tp      = round(entrada + sl_base * rr, 0)
        return "compra", score_c, razones_c, sl, tp, rr

    if coherente_venta and score_v >= 65 and (rsi_v is None or rsi_v > 30):
        sl_base = max(80, round(abs(v5) * 1.5, 0))
        rr      = 2.0 if score_v >= 80 else 1.5
        entrada = round(precio - 5, 0)
        sl      = round(entrada + sl_base, 0)
        tp      = round(entrada - sl_base * rr, 0)
        return "venta", score_v, razones_v, sl, tp, rr

    return "esperar", max(score_c, score_v), [], 0, 0, 0

# ══════════════════════════════════════════════════════════
# MENSAJES
# ══════════════════════════════════════════════════════════
def msg_entrada(dir, precio, score, razones, sl, tp, rr, v5, v30, rsi_v):
    sl_pts = abs(precio - sl)
    tp_pts = abs(tp - precio)
    # XM: 0.01 Token BTC = $0.10 por punto
    sl_usd = round(sl_pts * 0.10, 2)
    tp_usd = round(tp_pts * 0.10, 2)
    emoji  = "🟢" if dir == "compra" else "🔴"
    titulo = "ENTRADA COMPRA" if dir == "compra" else "ENTRADA VENTA"
    dir_v5 = f"+{v5:.0f}" if v5 >= 0 else f"{v5:.0f}"
    dir_v30= f"+{v30:.0f}" if v30 >= 0 else f"{v30:.0f}"
    calidad = "🔥 Muy alta" if score >= 85 else "✅ Alta" if score >= 75 else "👍 Buena"
    return (
        f"{emoji} <b>{titulo} — BTC/USD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Precio actual: <b>${precio:,.0f}</b>\n"
        f"📊 RSI: {rsi_v or 'N/D'}  |  Score: {score}/100\n"
        f"⏱ 5 min: {dir_v5} pts  |  30 min: {dir_v30} pts\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Entrada sugerida: <b>${precio + (5 if dir=='compra' else -5):,.0f}</b>\n"
        f"🎯 Take Profit: <b>${tp:,.0f}</b>  (+{tp_pts:.0f} pts  ≈ <b>+${tp_usd:.2f} USD</b>)\n"
        f"🛑 Stop Loss:   <b>${sl:,.0f}</b>  (-{sl_pts:.0f} pts  ≈ <b>-${sl_usd:.2f} USD</b>)\n"
        f"⚖️ Ratio R:R  1:{rr}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(f"  • {r}" for r in razones[:4]) + "\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📶 Calidad: <b>{calidad}</b>\n"
        f"⏰ {hora_txt()} — Revisar vela actual antes de entrar"
    )

def msg_soporte_roto(precio, sop):
    return (
        f"🚨 <b>SOPORTE ROTO — BTC/USD</b>\n"
        f"Soporte: <b>${sop:,.0f}</b>\n"
        f"Precio actual: <b>${precio:,.0f}</b>\n"
        f"⚠️ Esperar rebote confirmado antes de comprar\n"
        f"⏰ {hora_txt()}"
    )

def msg_rebote_soporte(precio, sop, v5):
    return (
        f"🟡 <b>REBOTE EN SOPORTE — BTC/USD</b>\n"
        f"Soporte: <b>${sop:,.0f}</b>\n"
        f"Precio: <b>${precio:,.0f}</b>  (+{v5:.0f} pts en 5m)\n"
        f"👀 Posible entrada compra — esperar confirmacion\n"
        f"⏰ {hora_txt()}"
    )

def msg_resumen(precio, dir, score, v5, v30, rsi_v, hist_len):
    estado = "🟢 Alcista" if dir == "compra" else "🔴 Bajista" if dir == "venta" else "⏳ Sin señal"
    return (
        f"📋 <b>Resumen BTC/USD — {hora_txt()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Precio: <b>${precio:,.0f}</b>\n"
        f"📊 RSI: {rsi_v or 'N/D'}  |  Score: {score}/100\n"
        f"⏱ 5m: {v5:+.0f} pts  |  30m: {v30:+.0f} pts\n"
        f"📡 Datos: {hist_len}/{HIST_MAX}\n"
        f"Estado: <b>{estado}</b>"
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

    v5_txt  = f"{v5:+.0f}" if v5 is not None else "N/D"
    v30_txt = f"{v30:+.0f}" if v30 is not None else "N/D"
    log(f"BTC=${precio:,.0f}  v5={v5_txt}  v30={v30_txt}  RSI={rsi_v}  hist={len(ps)}")

    # Necesitamos al menos 5 minutos de datos para v5 y EMAs
    if len(ps) < 60:
        log(f"Acumulando datos: {len(ps)}/60")
        return

    # ── 1. Soporte roto ──
    if soporte > 0 and precio < soporte - 15:
        if not en_cooldown("soporte_roto"):
            telegram(msg_soporte_roto(precio, soporte))
        return

    # ── 2. Rebote desde soporte (aviso previo sin ser señal completa) ──
    if soporte > 0 and precio >= soporte and precio <= soporte + 30:
        if v5 is not None and v5 > 20:
            if not en_cooldown("rebote_soporte"):
                telegram(msg_rebote_soporte(precio, soporte, v5))

    # ── 3. Señal de entrada principal ──
    dir, score, razones, sl, tp, rr = analizar(
        precio, v5, v30, e9, e21, e20, e50, rsi_v
    )
    log(f"Analisis: {dir} | score={score}")

    if dir == "compra" and not peek_cooldown("entrada_compra"):
        en_cooldown("entrada_compra")   # activar cooldown
        telegram(msg_entrada("compra", precio, score, razones, sl, tp, rr,
                              v5 or 0, v30 or 0, rsi_v))
        return

    if dir == "venta" and not peek_cooldown("entrada_venta"):
        en_cooldown("entrada_venta")
        telegram(msg_entrada("venta", precio, score, razones, sl, tp, rr,
                              v5 or 0, v30 or 0, rsi_v))
        return

    # ── 4. Resumen horario cada 1 hora (solo informativo) ──
    if not peek_cooldown("resumen"):
        en_cooldown("resumen")
        telegram(msg_resumen(precio, dir, score, v5, v30, rsi_v, len(ps)))

# ══════════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ══════════════════════════════════════════════════════════
def main():
    log("═══ Codigo de Oro BTC Bot v2.0 arrancando ═══")
    telegram(
        "✅ <b>Bot BTC/USD v2.0 activo</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🔍 Monitoreando BTC/USD 24/7\n"
        "📊 Analisis: EMA 9/21/20/50 + RSI + Impulso 5m/30m\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Logica de señal:</b>\n"
        "  • Solo avisa cuando hay contexto real\n"
        "  • COMPRA: lleva bajando + giro al alza confirmado\n"
        "  • VENTA: lleva subiendo + giro a la baja confirmado\n"
        "  • Cooldown 30 min entre señales del mismo tipo\n"
        "  • Resumen informativo cada 1 hora\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Intervalo: cada {INTERVALO}s  |  Buffer: {HIST_MAX} ticks (60 min)"
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
            log(f"Error inesperado: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
