import requests
import os
import csv
import time
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import urllib.parse
from html import escape

# Biblioteki do wysyłania maili
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# BEZPIECZNE POBIERANIE KLUCZY I HASEŁ Z GITHUB SECRETS
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER_RAW = os.environ.get("EMAIL_RECEIVER", "")

EMAIL_RECEIVERS = (
    [email.strip() for email in EMAIL_RECEIVER_RAW.split(",")]
    if EMAIL_RECEIVER_RAW
    else []
)

print("⚙️ Inicjalizacja bota...")

# --- PORTAL eLicytacje KAS (elicytacje.mf.gov.pl) ---
# Portal to aplikacja Angular, która pobiera dane z publicznego API JSON.
BASE_URL = "https://elicytacje.mf.gov.pl/auction"
API_URL = f"{BASE_URL}/api/v1"
SEKCJA_RUCHOMOSCI = "3e026ac1-291f-4898-9229-61d1074fcfe5"
KATEGORIA_POJAZDY = "f833e4c3-2fd9-4585-87a4-4b2a896cb86f"
# Stany ogłoszeń, które jeszcze mają sens (reszta: SOLD, UNSOLD, CLOSED, CANCELED, VOID)
AKTYWNE_STANY = {"WAITING", "INPROGRESS"}
ROZMIAR_STRONY = 200

# --- KRYTERIA OKAZJI ---
# Porównujemy AKTUALNĄ cenę (cena wywołania albo najwyższa oferta, jeśli licytacja
# trwa) z urzędową wartością szacunkową brutto.
# Uwaga: z mocy prawa I licytacja startuje od 75%, a II od 50% tej wartości,
# a wolna ręka zwykle mieści się w 10–40%.
PROG_LICYTACJA = 50.0  # II licytacje, dopóki cena nie przekroczy połowy wartości
PROG_WOLNA_REKA = 40.0  # wolna ręka tylko wyraźnie poniżej wartości
MIN_WARTOSC = 3000  # pomijamy drobnicę (motorowery, złom) – dojazd zjadłby zysk

PLIK_WYNIKOW = "okazje_licytacje.csv"
PLIK_HISTORII = "historia_linkow.txt"
STREFA_PL = ZoneInfo("Europe/Warsaw")

SESJA = requests.Session()
SESJA.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) BotSkarbowy/2.0",
        "Accept": "application/json",
    }
)

# --- WSPÓŁRZĘDNE KRAKOWA ---
KRAKOW_LAT = 50.06143
KRAKOW_LON = 19.93658

CACHE_MIAST = {}

NAGLOWEK_CSV = [
    "Data znalezienia",
    "Pojazd",
    "Typ oferty",
    "Miasto",
    "Od Krakowa [km]",
    "Link do ogłoszenia",
    "Wartość szacunkowa [zł]",
    "Aktualna cena [zł]",
    "Procent wartości [%]",
    "Cena wywołania [zł]",
    "Data licytacji",
]


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_distance_to_krakow(city):
    if not city:
        return None

    miasto_lower = city.lower().strip()
    if miasto_lower in CACHE_MIAST:
        return CACHE_MIAST[miasto_lower]

    print(f"   🗺️ Szukam współrzędnych dla miasta: {city}...")
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(city)},+Poland&format=json&limit=1"
        headers = {
            "User-Agent": f"LicytacjeBot/1.0 ({EMAIL_SENDER or 'nieznany@mail.com'})"
        }
        resp = requests.get(url, headers=headers, timeout=10).json()

        if resp:
            lat = float(resp[0]["lat"])
            lon = float(resp[0]["lon"])
            dystans = round(haversine(KRAKOW_LAT, KRAKOW_LON, lat, lon))
            CACHE_MIAST[miasto_lower] = dystans
            print(f"   📍 Obliczono dystans do {city}: {dystans} km")
            time.sleep(1.5)  # Zabezpieczenie przed limitem zapytań
            return dystans
    except Exception as e:
        print(f"   ❌ Błąd pobierania odległości dla {city}: {e}")

    CACHE_MIAST[miasto_lower] = None
    return None


