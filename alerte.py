"""Alerte en séance : notification dès que le S&P 500 touche −5 % sous la clôture de la veille.

Lancé toutes les 5 minutes pendant la séance américaine par le workflow « Alerte −5 % ».
Une seule notification par jour (la date de la dernière alerte est gardée dans alerte.json).
    python alerte.py                       # surveillance normale
    python alerte.py --test-notification   # envoie un exemple d'alerte pour vérifier
Variable d'environnement : NTFY_TOPIC, SITE_URL (facultatif).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, time
from zoneinfo import ZoneInfo

NY, PARIS = ZoneInfo("America/New_York"), ZoneInfo("Europe/Paris")
SEUIL = -0.05
ICI = os.path.dirname(os.path.abspath(__file__))
FICHIER = os.path.join(ICI, "alerte.json")
ETAT = next((p for p in (os.path.join(ICI, "etat.json"), os.path.join(ICI, "docs", "etat.json")) if os.path.exists(p)), "etat.json")


def pct(x):
    return f"{x * 100:+.1f}".replace(".", ",").replace("-", "−") + " %"


def envoyer(titre, texte):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise SystemExit("NTFY_TOPIC absent : ajoute le secret dans GitHub.")
    corps = json.dumps(dict(topic=topic, title=titre, message=texte, priority=5, tags=["warning"],
                            click=os.environ.get("SITE_URL", "").strip() or None)).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=corps, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print("Notification envoyée :", titre, r.status)


def message(var_bas, var, heure):
    """Texte de l'alerte, avec le levier des futures en place pendant la séance (calculé la veille au soir)."""
    texte = (f"Le S&P 500 a touché {pct(var_bas)} par rapport à la clôture d'hier ; il est à {pct(var)} à {heure} (heure de Paris). ")
    try:
        cto = json.load(open(ETAT))["cto"]
        lev = cto["levier_veille"]          # levier détenu pendant la séance d'aujourd'hui
        if lev > 1:
            texte += (f"Futures : levier en place {lev:.1f}×".replace(".", ",") + ", l'ordre conditionnel doit le ramener à 1×. "
                      "Vérifie dans IBKR qu'il a bien été exécuté. Le soir, tu recevras le levier à reprendre demain.")
        else:
            texte += f"Futures : levier en place {lev:.1f}×, déjà à 1× ou moins, rien à faire.".replace(".", ",", 1)
    except Exception:
        texte += "Vérifie dans IBKR que l'ordre conditionnel des futures a bien été exécuté."
    return texte


def main():
    if "--test-notification" in sys.argv:
        envoyer("Exemple d'alerte : S&P 500 à −5,3 %", "Ceci est un test. " + message(-0.053, -0.048, "16 h 42"))
        return
    now = datetime.now(NY)
    if now.weekday() >= 5 or not (time(9, 30) <= now.time() <= time(16, 5)):
        print("Bourse américaine fermée, rien à surveiller.")
        return
    today = now.date().isoformat()
    try:
        if json.load(open(FICHIER)).get("date") == today:
            print("Alerte déjà envoyée aujourd'hui.")
            return
    except (OSError, ValueError):
        pass

    # Cours : barre journalière du jour (en cours) et barres d'une minute ; on garde le plus bas des deux.
    # Si Yahoo ne répond pas, on n'échoue pas : le lancement suivant, 5 minutes plus tard, réessaie.
    try:
        import yfinance as yf
        t = yf.Ticker("^GSPC")
        jours = t.history(period="10d", interval="1d", auto_adjust=False)
        veille = [float(r.Close) for ix, r in jours.iterrows() if ix.date().isoformat() < today and r.Close == r.Close]
        bas_l, der = [], None
        for ix, r in jours.iterrows():
            if ix.date().isoformat() == today and r.Low == r.Low:
                bas_l.append(float(r.Low)); der = float(r.Close)
        try:
            seance = t.history(period="1d", interval="1m", auto_adjust=False)
            seance = seance[[ix.date().isoformat() == today for ix in seance.index]].dropna(subset=["Low", "Close"])
            if len(seance):
                bas_l.append(float(seance.Low.min())); der = float(seance.Close.iloc[-1])
        except Exception as e:
            print("Barres d'une minute indisponibles :", e)
    except Exception as e:
        print("Yahoo Finance indisponible, nouvel essai au prochain lancement :", e)
        return
    if not veille or not bas_l:
        print("Pas encore de cours pour aujourd'hui.")
        return
    cloture, bas, dernier = veille[-1], min(bas_l), der
    var_bas, var = bas / cloture - 1, dernier / cloture - 1
    print(f"S&P 500 : clôture d'hier {cloture:.2f}, plus bas du jour {bas:.2f} ({pct(var_bas)}), dernier {dernier:.2f} ({pct(var)})")
    if var_bas <= SEUIL:
        heure = datetime.now(PARIS).strftime("%H h %M")
        envoyer(f"S&P 500 à {pct(var_bas)} aujourd'hui", message(var_bas, var, heure))
        json.dump(dict(date=today, plus_bas=var_bas), open(FICHIER, "w"))


if __name__ == "__main__":
    main()
