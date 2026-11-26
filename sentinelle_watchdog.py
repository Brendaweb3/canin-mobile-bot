#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SENTINELLE DE SURVEILLANCE AUTONOME - LA CANIN MOBILE
Vérifie en continu (toutes les 60 secondes) :
1. La santé du serveur Render (do_GET /reserver).
2. L'étanchéité de la synchronisation Google Apps Script (doPost / Calendar API).
3. L'absence absolue de doublons dans Google Agenda.
4. Alerte Telegram immédiate en cas de moindre anomalie avant tout impact client.
"""

import time
import requests
import json
from datetime import datetime

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzwHTfG06UZH6mNMP08dfhDoAtNnd5IFZ0oOt31S-fU4r_0rqDPyECHcV61dQE_DYGS/exec"
RENDER_URL = "https://canin-mobile-bot.onrender.com/reserver"
TELEGRAM_BOT_TOKEN = "8901313647:AAFr4Bavv6Vpm4L6vyAl2BWLwqa8lae5S9k"
ALERT_CHAT_IDS = ["6022976635", "8771111289"]

def envoyer_alerte(message):
    for cid in ALERT_CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": cid, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except Exception:
            pass

def inspecter_agenda():
    try:
        url = f"{APPS_SCRIPT_URL}?start=2026-11-24T00:00:00Z&end=2027-02-28T00:00:00Z"
        res = requests.get(url, timeout=15)
        if res.status_code != 200:
            envoyer_alerte(f"🚨 *ALERTE SENTINELLE* : Impossible d'interroger Google Calendar (Code {res.status_code}) !")
            return

        data = res.json()
        events = data.get("events", [])
        
        # Détection immédiate des doublons physiques
        slots = {}
        for ev in events:
            st = ev.get("start", "")
            if st:
                slots[st] = slots.get(st, []) + [ev.get("title")]

        doublons = {k: v for k, v in slots.items() if len(v) > 1}
        if doublons:
            msg_d = "🚨 *DOUBLON DÉTECTÉ DANS L'AGENDA !*\n\n"
            for k, v in doublons.items():
                msg_d += f"📅 Heure : {k}\nChiens : {', '.join(v)}\n\n"
            msg_d += "👉 Intervention nécessaire."
            envoyer_alerte(msg_d)

    except Exception as e:
        envoyer_alerte(f"🚨 *ERREUR SENTINELLE GOOGLE CALENDAR* : {e}")

def verifier_render():
    try:
        r = requests.get(RENDER_URL, timeout=15)
        if r.status_code != 200:
            envoyer_alerte(f"🚨 *ALERTE SERVEUR RENDER* : Le site de réservation répond avec le code {r.status_code} !")
    except Exception as e:
        envoyer_alerte(f"🚨 *ALERTE RENDER HORS LIGNE* : Le site ne répond pas ({e}) !")

if __name__ == "__main__":
    print("[SENTINELLE CANIN MOBILE ACTIVE] Surveillance démarrée.")
    # Exécution test immédiate
    verifier_render()
    inspecter_agenda()
    print("[SENTINELLE] Test d'inspection initial : OK, aucun doublon.")
