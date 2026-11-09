"""Suivi quotidien des deux stratégies : calcule la composition visée, écrit etat.json et
historique.json pour le site, et envoie une notification ntfy quand un ordre est à passer.

À lancer chaque jour de bourse après la clôture américaine (le workflow GitHub s'en charge).
    python suivi.py                    # données Yahoo Finance
    python suivi.py --test donnees.json  # données de test, sans notification
Variable d'environnement : NTFY_TOPIC (nom secret du canal de notification), SITE_URL (facultatif).
"""
import json
import os
import sys
import urllib.request
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from regles import CTO, PEA, signaux_cto, signaux_pea

NY, PARIS = ZoneInfo("America/New_York"), ZoneInfo("Europe/Paris")
ICI = os.path.dirname(os.path.abspath(__file__))
ETAT = os.path.join(ICI, "etat.json")
HISTO = os.path.join(ICI, "historique.json")
# Coupe-circuit du PEA : dans le simulateur Nasdaq, il ne sert que s'il est exécuté au cours de clôture du jour
# de la baisse. La notification arrivant après la fermeture d'Euronext, il ne pourrait être appliqué que le
# lendemain, et rejoué ainsi il n'apporte rien (1986-2026 : 26,4 % par an et −60 % de pire baisse, comme sans
# coupe-circuit). Il reste donc affiché à titre d'information, sans changer la part de LQQ.
COUPE_CIRCUIT_PEA = False
TICKERS = {"ndx": "^NDX", "spx": "^GSPC", "es": "ES=F", "eurusd": "EURUSD=X"}


# ---------------------------------------------------------------- données
def charger_yahoo():
    import yfinance as yf
    series = {}
    for cle, tk in TICKERS.items():
        df = None
        for _ in range(3):
            try:
                df = yf.Ticker(tk).history(period="5y", interval="1d", auto_adjust=False)
                if len(df):
                    break
            except Exception as e:  # coupure réseau passagère : on réessaie
                print("Yahoo", tk, e)
        if df is None or not len(df):
            raise SystemExit(f"Pas de données pour {tk}")
        series[cle] = [dict(d=ix.date().isoformat(), o=float(r.Open), h=float(r.High), l=float(r.Low), c=float(r.Close))
                       for ix, r in df.iterrows() if r.Close == r.Close]
    return series


def sans_seance_en_cours(barres, maintenant):
    """Retire la barre du jour si la séance américaine n'est pas encore close (clôture partielle)."""
    ny = maintenant.astimezone(NY)
    if barres and barres[-1]["d"] == ny.date().isoformat() and ny.time() < time(16, 20):
        return barres[:-1]
    return barres


