import sys
import threading
import unittest
import xmlrpc.client
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from xmlrpc.server import SimpleXMLRPCServer

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import wubook  # noqa: E402
from wubook import (  # noqa: E402
    REAL_ROOM_IDS,
    WIRED_URL,
    Guest,
    MemoryCache,
    WubookError,
    WubookService,
    parse_guest,
)

TODAY = date(2026, 6, 1)
ARRIVAL = "10/06/2026"
DEPARTURE = "12/06/2026"
LAST_NIGHT = "11/06/2026"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Wired:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.rooms: list[dict[str, object]] = []
        self.values: dict[str, list[dict[str, object]]] = {}
        self.prices: dict[str, list[object]] = {}
        self.fail: int | None = None
        self.fail_read = False
        self.fault = False
        self.created: object = "temp-rcode"
        self.reservation_code: object = "HP-2048"

    def fetch_rooms(self, token: str, lcode: int) -> list[object]:
        return self._answer("fetch_rooms", (token, lcode), [0, self.rooms])

    def fetch_rooms_values(
        self,
        token: str,
        lcode: int,
        dfrom: str,
        dto: str,
        rooms: list[int],
    ) -> list[object]:
        return self._answer(
            "fetch_rooms_values",
            (token, lcode, dfrom, dto, rooms),
            [0, self.values],
        )

    def fetch_plan_prices(
        self,
        token: str,
        lcode: int,
        pid: int,
        dfrom: str,
        dto: str,
        rooms: list[int],
    ) -> list[object]:
        return self._answer(
            "fetch_plan_prices",
            (token, lcode, pid, dfrom, dto, rooms),
            [0, self.prices],
        )

    def new_reservation(
        self,
        token: str,
        lcode: int,
        dfrom: str,
        dto: str,
        rooms: dict[str, list[object]],
        customer: dict[str, str],
        amount: float,
        origin: str,
        ccard: int,
        ancillary: int,
        guests: dict[str, int],
        ignore_restrs: int,
        ignore_avail: int,
        status: int,
    ) -> list[object]:
        return self._answer(
            "new_reservation",
            (
                token,
                lcode,
                dfrom,
                dto,
                rooms,
                customer,
                amount,
                origin,
                ccard,
                ancillary,
                guests,
                ignore_restrs,
                ignore_avail,
                status,
            ),
            [0, self.created],
        )

    def fetch_booking(
        self,
        token: str,
        lcode: int,
        rcode: object,
        ancillary: int,
    ) -> list[object]:
        if self.fail_read:
            self.calls.append(("fetch_booking", (token, lcode, rcode, ancillary)))
            return [-13, "Wrong call"]
        return self._answer(
            "fetch_booking",
            (token, lcode, rcode, ancillary),
            [0, {"reservation_code": self.reservation_code}],
        )

    def _answer(self, name: str, args: tuple[object, ...], ok: list[object]) -> list[object]:
        self.calls.append((name, args))
        if self.fault:
            raise RuntimeError("boom")
        if self.fail is not None:
            return [self.fail, "detail"]
        return ok

    def names(self) -> list[str]:
        return [name for name, _args in self.calls]


def room(room_id: int, name: str, occupancy: int, subroom: int = 0) -> dict[str, object]:
    return {
        "id": room_id,
        "name": name,
        "occupancy": occupancy,
        "subroom": subroom,
    }


def cell(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "avail": 1,
        "booked": 0,
        "price": 10,
        "closed": 0,
        "closed_arrival": 0,
        "closed_departure": 0,
        "min_stay": 0,
        "max_stay": 0,
        "min_stay_arrival": 0,
    }
    values.update(overrides)
    return values


def stay_grid(*nights: dict[str, object], morning: dict[str, object] | None = None) -> list[dict[str, object]]:
    return [*nights, morning or cell()]


@contextmanager
def serve(wired: Wired):
    server = SimpleXMLRPCServer(("127.0.0.1", 0), logRequests=False, allow_none=True)
    server.register_instance(wired)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    proxy = xmlrpc.client.ServerProxy(f"http://{host}:{port}/", allow_none=True)
    try:
        yield proxy
    finally:
        server.shutdown()
        server.server_close()


def service(proxy: object, clock: Clock | None = None) -> WubookService:
    return WubookService(
        token="test-token",
        lcode=42,
        proxy=proxy,
        cache=MemoryCache(wubook.CACHE_TTL_SECONDS, clock or Clock()),
        today=lambda: TODAY,
    )


def guest() -> Guest:
    return parse_guest(
        last_name="Martin",
        first_name="Léa",
        email="lea@example.com",
        phone="0612345678",
        city="Grasse",
        arrival_hour="18:00",
    )