def wczytaj_historie():
    odwiedzone = set()
    if os.path.isfile(PLIK_HISTORII):
        print(f"📂 Wczytuję historię linków z pliku {PLIK_HISTORII}...")
        with open(PLIK_HISTORII, mode="r", encoding="utf-8") as plik:
            for linia in plik:
                odwiedzone.add(linia.strip())
        print(f"✅ Wczytano {len(odwiedzone)} odwiedzonych wcześniej linków.")
    else:
        print("📂 Plik historii nie istnieje. Zaczynamy z czystą kartą.")
    return odwiedzone


def zapisz_do_historii(link):
    with open(PLIK_HISTORII, mode="a", encoding="utf-8") as plik:
        plik.write(link + "\n")


def przygotuj_plik_csv():
    """Plik CSV z innymi kolumnami (stara wersja bota) – odkładamy go na bok."""
    if os.path.isfile(PLIK_WYNIKOW):
        with open(PLIK_WYNIKOW, encoding="utf-8-sig") as plik:
            pierwsza_linia = plik.readline().strip()
        if pierwsza_linia != ";".join(NAGLOWEK_CSV):
            os.replace(PLIK_WYNIKOW, "okazje_licytacje_stare.csv")
            print("📦 Stary plik CSV przeniesiono do okazje_licytacje_stare.csv")


def zapisz_okazje(okazja):
    plik_istnieje = os.path.isfile(PLIK_WYNIKOW)
    with open(PLIK_WYNIKOW, mode="a", newline="", encoding="utf-8-sig") as plik:
        writer = csv.writer(plik, delimiter=";")
        if not plik_istnieje:
            writer.writerow(NAGLOWEK_CSV)
        writer.writerow(
            [
                datetime.now(STREFA_PL).strftime("%Y-%m-%d %H:%M:%S"),
                okazja["nazwa"],
                "Wolna ręka" if okazja["wolna_reka"] else okazja["typ_licytacji"],
                okazja["miasto"],
                okazja["dystans"],
                okazja["link"],
                okazja["szacunkowa"],
                okazja["aktualna_cena"],
                f"{okazja['procent']:.2f}",
                okazja["wywolawcza"],
                okazja["data_licytacji"] or "Brak",
            ]
        )
    print(f"   💾 Zapisano okazję do pliku CSV: {okazja['nazwa']}")


# ---------------------------------------------------------------------------
# API eLicytacje
# ---------------------------------------------------------------------------


def api_get(sciezka):
    odp = SESJA.get(f"{API_URL}{sciezka}", timeout=20)
    odp.raise_for_status()
    return odp.json()


def pobierz_liste_pojazdow():
    """Pobiera wszystkie ogłoszenia z kategorii Pojazdy (strona po stronie)."""
    wszystkie = []
    strona = 0
    while True:
        url = (
            f"{API_URL}/announcement/external"
            f"?page={strona}&size={ROZMIAR_STRONY}&sort=creationTimestamp,desc"
        )
        filtr = {"sectionId": SEKCJA_RUCHOMOSCI, "categoryId": KATEGORIA_POJAZDY}
        odp = SESJA.post(url, json=filtr, timeout=30)
        odp.raise_for_status()
        dane = odp.json()
        wszystkie.extend(dane.get("content", []))
        laczna_liczba_stron = dane.get("totalPages", 1)
        print(
            f"   📄 Strona {strona + 1}/{laczna_liczba_stron} – "
            f"łącznie {dane.get('totalElements')} ogłoszeń w kategorii Pojazdy"
        )
        strona += 1
        if strona >= laczna_liczba_stron or not dane.get("content"):
            break
        time.sleep(1)
    return wszystkie


