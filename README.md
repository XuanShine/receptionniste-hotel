# Réceptionniste Hôtel

Réception IA du Panorama Hotel. Version 1 : un appel entrant 3CX arrive sur LiveKit Cloud par un trunk SIP, puis un agent Python répond avec Grok Voice.

```
Appelant → 3CX → SIP (PCMU, port 5060) → LiveKit → agent Python → Grok Voice
```

Appels entrants seulement. Pas de réservation, pas de PMS, pas d'appel sortant.

## Prérequis

- Python 3.12 et [uv](https://docs.astral.sh/uv/)
- Un projet [LiveKit Cloud](https://cloud.livekit.io/)
- Une clé API [xAI](https://console.x.ai/) (`XAI_API_KEY`)
- L'adresse IP publique du PBX 3CX

## Configuration

```bash
cp .env.example .env
```

Renseigner dans `.env` :

| Variable | Rôle |
| --- | --- |
| `LIVEKIT_URL` | URL WebSocket du projet, `wss://….livekit.cloud` |
| `LIVEKIT_API_KEY` | Clé API LiveKit |
| `LIVEKIT_API_SECRET` | Secret API LiveKit |
| `XAI_API_KEY` | Clé xAI, lue par le plugin Grok Voice |
| `SIP_TRUNK_NAME` | Nom du trunk entrant, défaut `3cx-panorama` |
| `SIP_ALLOWED_ADDRESSES` | IP publique de 3CX en CIDR, ex. `203.0.113.10/32` |
| `SIP_NUMBERS` | Numéros E.164 acceptés, séparés par des virgules. Vide : tout numéro venant de l'IP autorisée |
| `AGENT_NAME` | Nom dispatché, défaut `receptionniste` |
| `GROK_VOICE` | Voix Grok, défaut `Ara` |

`.env` n'est pas versionné. Les scripts le chargent au démarrage.

```bash
uv sync
```

## Trunk et dispatch

Le script crée le trunk et la règle s'ils n'existent pas. Il ne supprime ni ne modifie une ressource déjà présente.

```bash
uv run python src/provision_sip.py
```

Il affiche l'identifiant du trunk, l'identifiant de la règle, et l'hôte SIP à coller dans 3CX. Pour `wss://sous-domaine.livekit.cloud`, l'hôte est `sous-domaine.sip.livekit.cloud`, port `5060`.

La règle ouvre une room `call-…` par appel et dispatche l'agent `AGENT_NAME` sur ce trunk.

## Agent

```bash
uv run python src/agent.py console
```

`console` parle à Grok depuis le micro, sans 3CX. Utile pour vérifier la clé xAI et la voix.

```bash
uv run python src/agent.py dev
```

`dev` enregistre le worker sur LiveKit Cloud. Il doit tourner pour qu'un appel soit décroché. Le modèle est celui du plugin (`grok-voice-think-fast-1.0`), avec la détection de tour côté Grok.

L'agent accueille en français. Il ne consulte aucun système hôtelier.

Le worker écrit un fichier texte en français par appel dans `record/`, console compris. Ce dossier n'est pas versionné.

## Trunk 3CX

Dans l'admin 3CX, créer un trunk **Generic SIP, IP-based, sans registration** :

- Hôte : la valeur `Hôte SIP 3CX` affichée par le script de provision. Port **5060**, transport UDP.
- Authentification : aucune. LiveKit filtre par `SIP_ALLOWED_ADDRESSES`.
- Codecs : **PCMU** en premier, PCMA ensuite. Désactiver G.729 et G.722.
- Caller ID sortant : numéro d'origine (`$OriginatorCallerID`), pour que LiveKit voie l'appelant.
- Route : le DID de la réception, ou une extension de test, est renvoyé vers ce trunk.

L'IP que 3CX présente à LiveKit est l'IP publique du PBX, pas l'adresse du LAN. C'est celle de `SIP_ALLOWED_ADDRESSES`.

## Vérifier un appel

1. `uv run python src/provision_sip.py` se termine sans erreur API.
2. `uv run python src/agent.py console` : Grok répond au micro.
3. `uv run python src/agent.py dev` : le worker s'enregistre.
4. Appeler depuis 3CX. L'agent décroche, accueille en français, et une room `call-…` apparaît dans LiveKit.
