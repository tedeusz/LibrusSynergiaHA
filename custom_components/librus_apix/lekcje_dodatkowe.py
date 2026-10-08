"""Lekcje dodatkowe dopisywane recznie do planu z Librusa (bez zaleznosci od Home Assistanta).

Lekcja dodatkowa to zajecia, ktorych nie ma w planie szkoly (kolko, jezyk obcy, basen...).
Moze byc cotygodniowa (w wybrany dzien tygodnia) albo jednorazowa (w wybrana date).
Plan "po scaleniu" to plan z Librusa plus lekcje dodatkowe, posortowane wg godziny rozpoczecia,
dzieki czemu wszystkie sensory planu, kalendarz i czasy poczatku/konca lekcji widza je tak samo
jak lekcje szkolne.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

COTYGODNIOWO = "co_tydzien"
JEDNORAZOWO = "jednorazowo"
CO_2_TYGODNIE = "co_2_tygodnie"  # od podanej daty pierwszych zajec, co 14 dni
POWTARZANIA = (COTYGODNIOWO, CO_2_TYGODNIE, JEDNORAZOWO)
DNI_TYGODNIA = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
DNI_SKROT = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"]
# Numer lekcji dodatkowej w planie: nie ma numeru szkolnego, wiec zawsze "+"
NUMER_DODATKOWEJ = "+"
# Ile dni od poniedzialku biezacego tygodnia obejmuje plan scalony w danych (2 pobierane tygodnie)
HORYZONT_DNI = 14
# Jednorazowe lekcje starsze niz tyle dni sa usuwane przy wczytaniu
PRZEDAWNIENIE_DNI = 30
# Na ile dni do przodu pokazujemy terminy do odwolania/przywrocenia
TERMINY_DNI = 21

_GODZINA = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


def minuty(godzina: Any) -> Optional[int]:
    """"08:05" (albo "08:05:00") -> minuty od polnocy; None, gdy to nie godzina."""
    m = _GODZINA.match(str(godzina or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _hhmm(godzina: Any) -> str:
    m = minuty(godzina)
    if m is None:
        raise ValueError(f"Niepoprawna godzina: {godzina!r} (oczekiwano HH:MM)")
    return f"{m // 60:02d}:{m % 60:02d}"


def data_iso(wartosc: Any) -> Optional[str]:
    if wartosc in (None, ""):
        return None
    if isinstance(wartosc, datetime):
        return wartosc.date().isoformat()
    if isinstance(wartosc, date):
        return wartosc.isoformat()
    try:
        return datetime.strptime(str(wartosc).strip()[:10], "%Y-%m-%d").date().isoformat()
    except ValueError as err:
        raise ValueError(f"Niepoprawna data: {wartosc!r} (oczekiwano RRRR-MM-DD)") from err


def nowa_lekcja(
    przedmiot: str,
    od: Any,
    do: Any,
    *,
    powtarzanie: str = COTYGODNIOWO,
    dzien: Any = None,
    data: Any = None,
    miejsce: str = "",
    id: Optional[str] = None,
    odwolane: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Zwaliduj dane i zwroc rekord lekcji dodatkowej (ValueError z czytelnym komunikatem)."""
    przedmiot = " ".join(str(przedmiot or "").split())
    if not przedmiot:
        raise ValueError("Podaj nazwę zajęć")
    poczatek, koniec = _hhmm(od), _hhmm(do)
    if minuty(koniec) <= minuty(poczatek):
        raise ValueError("Godzina zakończenia musi być późniejsza niż rozpoczęcia")
    if powtarzanie not in POWTARZANIA:
        raise ValueError(f"Nieznany sposób powtarzania: {powtarzanie!r}")
    rekord: Dict[str, Any] = {
        "id": id or uuid.uuid4().hex[:8],
        "przedmiot": przedmiot,
        "powtarzanie": powtarzanie,
        "od": poczatek,
        "do": koniec,
        "miejsce": " ".join(str(miejsce or "").split()),
        "odwolane": sorted({data_iso(d) for d in odwolane or [] if d}),  # terminy (daty), ktore odwolano
    }
    if powtarzanie == JEDNORAZOWO:
        iso = data_iso(data)
        if not iso:
            raise ValueError("Dla zajęć jednorazowych podaj datę")
        rekord["data"] = iso
    elif powtarzanie == CO_2_TYGODNIE:
        iso = data_iso(data)
        if not iso:
            raise ValueError("Dla zajęć co 2 tygodnie podaj datę pierwszych zajęć")
        rekord["data"] = iso  # pierwszy termin; kolejne co 14 dni
        rekord["dzien"] = date.fromisoformat(iso).weekday()
    else:
        if isinstance(dzien, str) and dzien.strip().lower() in DNI_TYGODNIA:
            dzien = DNI_TYGODNIA.index(dzien.strip().lower())
        try:
            numer = int(dzien)
        except (TypeError, ValueError) as err:
            raise ValueError("Wybierz dzień tygodnia") from err
        if not 0 <= numer <= 6:
            raise ValueError("Dzień tygodnia musi być z zakresu 0 (pon) - 6 (niedz)")
        rekord["dzien"] = numer
    return rekord


