"""WuBook Wired pour le réceptionniste vocal.

Le catalogue et les grilles restent 5 minutes en cache. fetch_rooms_values
est limité à 240 appels par 12 h. La réservation relit la grille sans cache.
"""

from __future__ import annotations

import threading
import time
import xmlrpc.client
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

WIRED_URL = "https://wired.wubook.net/xrws/"
REAL_ROOM_IDS = (329039, 329667, 329670, 407751, 469743, 469744)
PLAN_ID = 0
BOARD = "nb"
ORIGIN = "vocal"
MIN_NIGHT_CENTS = 4500
CACHE_TTL_SECONDS = 300
_CALL_TIMEOUT_SECONDS = 20

_KNOWN_ERRORS = {
    -1: "Authentification WuBook refusée. Vérifiez WUBOOK_TOKEN et WUBOOK_LCODE.",
    -10: "Le code établissement ne correspond pas au jeton WuBook.",
    -13: "WuBook a rejeté les arguments de l'appel.",
    -17: "WuBook n'a pas de carte bancaire pour cette réservation.",
    -18: "WuBook limite le nombre d'appels. Réessayez plus tard.",
    -19: "WuBook refuse cette date parce qu'elle est dans le passé.",
}


class WubookError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Stay:
    arrival: date
    departure: date
    adults: int
    children: int

    @property
    def nights(self) -> int:
        return (self.departure - self.arrival).days

    @property
    def people(self) -> int:
        return self.adults + self.children


@dataclass(frozen=True)
class CatalogRoom:
    id: int
    name: str
    occupancy: int
    virtual: bool


@dataclass(frozen=True)
class NightRule:
    available: int
    closed: bool
    closed_arrival: bool
    closed_departure: bool
    min_stay: int
    max_stay: int
    min_stay_arrival: int


@dataclass(frozen=True)
class Offer:
    room_id: int
    name: str
    occupancy: int
    total_cents: int
    night_cents: tuple[int, ...]


@dataclass(frozen=True)
class Guest:
    last_name: str
    first_name: str
    email: str
    phone: str
    city: str
    street: str
    country: str
    arrival_hour: str
    notes: str