def utc_na_polski_czas(tekst_utc):
    """'2026-12-09T09:00:00Z' -> '2026-12-09 10:00' (czas polski)."""
    if not tekst_utc:
        return None
    try:
        dt = datetime.fromisoformat(tekst_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(STREFA_PL).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None


def zbuduj_nazwe(przedmiot, p, tytul):
    nazwa = " ".join(str(c) for c in [p.get("Marka"), p.get("Model")] if c)
    nazwa = nazwa or przedmiot.get("name") or tytul
    if p.get("Rok produkcji"):
        nazwa += f" ({p['Rok produkcji']})"
    if p.get("Przebieg"):
        try:
            km = int(float(p["Przebieg"]))
            if km > 0:
                nazwa += ", " + f"{km:,}".replace(",", " ") + " km"
        except (TypeError, ValueError):
            pass
    return nazwa


def pobierz_pojazd(ogl):
    uuid = ogl["uuid"]
    szczegoly = api_get(f"/announcements/{uuid}")
    ann = szczegoly["announcement"]
    item = api_get(f"/items/{ann['itemId']}")
    przedmiot = item.get("item", {}) or {}
    parametry = {
        x.get("parameterName"): x.get("parameterValue")
        for x in (item.get("parameters") or [])
    }

    grupa = ann.get("announcementTypeGroupCode") or ""
    wolna_reka = grupa.startswith("SPR") or any(
        k.startswith("SPR") for k in ogl.get("announcementTypeCodes", [])
    )
    sekwencja = (szczegoly.get("auction") or {}).get("auctionSequenceCode") or ""
    typ_licytacji = {"1L": "I licytacja", "2L": "II licytacja"}.get(
        sekwencja, "Licytacja"
    )

    wywolawcza = ogl.get("startingPrice") or ann.get("startingPrice")
    najwyzsza = ogl.get("highestBid")
    aktualna_cena = max(float(wywolawcza or 0), float(najwyzsza or 0)) or None

    adres = (szczegoly.get("item") or {}).get("localizationAddress") or {}

    return {
        "uuid": uuid,
        "link": f"{BASE_URL}/announcements/{uuid}",
        "nazwa": zbuduj_nazwe(przedmiot, parametry, ogl.get("title")),
        "tytul": ogl.get("title"),
        "parametry": parametry,
        "miasto": adres.get("city") or ogl.get("localization"),
        "wolna_reka": wolna_reka,
        "typ_licytacji": typ_licytacji,
        "wywolawcza": wywolawcza,
        "najwyzsza_oferta": najwyzsza,
        "aktualna_cena": aktualna_cena,
        # Cena wywołania na portalu jest brutto ("Zawiera VAT"), więc bierzemy brutto
        "szacunkowa": przedmiot.get("estimatedValueGross")
        or przedmiot.get("estimatedValueNet"),
        "stan": ogl.get("electronicStateCode"),
        "data_licytacji": utc_na_polski_czas(ogl.get("saleBeginDateTime")),
    }


# ---------------------------------------------------------------------------
# E-maile
# ---------------------------------------------------------------------------


def zl(kwota):
    if kwota is None:
        return "?"
    return f"{float(kwota):,.0f}".replace(",", " ") + " zł"


def wyslij_wiadomosc(temat, tresc):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECEIVERS)
    msg["Subject"] = temat
    msg.attach(MIMEText(tresc, "html"))

    print("🚀 Łączę się z serwerem SMTP...")
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 E-mail wysłany do: {', '.join(EMAIL_RECEIVERS)}")
    except Exception as e:
        print(f"❌ Błąd podczas wysyłania e-maila: {e}")


