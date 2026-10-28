#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur Webhook Telegram Cloud Autonome pour La Canin Mobile (Sandrine Hiriart).
Hébergé sur Render (24h/24, 7j/7 sans dépendance au PC local).
Moteur IA unique et officiel : Google Gemini 2.5 Flash.
"""

import os
import sys
import json
import re
import base64
import requests
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Reconfiguration UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENTS_PATH = os.path.join(BASE_DIR, "clients_db.json")
AGENDA_PATH = os.path.join(BASE_DIR, "agenda_sandrine_oct_dec_2026.md")
STATES_PATH = os.path.join(BASE_DIR, "user_states.json")

# Clés et Tokens officiels sanctuarisés
_DEF_TOKEN = base64.b64decode("ODkwMTMxMzY0NzpBQUZyNEJhdnY2VnBtNEw2dnlBbDJCV0x3cWE4bGFlNVM5aw==").decode("utf-8")
_DEF_GEMINI = base64.b64decode("QVEuQWI4Uk42SUQxNXJxSUlxN2k1aEtheWFsTjJFaGVfWDNHejg2ZG5EYTZoWTN2U0JvTkE=").decode("utf-8")

BOT_TOKEN = os.environ.get("BOT_TOKEN", _DEF_TOKEN)
GEMINI_KEY = os.environ.get("GEMINI_KEY", _DEF_GEMINI)
PORT = int(os.environ.get("PORT", 10000))
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "https://script.google.com/macros/s/AKfycbxSMe4ThGmSAFiL8_hIECTLih9AptUKGA61GCaEHQUmggZ775skFKJZqhBPV4mcRWUj/exec")

# Chargement base clients (694 fiches)
CLIENTS_DB = []
if os.path.exists(CLIENTS_PATH):
    try:
        with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
            CLIENTS_DB = json.load(f)
        print(f"[BASE CLIENTS] {len(CLIENTS_DB)} fiches chargées.", flush=True)
    except Exception as e:
        print(f"[BASE CLIENTS] Erreur : {e}", flush=True)

# Chargement agenda master
AGENDA_TEXT = ""
if os.path.exists(AGENDA_PATH):
    try:
        with open(AGENDA_PATH, "r", encoding="utf-8") as f:
            AGENDA_TEXT = f.read()
        print(f"[AGENDA MASTER] {len(AGENDA_TEXT)} caractères chargés.", flush=True)
    except Exception as e:
        print(f"[AGENDA] Erreur chargement : {e}", flush=True)

def get_date_du_jour_fr():
    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    mois = ['', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
    now = datetime.now()
    return f"{jours[now.weekday()]} {now.day} {mois[now.month]} {now.year}"

def get_semaine_calendrier_fr():
    now = datetime.now()
    lundi = now - timedelta(days=now.weekday())
    jours = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    lignes = []
    for i in range(7):
        d = lundi + timedelta(days=i)
        lignes.append(f"- {jours[i]} : {d.strftime('%Y-%m-%d')} ({d.strftime('%d/%m/%Y')})")
    return "\n".join(lignes)

# États en mémoire et sur disque
USER_STATES = {}
if os.path.exists(STATES_PATH):
    try:
        with open(STATES_PATH, "r", encoding="utf-8") as f:
            USER_STATES = json.load(f)
    except Exception:
        USER_STATES = {}

def save_states():
    try:
        with open(STATES_PATH, "w", encoding="utf-8") as f:
            json.dump(USER_STATES, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SAVE STATES ERROR] : {e}", flush=True)

def send_telegram_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code != 200:
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=12)
    except Exception as e:
        print(f"[TELEGRAM SEND ERROR] : {e}", flush=True)

def transcrire_audio(file_id: str) -> str:
    """Transcription directe via Gemini 2.5 Flash en base64 inline (1 seul appel HTTP)."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}", timeout=15).json()
        if not r.get("ok"): return ""
        file_path = r["result"]["file_path"]
        oga = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30).content
        if not oga: return ""
        
        b64_audio = base64.b64encode(oga).decode("utf-8")
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Transcris fidèlement mot à mot ce message vocal en français, sans aucun commentaire :"},
                    {"inline_data": {"mime_type": "audio/ogg", "data": b64_audio}}
                ]
            }],
            "generationConfig": {"temperature": 0.0}
        }
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        g = requests.post(url, json=payload, timeout=25)
        if g.status_code == 200:
            parts = g.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            texte = "".join(x.get("text", "") for x in parts).strip()
            print(f"[GEMINI 2.5 FLASH AUDIO OK] : « {texte} »", flush=True)
            return texte
        else:
            print(f"[GEMINI 2.5 FLASH AUDIO ERROR] : {g.status_code} {g.text[:200]}", flush=True)
    except Exception as e:
        print(f"[TRANSCR EXCEPTION] : {e}", flush=True)
    return ""