class MemoryCache:
    def __init__(self, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[object, tuple[float, object]] = {}

    def get(self, key: object) -> object | None:
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            expires, value = found
            if self._clock() >= expires:
                del self._items[key]
                return None
            return value

    def put(self, key: object, value: object) -> None:
        with self._lock:
            self._items[key] = (self._clock() + self._ttl, value)


def wire_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def format_euros(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def parse_euros(text: str) -> int:
    raw = text.strip().replace(" ", "").replace("€", "").replace(",", ".")
    try:
        cents = int((Decimal(raw) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise WubookError("Le prix total est illisible.") from None
    if cents < 1:
        raise WubookError("Le prix total est illisible.")
    return cents


def parse_stay(
    arrival: str,
    departure: str,
    adults: int,
    children: int,
    today: date,
) -> Stay:
    start = _parse_date(arrival)
    end = _parse_date(departure)
    if _whole(adults) is None or adults < 1:
        raise WubookError("Le nombre d'adultes doit être un entier supérieur à zéro.")
    if _whole(children) is None or children < 0:
        raise WubookError("Le nombre d'enfants doit être un entier positif ou zéro.")
    if end <= start:
        raise WubookError("Le départ doit être après l'arrivée.")
    if start < today:
        raise WubookError(
            "La date d'arrivée est dans le passé. "
            f"Aujourd'hui est le {today.strftime('%d/%m/%Y')}. "
            "Recommence avec cette année, ou l'année suivante si le jour est déjà passé."
        )
    return Stay(start, end, adults, children)


def occupancy_for_party(people: int) -> int | None:
    """Une ou deux personnes → double. Trois → triple. Quatre → familiale."""
    if people <= 2:
        return 2
    if people == 3:
        return 3
    if people == 4:
        return 4
    return None


def select_offers(
    stay: Stay,
    catalog: tuple[CatalogRoom, ...],
    rules: Mapping[int, tuple[NightRule, ...]],
    prices: Mapping[int, tuple[int, ...]],
) -> tuple[Offer, ...]:
    """Chambres réelles libres du type qui correspond au nombre de personnes, à 45 € ou plus."""
    wanted = occupancy_for_party(stay.people)
    chosen: list[Offer] = []
    for room in catalog:
        if room.id not in REAL_ROOM_IDS or room.virtual:
            continue
        if wanted is None or room.occupancy != wanted:
            continue
        row = rules.get(room.id)
        if row is None or len(row) != stay.nights + 1:
            continue
        slept = row[: stay.nights]
        if any(cell.available < 1 or cell.closed for cell in slept):
            continue
        if slept[0].closed_arrival or row[-1].closed_departure:
            continue
        required = max(cell.min_stay for cell in slept)
        required = max(required, slept[0].min_stay_arrival)
        if stay.nights < required:
            continue
        limits = [cell.max_stay for cell in slept if cell.max_stay > 0]
        if limits and stay.nights > min(limits):
            continue
        series = prices.get(room.id)
        if series is None or len(series) != stay.nights:
            continue
        if any(cents < MIN_NIGHT_CENTS for cents in series):
            continue
        chosen.append(
            Offer(
                room_id=room.id,
                name=room.name,
                occupancy=room.occupancy,
                total_cents=sum(series),
                night_cents=series,
            )
        )
    chosen.sort(key=lambda offer: (offer.total_cents, offer.room_id))
    return tuple(chosen)


class WubookService:
    def __init__(
        self,
        *,
        token: str,
        lcode: int,
        proxy: object,
        cache: MemoryCache,
        today: Callable[[], date],
        url: str = WIRED_URL,
    ) -> None:
        self._token = token
        self._lcode = lcode
        self._proxy = proxy
        self._cache = cache
        self._today = today
        self._url = url
        self._book_lock = threading.Lock()
        self._done: dict[object, dict[str, object]] = {}
        self._pending: dict[object, tuple[str | int, Offer]] = {}

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        proxy: object | None = None,
        cache: MemoryCache | None = None,
        today: Callable[[], date] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> WubookService:
        token = env.get("WUBOOK_TOKEN", "").strip()
        raw_lcode = env.get("WUBOOK_LCODE", "").strip()
        if not token or not raw_lcode:
            raise WubookError(
                "WuBook n'est pas configuré. Il manque WUBOOK_TOKEN ou WUBOOK_LCODE."
            )
        if not raw_lcode.isdecimal():
            raise WubookError("WUBOOK_LCODE doit être un entier.")
        url = WIRED_URL
        if proxy is None:
            transport: xmlrpc.client.Transport
            if url.startswith("https://"):
                transport = _TimeoutSafeTransport(_CALL_TIMEOUT_SECONDS)
            else:
                transport = _TimeoutTransport(_CALL_TIMEOUT_SECONDS)
            proxy = xmlrpc.client.ServerProxy(url, allow_none=True, transport=transport)
        store = cache or shared_cache(clock or time.monotonic)
        return cls(
            token=token,
            lcode=int(raw_lcode),
            proxy=proxy,
            cache=store,
            today=today or date.today,
            url=url,
        )

    def __repr__(self) -> str:
        return f"WubookService(lcode={self._lcode}, url={self._url})"

    @property
    def url(self) -> str:
        return self._url

    def disponibilite(
        self,
        arrival: str,
        departure: str,
        adults: int,
        children: int,
    ) -> dict[str, object]:
        stay = parse_stay(arrival, departure, adults, children, self._today())
        catalog = self._catalog()
        rules, prices = self._facts(stay, use_cache=True)
        return _availability_payload(stay, select_offers(stay, catalog, rules, prices))

    def reserver(
        self,
        *,
        arrival: str,
        departure: str,
        room_id: int,
        price_total: str,
        guest: Guest,
        adults: int,
        children: int,
    ) -> dict[str, object]:
        stay = parse_stay(arrival, departure, adults, children, self._today())
        if room_id not in REAL_ROOM_IDS:
            raise WubookError("Cette chambre n'est pas une chambre réelle de l'hôtel.")
        expected = parse_euros(price_total)
        key = _booking_key(stay, room_id, guest)
        with self._book_lock:
            done = self._done.get(key)
            if done is not None:
                return done
            pending = self._pending.get(key)
            if pending is None:
                offer = self._fresh_offer(stay, room_id, expected)
                created = self._create(stay, offer, guest)
                self._pending[key] = (created, offer)
            else:
                created, offer = pending
            try:
                code = _reservation_code(self._call("fetch_booking", created, 0))
            except WubookError as exc:
                raise WubookError(
                    f"La réservation {created} est créée, mais sa relecture a échoué. {exc}"
                ) from exc
            payload = _reservation_payload(stay, room_id, offer, code)
            del self._pending[key]
            self._done[key] = payload
            return payload

    def _fresh_offer(self, stay: Stay, room_id: int, expected: int) -> Offer:
        catalog = self._catalog()
        rules, prices = self._facts(stay, use_cache=False)
        matches = [offer for offer in select_offers(stay, catalog, rules, prices) if offer.room_id == room_id]
        if not matches:
            raise WubookError("Cette chambre n'est plus disponible pour ces dates.")
        offer = matches[0]
        if offer.total_cents != expected:
            raise WubookError(
                "Le prix a changé. Le total est maintenant "
                f"{format_euros(offer.total_cents)} €. "
                "Demande une nouvelle confirmation avant de réserver."
            )
        return offer

    def _create(self, stay: Stay, offer: Offer, guest: Guest) -> str | int:
        created = self._call(
            "new_reservation",
            wire_date(stay.arrival),
            wire_date(stay.departure),
            {str(offer.room_id): [1, BOARD]},
            _customer(guest),
            float(Decimal(offer.total_cents) / Decimal(100)),
            ORIGIN,
            0,
            0,
            {"men": stay.adults, "children": stay.children},
            0,
            0,
            1,
        )
        return _created_code(created)

    def _catalog(self) -> tuple[CatalogRoom, ...]:
        cached = self._cache.get("rooms")
        if isinstance(cached, tuple):
            return cached
        rooms = parse_catalog(self._call("fetch_rooms"))
        self._cache.put("rooms", rooms)
        return rooms

    def _facts(
        self,
        stay: Stay,
        *,
        use_cache: bool,
    ) -> tuple[Mapping[int, tuple[NightRule, ...]], Mapping[int, tuple[int, ...]]]:
        last_night = stay.departure - timedelta(days=1)
        key = ("facts", stay.arrival.toordinal(), stay.departure.toordinal())
        if use_cache:
            cached = self._cache.get(key)
            if isinstance(cached, tuple) and len(cached) == 2:
                return cached
        # dto est inclus. Les prix s'arrêtent à la dernière nuit. Les valeurs
        # vont jusqu'au matin du départ pour lire closed_departure.
        raw_values = self._call(
            "fetch_rooms_values",
            wire_date(stay.arrival),
            wire_date(stay.departure),
            list(REAL_ROOM_IDS),
        )
        raw_prices = self._call(
            "fetch_plan_prices",
            PLAN_ID,
            wire_date(stay.arrival),
            wire_date(last_night),
            list(REAL_ROOM_IDS),
        )
        facts = (parse_rules(raw_values), parse_prices(raw_prices))
        self._cache.put(key, facts)
        return facts

    def _call(self, method: str, *args: object) -> object:
        remote = getattr(self._proxy, method)
        try:
            payload = remote(self._token, self._lcode, *args)
        except xmlrpc.client.Fault as exc:
            raise WubookError(f"WuBook a renvoyé une faute XML-RPC ({exc.faultCode}).") from exc
        except (xmlrpc.client.ProtocolError, OSError, TimeoutError):
            raise WubookError("WuBook est injoignable.") from None
        return unwrap(payload)


_SHARED: MemoryCache | None = None
_SHARED_LOCK = threading.Lock()


def shared_cache(clock: Callable[[], float]) -> MemoryCache:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = MemoryCache(CACHE_TTL_SECONDS, clock)
        return _SHARED


def unwrap(payload: object) -> object:
    if not isinstance(payload, (list, tuple)) or len(payload) != 2:
        raise WubookError("WuBook a renvoyé une réponse illisible.")
    code, result = payload
    if isinstance(code, bool) or not isinstance(code, int):
        raise WubookError("WuBook a renvoyé une réponse illisible.")
    if code != 0:
        raise WubookError(_error_message(code, result), code)
    return result


def parse_catalog(payload: object) -> tuple[CatalogRoom, ...]:
    if not isinstance(payload, list):
        raise WubookError("WuBook a renvoyé un catalogue illisible.")
    rooms: list[CatalogRoom] = []
    for item in payload:
        if not isinstance(item, dict):
            raise WubookError("WuBook a renvoyé un catalogue illisible.")
        room_id = _room_id(item.get("id"))
        if room_id is None or room_id not in REAL_ROOM_IDS:
            continue
        name = item.get("name")
        occupancy = _whole(item.get("occupancy"))
        subroom = _whole(item.get("subroom"))
        if not isinstance(name, str) or name.strip() == "" or occupancy is None or occupancy < 1 or subroom is None:
            raise WubookError("WuBook a renvoyé un catalogue illisible.")
        rooms.append(CatalogRoom(room_id, name.strip(), occupancy, subroom != 0))
    return tuple(rooms)


def parse_rules(payload: object) -> dict[int, tuple[NightRule, ...]]:
    rows = _room_map(payload, "calendrier")
    parsed: dict[int, tuple[NightRule, ...]] = {}
    for room_id, value in rows.items():
        if not isinstance(value, list):
            raise WubookError("WuBook a renvoyé un calendrier illisible.")
        parsed[room_id] = tuple(_night(cell) for cell in value)
    return parsed


def parse_prices(payload: object) -> dict[int, tuple[int, ...]]:
    rows = _room_map(payload, "prix")
    parsed: dict[int, tuple[int, ...]] = {}
    for room_id, value in rows.items():
        if not isinstance(value, list):
            raise WubookError("WuBook a renvoyé des prix illisibles.")
        nights: list[int] = []
        complete = True
        for amount in value:
            cents = _cents(amount)
            if cents is None:
                complete = False
                break
            nights.append(cents)
        if complete:
            parsed[room_id] = tuple(nights)
    return parsed


def parse_guest(
    *,
    last_name: str,
    first_name: str,
    email: str,
    phone: str,
    city: str = "",
    street: str = "",
    country: str = "",
    arrival_hour: str = "",
    notes: str = "",
) -> Guest:
    last = _text(last_name, "nom")
    first = _text(first_name, "prénom")
    mail = _text(email, "e-mail")
    tel = _text(phone, "téléphone")
    if "@" not in mail or "." not in mail.split("@", 1)[1]:
        raise WubookError("L'e-mail est illisible.")
    land = country.strip().upper()
    if land == "":
        land = "FR"
    if len(land) != 2 or not land.isalpha():
        raise WubookError("Le pays doit être un code sur deux lettres.")
    return Guest(
        last_name=last,
        first_name=first,
        email=mail,
        phone=tel,
        city=_optional(city),
        street=_optional(street),
        country=land,
        arrival_hour=_optional(arrival_hour),
        notes=_optional(notes),
    )


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: object) -> object:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: object) -> object:
        connection = super().make_connection(host)
        connection.timeout = self._timeout
        return connection


def _availability_payload(stay: Stay, offers: tuple[Offer, ...]) -> dict[str, object]:
    return {
        "arrivee": wire_date(stay.arrival),
        "depart": wire_date(stay.departure),
        "nuits": stay.nights,
        "adultes": stay.adults,
        "enfants": stay.children,
        "chambres": [
            {
                "id": offer.room_id,
                "nom": offer.name,
                "capacite": offer.occupancy,
                "prix_total_eur": format_euros(offer.total_cents),
                "prix_par_nuit_eur": [format_euros(cents) for cents in offer.night_cents],
            }
            for offer in offers
        ],
    }


def _reservation_payload(
    stay: Stay,
    room_id: int,
    offer: Offer,
    code: str,
) -> dict[str, object]:
    return {
        "reservation": code,
        "chambre": room_id,
        "nom_chambre": offer.name,
        "prix_total_eur": format_euros(offer.total_cents),
        "arrivee": wire_date(stay.arrival),
        "depart": wire_date(stay.departure),
    }


def _customer(guest: Guest) -> dict[str, str]:
    return {
        "lname": guest.last_name,
        "fname": guest.first_name,
        "email": guest.email,
        "phone": guest.phone,
        "city": guest.city,
        "street": guest.street,
        "country": guest.country,
        "arrival_hour": guest.arrival_hour,
        "notes": guest.notes,
    }


def _booking_key(stay: Stay, room_id: int, guest: Guest) -> tuple[object, ...]:
    return (
        stay.arrival.toordinal(),
        stay.departure.toordinal(),
        room_id,
        guest.last_name.casefold(),
        guest.first_name.casefold(),
        guest.email.casefold(),
        guest.phone,
        stay.adults,
        stay.children,
    )


def _error_message(code: int, result: object) -> str:
    known = _KNOWN_ERRORS.get(code)
    if known is not None:
        return known
    if isinstance(result, str) and result.strip():
        return f"WuBook a renvoyé l'erreur {code}. {result.strip()}"
    return f"WuBook a renvoyé l'erreur {code}."


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise WubookError("La date doit être au format jj/mm/aaaa.")
    text = value.strip()
    try:
        parsed = datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        raise WubookError("La date doit être au format jj/mm/aaaa.") from None
    if wire_date(parsed) != text:
        raise WubookError("La date doit être au format jj/mm/aaaa.")
    return parsed


def _night(cell: object) -> NightRule:
    if not isinstance(cell, dict):
        raise WubookError("WuBook a renvoyé un calendrier illisible.")
    available = _whole(cell.get("avail"))
    if available is None:
        raise WubookError("WuBook a renvoyé un calendrier illisible.")
    return NightRule(
        available=available,
        closed=_flag(cell.get("closed")),
        closed_arrival=_flag(cell.get("closed_arrival")),
        closed_departure=_flag(cell.get("closed_departure")),
        min_stay=_count(cell.get("min_stay")),
        max_stay=_count(cell.get("max_stay")),
        min_stay_arrival=_count(cell.get("min_stay_arrival")),
    )


def _flag(value: object) -> bool:
    if value in (0, "0"):
        return False
    if value in (1, "1"):
        return True
    raise WubookError("WuBook a renvoyé un calendrier illisible.")


def _count(value: object) -> int:
    number = _whole(value)
    if number is None or number < 0:
        raise WubookError("WuBook a renvoyé un calendrier illisible.")
    return number


def _cents(amount: object) -> int | None:
    if amount is None:
        return None
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise WubookError("WuBook a renvoyé des prix illisibles.")
    cents = int((Decimal(str(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    if cents < 1:
        return None
    return cents


def _room_map(payload: object, label: str) -> dict[int, object]:
    if not isinstance(payload, dict):
        raise WubookError(f"WuBook a renvoyé un {label} illisible.")
    rows: dict[int, object] = {}
    for key, value in payload.items():
        room_id = _room_id(key)
        if room_id is None:
            raise WubookError(f"WuBook a renvoyé un {label} illisible.")
        rows[room_id] = value
    return rows


def _room_id(value: object) -> int | None:
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0:
        return None
    return value


def _whole(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _created_code(result: object) -> str | int:
    if isinstance(result, bool):
        raise WubookError("WuBook n'a pas renvoyé le code de la réservation.")
    if isinstance(result, int) and result > 0:
        return result
    if isinstance(result, str) and result.strip():
        return result.strip()
    raise WubookError("WuBook n'a pas renvoyé le code de la réservation.")


def _reservation_code(payload: object) -> str:
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise WubookError("WuBook n'a pas renvoyé le code de la réservation.")
    return _code_text(payload.get("reservation_code"))


def _code_text(value: object) -> str:
    if isinstance(value, bool):
        raise WubookError("WuBook n'a pas renvoyé le code de la réservation.")
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise WubookError("WuBook n'a pas renvoyé le code de la réservation.")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise WubookError(f"Il manque le {label}.")
    return value.strip()


def _optional(value: object) -> str:
    if not isinstance(value, str):
        raise WubookError("Un champ client est illisible.")
    return value.strip()
