# Réceptionniste Hôtel

Réception IA du Panorama Hotel. Version 1 : un appel entrant 3CX arrive sur LiveKit Cloud par un trunk SIP, puis un agent Python répond avec Grok Voice.

```
Appelant → 3CX → SIP (PCMU, port 5060) → LiveKit → agent Python → Grok Voice
```

Appels entrants seulement. Pas d'appel sortant. Les disponibilités passent par WuBook Wired. L'agent ne crée pas la réservation : après le devis, il laisse un message pour qu'un réceptionniste la prenne.

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
| `WUBOOK_TOKEN` | Jeton permanent WuBook Wired |
| `WUBOOK_LCODE` | Code entier de l'établissement WuBook |

`.env` n'est pas versionné. Les scripts le chargent au démarrage.

```bash
uv sync
```

## Trunk et dispatch

Le script crée le trunk et la règle s'ils n'existent pas. Il ne supprime rien. Sur un trunk déjà présent, il ne change ni l'allowlist ni les numéros. Il pose `headers_to_attributes` s'il manque, par `UpdateSIPInboundTrunk`. Un second passage ne rappelle pas l'API.

```bash
uv run python src/provision_sip.py
```

Cette commande met à jour le trunk entrant nommé `SIP_TRUNK_NAME`. En production, c'est `ST_BxA4aAxSS3y4`. Le mappage est `Remote-Party-ID` vers `sip.rpid` et `P-Asserted-Identity` vers `sip.pai`. Le script affiche l'identifiant du trunk, l'identifiant de la règle, et l'hôte SIP à coller dans 3CX. Pour `wss://sous-domaine.livekit.cloud`, l'hôte est `sous-domaine.sip.livekit.cloud`, port `5060`.

La CLI `lk` n'a pas de flag pour ce champ. Un fichier JSON remplace tout le trunk. Le fichier doit donc reprendre les champs déjà en place, dont `numbers` et `allowed_addresses`. Le script Python ci-dessus fait la mise à jour sans les effacer.

```bash
lk sip inbound update --id ST_BxA4aAxSS3y4 - <<'EOF'
{
  "headers_to_attributes": {
    "Remote-Party-ID": "sip.rpid",
    "P-Asserted-Identity": "sip.pai"
  }
}
EOF
```

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

L'agent accueille en français. Pour un séjour daté, il interroge WuBook. Il n'annonce aucun prix que l'outil n'a pas renvoyé.

Au démarrage d'un appel, l'agent lit le display name entre guillemets de `sip.rpid`, puis celui de `sip.pai`, puis `sip.phoneNumber`. Une valeur vide, `anonymous`, ou le numéro de l'hôtel `0033422480521` est ignorée. Les formes `+33422480521`, `0422480521`, et ces mêmes valeurs avec un préfixe `sip:` ou `tel:` comptent aussi comme l'hôtel. `0651216491` et `+33651216491` sont des numéros de client. S'il reste un numéro, l'agent demande « Est-ce que je peux utiliser le numéro depuis lequel vous appelez ? ». Il ne le met dans `prendre_message` qu'après un oui. Sinon il demande le téléphone.

Pour une question sur l'hôtel ou Grasse qui n'est pas dans les instructions, l'agent dit « Je regarde ça. » et appelle l'API xAI avec l'outil `web_search`. Le délai est de 8 secondes. Si la recherche échoue, il propose un collègue.

Si l'appelant demande à parler à un réceptionniste, l'agent dit « Très bien, je vous transfère à mon collègue. » puis envoie un SIP REFER vers le poste 10. La cible par défaut est `sip:10@hotelpanorama.on3cx.fr`. LiveKit joue la tonalité d'attente et laisse sonner 60 secondes.

| Variable | Rôle |
| --- | --- |
| `SIP_TRANSFER_TO` | URI REFER complet (`sip:` ou `tel:`). S'il est défini, il remplace le poste et l'hôte |
| `SIP_TRANSFER_EXTENSION` | Poste, défaut `10` |
| `SIP_TRANSFER_HOST` | Hôte du PBX dans l'URI, défaut `hotelpanorama.on3cx.fr` |
| `SIP_OUTBOUND_TRUNK_ID` | Trunk sortant LiveKit. Utilisé seulement si le REFER est refusé : l'agent appelle alors le poste et le fait entrer dans la room |