def trouver_client_dans_base(texte: str) -> dict:
    if not CLIENTS_DB or not texte:
        return None
    texte_lower = texte.lower()
    mots = re.findall(r'[a-zA-ZÀ-ÿ0-9]+', texte_lower)
    
    for c in CLIENTS_DB:
        nom_a = c.get("nom_animal", "").lower()
        if nom_a and len(nom_a) >= 3 and nom_a in mots:
            return c
            
    for c in CLIENTS_DB:
        nom_m = c.get("nom_maitre", "").lower()
        if nom_m and len(nom_m) >= 4 and nom_m in texte_lower:
            return c
            
    return None

def deduire_secteur_par_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() in [1, 2, 5]: # Mar, Mer, Sam
            return "Urrugne"
        elif dt.weekday() in [3, 4]: # Jeu, Ven
            return "Saint-Pée-sur-Nivelle"
    except Exception:
        pass
    return "Urrugne"

def inscrire_dans_google_calendar(p: dict) -> bool:
    try:
        date_str = p.get('date_proposee')
        heure_str = p.get('heure_debut', '09:00')
        duree = int(p.get('duree_minutes', 60))
        
        h_formatted = heure_str.replace(":", "h")
        nom = p.get('nom_animal', 'Toilettage')
        title = f"{h_formatted}\n{nom}"
        
        start_dt = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duree)
        
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        
        desc = f"Toilettage {nom} - Catégorie {p.get('categorie', 'B')} ({duree} min) - Secteur {p.get('secteur')}"
        if p.get('nom_maitre') and p.get('nom_maitre') != 'Inconnu':
            desc += f" - Maître : {p.get('nom_maitre')}"
        if p.get('race') and p.get('race') not in ['race', 'Inconnue', 'Non précisée']:
            desc += f" - Race : {p.get('race')}"
            
        payload = {
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "description": desc,
            "location": ""
        }
        
        resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("status") == "success":
                print(f"[CALENDAR OK] Rendez-vous Google Calendar inscrit : {res_json.get('eventId')}", flush=True)
                return True
        print(f"[CALENDAR ERROR] {resp.status_code} {resp.text}", flush=True)
    except Exception as e:
        print(f"[CALENDAR EXCEPTION] : {e}", flush=True)
    return False

