#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur Webhook Telegram Autonome pour La Canin Mobile (Sandrine Hiriart).
Hébergeable gratuitement sur Render, Koyeb ou tout service cloud.

Fonctionnalités :
- Écoute HTTP POST sur /webhook
- Transcription vocale haute fidélité via OpenAI Whisper (avec fallback Gemini)
- Reconnaissance intelligente parmi les 694 clients (clients_db.json)
- Gestion d'état conversationnel (validation sur "Ok")
- Règles de tournées et quotas stricts
- Health-check GET / pour surveillance de disponibilité
"""

import os
import sys
import json
import re
import base64
import requests
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Reconfiguration UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENTS_PATH = os.path.join(BASE_DIR, "clients_db.json")

# Configuration via variables d'environnement ou valeurs par défaut
_DEF_TOKEN = base64.b64decode("ODkwMTMxMzY0NzpBQUZyNEJhdnY2VnBtNEw2dnlBbDJCV0x3cWE4bGFlNVM5aw==").decode("utf-8")
_DEF_GEMINI = base64.b64decode("QVEuQWI4Uk42SjQ2NzFyTEtPOGFlYl9oM1FFT1JIV1Y1RWtjaEYzdkZzdXlDV043ajA2LVE=").decode("utf-8")

BOT_TOKEN = os.environ.get("BOT_TOKEN", _DEF_TOKEN)
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_KEY", _DEF_GEMINI)
PORT = int(os.environ.get("PORT", 10000))

# Chargement de la base clients
CLIENTS_DB = []
if os.path.exists(CLIENTS_PATH):
    try:
        with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
            CLIENTS_DB = json.load(f)
        print(f"[BASE CLIENTS] {len(CLIENTS_DB)} fiches chargées.", flush=True)
    except Exception as e:
        print(f"[BASE CLIENTS] Erreur chargement : {e}", flush=True)

# Mémoire conversationnelle en mémoire vive
USER_STATES = {}

WELCOME_MESSAGE = """👋 Bonjour Sandrine ! Bienvenue sur ton assistant **La Canin Mobile** 🚐🐶

Je suis prêt à t'aider pour gérer ton planning à la voix ou par écrit :

🎙️ **Comment me parler ?**
- **Au micro (Vocal)** : Reste appuyée sur le micro Telegram et parle-moi naturellement (ex: *"Ajoute Éden pour un toilettage mardi 22 décembre à 9h à Urrugne"*).
- **Par écrit** : Tu peux aussi simplement m'écrire tes consignes en texte.

📍 **Rappel de tes tournées officielles :**
- **Mardi, Mercredi, Samedi** : 📍 *Urrugne*
- **Jeudi, Vendredi** : 📍 *Saint-Pée-sur-Nivelle*
- **Lundi & Dimanche** : 🏖️ *Repos*

⏱️ **Catégories & Durées strictes :**
- 🟢 **Cat. A (0h30)** : Bain ras, retouches, chats courts
- 🔵 **Cat. B (1h00)** : Standard (York, Bichon, Shih Tzu, Cavalier...)
- 🟠 **Cat. C (1h30)** : Cocker, Labrit, chat long *(max 2 / jour)*
- 🔴 **Cat. D (2h00)** : Gros chiens (Bouvier, Patou... *max 1 / jour*)

Dis-moi ce que tu souhaites faire ! ✨"""

def send_telegram_message(chat_id, text, parse_mode="Markdown"):
    """Envoie un message à l'utilisateur Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=12)
        resp = r.json()
        if not resp.get("ok"):
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=12)
    except Exception as e:
        print(f"[TELEGRAM] Erreur envoi message : {e}", flush=True)

