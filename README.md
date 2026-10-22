# Canin Mobile Sandrine - Bot Webhook Serveur

Serveur Webhook cloud autonome pour le bot Telegram vocal de Sandrine Hiriart (@canin_mobile_sandrine_bot).

## Caractéristiques
- Serveur HTTP léger et autonome pour hébergement cloud (Render / Koyeb)
- Transcription vocale instantanée via OpenAI Whisper (modèle whisper-1)
- Intelligence de qualification des prestations canines / félines (A, B, C, D)
- Règles de tournée sectorielle : Urrugne (Mar/Mer/Sam) et Saint-Pée-sur-Nivelle (Jeu/Ven)
- Endpoints :
  - `GET /health` : Monitoring de vie du serveur
  - `POST /webhook` : Réception des mises à jour Telegram (messages texte et vocaux)