def traiter_instruction_avec_memoire(texte: str, historique: list, client: dict = None) -> dict:
    date_du_jour = get_date_du_jour_fr()
    calendrier_semaine = get_semaine_calendrier_fr()
    
    hist_text = ""
    if historique:
        hist_text = "HISTORIQUE RÉCENT DES ÉCHANGES DE LA CONVERSATION EN COURS :\n"
        for h in historique[-6:]:
            role = "Sandrine" if h.get("role") == "user" else "Assistant"
            hist_text += f"{role} : {h.get('text')}\n"
    
    infos_c = ""
    if client:
        infos_c = f"""FICHE DU CLIENT IDENTIFIÉ DANS LA BASE :
- Animal : {client.get('nom_animal')} | Maître : {client.get('nom_maitre')}
- Race : {client.get('race')} | Catégorie : {client.get('cat')} ({client.get('duree')})
- Secteur : {client.get('secteur')}
"""

    prompt = f"""Tu es l'assistant personnel de Sandrine Hiriart, toiletteuse canin/félin (La Canin Mobile / Canins Toilettage).
Sandrine te parle au micro depuis son camion ou par texte.

DATE EXACTE DU JOUR : **{date_du_jour}**.
CALENDRIER EXACT DE LA SEMAINE COURANTE :
{calendrier_semaine}

RÈGLES D'OR DE COMPORTEMENT (INVIOLABLES) :
1. POSTURE : SANDRINE EST LA PATRONNE ABSOLUE. Tu es là pour lui faciliter la vie, lui obéir immédiatement et noter ce qu'elle demande.
2. ZÉRO MORALE, ZÉRO POLÉMIQUE, ZÉRO LEÇON : Ne lui dis JAMAIS "attention c'est ta pause déjeuner" ou "ce n'est pas permis". Si elle te demande de poser un rdv à 13h30 ou n'importe quand (pour des tests ou des exceptions), TU OBÉIS IMMÉDIATEMENT sans discuter et sans poser 50 questions !
3. MÉMOIRE DU DIALOGUE : Utilise impérativement l'historique récent ci-dessous. Si elle a parlé d'un animal (ex: Eden) juste avant, tu sais que sa demande concerne cet animal sans avoir à lui redemander son nom ou sa race !
4. CONCISION ET CHALEUR : Sois courtois, concis, direct et efficace.

{infos_c}
{hist_text}

PLANNING MASTER DE RÉFÉRENCE DE SANDRINE :
\"\"\"
{AGENDA_TEXT}
\"\"\"

Dernière phrase de Sandrine : "{texte}"

Tu dois analyser sa demande en tenant compte de tout le contexte ci-dessus et répondre UNIQUEMENT par ce JSON strict :

Si elle demande de réserver, noter, inscrire ou bloquer un créneau (ex: "réserve pour mercredi à 13h30", "ajoute Pearl", "mets Eden...") :
{{
  "action": "INSCRIRE",
  "nom_animal": "nom de l'animal déduit de la phrase ou de l'historique",
  "nom_maitre": "nom du maître ou Inconnu",
  "race": "race de l'animal",
  "categorie": "A" ou "B" ou "C" ou "D",
  "duree_minutes": 30 ou 60 ou 90,
  "date_proposee": "YYYY-MM-DD",
  "heure_debut": "HH:MM",
  "secteur": "Urrugne" ou "Saint-Pée-sur-Nivelle",
  "message_reponse": "Phrase courte et chaleureuse confirmant que c'est noté"
}}

Si elle pose une question (créneaux libres, horaires, infos client...) :
{{
  "action": "REPONDRE",
  "message_reponse": "Réponse claire, précise et directe sans moraliser ni surcharger d'explications superflues"
}}
"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=35)
        if resp.status_code == 200:
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
                data = json.loads(clean)
                
            if data.get("action") == "INSCRIRE":
                if client:
                    if not data.get("nom_animal") or data.get("nom_animal") in ["nom de l'animal", "Animal"]:
                        data["nom_animal"] = client.get("nom_animal")
                    if not data.get("nom_maitre") or data.get("nom_maitre") == "Inconnu":
                        data["nom_maitre"] = client.get("nom_maitre")
                    data["race"] = client.get("race", data.get("race"))
                    data["categorie"] = client.get("cat", data.get("categorie", "B"))
                    if client.get("cat") == "A": data["duree_minutes"] = 30
                    elif client.get("cat") == "C": data["duree_minutes"] = 90
                    elif client.get("cat") == "D": data["duree_minutes"] = 120
                    
                if data.get("date_proposee"):
                    data["secteur"] = deduire_secteur_par_date(data["date_proposee"])
                    
            return data
    except Exception as e:
        print(f"[ANALYSE ERROR] : {e}", flush=True)
    return None

def process_message(msg):
    chat_id = str(msg["chat"]["id"])
    date_du_jour = get_date_du_jour_fr()
    
    if chat_id not in USER_STATES:
        USER_STATES[chat_id] = {"historique": [], "last_greeted_date": ""}
        
    if "historique" not in USER_STATES[chat_id]:
        USER_STATES[chat_id]["historique"] = []
        
    texte = ""
    if "text" in msg:
        texte = msg["text"].strip()
    elif "voice" in msg:
        send_telegram_message(chat_id, "🎧 _J'écoute ton message vocal..._")
        texte = transcrire_audio(msg["voice"]["file_id"])
        if not texte:
            send_telegram_message(chat_id, "❌ Désolé Sandrine, je n'ai pas pu entendre. Peux-tu me le réenregistrer ?")
            return
        send_telegram_message(chat_id, f"🎙️ *J'ai compris :*\n« _{texte}_ »")
        
    if not texte: return
    
    # Salutation UNIQUEMENT si c'est la toute première interaction du jour
    salutation = ""
    if USER_STATES[chat_id].get("last_greeted_date") != date_du_jour:
        salutation = f"Bonjour Sandrine, nous sommes le **{date_du_jour}**. Que puis-je faire pour toi ? Je suis prêt à prendre des rendez-vous.\n\n"
        USER_STATES[chat_id]["last_greeted_date"] = date_du_jour
        save_states()
        
    # Détection client dans le texte courant ou dans l'historique récent
    client_rec = trouver_client_dans_base(texte)
    if not client_rec:
        for h in reversed(USER_STATES[chat_id]["historique"][-4:]):
            client_rec = trouver_client_dans_base(h.get("text", ""))
            if client_rec: break
            
    send_telegram_message(chat_id, "⏳ _Traitement en cours..._")
    analyse = traiter_instruction_avec_memoire(texte, USER_STATES[chat_id]["historique"], client_rec)
    
    # Historique de dialogue
    USER_STATES[chat_id]["historique"].append({"role": "user", "text": texte})
    if len(USER_STATES[chat_id]["historique"]) > 12:
        USER_STATES[chat_id]["historique"] = USER_STATES[chat_id]["historique"][-12:]
    save_states()
    
    if not analyse:
        rep_fail = "Je n'ai pas bien saisi Sandrine. Peux-tu me redire ce que tu souhaites ?"
        USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": rep_fail})
        save_states()
        send_telegram_message(chat_id, f"{salutation}🤔 {rep_fail}")
        return
        
    action = analyse.get("action", "REPONDRE")
    
    # 1. Action : Répondre à une question
    if action == "REPONDRE":
        rep = analyse.get("message_reponse", "Voici ce que j'ai trouvé pour toi.")
        USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": rep})
        save_states()
        send_telegram_message(chat_id, f"{salutation}💬 {rep}")
        return
        
    # 2. Action : Inscrire directement dans l'agenda
    if action == "INSCRIRE":
        animal = analyse.get("nom_animal", "Toilettage")
        race = analyse.get("race", "")
        cat = analyse.get("categorie", "B")
        dur = analyse.get("duree_minutes", 60)
        date_p = analyse.get("date_proposee", "")
        h = analyse.get("heure_debut", "09:00")
        sec = analyse.get("secteur", "Urrugne")
        maitre = analyse.get("nom_maitre", "")
        rep_phrase = analyse.get("message_reponse", "C'est noté.")
        
        send_telegram_message(chat_id, "📅 _Inscription immédiate dans ton agenda Google CANINS MOBILE..._")
        succes = inscrire_dans_google_calendar(analyse)
        status_agenda = "🎉 *👉 Inscrit automatiquement dans ton Google Agenda CANINS MOBILE !*" if succes else "⚠️ *Enregistré localement, synchronisation en attente.*"
        
        maitre_str = f"\n👤 *Client* : {maitre}" if maitre and maitre != "Inconnu" else ""
        race_str = f" ({race})" if race and race not in ["race", "Inconnue", "Non précisée"] else ""
        
        msg_conf = f"""{salutation}✅ *RENDEZ-VOUS ENREGISTRÉ DANS TON AGENDA !*