# ---------------------------------------------------------------- calendrier
def prochaine_seance(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def heure_paris(jour, h, m):
    """Heure de Paris correspondant à h:m à New York ce jour-là (gère les décalages d'heure d'été)."""
    t = datetime.combine(jour, time(h, m), NY).astimezone(PARIS)
    return f"{t.hour} h {t.minute:02d}"


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def jour_fr(d):
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def pct(x, signe=False):
    s = f"{x * 100:+.1f}" if signe else f"{x * 100:.1f}"
    return s.replace(".", ",").replace("-", "−") + " %"


def lev(x):
    return f"{x:.1f}".replace(".", ",") + "×"


# ---------------------------------------------------------------- calcul
def calculer(series):
    ndx, spx = series["ndx"], series["spx"]
    sp = signaux_pea([b["c"] for b in ndx])
    sc = signaux_cto([b["c"] for b in spx])
    # coupe-circuit : plus bas de la séance à 5 % ou plus sous la clôture de la veille
    cc_ndx = [i > 0 and b["l"] > 0 and b["l"] / ndx[i - 1]["c"] - 1 <= -PEA["coupe_circuit"] for i, b in enumerate(ndx)]
    cc_spx = [i > 0 and b["l"] > 0 and b["l"] / spx[i - 1]["c"] - 1 <= -CTO["coupe_circuit"] for i, b in enumerate(spx)]
    # part de LQQ à détenir après l'ordre du lendemain (coupe-circuit : 50 % au plus pour une séance)
    part = [min(s["part"], 0.5) if COUPE_CIRCUIT_PEA and cc_ndx[i] else s["part"] for i, s in enumerate(sp)]
    return sp, sc, part, cc_ndx, cc_spx


def construire_etat(series, maintenant, ancien):
    ndx, spx = series["ndx"], series["spx"]
    sp, sc, part, cc_ndx, cc_spx = calculer(series)
    i, j = len(ndx) - 1, len(spx) - 1
    es, eurusd = series["es"][-1]["c"], series["eurusd"][-1]["c"]
    seance = date.fromisoformat(spx[j]["d"])
    demain = prochaine_seance(seance)
    s, c = sp[i], sc[j]

    pea = dict(
        seance=ndx[i]["d"], cloture=ndx[i]["c"], variation=ndx[i]["c"] / ndx[i - 1]["c"] - 1,
        plus_bas=ndx[i]["l"] / ndx[i - 1]["c"] - 1,
        part=part[i], part_veille=part[i - 1], palier_vol=s["palier_vol"], vol7=s["vol7"],
        ecart_haut20=s["ecart_haut20"], frein_rapide=s["frein_rapide"], frein_krach=s["frein_krach"],
        coupe_circuit=cc_ndx[i], coupe_circuit_applique=COUPE_CIRCUIT_PEA, seuils=PEA)
    cto = dict(
        seance=spx[j]["d"], cloture=spx[j]["c"], variation=spx[j]["c"] / spx[j - 1]["c"] - 1,
        plus_bas=spx[j]["l"] / spx[j - 1]["c"] - 1,
        levier=c["levier"], levier_veille=sc[j - 1]["levier"], levier_vise=c["levier_vise"],
        vol10=c["vol10"], vol20=c["vol20"], mm200=c["mm200"], ecart_mm200=spx[j]["c"] / c["mm200"] - 1 if c["mm200"] else 0,
        tendance_haussiere=c["tendance_haussiere"], frein_krach=c["frein_krach"],
        coupe_circuit=cc_spx[j], levier_seance=sc[j - 2]["levier"], coupe_circuit_actif=cc_spx[j] and sc[j - 2]["levier"] > 1,
        es=es, eurusd=eurusd, mes_point_usd=5, seuils=CTO,
        heure_ordre=f"{heure_paris(demain, 15, 40)} – {heure_paris(demain, 15, 55)}")

    actions, alertes = [], []
    # ---- PEA
    if pea["part"] != pea["part_veille"]:
        baisse = pea["part"] < pea["part_veille"]
        if COUPE_CIRCUIT_PEA and pea["coupe_circuit"] and baisse:
            raison = "coupe-circuit, le Nasdaq-100 a touché " + pct(pea["plus_bas"], True) + " en séance aujourd'hui (pour une séance)"
        elif s["frein_krach"]:
            raison = "frein de krach (plus de 20 % sous le plus haut sur 12 mois et sous la MM200)"
        elif s["frein_rapide"] and s["palier_vol"] > pea["part"]:
            raison = "frein rapide, le Nasdaq-100 est " + pct(s["ecart_haut20"], True) + " sous son plus haut des 20 séances"
        elif COUPE_CIRCUIT_PEA and not baisse and cc_ndx[i - 1]:
            raison = "fin du coupe-circuit d'hier"
        else:
            raison = "volatilité du Nasdaq-100 sur 7 séances à " + pct(s["vol7"])
        avant, apres = int(round(pea["part_veille"] * 100)), int(round(pea["part"] * 100))
        actions.append(dict(compte="PEA", titre=f"PEA : LQQ de {avant} % à {apres} %",
                            texte=f"{'Baisser' if baisse else 'Remonter'} la part de LQQ de {avant} % à {apres} % du PEA "
                                  f"(le reste en ETF monétaire), {jour_fr(demain)} sur Euronext, de préférence entre 17 h et 17 h 25. "
                                  f"Raison : {raison}."))
    # ---- CTO
    if abs(cto["levier"] - cto["levier_veille"]) > 1e-9:
        raison = ("frein de krach" if c["frein_krach"] else "tendance baissière (levier × 0,25)" if not c["tendance_haussiere"] and cto["levier"] < cto["levier_veille"]
                  else "volatilité 10/20 jours à " + pct(max(c["vol10"], c["vol20"])))
        actions.append(dict(compte="CTO", titre=f"CTO : futures S&P 500 à {lev(cto['levier'])}",
                            texte=f"Passer le levier des futures S&P 500 de {lev(cto['levier_veille'])} à {lev(cto['levier'])}, "
                                  f"{jour_fr(demain)} juste avant la clôture ({cto['heure_ordre']}, heure de Paris). Raison : {raison}. "
                                  f"Nombre de MES et ordre conditionnel à mettre à jour : voir le site."))
    if cto["coupe_circuit_actif"]:
        alertes.append(dict(compte="CTO", titre="CTO : coupe-circuit déclenché",
                            texte=f"Le S&P 500 a touché {pct(cto['plus_bas'], True)} aujourd'hui avec un levier de {lev(cto['levier_seance'])} en séance : "
                                  f"l'ordre conditionnel a dû ramener le levier à 1×. Vérifie qu'il a bien été exécuté. "
                                  f"{jour_fr(demain).capitalize()}, juste avant la clôture, revenir au levier de {lev(cto['levier'])} et replacer l'ordre conditionnel."))
    if demain.year != seance.year:
        alertes.append(dict(compte="CTO", titre="CTO : rééquilibrage annuel",
                            texte="Premier jour de bourse de l'année : après le paiement de l'impôt, ramener l'ETF managed futures à 50 % du compte."))

    etat = dict(
        maj=maintenant.astimezone(PARIS).isoformat(timespec="minutes"), seance=seance.isoformat(), prochaine=demain.isoformat(),
        pea=pea, cto=cto, actions=actions, alertes=alertes,
        journal=(ancien or {}).get("journal", []))
    deja = (ancien or {}).get("seance") == etat["seance"]
    if not deja:
        for a in actions + alertes:
            etat["journal"].insert(0, dict(seance=etat["seance"], **a))
        etat["journal"] = etat["journal"][:60]
    histo = [dict(d=ndx[k]["d"], ndx=round(ndx[k]["c"], 2), part=part[k]) for k in range(max(0, i - 520), i + 1)]
    histo_cto = [dict(d=spx[k]["d"], spx=round(spx[k]["c"], 2), lev=round(sc[k]["levier"], 3)) for k in range(max(0, j - 520), j + 1)]
    return etat, dict(pea=histo, cto=histo_cto), deja


# ---------------------------------------------------------------- notification
def notifier(etat, topic, site):
    msgs = etat["actions"] + etat["alertes"]
    for m in msgs:
        corps = json.dumps(dict(topic=topic, title=m["titre"], message=m["texte"], priority=4 if m in etat["actions"] else 5,
                                tags=["chart_with_upwards_trend"], click=site or None)).encode()
        req = urllib.request.Request("https://ntfy.sh/", data=corps, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            print("Notification envoyée :", m["titre"], r.status)
    if not msgs:
        print("Rien à faire aujourd'hui, pas de notification.")


def notifier_test(topic, site):
    corps = json.dumps(dict(topic=topic, title="Suivi des stratégies : test", message="Les notifications fonctionnent. "
                            "Tu recevras un message le soir après la clôture américaine quand un ordre sera à passer le lendemain.",
                            tags=["white_check_mark"], click=site or None)).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=corps, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print("Notification de test envoyée", r.status)


def main():
    if "--test-notification" in sys.argv:
        topic = os.environ.get("NTFY_TOPIC", "").strip()
        if not topic:
            raise SystemExit("NTFY_TOPIC absent : ajoute le secret dans GitHub (voir LISEZMOI).")
        notifier_test(topic, os.environ.get("SITE_URL", "").strip())
    test = "--test" in sys.argv
    maintenant = datetime.now(tz=PARIS)
    if test:
        series = json.load(open(sys.argv[sys.argv.index("--test") + 1]))
    else:
        series = {k: sans_seance_en_cours(v, maintenant) for k, v in charger_yahoo().items()}
    ancien = json.load(open(ETAT)) if os.path.exists(ETAT) else None
    etat, histo, deja = construire_etat(series, maintenant, ancien)
    os.makedirs(os.path.dirname(ETAT), exist_ok=True)
    json.dump(etat, open(ETAT, "w"), ensure_ascii=False, indent=1)
    json.dump(histo, open(HISTO, "w"), ensure_ascii=False, separators=(",", ":"))
    print("Séance du", etat["seance"], "| PEA", etat["pea"]["part"], "| CTO", round(etat["cto"]["levier"], 2), "|", len(etat["actions"]), "action(s)")
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if deja:
        print("Séance déjà traitée : pas de nouvelle notification.")
    elif test or not topic:
        print("Pas de notification (mode test ou NTFY_TOPIC absent).")
    else:
        notifier(etat, topic, os.environ.get("SITE_URL", "").strip())


if __name__ == "__main__":
    main()