Sur le trunk 3CX **LiveKitGrok**, activer **Allow Carrier Side Transfers (SIP REFER)**. Sans cette option, 3CX répond 403 et l'appelant reste avec l'agent, qui donne le 04 93 36 80 80. Le repli `SIP_OUTBOUND_TRUNK_ID` suppose un trunk sortant LiveKit vers 3CX, et une route entrante de ce trunk qui envoie le numéro composé (le poste `10`) vers l'extension 10.

Le worker écrit un fichier texte en français par appel dans `record/`, console compris. Ce dossier n'est pas versionné.

## WuBook

L'agent parle à WuBook Wired en XML-RPC, sur `https://wired.wubook.net/xrws/`. Le slash final fait partie de l'adresse. `WUBOOK_TOKEN` est le jeton permanent. `WUBOOK_LCODE` est le code de l'établissement. Chaque réponse est une paire `[code, résultat]`. `0` veut dire que l'appel a réussi. Un code négatif est une erreur. Les codes `-1`, `-10`, `-13`, `-17`, `-18` et `-19` deviennent un message explicite.

`verifier_disponibilite_et_prix` lit `fetch_rooms_values` et `fetch_plan_prices` pour le plan `0`. La date de fin de ces deux lectures est incluse. La dernière nuit cotée est donc le jour de départ moins un jour. L'outil ne garde que les chambres réelles `329039`, `329667`, `329670`, `407751`, `469743` et `469744`, après les avoir vérifiées avec `fetch_rooms`. Une chambre est proposée si elle est libre chaque nuit, sans fermeture, si le séjour minimum est respecté, si le séjour maximum l'est aussi quand WuBook en pose un, et si `occupancy` est exactement 2 pour une ou deux personnes, 3 pour trois personnes, 4 pour quatre. Au-delà, aucune chambre n'est proposée. Le prix vient du plan. Une nuit sous 45 € écarte la chambre. La réponse donne le total et le prix de chaque nuit.

L'agent n'expose pas d'outil de réservation. Après un devis et une confirmation du client, il appelle `prendre_message` avec les dates, la chambre, les personnes, le prix, l'email et les suppléments. Un réceptionniste crée ensuite la réservation. Aucune carte n'est demandée au téléphone. `WubookService.reserver` reste dans le client WuBook et n'est pas appelé par l'agent.

Le catalogue des chambres et les grilles restent en cache 5 minutes. `fetch_rooms_values` est limité à 240 appels par 12 h. La relecture qui précède une réservation n'utilise pas ce cache. Les tests ouvrent un serveur XML-RPC local. Ils n'appellent pas WuBook.

## Trunk 3CX

Dans l'admin 3CX, créer un trunk **Generic SIP, IP-based, sans registration** :

- Hôte : la valeur `Hôte SIP 3CX` affichée par le script de provision. Port **5060**, transport UDP.
- Authentification : aucune. LiveKit filtre par `SIP_ALLOWED_ADDRESSES`.
- Codecs : **PCMU** en premier, PCMA ensuite. Désactiver G.729 et G.722.
- Caller ID sortant : numéro d'origine (`$OriginatorCallerID`), pour que LiveKit voie l'appelant.
- Route : le DID de la réception, ou une extension de test, est renvoyé vers ce trunk.

3CX force la partie user à `0033422480521`, le numéro principal du trunk. Le numéro du client est le display name de `Remote-Party-ID` et de `P-Asserted-Identity`. LiveKit ne le copie dans `sip.rpid` et `sip.pai` qu'après le mappage du trunk.

L'IP que 3CX présente à LiveKit est l'IP publique du PBX, pas l'adresse du LAN. C'est celle de `SIP_ALLOWED_ADDRESSES`.

## Vérifier un appel

1. `uv run python src/provision_sip.py` se termine sans erreur API.
2. `uv run python src/agent.py console` : Grok répond au micro.
3. `uv run python src/agent.py dev` : le worker s'enregistre.
4. Appeler depuis 3CX. L'agent décroche, accueille en français, et une room `call-…` apparaît dans LiveKit.