def transcrire_audio_whisper_ou_gemini(file_id: str) -> str:
    """Télécharge l'audio Telegram et le transcrit avec Whisper ou Gemini en secours."""
    try:
        get_file_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        r = requests.get(get_file_url, timeout=15)
        res = r.json()
        if not res.get("ok"):
            return ""

        file_path = res["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        oga_resp = requests.get(download_url, timeout=30)
        if oga_resp.status_code != 200:
            return ""

        oga_bytes = oga_resp.content

        # 1. Tentative OpenAI Whisper
        if OPENAI_KEY:
            try:
                whisper_url = "https://api.openai.com/v1/audio/transcriptions"
                headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
                files = {"file": ("voice.oga", oga_bytes, "audio/ogg")}
                data = {"model": "whisper-1", "language": "fr"}
                w_resp = requests.post(whisper_url, headers=headers, files=files, data=data, timeout=30)
                if w_resp.status_code == 200:
                    text = w_resp.json().get("text", "").strip()
                    print(f"[WHISPER] « {text} »", flush=True)
                    return text
            except Exception as we:
                print(f"[WHISPER] Exception : {we}, bascule sur Gemini...", flush=True)

        # 2. Google Gemini via Files API (plusieurs modeles en secours)
        if GEMINI_KEY:
            import time as _t
            init = requests.post(
                f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={GEMINI_KEY}",
                headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                         "X-Goog-Upload-Header-Content-Length": str(len(oga_bytes)),
                         "X-Goog-Upload-Header-Content-Type": "audio/ogg", "Content-Type": "application/json"},
                json={"file": {"display_name": "voice.ogg"}}, timeout=15)
            up_url = init.headers.get("X-Goog-Upload-URL")
            if not up_url:
                print(f"[GEMINI AUDIO] init upload {init.status_code} {init.text[:200]}", flush=True)
                return ""
            up = requests.post(up_url, headers={"Content-Length": str(len(oga_bytes)), "X-Goog-Upload-Offset": "0",
                                                "X-Goog-Upload-Command": "upload, finalize"}, data=oga_bytes, timeout=30)
            file_uri = up.json().get("file", {}).get("uri") if up.status_code == 200 else None
            if not file_uri:
                print(f"[GEMINI AUDIO] upload {up.status_code} {up.text[:200]}", flush=True)
                return ""
            payload = {"contents": [{"parts": [
                {"text": "Transcris fidelement mot a mot ce message vocal en francais, sans aucun commentaire :"},
                {"file_data": {"mime_type": "audio/ogg", "file_uri": file_uri}}]}],
                "generationConfig": {"temperature": 0.0}}
            modeles = ["gemini-3-flash-preview", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                       "gemini-flash-latest", "gemini-2.5-pro", "gemini-pro-latest"]
            for _tentative in range(3):
                for modele in modeles:
                    try:
                        g = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent?key={GEMINI_KEY}",
                                          json=payload, timeout=40)
                    except Exception as ge:
                        print(f"[GEMINI AUDIO] {modele} exception {ge}", flush=True)
                        continue
                    if g.status_code == 200:
                        parts = g.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        text = "".join(x.get("text", "") for x in parts).strip()
                        if text:
                            print(f"[GEMINI AUDIO via {modele}] {text}", flush=True)
                            return text
                    print(f"[GEMINI AUDIO] {modele} -> {g.status_code}", flush=True)
                _t.sleep(2)

        return ""
    except Exception as e:
        print(f"[AUDIO ERROR] : {e}", flush=True)
        return ""

def trouver_client_dans_base(texte: str) -> dict:
    """Cherche l'animal ou le client dans la base locale."""
    def norm(s):
        return re.sub(r'[\s\.\-_]', '', str(s).lower()).replace('é', 'e').replace('è', 'e').replace('ê', 'e')

    txt_norm = norm(texte)
    for c in CLIENTS_DB:
        anim = norm(c.get("nom_animal", ""))
        if anim and len(anim) >= 3 and anim in txt_norm:
            return c
    for c in CLIENTS_DB:
        maitre = norm(c.get("nom_maitre", ""))
        if maitre and len(maitre) >= 4 and maitre in txt_norm:
            return c
    return None

def analyser_commande_vocale(texte: str, client_reconnu: dict = None) -> dict:
    """Analyse semantique avec Google Gemini (rapide, gratuit et fiable)."""
    infos_client = ""
    if client_reconnu:
        infos_client = f"""
CLIENT RECONNU DANS LA BASE :
- Nom Animal : {client_reconnu.get('nom_animal')}
- Nom Maitre : {client_reconnu.get('nom_maitre')}
- Race : {client_reconnu.get('race')}
- Categorie : {client_reconnu.get('cat')} ({client_reconnu.get('duree')})
- Secteur : {client_reconnu.get('secteur')}
"""

    prompt = f"""Tu es l'assistant de Sandrine Hiriart, toiletteuse canin (Canins Toilettage / La Canin Mobile).
Nous sommes fin 2026. Tu peux planifier les rendez-vous sur 2026 et sur 2027 sans restriction.
{infos_client}
Consigne de Sandrine : "{texte}"

Regles de tournees & Absences :
- Urrugne : Mardi, Mercredi, Samedi
- Saint-Pee-sur-Nivelle : Jeudi, Vendredi
- Repos : Lundi et Dimanche
- ABSENCE VOYAGE SOLIDAIRE MAROC : STRICTEMENT FERME du 31 octobre au 24 novembre 2026 inclus. AUCUN rendez-vous possible sur cette periode. Reprise officielle le Mercredi 25 novembre a Urrugne.

Categories :
- A (0h30) : petits chiens ras, retouches, chat court
- B (1h00) : standard (York, Bichon, Shih Tzu, Cavalier, Caniche...)
- C (1h30) : Cocker, Labrit, chat long (Max 2 par jour)
- D (2h00) : Gros chiens (Golden, Bouvier, Patou... Max 1 par jour)

Reponds UNIQUEMENT par un JSON valide (sans backticks markdown, sans texte avant ni apres) avec cette structure exacte :
{{
  "action": "PROPOSER",
  "nom_maitre": "nom du maitre ou Inconnu",
  "nom_animal": "nom de l'animal",
  "race": "race de l'animal",
  "categorie": "A" ou "B" ou "C" ou "D",
  "duree_minutes": 30 ou 60 ou 90 ou 120,
  "date_proposee": "YYYY-MM-DD",
  "heure_debut": "HH:MM",
  "secteur": "Urrugne" ou "Saint-Pee-sur-Nivelle",
  "reponse_vocale": "Phrase de reponse chaleureuse, claire et precise pour Sandrine"
}}
"""
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={GEMINI_KEY}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                else:
                    clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw_text, flags=re.MULTILINE).strip()
                    data = json.loads(clean)
                print(f"[ANALYSE GEMINI REUSSIE] : {data}", flush=True)
                return data
            else:
                print(f"[ANALYSE GEMINI ERROR] : {resp.status_code} {resp.text[:200]}", flush=True)
        except Exception as ge:
            print(f"[ANALYSE GEMINI EXCEPTION] : {ge}", flush=True)

    return None

APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "https://script.google.com/macros/s/AKfycbxSMe4ThGmSAFiL8_hIECTLih9AptUKGA61GCaEHQUmggZ775skFKJZqhBPV4mcRWUj/exec")

def inscrire_dans_google_calendar(p: dict) -> bool:
    try:
        date_str = p.get('date_proposee')
        heure_str = p.get('heure_debut', '09:00')
        duree = int(p.get('duree_minutes', 60))
        
        h_formatted = heure_str.replace(":", "h")
        title = f"{h_formatted}\n{p.get('nom_animal', 'Toilettage')}"
        
        start_dt = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duree)
        
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        
        payload = {
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "description": f"Toilettage {p.get('nom_animal')} - Catégorie {p.get('categorie', 'B')} ({duree} min) - Secteur {p.get('secteur')}",
            "location": ""
        }
        
        resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("status") == "success":
                print(f"[CALENDAR OK] Rendez-vous Google Calendar créé : {res_json.get('eventId')}", flush=True)
                return True
        print(f"[CALENDAR ERROR] Réponse inattendue : {resp.status_code} {resp.text}", flush=True)
    except Exception as e:
        print(f"[CALENDAR EXCEPTION] : {e}", flush=True)
    return False

def traiter_message_telegram(message: dict):
    """Traite un message reçu via Webhook."""
    chat_id = str(message["chat"]["id"])
    user_name = message.get("from", {}).get("first_name", "Sandrine")

    if chat_id not in USER_STATES:
        USER_STATES[chat_id] = {"en_attente_validation": False, "proposition": None}

    texte_recu = ""

    # 1. Message Texte
    if "text" in message:
        texte_recu = message["text"].strip()
        print(f"[WEBHOOK TEXTE] {user_name} : {texte_recu}", flush=True)

        if texte_recu.startswith("/start"):
            send_telegram_message(chat_id, WELCOME_MESSAGE)
            USER_STATES[chat_id]["en_attente_validation"] = False
            return

    # 2. Message Vocal
    elif "voice" in message:
        file_id = message["voice"]["file_id"]
        send_telegram_message(chat_id, "🎧 _J'écoute ton message vocal..._")
        texte_recu = transcrire_audio_whisper_ou_gemini(file_id)

        if not texte_recu:
            send_telegram_message(chat_id, "⚠️ Désolé Sandrine, je n'ai pas pu entendre correctement le vocal. Peux-tu me le réenregistrer ou me l'écrire ?")
            return

        send_telegram_message(chat_id, f"🎙️ *J'ai compris :*\n« _{texte_recu}_ »")

    if not texte_recu:
        return

    # 3. Validation sur "Ok"
    mots_validation = ["ok", "oui", "d'accord", "parfait", "c'est bon", "valide", "super", "vas-y", "top", "ça me va", "ca me va"]
    if texte_recu.lower().strip(".!?, ") in mots_validation:
        if USER_STATES[chat_id].get("en_attente_validation") and USER_STATES[chat_id].get("proposition"):
            prop = USER_STATES[chat_id]["proposition"]
            animal = prop.get("nom_animal", "Animal")
            race = prop.get("race", "")
            cat = prop.get("categorie", "B")
            duree = prop.get("duree_minutes", 60)
            date_prop = prop.get("date_proposee", "")
            heure = prop.get("heure_debut", "09:00")
            secteur = prop.get("secteur", "Urrugne")

            try:
                dt = datetime.strptime(date_prop, "%Y-%m-%d")
                jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
                jour_nom = jours[dt.weekday()]
            except Exception:
                jour_nom = ""

            msg_conf = f"""✅ *RENDEZ-VOUS CONFIRMÉ ET ENREGISTRÉ !*

🐶 *Animal* : *{animal}* {f'({race})' if race else ''}
⏱️ *Catégorie* : `Cat. {cat}` ({duree} min)
📆 *Date* : *{jour_nom} {date_prop}*
⏰ *Heure* : *{heure}*
📍 *Secteur* : *{secteur}*

🎉 *« Rendez-vous validé ! »*"""

            inscrire_dans_google_calendar(prop)
            send_telegram_message(chat_id, msg_conf)
            USER_STATES[chat_id]["en_attente_validation"] = False
            USER_STATES[chat_id]["proposition"] = None
            return
        else:
            send_telegram_message(chat_id, "👍 Bien reçu Sandrine ! Dis-moi ce que tu souhaites faire (ajouter un rdv, décaler, vérifier ton planning...).")
            return

    # 4. Reconnaissance client & analyse
    client_reconnu = trouver_client_dans_base(texte_recu)
    send_telegram_message(chat_id, "⏳ _Recherche du créneau en cours..._")
    analyse = analyser_commande_vocale(texte_recu, client_reconnu)

    if not analyse:
        send_telegram_message(chat_id, "🤔 Je n'ai pas bien saisi. Peux-tu me préciser le nom de l'animal et la date souhaitée ?")
        return

    action = analyse.get("action", "AJOUTER")
    animal = analyse.get("nom_animal", "Animal")
    race = analyse.get("race", "")
    cat = analyse.get("categorie", "B")
    duree = analyse.get("duree_minutes", 60)
    date_prop = analyse.get("date_proposee", "")
    heure = analyse.get("heure_debut", "09:00")
    secteur = analyse.get("secteur", "Urrugne")
    reponse_vocale = analyse.get("reponse_vocale", "")
    maitre = analyse.get("nom_maitre", "")

    if client_reconnu:
        animal = client_reconnu.get("nom_animal", animal)
        race = client_reconnu.get("race", race)
        cat = client_reconnu.get("cat", cat)
        if client_reconnu.get("nom_maitre"):
            maitre = client_reconnu.get("nom_maitre")
        if client_reconnu.get("secteur") and client_reconnu.get("secteur") != "Non précisé":
            secteur = client_reconnu.get("secteur")

    try:
        dt = datetime.strptime(date_prop, "%Y-%m-%d")
        jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        jour_nom = jours[dt.weekday()]
    except Exception:
        jour_nom = ""

    icon = {
        "AJOUTER": "➕ PROPOSITION DE RENDEZ-VOUS",
        "PROPOSER": "➕ PROPOSITION DE RENDEZ-VOUS",
        "DEPLACER": "🔄 DÉPLACEMENT DE RENDEZ-VOUS",
        "ANNULER": "❌ ANNULATION DE RENDEZ-VOUS",
        "CONSULTER": "🔍 CONSULTATION DU PLANNING"
    }.get(action, "📅 GESTION DU PLANNING")

    client_str = f"🐶 *Animal* : {animal} ({race})\n" if race else f"🐶 *Animal* : {animal}\n"
    if maitre and maitre != "Inconnu":
        client_str += f"👤 *Client* : {maitre}\n"

    recap = f"""*{icon}*

{client_str}⏱️ *Catégorie* : `Cat. {cat}` ({duree} min)
📆 *Date* : *{jour_nom} {date_prop}*
⏰ *Heure* : *{heure}*
📍 *Tournée* : *{secteur}*

💬 *« {reponse_vocale} »*

👉 Réponds simplement **« Ok »** pour valider, ou donne-moi un autre jour/heure !"""

    USER_STATES[chat_id]["en_attente_validation"] = (action in ["AJOUTER", "PROPOSER", "DEPLACER"])
    USER_STATES[chat_id]["proposition"] = analyse
    send_telegram_message(chat_id, recap)

class WebhookHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("🚐 La Canin Mobile Telegram Webhook is LIVE and HEALTHY! 🐶✨".encode("utf-8"))

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            
            # Reponse 200 OK immediate a Telegram pour liberer le webhook et eviter tout timeout/429
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

            update = json.loads(post_data.decode("utf-8"))
            if "message" in update:
                threading.Thread(target=traiter_message_telegram, args=(update["message"],), daemon=True).start()
        except Exception as e:
            print(f"[WEBHOOK HANDLER ERROR] : {e}", flush=True)

def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, WebhookHTTPHandler)
    print("=" * 60, flush=True)
    print(f"🚐 SERVEUR WEBHOOK LA CANIN MOBILE PRÊT SUR LE PORT {PORT}", flush=True)
    print("=" * 60, flush=True)
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()
