# Réceptionniste Hôtel

Réception IA du Panorama Hotel. Version 1 : un appel entrant 3CX arrive sur LiveKit Cloud par un trunk SIP, puis un agent Python répond. Cartesia entend et parle. Grok répond.

```
Appelant → 3CX → SIP → LiveKit → agent Python
                                  ├ entend : Cartesia ink-2, par LiveKit Inference
                                  ├ répond : Grok 4.3, par LiveKit Inference (repli xAI si XAI_API_KEY)
                                  └ parle  : Cartesia sonic-3, voix Henri
```

Appels entrants seulement. Pas d'appel sortant. Les disponibilités et les réservations passent par WuBook Wired.

## Prérequis

- Python 3.12 et [uv](https://docs.astral.sh/uv/)
- Un projet [LiveKit Cloud](https://cloud.livekit.io/). LiveKit Inference transcrit, appelle Grok et synthétise la voix.
- Une clé API [xAI](https://console.x.ai/) (`XAI_API_KEY`), facultative. Elle sert si LiveKit Inference refuse une requête Grok.
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
| `XAI_API_KEY` | Facultatif. Si LiveKit Inference refuse une requête Grok, l'agent la renvoie directement à xAI |
| `CARTESIA_VOICE_ID` | Voix Cartesia, défaut Henri `d9f4af15-c402-4f50-bbda-d8823d028d6a` |
| `SIP_TRUNK_NAME` | Nom du trunk entrant, défaut `3cx-panorama` |
| `SIP_ALLOWED_ADDRESSES` | IP publique de 3CX en CIDR, ex. `203.0.113.10/32` |
| `SIP_NUMBERS` | Numéros E.164 acceptés, séparés par des virgules. Vide : tout numéro venant de l'IP autorisée |
| `AGENT_NAME` | Nom dispatché, défaut `receptionniste` |
| `WUBOOK_TOKEN` | Jeton permanent WuBook Wired |
| `WUBOOK_LCODE` | Code entier de l'établissement WuBook |

`.env` n'est pas versionné. Les scripts le chargent au démarrage.

Installer les dépendances, puis lancer les tests.

```bash
uv sync
uv run python -m unittest discover -s tests
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

`console` parle depuis le micro, sans 3CX. Ce premier appel vérifie ink-2. La liste des modèles LiveKit du 2026-10-19 marque ink-2 comme anglais seulement.

```bash
uv run python src/agent.py dev
```

`dev` enregistre le worker sur LiveKit Cloud. Il doit tourner pour qu'un appel soit décroché. L'agent entend avec `cartesia/ink-2`, répond avec `xai/grok-4.3` et parle avec `cartesia/sonic-3`, voix Henri. Le SDK ajoute le VAD Silero et son détecteur de tour. La phrase de transfert n'est pas interruptible. La synthèse reste en français, même si les instructions autorisent l'anglais ou l'italien. La dépendance reste `livekit-agents[xai]~=1.8`. LiveKit Inference est dans le paquet. L'extra `xai` sert au repli.

L'agent accueille en français. Pour un séjour daté, il interroge WuBook. Il n'annonce aucun prix que l'outil n'a pas renvoyé.

Si l'appelant demande à parler à un réceptionniste, l'agent dit « Très bien, je vous transfère à mon collègue. » puis envoie un SIP REFER vers le poste 10. La cible par défaut est `sip:10@hotelpanorama.on3cx.fr`. LiveKit joue la tonalité d'attente et laisse sonner 60 secondes.

| Variable | Rôle |
| --- | --- |
| `SIP_TRANSFER_TO` | URI REFER complet (`sip:` ou `tel:`). S'il est défini, il remplace le poste et l'hôte |
| `SIP_TRANSFER_EXTENSION` | Poste, défaut `10` |
| `SIP_TRANSFER_HOST` | Hôte du PBX dans l'URI, défaut `hotelpanorama.on3cx.fr` |
| `SIP_OUTBOUND_TRUNK_ID` | Trunk sortant LiveKit. Utilisé seulement si le REFER est refusé : l'agent appelle alors le poste et le fait entrer dans la room |

Sur le trunk 3CX **LiveKitGrok**, activer **Allow Carrier Side Transfers (SIP REFER)**. Sans cette option, 3CX répond 403 et l'appelant reste avec l'agent, qui donne le 04 93 36 80 80. Le repli `SIP_OUTBOUND_TRUNK_ID` suppose un trunk sortant LiveKit vers 3CX, et une route entrante de ce trunk qui envoie le numéro composé (le poste `10`) vers l'extension 10.

## WuBook

L'agent parle à WuBook Wired en XML-RPC, sur `https://wired.wubook.net/xrws/`. Le slash final fait partie de l'adresse. `WUBOOK_TOKEN` est le jeton permanent. `WUBOOK_LCODE` est le code de l'établissement. Chaque réponse est une paire `[code, résultat]`. `0` veut dire que l'appel a réussi. Un code négatif est une erreur. Les codes `-1`, `-10`, `-13`, `-17`, `-18` et `-19` deviennent un message explicite.

`verifier_disponibilite_et_prix` lit `fetch_rooms_values` et `fetch_plan_prices` pour le plan `0`. La date de fin de ces deux lectures est incluse. La dernière nuit cotée est donc le jour de départ moins un jour. L'outil ne garde que les chambres réelles `329039`, `329667`, `329670`, `407751`, `469743` et `469744`, après les avoir vérifiées avec `fetch_rooms`. Une chambre est proposée si elle est libre chaque nuit, sans fermeture, si le séjour minimum est respecté, si le séjour maximum l'est aussi quand WuBook en pose un, et si `occupancy` couvre les adultes et les enfants. Le prix vient du plan. Une nuit sous 45 € écarte la chambre. La réponse donne le total et le prix de chaque nuit.

`creer_reservation` relit la disponibilité et le prix, puis appelle `new_reservation` avec `status` `1`, `ignore_restrs` `0`, `ignore_avail` `0`, `ccard` `0` et `origin` `vocal`. Il relit ensuite la réservation avec `fetch_booking` et renvoie le code lu. Aucune carte n'est demandée au téléphone.

Le catalogue des chambres et les grilles restent en cache 5 minutes. `fetch_rooms_values` est limité à 240 appels par 12 h. La relecture qui précède une réservation n'utilise pas ce cache. Les tests ouvrent un serveur XML-RPC local. Ils n'appellent pas WuBook.

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
