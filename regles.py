"""Règles des deux stratégies, reprises des simulateurs (mêmes seuils, même ordre des tests).

Les fonctions prennent une liste de clôtures quotidiennes (de la plus ancienne à la plus récente)
et rendent, pour chaque séance, l'état calculé sur la clôture de cette séance.
"""
import math


def volatilites(closes, n, echantillon=False):
    """Volatilité annualisée sur n séances : écart-type des variations quotidiennes × √252.
    `echantillon=True` divise par n − 1 (simulateur Nasdaq), sinon par n (simulateur S&P 500)."""
    r = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    out = [0.0] * len(closes)
    for i in range(n, len(closes)):
        w = r[i - n + 1:i + 1]
        m = sum(w) / n
        var = sum((x - m) ** 2 for x in w) / (n - 1 if echantillon else n)
        out[i] = math.sqrt(max(0.0, var) * 252)
    return out


def moyenne_mobile(closes, n=200):
    out = [0.0] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def plus_haut(closes, n):
    out = []
    for i in range(len(closes)):
        out.append(max(closes[max(0, i - n + 1):i + 1]))
    return out


def frein_krach(closes, seuil):
    """Actif après 3 clôtures d'affilée à plus de `seuil` sous le plus haut sur 12 mois ET sous la MM200 ;
    relâché dès qu'une des deux conditions disparaît."""
    ma, h12 = moyenne_mobile(closes), plus_haut(closes, 252)
    out, run, on = [], 0, False
    for i, c in enumerate(closes):
        cond = ma[i] > 0 and c / h12[i] - 1 < -seuil and c < ma[i]
        run = run + 1 if cond else 0
        if on and not cond:
            on = False
        if run >= 3:
            on = True
        out.append(on)
    return out


# ---------------------------------------------------------------- PEA : LQQ, 100/50/0 + frein rapide
PEA = dict(baisse_50=0.34, remonte_100=0.30, baisse_0=0.60, remonte_50=0.50,
           frein_rapide=0.10, frein_rapide_fin=0.05, krach=0.20, coupe_circuit=0.05)


def signaux_pea(closes, p=PEA):
    """Part de LQQ visée après chaque clôture du Nasdaq-100 (1, 0,5 ou 0) et le détail des indicateurs.
    Comme dans le simulateur Nasdaq, l'ordre se passe à la séance suivante."""
    v7, h20, br = volatilites(closes, 7, echantillon=True), plus_haut(closes, 20), frein_krach(closes, p['krach'])
    cur, cap, out = 1.0, False, []
    for i, c in enumerate(closes):
        v = v7[i]
        if v > 0:
            if cur == 1 and v > p['baisse_50']:
                cur = 0.5
            elif cur == 0.5 and v < p['remonte_100']:
                cur = 1.0
            if cur == 0.5 and v > p['baisse_0']:
                cur = 0.0
            elif cur == 0 and v < p['remonte_50']:
                cur = 0.5
        dd = c / h20[i] - 1
        if dd < -p['frein_rapide']:
            cap = True
        elif dd > -p['frein_rapide_fin']:
            cap = False
        w = min(cur, 0.5) if cap else cur
        if br[i]:
            w = 0.0
        out.append(dict(part=w, palier_vol=cur, vol7=v, ecart_haut20=dd, frein_rapide=cap, frein_krach=br[i]))
    return out


# ---------------------------------------------------------------- CTO : futures S&P 500 version 2
CTO = dict(cible=0.40, plafond=4.0, krach=0.14, sortie_tendance=-0.02, retour_tendance=0.01,
           tendance_baissiere=0.25, ecart_min=1.0, coupe_circuit=0.05, part_mf=0.50)


def signaux_cto(closes, p=CTO):
    """Levier retenu après chaque clôture du S&P 500 (à appliquer à la clôture de la séance suivante)."""
    v10, v20 = volatilites(closes, 10), volatilites(closes, 20)
    ma, br = moyenne_mobile(closes), frein_krach(closes, p['krach'])
    on, lev, out = True, 0.0, []
    for i, c in enumerate(closes):
        if ma[i] > 0:
            rel = c / ma[i] - 1
            if on and rel < p['sortie_tendance']:
                on = False
            elif not on and rel > p['retour_tendance']:
                on = True
        v = max(v10[i], v20[i])
        vise = min(p['plafond'], p['cible'] / v) if v > 0 else 0.0
        if not on:
            vise *= p['tendance_baissiere']
        if br[i]:
            lev = 0.0
        elif abs(vise - lev) > p['ecart_min']:
            lev = vise
        out.append(dict(levier=lev, levier_vise=vise, vol10=v10[i], vol20=v20[i], mm200=ma[i],
                        tendance_haussiere=on, frein_krach=br[i]))
    return out