def opis_terminu(l: Dict[str, Any]) -> str:
    """"śr 16:00–17:00" albo "2026-10-15 16:00–17:00" - do list i etykiet."""
    if l["powtarzanie"] == CO_2_TYGODNIE:
        poczatek = date.fromisoformat(l["data"])
        return f"co 2 tyg. {DNI_SKROT[l['dzien']]} {l['od']}–{l['do']} (od {poczatek:%d.%m})"
    kiedy = l["data"] if l["powtarzanie"] == JEDNORAZOWO else DNI_SKROT[l["dzien"]]
    return f"{kiedy} {l['od']}–{l['do']}"


def etykiety(lekcje: List[Dict[str, Any]]) -> Dict[str, str]:
    """{id: etykieta} - unikalne napisy do listy wyboru (przy powtorzeniu dopisujemy id)."""
    wynik: Dict[str, str] = {}
    uzyte: set = set()
    for l in lekcje:
        napis = f"{l['przedmiot']} · {opis_terminu(l)}"
        if napis in uzyte:
            napis = f"{napis} ({l['id']})"
        uzyte.add(napis)
        wynik[l["id"]] = napis
    return wynik


def pasuje(l: Dict[str, Any], dzien: date) -> bool:
    """Czy zajecia odbywaja sie danego dnia (z uwzglednieniem odwolanych terminow - te tez "pasuja")."""
    if l["powtarzanie"] == JEDNORAZOWO:
        return l["data"] == dzien.isoformat()
    if l["powtarzanie"] == CO_2_TYGODNIE:
        return dzien >= date.fromisoformat(l["data"]) and (dzien - date.fromisoformat(l["data"])).days % 14 == 0
    return l["dzien"] == dzien.weekday()


def lekcje_na_dzien(lekcje: Iterable[Dict[str, Any]], dzien: date) -> List[Dict[str, Any]]:
    """Lekcje dodatkowe przypadajace na dzien - w formacie lekcji planu (jak z _build_plan)."""
    wynik = []
    for l in lekcje:
        if pasuje(l, dzien):
            odwolana = dzien.isoformat() in l.get("odwolane", [])
            wynik.append({
                "numer": NUMER_DODATKOWEJ,
                "przedmiot": l["przedmiot"],
                "nauczyciel_sala": l.get("miejsce", ""),
                "od": l["od"],
                "do": l["do"],
                "zmiana": "Odwołane" if odwolana else "",
                "odwolana": odwolana,
                "dodatkowa": True,
                "id_dodatkowej": l["id"],
            })
    return wynik


def _klucz_startu(lekcja: Dict[str, Any]) -> tuple:
    start = minuty(lekcja.get("od"))
    koniec = minuty(lekcja.get("do"))
    return (start if start is not None else -1, koniec if koniec is not None else -1)


def scal_dzien(
    lekcje_librus: List[Dict[str, Any]], dodatkowe: Iterable[Dict[str, Any]], dzien: date
) -> List[Dict[str, Any]]:
    """Lekcje szkolne + dodatkowe tego dnia, wg godziny rozpoczecia (stabilnie; bez zmian, gdy brak dodatkowych)."""
    extra = lekcje_na_dzien(dodatkowe, dzien)
    if not extra:
        return lekcje_librus
    return sorted([*lekcje_librus, *extra], key=_klucz_startu)


def horyzont(dzis: date, dni: int = HORYZONT_DNI) -> List[date]:
    """Poniedzialek biezacego tygodnia i kolejne `dni` dni."""
    start = dzis - timedelta(days=dzis.weekday())
    return [start + timedelta(days=i) for i in range(dni)]