class QuoteTest(unittest.TestCase):
    def test_quotes_only_real_open_rooms_from_the_plan(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            result = service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)

        self.assertEqual(result["nuits"], 2)
        self.assertEqual(
            result["chambres"],
            [
                {
                    "id": 329039,
                    "nom": "Double",
                    "capacite": 2,
                    "prix_total_eur": "130.00",
                    "prix_par_nuit_eur": ["60.00", "70.00"],
                },
                {
                    "id": 329667,
                    "nom": "Triple",
                    "capacite": 3,
                    "prix_total_eur": "160.00",
                    "prix_par_nuit_eur": ["80.00", "80.00"],
                },
            ],
        )
        values = _call(wired, "fetch_rooms_values")
        prices = _call(wired, "fetch_plan_prices")
        self.assertEqual(values[0:2], ("test-token", 42))
        self.assertEqual(values[2:4], (ARRIVAL, DEPARTURE))
        self.assertEqual(values[4], list(REAL_ROOM_IDS))
        self.assertEqual(prices[2:5], (0, ARRIVAL, LAST_NIGHT))
        self.assertEqual(wired.names().count("fetch_rooms_values"), 1)
        self.assertNotIn(111111, [room["id"] for room in result["chambres"]])

    def test_one_night_stops_the_price_call_on_arrival(self) -> None:
        wired = Wired()
        wired.rooms = [room(329039, "Double", 2)]
        wired.values = {"329039": stay_grid(cell())}
        wired.prices = {"329039": [45]}
        with serve(wired) as proxy:
            result = service(proxy).disponibilite(ARRIVAL, "11/06/2026", 1, 0)

        self.assertEqual(result["chambres"][0]["prix_total_eur"], "45.00")
        self.assertEqual(_call(wired, "fetch_plan_prices")[3:5], (ARRIVAL, ARRIVAL))
        self.assertEqual(_call(wired, "fetch_rooms_values")[3], "11/06/2026")

    def test_string_flags_and_exact_floor_stay_open(self) -> None:
        wired = Wired()
        wired.rooms = [room(329039, "Double", 2)]
        wired.values = {
            "329039": stay_grid(
                cell(closed="0", closed_arrival="0", min_stay="2", min_stay_arrival="2"),
                cell(closed="0"),
                morning=cell(closed_departure="0"),
            )
        }
        wired.prices = {"329039": [45, 45]}
        with serve(wired) as proxy:
            result = service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertEqual(result["chambres"][0]["prix_par_nuit_eur"], ["45.00", "45.00"])

    def test_filters_closure_stay_capacity_and_floor(self) -> None:
        cases = {
            "closed": stay_grid(cell(), cell(closed=1)),
            "closed_arrival": stay_grid(cell(closed_arrival=1), cell()),
            "closed_departure": stay_grid(cell(), cell(), morning=cell(closed_departure="1")),
            "min_stay": stay_grid(cell(min_stay=3), cell()),
            "min_stay_arrival": stay_grid(cell(min_stay_arrival=3), cell()),
            "max_stay": stay_grid(cell(max_stay=1), cell()),
            "sold": stay_grid(cell(avail=0), cell()),
            "short_price": stay_grid(cell(), cell()),
            "under_floor": stay_grid(cell(), cell()),
        }
        prices = {
            "closed": [60, 60],
            "closed_arrival": [60, 60],
            "closed_departure": [60, 60],
            "min_stay": [60, 60],
            "min_stay_arrival": [60, 60],
            "max_stay": [60, 60],
            "sold": [60, 60],
            "short_price": [60],
            "under_floor": [44.99, 80],
        }
        for name, grid in cases.items():
            with self.subTest(name=name):
                wired = Wired()
                wired.rooms = [room(329039, "Double", 2)]
                wired.values = {"329039": grid}
                wired.prices = {"329039": prices[name]}
                with serve(wired) as proxy:
                    result = service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
                self.assertEqual(result["chambres"], [])

    def test_capacity_counts_adults_and_children(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            result = service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 1)
        self.assertEqual([item["id"] for item in result["chambres"]], [329667])

    def test_virtual_room_is_left_out(self) -> None:
        wired = Wired()
        wired.rooms = [room(329039, "Double", 2, subroom=100)]
        wired.values = {"329039": stay_grid(cell(), cell())}
        wired.prices = {"329039": [60, 60]}
        with serve(wired) as proxy:
            result = service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertEqual(result["chambres"], [])

    def test_cache_lasts_five_minutes_and_rooms_are_reused(self) -> None:
        clock = Clock()
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy, clock)
            desk.disponibilite(ARRIVAL, DEPARTURE, 2, 0)
            clock.now = 299
            desk.disponibilite(ARRIVAL, DEPARTURE, 2, 0)
            desk.disponibilite("11/06/2026", "13/06/2026", 2, 0)
            self.assertEqual(wired.names().count("fetch_rooms"), 1)
            self.assertEqual(wired.names().count("fetch_rooms_values"), 2)
            clock.now = 300
            desk.disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertEqual(wired.names().count("fetch_rooms_values"), 3)
        self.assertEqual(wired.names().count("fetch_rooms"), 2)

    def test_known_errors_are_translated(self) -> None:
        expected = {
            -1: "Authentification WuBook refusée",
            -10: "code établissement",
            -13: "arguments",
            -17: "carte bancaire",
            -18: "limite le nombre d'appels",
            -19: "dans le passé",
        }
        for code, fragment in expected.items():
            with self.subTest(code=code):
                wired = catalog()
                wired.fail = code
                with serve(wired) as proxy:
                    with self.assertRaises(WubookError) as caught:
                        service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
                self.assertEqual(caught.exception.code, code)
                self.assertIn(fragment, str(caught.exception))

    def test_unknown_error_keeps_the_code(self) -> None:
        wired = catalog()
        wired.fail = -25
        with serve(wired) as proxy:
            with self.assertRaises(WubookError) as caught:
                service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertIn("-25", str(caught.exception))
        self.assertIn("detail", str(caught.exception))

    def test_xmlrpc_fault_and_dead_port_stay_readable(self) -> None:
        wired = catalog()
        wired.fault = True
        with serve(wired) as proxy:
            with self.assertRaises(WubookError) as caught:
                service(proxy).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertIn("faute XML-RPC", str(caught.exception))

        dead = xmlrpc.client.ServerProxy("http://127.0.0.1:1/", allow_none=True)
        with self.assertRaises(WubookError) as caught:
            service(dead).disponibilite(ARRIVAL, DEPARTURE, 2, 0)
        self.assertIn("injoignable", str(caught.exception))

    def test_rejects_bad_dates_and_party_without_calling(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            for args in (
                ("2026-06-10", DEPARTURE, 2, 0),
                ("32/06/2026", DEPARTURE, 2, 0),
                (DEPARTURE, ARRIVAL, 2, 0),
                ("01/05/2026", ARRIVAL, 2, 0),
                (ARRIVAL, DEPARTURE, 0, 0),
                (ARRIVAL, DEPARTURE, True, 0),
                (ARRIVAL, DEPARTURE, 2, -1),
            ):
                with self.subTest(args=args):
                    with self.assertRaises(WubookError):
                        desk.disponibilite(*args)
        self.assertEqual(wired.calls, [])


class ReservationTest(unittest.TestCase):
    def test_rechecks_then_returns_the_fetched_code(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            desk.disponibilite(ARRIVAL, DEPARTURE, 2, 1)
            booked = desk.reserver(
                arrival=ARRIVAL,
                departure=DEPARTURE,
                room_id=329667,
                price_total="160.00",
                guest=guest(),
                adults=2,
                children=1,
            )

        self.assertEqual(booked["reservation"], "HP-2048")
        self.assertEqual(booked["nom_chambre"], "Triple")
        self.assertEqual(wired.names().count("fetch_rooms_values"), 2)
        created = _call(wired, "new_reservation")
        self.assertEqual(created[2:4], (ARRIVAL, DEPARTURE))
        self.assertEqual(created[4], {"329667": [1, "nb"]})
        self.assertEqual(created[5]["lname"], "Martin")
        self.assertEqual(created[5]["fname"], "Léa")
        self.assertEqual(created[5]["email"], "lea@example.com")
        self.assertEqual(created[5]["country"], "FR")
        self.assertEqual(created[6], 160.0)
        self.assertEqual(created[7:], ("vocal", 0, 0, {"men": 2, "children": 1}, 0, 0, 1))
        read = _call(wired, "fetch_booking")
        self.assertEqual(read[2:], ("temp-rcode", 0))

    def test_price_change_and_sold_room_do_not_book(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            desk.disponibilite(ARRIVAL, DEPARTURE, 2, 0)
            wired.prices["329039"] = [90, 90]
            with self.assertRaises(WubookError) as caught:
                desk.reserver(
                    arrival=ARRIVAL,
                    departure=DEPARTURE,
                    room_id=329039,
                    price_total="130.00",
                    guest=guest(),
                    adults=2,
                    children=0,
                )
            self.assertIn("180.00", str(caught.exception))
            wired.prices["329039"] = [60, 70]
            wired.values["329039"] = stay_grid(cell(avail=0), cell())
            with self.assertRaises(WubookError) as caught:
                desk.reserver(
                    arrival=ARRIVAL,
                    departure=DEPARTURE,
                    room_id=329039,
                    price_total="130.00",
                    guest=guest(),
                    adults=2,
                    children=0,
                )
        self.assertIn("plus disponible", str(caught.exception))
        self.assertNotIn("new_reservation", wired.names())

    def test_booking_another_room_keeps_the_first(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            first = _book(desk, "130.00", adults=2, children=0, room_id=329039)
            _book(desk, "160.00", adults=2, children=1, room_id=329667)
            again = _book(desk, "130.00", adults=2, children=0, room_id=329039)
        self.assertEqual(again, first)
        self.assertEqual(wired.names().count("new_reservation"), 2)

    def test_same_request_does_not_book_twice(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            first = _book(desk, "160.00", adults=2, children=1, room_id=329667)
            second = _book(desk, "160.00", adults=2, children=1, room_id=329667)
        self.assertEqual(first, second)
        self.assertEqual(wired.names().count("new_reservation"), 1)

    def test_failed_read_is_retried_without_a_second_create(self) -> None:
        wired = catalog()
        wired.fail_read = True
        with serve(wired) as proxy:
            desk = service(proxy)
            with self.assertRaises(WubookError) as caught:
                _book(desk, "160.00", adults=2, children=1, room_id=329667)
            self.assertIn("temp-rcode", str(caught.exception))
            self.assertIn("relecture", str(caught.exception))
            wired.fail_read = False
            booked = _book(desk, "999.00", adults=2, children=1, room_id=329667)
        self.assertEqual(booked["reservation"], "HP-2048")
        self.assertEqual(wired.names().count("new_reservation"), 1)
        self.assertEqual(wired.names().count("fetch_booking"), 2)

    def test_foreign_room_and_bad_price_do_not_call(self) -> None:
        wired = catalog()
        with serve(wired) as proxy:
            desk = service(proxy)
            with self.assertRaises(WubookError):
                _book(desk, "160.00", adults=2, children=1, room_id=111111)
            with self.assertRaises(WubookError):
                _book(desk, "gratuit", adults=2, children=1, room_id=329667)
        self.assertEqual(wired.calls, [])


class ConfigTest(unittest.TestCase):
    def test_wired_url_keeps_the_slash_and_hides_the_token(self) -> None:
        self.assertEqual(WIRED_URL, "https://wired.wubook.net/xrws/")
        desk = WubookService.from_env(
            {"WUBOOK_TOKEN": "secret-token", "WUBOOK_LCODE": "42"},
            proxy=object(),
            cache=MemoryCache(300, lambda: 0.0),
        )
        self.assertEqual(desk.url, WIRED_URL)
        self.assertNotIn("secret-token", repr(desk))

    def test_missing_or_bad_config(self) -> None:
        with self.assertRaises(WubookError) as caught:
            WubookService.from_env({})
        self.assertIn("WUBOOK_TOKEN", str(caught.exception))
        with self.assertRaises(WubookError) as caught:
            WubookService.from_env({"WUBOOK_TOKEN": "t", "WUBOOK_LCODE": "abc"})
        self.assertIn("entier", str(caught.exception))


def catalog() -> Wired:
    wired = Wired()
    wired.rooms = [
        room(329039, "Double", 2),
        room(329667, "Triple", 3),
        room(329670, "Fermée", 2),
        room(407751, "Long séjour", 2),
        room(469743, "Pas chère", 2),
        room(469744, "Pleine", 2),
        room(111111, "Airbnb", 2),
    ]
    wired.values = {
        "329039": stay_grid(cell(), cell()),
        "329667": stay_grid(cell(), cell()),
        "329670": stay_grid(cell(), cell(closed=1)),
        "407751": stay_grid(cell(min_stay=3), cell()),
        "469743": stay_grid(cell(), cell()),
        "469744": stay_grid(cell(avail=0), cell()),
        "111111": stay_grid(cell(), cell()),
    }
    wired.prices = {
        "329039": [60, 70],
        "329667": [80, 80],
        "329670": [60, 60],
        "407751": [60, 60],
        "469743": [40, 80],
        "469744": [60, 60],
        "111111": [50, 50],
    }
    return wired


def _call(wired: Wired, name: str) -> tuple[object, ...]:
    for called, args in wired.calls:
        if called == name:
            return args
    raise AssertionError(name)


def _book(
    desk: WubookService,
    price: str,
    *,
    adults: int,
    children: int,
    room_id: int,
) -> dict[str, object]:
    return desk.reserver(
        arrival=ARRIVAL,
        departure=DEPARTURE,
        room_id=room_id,
        price_total=price,
        guest=guest(),
        adults=adults,
        children=children,
    )


if __name__ == "__main__":
    unittest.main()