def wyslij_email(lista_znalezionych):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVERS:
        print("⚠️ Brak pełnych danych e-mail. Pomijam wysyłanie.")
        return

    print("✉️ Przygotowuję e-mail z okazjami...")
    # Najpierw największy rabat względem rynku
    lista_znalezionych.sort(key=lambda x: x["procent"])

    temat = f"🔥 Bot znalazł {len(lista_znalezionych)} nowe okazje skarbowe!"
    teraz = datetime.now(STREFA_PL).replace(tzinfo=None)

    tresc = (
        f"<h3>Dzisiejsze okazje (licytacje ≤ {PROG_LICYTACJA:.0f}%, wolna ręka "
        f"≤ {PROG_WOLNA_REKA:.0f}% wartości szacunkowej, wartość od {zl(MIN_WARTOSC)}):</h3><ul>"
    )
    for okazja in lista_znalezionych:
        dystans_str = (
            f"{okazja['dystans']} km" if okazja["dystans"] is not None else "Nieznana"
        )
        etykieta = (
            ' <span style="background-color: #ff9800; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;">⚡ WOLNA RĘKA</span>'
            if okazja["wolna_reka"]
            else f' <span style="background-color: #607d8b; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px;">{okazja["typ_licytacji"]}</span>'
        )
        if okazja["stan"] == "INPROGRESS":
            etykieta += ' <span style="background-color: #e53935; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px;">TRWA</span>'

        cena_str = f"<b>{zl(okazja['aktualna_cena'])}</b>"
        if okazja["najwyzsza_oferta"]:
            cena_str += f" (najwyższa oferta; wywołanie {zl(okazja['wywolawcza'])})"
        else:
            cena_str += " (cena wywołania)"

        data_lic_str = okazja.get("data_licytacji")
        czas_do_licytacji = "Brak informacji o dacie"
        przycisk_kalendarz = ""
        if data_lic_str:
            data_lic = datetime.strptime(data_lic_str, "%Y-%m-%d %H:%M")
            roznica = data_lic - teraz
            if roznica.total_seconds() > 0:
                czas_do_licytacji = (
                    f"za <b>{roznica.days} dni i {roznica.seconds // 3600} godzin</b> "
                    f"({data_lic.strftime('%d.%m.%Y %H:%M')})"
                )
            else:
                czas_do_licytacji = (
                    f"<b>Sprzedaż już trwa</b> (od {data_lic.strftime('%d.%m.%Y %H:%M')})"
                )

            data_poczatek = data_lic.strftime("%Y%m%dT%H%M%S")
            data_koniec = (data_lic + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
            tytul = urllib.parse.quote(f"Licytacja US: {okazja['nazwa']}")
            opis = urllib.parse.quote(
                f"Cena: {zl(okazja['aktualna_cena'])}\n"
                f"Wartość szacunkowa: {zl(okazja['szacunkowa'])}\n"
                f"Ogłoszenie: {okazja['link']}"
            )
            lokalizacja = urllib.parse.quote(okazja["miasto"] or "Polska")
            url_kalendarz = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={tytul}&dates={data_poczatek}/{data_koniec}&details={opis}&location={lokalizacja}"
            przycisk_kalendarz = f'<a href="{url_kalendarz}" style="background-color: #4285F4; color: white; padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 12px; margin-top: 5px; display: inline-block;">📅 Dodaj do Kalendarza Google</a>'

        tresc += f"""
        <li style="margin-bottom: 25px; border-bottom: 1px solid #ddd; padding-bottom: 15px;">
            <b>Pojazd:</b> {escape(okazja["nazwa"] or "Brak danych")}{etykieta}<br>
            <b>Lokalizacja:</b> {escape(okazja["miasto"] or "Brak danych")} (od Krakowa: {dystans_str})<br>
            <b>Cena:</b> {cena_str}<br>
            <b>Wartość szacunkowa:</b> <span style="color: blue;">{zl(okazja["szacunkowa"])}</span><br>
            <b>Opłacalność:</b> <span style="color: green; font-weight: bold;">{okazja["procent"]:.0f}% wartości szacunkowej</span><br>
            <b>Start sprzedaży:</b> {czas_do_licytacji}<br>
            🔗 <a href="{okazja["link"]}">Przejdź do ogłoszenia</a><br>
            {przycisk_kalendarz}
        </li>
        """
    tresc += (
        "</ul><small>Wiadomość wygenerowana automatycznie przez Twojego bota 🤖</small>"
    )
    wyslij_wiadomosc(temat, tresc)


def wyslij_email_brak_okazji():
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVERS:
        return

    print("✉️ Przygotowuję e-mail o braku okazji...")
    tresc = f"""
    <h3>Cześć!</h3>
    <p>Dzisiejszy przegląd portalu eLicytacje KAS został zakończony.</p>
    <p>Niestety, <b>nie znalazłem dzisiaj żadnych nowych ofert</b> pojazdów, które spełniałyby
    Twoje kryteria (licytacje ≤ {PROG_LICYTACJA:.0f}%, wolna ręka ≤ {PROG_WOLNA_REKA:.0f}%
    wartości szacunkowej, wartość od {zl(MIN_WARTOSC)}).</p>
    <p>Jutro rano znów sprawdzę portal i dam Ci znać, jeśli pojawi się coś interesującego.</p>
    <br><small>Wiadomość wygenerowana automatycznie przez Twojego bota 🤖</small>
    """
    wyslij_wiadomosc("ℹ️ Bot Skarbowy: Brak nowych okazji na dziś", tresc)


# ---------------------------------------------------------------------------
# Główna pętla
# ---------------------------------------------------------------------------


def uruchom_bota():
    print("\n" + "=" * 60)
    print("🚀 ROZPOCZYNAM DZIAŁANIE BOTA (portal eLicytacje KAS)")
    print("=" * 60)

    przygotuj_plik_csv()
    odwiedzone_linki = wczytaj_historie()

    print("🌐 Pobieram listę pojazdów z elicytacje.mf.gov.pl ...")
    try:
        ogloszenia = pobierz_liste_pojazdow()
    except Exception as e:
        print(f"❌ Błąd pobierania listy ogłoszeń: {e}")
        return

    aktywne = [o for o in ogloszenia if o.get("electronicStateCode") in AKTYWNE_STANY]
    nowe = [o for o in aktywne if f"{BASE_URL}/announcements/{o['uuid']}" not in odwiedzone_linki]
    print(
        f"📌 Pobrano {len(ogloszenia)} ogłoszeń: {len(aktywne)} aktywnych, "
        f"w tym {len(nowe)} nowych do sprawdzenia."
    )

    znalezione_dzisiaj = []
    for index, ogl in enumerate(nowe, start=1):
        try:
            p = pobierz_pojazd(ogl)
        except Exception as e:
            print(f"   🛑 Błąd pobierania ogłoszenia {ogl.get('uuid')}: {e}")
            continue

        if p["szacunkowa"] and p["aktualna_cena"]:
            p["procent"] = p["aktualna_cena"] / float(p["szacunkowa"]) * 100
        else:
            p["procent"] = None

        if p["procent"] is not None:
            print(
                f"[{index}/{len(nowe)}] 🚗 {p['nazwa']}: cena {zl(p['aktualna_cena'])}, "
                f"wartość {zl(p['szacunkowa'])} ({p['procent']:.0f}%) | "
                f"{'wolna ręka' if p['wolna_reka'] else p['typ_licytacji']} | {p['miasto']}"
            )
            prog = PROG_WOLNA_REKA if p["wolna_reka"] else PROG_LICYTACJA
            if p["procent"] <= prog and float(p["szacunkowa"]) >= MIN_WARTOSC:
                print("      🔥 TO OKAZJA! Liczę dystans do Krakowa...")
                p["dystans"] = get_distance_to_krakow(p["miasto"])
                zapisz_okazje(p)
                znalezione_dzisiaj.append(p)
        else:
            print(f"[{index}/{len(nowe)}] 🤷 {p['nazwa']}: brak ceny lub wartości – pomijam.")

        zapisz_do_historii(p["link"])
        odwiedzone_linki.add(p["link"])
        time.sleep(0.5)  # nie zasypujemy serwera zapytaniami

    print("\n" + "=" * 60)
    print("🏁 ETAP POBIERANIA ZAKOŃCZONY")
    if znalezione_dzisiaj:
        print(f"🎉 Znalazłem dzisiaj {len(znalezione_dzisiaj)} okazji! Wysyłam raport...")
        wyslij_email(znalezione_dzisiaj)
    else:
        print("🤷‍♂️ Brak nowych okazji. Wysyłam e-mail z pustym raportem...")
        wyslij_email_brak_okazji()


if __name__ == "__main__":
    uruchom_bota()
