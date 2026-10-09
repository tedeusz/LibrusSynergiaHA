# 🎓 Librus APIX Integration for Home Assistant

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/LukMaverick)

Integracja Home Assistant z systemem Librus Synergia: oceny (także opisowe), wiadomości, plan lekcji, zadania domowe, terminarz, frekwencja, ogłoszenia, uwagi, zachowanie i powiadomienia.

> **Fork.** To rozszerzona wersja projektu [LukMaverick/LibrusSynergiaHA](https://github.com/LukMaverick/LibrusSynergiaHA) (v1.1.5), zbudowanego na bibliotece [librus-apix](https://github.com/RustySnek/librus-apix). Zmiany obejmują wyłącznie integrację (`custom_components/librus_apix`). Szczegóły w sekcji [Co dodaje ten fork](#-co-dodaje-ten-fork).

## ✨ Funkcje

- 📊 **Oceny** - wszystkie oceny bieżącego semestru, także **oceny opisowe [OO]** z klas I–III (np. „Uż”), średnie ważone i oficjalne średnie Librusa
- 📈 **Statystyki** - średnie ocen, liczba ocen, trend
- 📧 **Wiadomości** - lista z treścią pobieraną na żądanie i stronicowaniem do starszych wiadomości
- 🗓️ **Plan lekcji** - plan dnia i tygodnia z zastępstwami i odwołanymi lekcjami, aktualna lekcja, czasy początku i końca lekcji
- ➕ **Zajęcia dodatkowe** - ręcznie dopisywane do planu zajęcia, których nie ma w Librusie (koło, język, basen); plan = Librus + dodatkowe
- 📝 **Zadania domowe, terminarz i sprawdziany** - pogrupowane po dniach, ze stronicowaniem
- ✅ **Frekwencja** - procent, liczniki nieobecności, spóźnień i zwolnień oraz lista wpisów ze stronicowaniem
- 📢 **Ogłoszenia szkoły** i 📖 **tematy lekcji**
- 📝 **Uwagi i zachowanie** - uwagi i pochwały ze stronicowaniem, ocena zachowania (semestry i roczna) oraz wpisy pozytywne i negatywne
- 🔔 **Zdarzenia HA** - nowa ocena, wiadomość, zadanie, zdarzenie w terminarzu, ogłoszenie, wpis frekwencji, uwaga i wpis o zachowaniu
- 📅 **Kalendarz** HA z terminarzem i zadaniami domowymi

## 🚀 Sensory i encje

Nazwy encji zależą od nazwy ucznia, np. `sensor.librus_<imie_nazwisko>_oceny`. Dokładne nazwy znajdziesz w **Narzędzia deweloperskie → Stany**.

| Encja | Opis |
|-------|------|
| `sensor.librus_..._oceny` | Oceny bieżącego semestru (liczba ocen; atrybuty: przedmioty, ostatnie oceny, ekran przeglądania) |
| `sensor.librus_..._srednia_ocen`, `..._srednia_<przedmiot>` | Średnie (`state_class: measurement`, wykres historii) |
| `sensor.librus_..._<przedmiot>` | Oceny z danego przedmiotu |
| `sensor.librus_..._wiadomosci` | Wiadomości (liczba nieprzeczytanych; atrybuty: lista, otwarta wiadomość, ekran przeglądania) |
| `sensor.librus_..._wiadomosci_wyslane` | Wiadomości wysłane (atrybuty: `przegladanie` – ekran po 5 pozycji z odbiorcą, tematem i datą; `otwarta` – kliknięta wiadomość z treścią) |
| `sensor.librus_..._plan_lekcji_dzis`, `..._plan_lekcji_nastepny_dzien`, `..._plan_lekcji_tydzien` | Plan lekcji na dziś, następny dzień nauki i tydzień (z przeglądaniem tygodni) |
| `sensor.librus_..._lekcje_dodatkowe` | Zajęcia dodatkowe dopisane ręcznie (liczba; atrybuty: `lekcje` z `id`, terminem, miejscem i `odwolane_terminy`, `dzis` oraz `terminy` z najbliższych 3 tygodni) |
| `text.`, `select.`, `time.`, `date.`, `button.librus_..._lekcja_dodatkowa_*` | Formularz dodawania, edycji i usuwania zajęć dodatkowych w UI: pola `..._nazwa`, `..._miejsce`, `..._powtarzanie`, `..._dzien`, `..._data`, `..._obowiazuje_od`, `..._obowiazuje_do`, `..._od`, `..._do`, lista `..._edycja`, przyciski `..._dodaj`, `..._zapisz_zmiany`, `..._usun`, `..._wyczysc_daty_obowiazywania` i lista `..._do_usuniecia` (patrz niżej) |
| `todo.librus_..._zajecia_dodatkowe_terminy` | Terminy zajęć dodatkowych z tygodnia pokazanego w planie; zaznaczenie terminu odwołuje go |
| `sensor.librus_..._aktualna_lekcja` | Trwająca lekcja i następna |
| `sensor.librus_..._poczatek_lekcji_dzis`, `..._koniec_lekcji_dzis`, `..._poczatek_lekcji_nastepny_dzien` | Znaczniki czasu do wyzwalaczy w automatyzacjach |
| `sensor.librus_..._zadania_domowe`, `..._zadania` | Zadania domowe (z treścią zadań z najbliższych dni) |
| `sensor.librus_..._terminarz` | Terminarz i sprawdziany |
| `sensor.librus_..._frekwencja` | Frekwencja w procentach (ogółem i dla semestrów) |
| `sensor.librus_..._nieobecnosci` | Liczniki nieobecności, spóźnień i zwolnień oraz lista wpisów |
| `sensor.librus_..._ogloszenia` | Ogłoszenia szkoły |
| `sensor.librus_..._uwagi` | Uwagi i pochwały (liczba wpisów; atrybuty: liczniki wg rodzaju, ostatnia, lista, ekran przeglądania) |
| `sensor.librus_..._zachowanie` | Ocena zachowania (roczna, a gdy jej nie ma - z semestru) oraz wpisy pozytywne i negatywne |
| `sensor.librus_..._tematy_lekcji` | Tematy zrealizowanych lekcji z ostatnich dni |
| `sensor.librus_..._szczesliwy_numerek`, `..._informacje_o_uczniu` | Szczęśliwy numerek i dane ucznia |
| `binary_sensor.librus_..._lekcja_trwa`, `..._lekcje_dzis` | Czy trwa lekcja, czy dziś są lekcje |
| `calendar.librus_..._terminarz_i_zadania` | Kalendarz z terminarzem i zadaniami domowymi |

Sensory średnich mają `state_class: measurement` — HA automatycznie rysuje dla nich wykres historyczny po kliknięciu w encję.

## 🆕 Co dodaje ten fork

- **Oceny opisowe [OO]** (klasy I–III). Strona Librusa wypełnia je skryptem przez API, więc biblioteka ich nie widzi. Integracja pobiera je z API Synergii i pokazuje jako ocenę „OO” z obszarem, wymaganiami, opisem i nauczycielem. Jeśli API chwilowo nie odpowie, zostają ostatnie znane oceny (w logu pojawia się ostrzeżenie z kodami odpowiedzi).
- **Własny parser strony ocen** uzupełniający wyniki biblioteki, gdy ta pominie część ocen.
- **Warstwowe odświeżanie** zamiast jednego rzadkiego cyklu (patrz niżej).
- **Nowe sensory, kalendarz i sensory binarne**: plan lekcji, zadania domowe, frekwencja, ogłoszenia, tematy lekcji, aktualna lekcja, uwagi i zachowanie.
- **Zajęcia dodatkowe w planie**: ręczne uzupełnianie planu o zajęcia spoza Librusa, widoczne we wszystkich czujnikach planu i w kalendarzu.
- **Przeglądanie list usługami** (bez dodatkowych zapytań do Librusa) do budowy dashboardów ze stronicowaniem.
- **Treść wiadomości na żądanie**, zapamiętywana w `Store`, bez oznaczania starych wiadomości jako przeczytanych.
- **Zdarzenia HA** dla nowych ocen, wiadomości, zadań, terminarza, ogłoszeń, wpisów frekwencji, uwag i wpisów o zachowaniu.
- **Usługa diagnostyczna** `librus_apix.diagnostyka_ocen`.

### Odświeżanie danych

| Co | Jak często |
|----|-----------|
| Wiadomości (szybkie sprawdzanie nowych) | co 3 minuty |
| Oceny (razem z zachowaniem), zadania, terminarz, frekwencja, ogłoszenia, uwagi + zdarzenia | co 15 minut |
| Pełne odświeżenie wszystkiego (m.in. plan, tematy lekcji, średnie Librusa) | co 2 godziny |

Wartości można zmienić w stałych na początku `coordinator.py` (`WIADOMOSCI_INTERWAL`, `ZDARZENIA_INTERWAL`, `PLAN_INTERWAL`) oraz `SCAN_INTERVAL` w `const.py`.

## 🧰 Usługi

Usługi przesuwają ekran listy (atrybut `przegladanie` odpowiedniego sensora) i nie wysyłają zapytań do Librusa. Parametr `kierunek`: `nastepna` (starsze), `poprzednia` (nowsze) lub `najnowsze`.

| Usługa | Opis |
|--------|------|
| `librus_apix.przegladaj` | Lista wiadomości (przechodzi też do starszych stron Librusa) |
| `librus_apix.otworz_wyslana` | Treść wysłanej wiadomości z pozycji ekranu (pobierana raz, zapisywana) |
| `librus_apix.przegladaj_wyslane` | Lista wysłanych (ekran po 5 pozycji; starsze strony z Librusa, `najnowsze` odświeża listę) |
| `librus_apix.pobierz_tresc` | Pobiera i zapisuje treść wiadomości z podanej pozycji listy (`indeks`) |
| `librus_apix.przegladaj_ogloszenia` | Lista ogłoszeń (ekran po 5 pozycji) |
| `librus_apix.otworz_ogloszenie` | Pokazuje pełną treść ogłoszenia z pozycji `indeks` bieżącego ekranu (atrybut `otwarte` czujnika ogłoszeń; bez zapytań do Librusa) |
| `librus_apix.przegladaj_terminarz` | Kolejne dni ze zdarzeniami w terminarzu |
| `librus_apix.przegladaj_zadania` | Kolejne dni z zadaniami domowymi |
| `librus_apix.przegladaj_oceny` | Lista ostatnich ocen |
| `librus_apix.przegladaj_frekwencje` | Lista nieobecności, spóźnień i zwolnień |
| `librus_apix.przegladaj_uwagi` | Lista uwag i pochwał |
| `librus_apix.przegladaj_plan` | Tydzień planu lekcji: `kierunek` = `poprzedni`, `biezacy`, `nastepny` |
| `librus_apix.dodaj_lekcje_dodatkowa` | Dopisuje zajęcia do planu: `przedmiot`, `od`, `do`, `powtarzanie` (`co_tydzien` / `co_2_tygodnie` / `jednorazowo`), `dzien` (0 = poniedziałek … 6; dla `co_tydzien`) albo `data` (dla `jednorazowo` i `co_2_tygodnie` – data pierwszych zajęć), opcjonalnie `miejsce` oraz `wazne_od` / `wazne_do` (zakres obowiązywania zajęć cyklicznych) |
| `librus_apix.edytuj_lekcje_dodatkowa` | Zmienia wybrane pola zajęć (`id` + dowolne z: `przedmiot`, `od`, `do`, `powtarzanie`, `dzien`, `data`, `wazne_od`, `wazne_do`, `miejsce`; pusty napis `""` w `wazne_od`/`wazne_do` usuwa datę); pominięte zostają bez zmian |
| `librus_apix.odwolaj_termin_lekcji_dodatkowej` / `librus_apix.przywroc_termin_lekcji_dodatkowej` | Odwołuje (przywraca) zajęcia tylko w jednym dniu: `id` + `data` |
| `librus_apix.usun_lekcje_dodatkowa` | Usuwa zajęcia po `id` (z atrybutu `lekcje` sensora `..._lekcje_dodatkowe`) |
| `librus_apix.diagnostyka_ocen` | Zapisuje raport diagnostyczny ocen w katalogu konfiguracji HA |

### Diagnostyka ocen

Jeśli brakuje ocen, uruchom `librus_apix.diagnostyka_ocen` (Narzędzia deweloperskie → Usługi). Zapisze w katalogu konfiguracji plik `librus_apix_diagnostyka_ocen.txt` (oraz `librus_apix_strona_ocen.html` i, jeśli pobrano uwagi, `librus_apix_strona_uwag.html`). Przed wysłaniem raportu komukolwiek **usuń z niego dane osobowe** (imię i nazwisko ucznia, nauczycieli, identyfikatory).

## 📦 Instalacja

### Opcja 1: HACS (Zalecana)

Kliknij poniższy przycisk, aby automatycznie dodać repozytorium do HACS z właściwą kategorią:

[![Otwórz w HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tedeusz&repository=LibrusSynergiaHA&category=integration)

Lub ręcznie:

1. Otwórz HACS w Home Assistant
2. Kliknij trzy kropki (⋮) w prawym górnym rogu
3. Wybierz **"Custom repositories"**
4. W polu URL wpisz dokładnie: `https://github.com/tedeusz/LibrusSynergiaHA`  
   ⚠️ **Bez `.git` na końcu!**
5. W polu **Category** wybierz: **`Integration`**  
   ⚠️ **NIE wybieraj "AppDaemon", "Plugin" ani żadnej innej opcji!**
6. Kliknij **ADD**
7. Znajdź **"Librus Synergia HA"** na liście i zainstaluj
8. Restartuj Home Assistant

> **Uwaga:** Błąd *"is not a valid app repository"* pojawia się, gdy w kroku 5 zostanie wybrana nieprawidłowa kategoria (np. "AppDaemon"). Upewnij się, że wybrano **Integration**.

### Opcja 2: Instalacja manualna

1. Skopiuj folder `custom_components/librus_apix` do `config/custom_components/`
2. Restartuj Home Assistant
3. Idź do Konfiguracja > Integracje > Dodaj integrację
4. Wyszukaj "Librus APIX"

## ⚙️ Konfiguracja

1. W Home Assistant: **Konfiguracja** > **Integracje** > **Dodaj integrację**
2. Wyszukaj **"Librus APIX"**  
3. Podaj swoje dane logowania do Librus Synergia:
   - **Login/Username**: Twój login do Librus
   - **Hasło**: Twoje hasło do Librus
4. Kliknij **"Prześlij"**

## 🔧 Środowisko testowe

Projekt zawiera local środowisko testowe z Docker:

```bash
# Uruchom środowisko testowe
docker-compose up -d

# Home Assistant dostępny pod: http://localhost:8123
# Code Server dostępny pod: http://localhost:8443 (hasło: homeassistant)
```

## 📊 Przykładowe karty Lovelace

### Karta ocen i średnich
```yaml
type: entities
title: "📚 Oceny Librus"
entities:
  - entity: sensor.librus_srednia_ocen
    name: "Globalna średnia"
  - entity: sensor.librus_oceny
    name: "Liczba ocen"
  - entity: sensor.librus_szczesliwy_numerek
    name: "Szczęśliwy numerek"
```

### Karta wiadomości (Mushroom)

> **Wymagane:** [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom) zainstalowane przez HACS.

#### Jak znaleźć nazwę swojej encji?
1. Idź do **Developer Tools → States**
2. Wyszukaj `wiadomosci`
3. Skopiuj pełną nazwę encji (np. `sensor.wiadomosci`)
4. Zamień `sensor.wiadomosci` poniżej na swoją nazwę

```yaml
type: vertical-stack
cards:
  - type: custom:mushroom-title-card
    title: 📬 Wiadomości Librus
    subtitle: >
      {% set n = state_attr('sensor.wiadomosci', 'liczba_nieprzeczytanych') %}
      {% if n > 0 %}{{ n }} nieprzeczytanych{% else %}Wszystkie przeczytane{% endif %}

  - type: custom:mushroom-template-card
    primary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[0].temat | default('brak') }}
    secondary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[0].nadawca | default('') }}
      · {{ state_attr('sensor.wiadomosci', 'wiadomosci')[0].data | default('') }}
    icon: mdi:message-text
    icon_color: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[0].nieprzeczytana %}red{% else %}grey{% endif %}
    badge_icon: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[0].ma_zalacznik %}mdi:paperclip{% endif %}

  - type: custom:mushroom-template-card
    primary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[1].temat | default('brak') }}
    secondary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[1].nadawca | default('') }}
      · {{ state_attr('sensor.wiadomosci', 'wiadomosci')[1].data | default('') }}
    icon: mdi:message-text
    icon_color: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[1].nieprzeczytana %}red{% else %}grey{% endif %}
    badge_icon: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[1].ma_zalacznik %}mdi:paperclip{% endif %}

  - type: custom:mushroom-template-card
    primary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[2].temat | default('brak') }}
    secondary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[2].nadawca | default('') }}
      · {{ state_attr('sensor.wiadomosci', 'wiadomosci')[2].data | default('') }}
    icon: mdi:message-text
    icon_color: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[2].nieprzeczytana %}red{% else %}grey{% endif %}
    badge_icon: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[2].ma_zalacznik %}mdi:paperclip{% endif %}

  - type: custom:mushroom-template-card
    primary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[3].temat | default('brak') }}
    secondary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[3].nadawca | default('') }}
      · {{ state_attr('sensor.wiadomosci', 'wiadomosci')[3].data | default('') }}
    icon: mdi:message-text
    icon_color: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[3].nieprzeczytana %}red{% else %}grey{% endif %}
    badge_icon: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[3].ma_zalacznik %}mdi:paperclip{% endif %}

  - type: custom:mushroom-template-card
    primary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[4].temat | default('brak') }}
    secondary: >
      {{ state_attr('sensor.wiadomosci', 'wiadomosci')[4].nadawca | default('') }}
      · {{ state_attr('sensor.wiadomosci', 'wiadomosci')[4].data | default('') }}
    icon: mdi:message-text
    icon_color: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[4].nieprzeczytana %}red{% else %}grey{% endif %}
    badge_icon: >
      {% if state_attr('sensor.wiadomosci', 'wiadomosci')[4].ma_zalacznik %}mdi:paperclip{% endif %}
```

Legenda ikon:
- 🔴 czerwona = nieprzeczytana
- ⚫ szara = przeczytana
- 📎 badge = ma załącznik

### Karta terminarza (wszystkie zdarzenia)

> Znajdź nazwę encji w **Developer Tools → States** (szukaj `terminarz`).

```yaml
type: markdown
title: 📅 Terminarz
content: >
  {% set zdarzenia = state_attr('sensor.librus_imie_nazwisko_terminarz',
  'zdarzenia') %} {% if zdarzenia %} | Data | Dzień | Typ | Przedmiot | Opis |
   |------|-------|-----|-----------|------|
  {% for z in zdarzenia %} | **{{ z.data }}** | {{ z.tydzien }} | {{ z.tytul }}
  | {{ z.przedmiot }} | {{ z.szczegoly.Opis if z.szczegoly.Opis != 'unknown'
  else '' }} |

  {% endfor %} {% else %} Brak nadchodzących zdarzeń. {% endif %}
```

### Karta sprawdzianów i klasówek (bez dni wolnych)

```yaml
type: markdown
title: 📝 Sprawdziany i klasówki
content: >
  {% set zdarzenia = state_attr('sensor.librus_imie_nazwisko_terminarz',
  'zdarzenia') %} {% set typy_testow = ['Sprawdzian', 'Kartkówka', 'Klasówka',
  'Praca klasowa'] %} {% set sprawdziany = zdarzenia | selectattr('tytul', 'in',
  typy_testow) | list %} {% if sprawdziany %} | Data | Dzień | Typ | Przedmiot |
  Opis |
   |------|-------|-----|-----------|------|
  {% for z in sprawdziany %} | **{{ z.data }}** | {{ z.tydzien }} | {{ z.tytul
  }} | {{ z.przedmiot }} | {{ z.szczegoly.Opis if z.szczegoly.Opis != 'unknown'
  else '' }} |

  {% endfor %} {% else %} Brak nadchodzących zdarzeń. {% endif %}
```

### Wykres średniej z przedmiotu (Gauge)
```yaml
type: gauge
entity: sensor.librus_srednia_matematyka
name: "Matematyka - średnia"
min: 1
max: 6
severity:
  green: 4.5
  yellow: 3
  red: 0
```

## 🖼️ Gotowe dashboardy i automatyzacje (przykłady)

W katalogu [`examples/`](examples) są gotowe pulpity i automatyzacje używające opisanych wyżej sensorów i usług. Wszystkie nazwy encji są ogólne, więc przed użyciem **zamień** `librus_imie_nazwisko` na nazwę swojej encji (Narzędzia deweloperskie → Stany), a w powiadomieniach `notify.mobile_app_TWOJ_TELEFON` na swoją usługę powiadomień.

| Plik | Zawartość |
|------|-----------|
| `examples/dashboards/librus_widok_glowny.yaml` | Widok główny z kafelkami przechodzącymi do pozostałych pulpitów |
| `examples/dashboards/oceny_dashboard.yaml` | Dwie zakładki: oceny wg przedmiotów i lista ostatnich ocen ze stronicowaniem oraz uwagi i zachowanie |
| `examples/dashboards/plan_lekcji_dashboard.yaml` | Dwie zakładki: plan tygodnia ze stronicowaniem, aktualna lekcja i tematy lekcji oraz formularz zajęć dodatkowych |
| `examples/dashboards/zadania_dashboard.yaml` | Zadania domowe po dniach ze stronicowaniem |
| `examples/dashboards/terminarz_dashboard.yaml`, `sprawdziany_dashboard.yaml` | Terminarz i sprawdziany ze stronicowaniem |
| `examples/dashboards/wiadomosci_dashboard.yaml` | Wiadomości ze stronicowaniem i podglądem treści |
| `examples/dashboards/librus_szkola_dashboard.yaml` | Ogłoszenia szkoły ze stronicowaniem |
| `examples/dashboards/frekwencja_dashboard.yaml` | Frekwencja, liczniki i lista wpisów ze stronicowaniem |
| `examples/automations/librus_automatyzacje.yaml` | Powiadomienia: nowe zadanie, nieobecność, ocena, ogłoszenie, uwaga, wpis o zachowaniu, wiadomość, koniec lekcji (także zajęć dodatkowych), przypomnienie o terminach |

Przyciski przeglądania w pulpitach używają kart [Mushroom](https://github.com/piitaya/lovelace-mushroom) (instalacja przez HACS). Wklejanie pulpitu: Ustawienia → Pulpity → (pulpit) → Edytuj → ⋮ → Edytor YAML.

## 🔔 Automatyzacje powiadomień na telefon

Integracja wysyła zdarzenia Home Assistant gdy pojawi się nowa wiadomość lub ocena.
Zdarzenia są wykrywane przy kolejnych odświeżeniach (wiadomości co 3 minuty, pozostałe co 15 minut). Pierwsze odświeżenie po starcie HA tylko zapamiętuje stan — **nie wysyła duplikatów**. Oznacza to także, że elementy, które pojawiły się w czasie restartu HA, nie wywołają powiadomienia.

> **Test bez czekania:** Idź do **Developer Tools → Events**, Event type: `librus_apix_nowa_wiadomosc`, Event data jak poniżej i kliknij **Fire Event**.

### 📬 Powiadomienie o nowej wiadomości

Zdarzenie: `librus_apix_nowa_wiadomosc`  
Dostępne dane: `nadawca`, `temat`, `data`, `ma_zalacznik`, `nieprzeczytana`, `tresc`, `indeks`

> **Uwaga:** Treść nowej wiadomości jest dociągana tylko wtedy, gdy jest potrzebna, a wcześniejsze wiadomości nie są przez to oznaczane jako przeczytane. Zdarzenie może przyjść bez treści (`tresc` puste).

```yaml
automation:
  - alias: "Librus - nowa wiadomosc"
    trigger:
      - platform: event
        event_type: librus_apix_nowa_wiadomosc
    action:
      - service: notify.mobile_app_NAZWA_TWOJEGO_TELEFONU
        data:
          title: "📬 Librus: nowa wiadomość"
          message: >-
            {% set msg = state_attr('sensor.librus_IMIE_NAZWISKO_wiadomosci', 'wiadomosci')
               | selectattr('nieprzeczytana', 'equalto', true) | list | first | default({}) %}
            Od: {{ msg.nadawca | default('nieznany') }}
            Temat: {{ msg.temat | default('brak') }}
```

> **Uwaga:** Zamień `sensor.librus_IMIE_NAZWISKO_wiadomosci` na nazwę swojego sensora widoczną w Developer Tools → States.

### 📝 Powiadomienie o nowej ocenie

Zdarzenie: `librus_apix_nowa_ocena`  
Dostępne dane: `przedmiot`, `ocena`, `data`, `kategoria`, `nauczyciel`, `waga`, `liczy_sie`, `komentarz`. Dla ocen opisowych `ocena` to „OO”, a `komentarz` zawiera treść opisu.

```yaml
automation:
  - alias: "Librus - nowa ocena"
    trigger:
      platform: event
      event_type: librus_apix_nowa_ocena
    action:
      - service: notify.mobile_app_NAZWA_TWOJEGO_TELEFONU
        data:
          title: "🎓 Librus: nowa ocena {{ trigger.event.data.ocena }}"
          message: >-
            {{ trigger.event.data.przedmiot }}
            Ocena: {{ trigger.event.data.ocena }}
            Kategoria: {{ trigger.event.data.kategoria }}
            Nauczyciel: {{ trigger.event.data.nauczyciel }}
```

> **Gdzie znaleźć nazwę telefonu?** HA → Settings → Devices & Services → Mobile App → nazwa urządzenia (np. `notify.mobile_app_samsung_galaxy_s24`)

### Pozostałe zdarzenia

| Zdarzenie | Dane |
|-----------|------|
| `librus_apix_nowe_zadanie` | `przedmiot`, `kategoria`, `termin`, `nauczyciel` |
| `librus_apix_nowe_zdarzenie` | `data`, `tytul`, `przedmiot`, `godzina` |
| `librus_apix_nowe_ogloszenie` | `tytul`, `autor`, `data`, `tresc` |
| `librus_apix_nowa_nieobecnosc` | `data`, `symbol`, `typ`, `przedmiot`, `godzina_lekcyjna`, `nauczyciel` |
| `librus_apix_nowa_uwaga` | `data`, `nauczyciel`, `rodzaj`, `znak` (`pozytywna` / `negatywna` / `neutralna`), `kategoria`, `tresc` |
| `librus_apix_nowy_wpis_zachowania` | `okres`, `ocena`, `rodzaj` (`pozytywne` / `negatywne` / `neutralne`), `data`, `nauczyciel`, `komentarz` |

### Uwagi i zachowanie

- **Zachowanie** jest czytane z tej samej strony ocen, więc nie kosztuje dodatkowego zapytania. Ocena śródroczna jest „propozycją”, dopóki etykieta w Librusie mówi o ocenie przewidywanej.
- **Uwagi** to osobna strona Librusa (jedno lekkie zapytanie co 15 minut). Jeśli konto nie ma modułu Uwagi, integracja stwierdza to jednym zapytaniem kontrolnym i pomija go przez 24 godziny, zamiast logować się ponownie co cykl (atrybut `dostepne` = `false`).
- Gdy strona uwag ma nieznany układ (nic nie odczytano i brak napisu „Brak uwag”), lista nie jest kasowana, atrybut `nierozpoznany_uklad` = `true`, a usługa `librus_apix.diagnostyka_ocen` zapisze jej HTML do analizy.

### Zajęcia dodatkowe

Zajęcia spoza planu Librusa dodajesz w UI, bez edycji YAML-a: wpisz nazwę (i opcjonalnie miejsce), wybierz `co tydzień` + dzień tygodnia, `co 2 tygodnie` + datę pierwszych zajęć albo `jednorazowo` + datę, ustaw godziny i naciśnij przycisk `..._lekcja_dodatkowa_dodaj`. Ten sam efekt daje usługa `librus_apix.dodaj_lekcje_dodatkowa`. Gotowy formularz (dodawanie, edycja, usuwanie) jest w drugiej zakładce `plan_lekcji_dashboard.yaml`, a lista terminów do odwołania na dole pierwszej.

- **Zajęcia co 2 tygodnie**: podaj datę pierwszych zajęć – powtarzają się co 14 dni od tej daty (dzień tygodnia wynika z daty, wcześniej niż od niej zajęć nie ma). Odwoływanie pojedynczych terminów działa tak samo jak przy zajęciach co tydzień.
- **Data rozpoczęcia i zakończenia**: zajęcia cykliczne możesz ograniczyć do zakresu dat (obie granice włącznie). `Obowiązuje od` dotyczy zajęć co tydzień (przy „co 2 tygodnie” początkiem jest data pierwszych zajęć), `Obowiązuje do` – obu rodzajów cyklicznych; zajęcia jednorazowe mają jedną datę. Pola są opcjonalne, a przycisk `..._lekcja_dodatkowa_wyczysc_daty_obowiazywania` czyści je w formularzu (przy edycji pozwala usunąć ograniczenie). Poza zakresem zajęć nie ma w planie, kalendarzu ani na liście terminów.
- Zajęcia są zapisane w `Store` Home Assistanta (przeżywają restart i aktualizację), osobno dla każdego konta. Nic nie jest usuwane automatycznie – zajęcia, które się skończyły, zostają na liście, dopóki ich sam nie usuniesz.
- Plan, który widzą czujniki (`..._plan_lekcji_dzis`, `..._nastepny_dzien`, `..._tydzien`, `..._aktualna_lekcja`), kalendarz oraz znaczniki `..._poczatek_lekcji_*` i `..._koniec_lekcji_dzis`, to **suma planu z Librusa i zajęć dodatkowych** posortowana wg godziny rozpoczęcia. Dlatego automatyzacja „koniec ostatniej lekcji” uwzględnia je bez żadnych zmian w wyzwalaczu.
- Zajęcia dodatkowe mają `numer` równy `+` i atrybut `dodatkowa: true` (w kalendarzu mają przedrostek ➕). Jeśli Librus chwilowo nie zwróci planu, plan składa się z samych zajęć dodatkowych, a po następnym udanym pobraniu wraca pełny.
- **Odwołanie jednego terminu**: na liście `todo.librus_..._zajecia_dodatkowe_terminy` (pod planem; pokazuje zajęcia z tygodnia wyświetlanego w planie i przesuwa się razem z nim) zaznacz termin, żeby go odwołać; odznaczenie go przywraca. Zajęcia zostają w planie jako ❌ odwołane (jak odwołana lekcja szkolna: nie liczą się do ostatniej lekcji ani do „aktualnej lekcji”), kolejne tygodnie bez zmian. To samo robią usługi `odwolaj_termin_lekcji_dodatkowej` i `przywroc_termin_lekcji_dodatkowej`.
- **Edycja**: wybierz zajęcia na liście `select.librus_..._lekcja_dodatkowa_edycja` (ładują się do formularza), zmień pola i naciśnij `..._zapisz_zmiany`; „➕ nowe zajęcia” wraca do dodawania. Zachowują identyfikator i te odwołane terminy, które nadal pasują do nowego dnia.
- **Usuwanie** (całych zajęć, nie jednego terminu): wybierz zajęcia na liście `..._lekcja_dodatkowa_do_usuniecia` i naciśnij `..._lekcja_dodatkowa_usun` albo użyj usługi `usun_lekcje_dodatkowa`.
- Lista zadań ma wbudowane w Home Assistanta nagłówki „Aktywne” i „Ukończone”: odwołane terminy trafiają do sekcji „Ukończone” (przekreślone, z dopiskiem „odwołane”). Tych nagłówków nie da się zmienić w konfiguracji karty.
- Ponowne dodanie takich samych zajęć (ta sama nazwa, godziny i termin) jest odrzucane.

## 🛠️ Rozwój

### Wymagania
- Python 3.9+
- Home Assistant 2023.12+
- librus-apix library

### Setup środowiska deweloperskiego
```bash
# Klonuj repozytorium
git clone https://github.com/twoje-username/librus-ha-integration
cd librus-ha-integration

# Uruchom środowisko testowe
docker-compose up -d

# Edytuj kod w Code Server (http://localhost:8443)
```

### Uruchomienie testów
```bash
pytest tests/
```

## 📝 Logi

Aby włączyć szczegółowe logi, dodaj do `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.librus_apix: debug
```

## ⚠️ Bezpieczeństwo

- **Nie udostępniaj swoich danych logowania!**  
- Dane są przechowywane lokalnie w Home Assistant
- Komunikacja z Librus odbywa się przez bezpieczne API
- Hasła są zaszyfrowane w konfiguracji

## 🐛 Zgłaszanie błędów

Jeśli znajdziesz błąd:

1. Włącz logi debug (patrz wyżej)
2. Skopiuj logi z błędem
3. Utwórz issue na GitHub z:
   - Opisem problemu
   - Krokami do reprodukcji
   - Logami (usuń dane osobowe!)

## 📄 Licencja

MIT License - patrz [LICENSE](LICENSE)

## 🤝 Wkład

Pull requesty i zgłoszenia są mile widziane. Pamiętaj, żeby w opisach i logach nie umieszczać danych osobowych.

### 🙏 Podziękowania

Specjalne podziękowania dla **KB** za wsparcie i pomoc w rozwoju projektu.

Podejście do rozpoznawania układu strony uwag i wiersza zachowania podejrzano w forkach [mchuc](https://github.com/mchuc/LibrusSynergiaHA) i [TomaszSyc](https://github.com/TomaszSyc/LibrusSynergiaHA) (kod napisany od nowa).

## 👨‍💻 Autor

Projekt oryginalny: [LukMaverick/LibrusSynergiaHA](https://github.com/LukMaverick/LibrusSynergiaHA), zbudowany na bibliotece [librus-apix](https://github.com/RustySnek/librus-apix). Ten fork rozwija integrację o funkcje opisane wyżej.

---

**⭐ Jeśli podoba Ci się projekt, zostaw gwiazdkę na GitHub!**

## ☕ Wesprzyj projekt

Jeśli integracja jest dla Ciebie przydatna, możesz postawić kawę 😊

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/LukMaverick)