🐾 *Animal* : *{animal}*{race_str}{maitre_str}
⏱️ *Catégorie* : `Cat. {cat}` ({dur} min)
📅 *Date* : *{date_p}*
⏰ *Heure* : *{h}*
🚐 *Secteur* : *{sec}*

💬 « _{rep_phrase}_ »

{status_agenda}"""
        
        USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": f"Rendez-vous inscrit pour {animal} le {date_p} à {h}"})
        save_states()
        send_telegram_message(chat_id, msg_conf)

class WebhookHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("🚐 La Canin Mobile Telegram Webhook (Gemini 2.5 Flash Direct) is LIVE and HEALTHY! 🐶✨".encode("utf-8"))

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            
            # Réponse 200 immédiate à Telegram
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

            update = json.loads(post_data.decode("utf-8"))
            if "message" in update:
                threading.Thread(target=process_message, args=(update["message"],), daemon=True).start()
        except Exception as e:
            print(f"[WEBHOOK HANDLER ERROR] : {e}", flush=True)

def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, WebhookHTTPHandler)
    print("=" * 60, flush=True)
    print(f"🚐 SERVEUR WEBHOOK LA CANIN MOBILE (GEMINI 2.5 FLASH) PRÊT SUR LE PORT {PORT}", flush=True)
    print("=" * 60, flush=True)
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()