def scal_plan(
    plan_librus: Dict[str, List[Dict[str, Any]]],
    dodatkowe: List[Dict[str, Any]],
    dzis: date,
) -> Dict[str, List[Dict[str, Any]]]:
    """Plan z Librusa + lekcje dodatkowe w horyzoncie planu; dni z Librusa zostaja, dodatkowe tworza brakujace."""
    if not dodatkowe:
        return plan_librus
    wynik = dict(plan_librus)
    for dzien in horyzont(dzis):
        iso = dzien.isoformat()
        polaczone = scal_dzien(plan_librus.get(iso, []), dodatkowe, dzien)
        if iso in plan_librus or polaczone:
            wynik[iso] = polaczone
    return wynik


def usun_przedawnione(lekcje: List[Dict[str, Any]], dzis: date) -> List[Dict[str, Any]]:
    """Odrzuc jednorazowe lekcje starsze niz PRZEDAWNIENIE_DNI i stare odwolane terminy; cotygodniowe zostaja."""
    granica = (dzis - timedelta(days=PRZEDAWNIENIE_DNI)).isoformat()
    return [
        {**l, "odwolane": [d for d in l.get("odwolane", []) if d >= granica]}
        for l in lekcje
        if l["powtarzanie"] != JEDNORAZOWO or l["data"] >= granica
    ]


def terminy(lekcje: Iterable[Dict[str, Any]], od: date, dni: int = TERMINY_DNI) -> List[Dict[str, Any]]:
    """Terminy lekcji dodatkowych od dnia `od` przez `dni` dni - do odwolywania/przywracania."""
    wynik = []
    for przesuniecie in range(dni):
        dzien = od + timedelta(days=przesuniecie)
        for l in lekcje:
            if pasuje(l, dzien):
                wynik.append({
                    "klucz": f"{l['id']}|{dzien.isoformat()}",
                    "id": l["id"],
                    "data": dzien.isoformat(),
                    "przedmiot": l["przedmiot"],
                    "od": l["od"],
                    "do": l["do"],
                    "miejsce": l.get("miejsce", ""),
                    "odwolana": dzien.isoformat() in l.get("odwolane", []),
                })
    wynik.sort(key=lambda t: (t["data"], minuty(t["od"]) or 0))
    return wynik


def ustaw_odwolanie(l: Dict[str, Any], data: Any, odwolana: bool = True) -> bool:
    """Odwolaj (albo przywroc) jeden termin zajec; zwraca True, gdy cos sie zmienilo. ValueError, gdy zajec tego dnia nie ma."""
    iso = data_iso(data)
    if not iso:
        raise ValueError("Podaj datę terminu")
    if not pasuje(l, date.fromisoformat(iso)):
        raise ValueError(f"Zajęcia „{l['przedmiot']}” nie odbywają się w dniu {iso}")
    obecne = set(l.get("odwolane", []))
    if (iso in obecne) == odwolana:
        return False
    obecne.add(iso) if odwolana else obecne.discard(iso)
    l["odwolane"] = sorted(obecne)
    return True


def zmien(
    l: Dict[str, Any], *, przedmiot: Any = None, od: Any = None, do: Any = None, powtarzanie: Any = None,
    dzien: Any = None, data: Any = None, miejsce: Any = None,
) -> Dict[str, Any]:
    """Nowy rekord z zastosowanymi zmianami (None = bez zmian); zachowuje id i te odwolane terminy, ktore nadal pasuja."""
    nowy = nowa_lekcja(
        l["przedmiot"] if przedmiot is None else przedmiot,
        l["od"] if od is None else od,
        l["do"] if do is None else do,
        powtarzanie=l["powtarzanie"] if powtarzanie is None else powtarzanie,
        dzien=l.get("dzien") if dzien is None else dzien,
        data=l.get("data") if data is None else data,
        miejsce=l.get("miejsce", "") if miejsce is None else miejsce,
        id=l["id"],
    )
    nowy["odwolane"] = [d for d in l.get("odwolane", []) if pasuje(nowy, date.fromisoformat(d))]
    return nowy


def z_zapisu(surowe: Any) -> List[Dict[str, Any]]:
    """Wczytaj liste z zapisu (Store); uszkodzone rekordy pomijamy zamiast psuc start integracji."""
    wynik = []
    for r in surowe or []:
        try:
            wynik.append(nowa_lekcja(
                r["przedmiot"], r["od"], r["do"], powtarzanie=r.get("powtarzanie", COTYGODNIOWO),
                dzien=r.get("dzien"), data=r.get("data"), miejsce=r.get("miejsce", ""), id=r.get("id"),
                odwolane=r.get("odwolane"),
            ))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return wynik
