# Réceptionniste Hôtel

Réception téléphonique de l'Hôtel Panorama, à Grasse. La version 1
s'enregistre sur un trunk ou une extension SIP 3CX, décroche, joue
« Bonjour, je suis le réceptionniste de l'Hôtel Panorama », puis raccroche.

Le comportement, le choix de PJSUA2 et la suite (LiveKit, Grok Voice, Opus)
sont décrits dans [SPEC.md](SPEC.md).

## Prérequis

- Python 3.10, 3.11 ou 3.12, sur Linux x86_64, macOS ou Windows amd64.
- Un compte SIP 3CX qui accepte l'enregistrement (extension, ou trunk avec
  enregistrement).
- Sur cette extension : PCMA, PCMU ou G.722. La roue PJSUA2 utilisée ici ne
  contient pas Opus.

Python 3.13 et Linux aarch64 (Raspberry Pi) n'ont pas de roue précompilée.
Il faut alors compiler pjproject. Le détail est dans SPEC.md.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Renseigner dans `.env` :

| Variable | Exemple | Rôle |
| --- | --- | --- |
| `SIP_USER` | `100` | Identifiant SIP |
| `SIP_PASSWORD` | | Mot de passe digest |
| `SIP_DOMAIN` | `pbx.hotel-panorama.fr` | Hôte du PBX, sans `sip:` et sans port |
| `SIP_PORT` | `5060` | Port du registrar |

Si l'identifiant d'authentification 3CX diffère de l'utilisateur, ajouter
`SIP_AUTH_USER`. Si le processus n'est pas joignable sur son adresse locale,
ajouter `SIP_PUBLIC_ADDRESS` (adresse que 3CX doit rappeler) et ouvrir le
port SIP ainsi que la plage RTP/RTCP `4000`–`4021`.

Ne pas commiter `.env`.

## Lancement

Vérifier la configuration et le fichier audio, sans s'enregistrer :

```bash
python -m reception --check
```

Démarrer la réception :

```bash
python -m reception
```

Le journal indique l'état d'enregistrement (`200 OK`, actif) puis chaque
appel : décrochage, lecture, raccrochage. `Ctrl+C` désenregistre le compte
et quitte.

Côté 3CX, router un numéro de test vers cette extension et appeler depuis
un poste ou un mobile. L'appelant entend la phrase, puis la communication
se termine.

## Tests

Aucune de ces commandes n'a besoin d'un trunk ni d'un mot de passe réel.

```bash
pip install -r requirements-dev.txt
pytest
```

Le test de lecture PJSUA2 joue le WAV sur un périphérique audio nul et
vérifie que la fin de fichier n'arrive qu'une fois. Il est ignoré si la
roue native n'est pas installée.

## Régénérer l'annonce

L'audio versionné est `assets/greeting_fr.wav` (PCM 16 bits, 16 kHz, mono),
produit avec gTTS puis ffmpeg. Le programme lit ce fichier : il n'appelle
pas de service de synthèse pendant un appel.

```bash
pip install -r requirements-tts.txt
python scripts/generate_greeting.py
```

`ffmpeg` doit être installé. Si gTTS ne répond pas, le script utilise
`espeak-ng` quand il est présent. La phrase vient de
`reception/greeting.py` ; il ne faut pas en écrire une deuxième copie.
