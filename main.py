import requests
import os
import csv
import time
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import urllib.parse

# Biblioteki do wysyłania maili
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# BEZPIECZNE POBIERANIE HASEŁ Z GITHUB SECRETS
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER_RAW = os.environ.get("EMAIL_RECEIVER", "")


# Obsługa wielu maili
EMAIL_RECEIVERS = (
    [email.strip() for email in EMAIL_RECEIVER_RAW.split(",")]
    if EMAIL_RECEIVER_RAW
    else []
)

print("⚙️ Inicjalizacja bota...")

# --- PORTAL eLicytacje KAS (elicytacje.mf.gov.pl) ---
# Portal to aplikacja Angular, która pobiera dane z publicznego API JSON.
# Dzięki temu nie trzeba już parsować PDF-ów przez AI – ceny i wartości są w polach.
BASE_URL = "https://elicytacje.mf.gov.pl/auction"
API_URL = f"{BASE_URL}/api/v1"
SEKCJA_RUCHOMOSCI = "3e026ac1-291f-4898-9229-61d1074fcfe5"
KATEGORIA_POJAZDY = "f833e4c3-2fd9-4585-87a4-4b2a896cb86f"
# Stany ogłoszeń, które jeszcze mają sens (reszta: SOLD, UNSOLD, CLOSED, CANCELED, VOID)
AKTYWNE_STANY = {"WAITING", "INPROGRESS"}
PROG_OKAZJI = 50.0  # cena wywołania <= tylu % wartości szacunkowej
ROZMIAR_STRONY = 200

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
        url = f"https://nominatim.openstreetmap.org/search?q={city},+Poland&format=json&limit=1"
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


def zapisz_okazje(
    link,
    szacunkowa,
    wywolawcza,
    procent,
    nazwa,
    miasto,
    dystans,
    wolna_reka,
    data_licytacji,
):
    plik_istnieje = os.path.isfile(PLIK_WYNIKOW)
    with open(PLIK_WYNIKOW, mode="a", newline="", encoding="utf-8-sig") as plik:
        writer = csv.writer(plik, delimiter=";")
        if not plik_istnieje:
            writer.writerow(
                [
                    "Data znalezienia",
                    "Pojazd",
                    "Typ ofery",
                    "Miasto",
                    "Od Krakowa [km]",
                    "Link do ogłoszenia",
                    "Wartość szacunkowa [zł]",
                    "Cena wywoławcza [zł]",
                    "Procent wartości [%]",
                    "Data licytacji",
                ]
            )
        teraz = datetime.now(STREFA_PL).strftime("%Y-%m-%d %H:%M:%S")
        typ_oferty = "Wolna ręka" if wolna_reka else "Licytacja"
        writer.writerow(
            [
                teraz,
                nazwa,
                typ_oferty,
                miasto,
                dystans,
                link,
                szacunkowa,
                wywolawcza,
                f"{procent:.2f}",
                data_licytacji or "Brak",
            ]
        )
    print(f"   💾 Zapisano okazję do pliku CSV: {nazwa}")


def wyslij_email(lista_znalezionych):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVERS:
        print("⚠️ Brak pełnych danych e-mail. Pomijam wysyłanie.")
        return

    print("✉️ Przygotowuję e-mail z okazjami...")
    lista_znalezionych.sort(
        key=lambda x: x["szacunkowa"] if x["szacunkowa"] is not None else -float("inf"),
        reverse=True,
    )

    temat = f"🔥 Bot znalazł {len(lista_znalezionych)} nowe okazje skarbowe!"
    teraz = datetime.now(STREFA_PL).replace(tzinfo=None)

    tresc = "<h3>Dzisiejsze okazje (poniżej 50% wartości LUB z wolnej ręki):</h3><ul>"
    for okazja in lista_znalezionych:
        dystans_str = (
            f"{okazja['dystans']} km" if okazja["dystans"] is not None else "Nieznana"
        )
        oznaczenie_wolna_reka = (
            ' <span style="background-color: #ff9800; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;">⚡ WOLNA RĘKA</span>'
            if okazja["wolna_reka"]
            else ""
        )

        data_lic_str = okazja.get("data_licytacji")
        czas_do_licytacji = "Brak informacji o dacie"
        przycisk_kalendarz = ""

        if data_lic_str:
            try:
                data_lic = datetime.strptime(data_lic_str, "%Y-%m-%d %H:%M")
                roznica = data_lic - teraz

                if roznica.total_seconds() > 0:
                    dni = roznica.days
                    godziny = roznica.seconds // 3600
                    czas_do_licytacji = f"za <b>{dni} dni i {godziny} godzin</b> ({data_lic.strftime('%d.%m.%Y %H:%M')})"
                else:
                    czas_do_licytacji = f"<b>Licytacja już trwa lub minęła</b> ({data_lic.strftime('%d.%m.%Y %H:%M')})"

                data_poczatek = data_lic.strftime("%Y%m%dT%H%M%S")
                data_koniec = (data_lic + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
                tytul = urllib.parse.quote(f"Licytacja US: {okazja['nazwa']}")
                opis = urllib.parse.quote(
                    f"Szczegóły licytacji:\nCena wywoławcza: {okazja['wywolawcza']} zł\nLink do ogłoszenia: {okazja['link']}"
                )
                lokalizacja = urllib.parse.quote(okazja["miasto"] or "Polska")

                url_kalendarz = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={tytul}&dates={data_poczatek}/{data_koniec}&details={opis}&location={lokalizacja}"
                przycisk_kalendarz = f'<a href="{url_kalendarz}" style="background-color: #4285F4; color: white; padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 12px; margin-top: 5px; display: inline-block;">📅 Dodaj do Kalendarza Google</a>'

            except Exception as e:
                print(
                    f"   ⚠️ Błąd parsowania daty dla {okazja['nazwa']}: {data_lic_str} -> {e}"
                )
                czas_do_licytacji = f"Data z ogłoszenia: {data_lic_str}"

        tresc += f"""
        <li style="margin-bottom: 25px; border-bottom: 1px solid #ddd; padding-bottom: 15px;">
            <b>Pojazd:</b> {okazja["nazwa"] or "Brak danych"}{oznaczenie_wolna_reka} <br>
            <b>Lokalizacja:</b> {okazja["miasto"] or "Brak danych"} (od Krakowa: {dystans_str})<br>
            <b>Oszacowano:</b> <span style="color: blue;">{okazja["szacunkowa"]} zł</span> | <b>Wywoławcza:</b> {okazja["wywolawcza"]} zł<br>
            <b>Opłacalność:</b> <span style="color: green; font-weight: bold;">{okazja["procent"]}% wartości</span><br>
            <b>Czas do licytacji:</b> {czas_do_licytacji}<br>
            🔗 <a href="{okazja["link"]}">Przejdź do ogłoszenia</a><br>
            {przycisk_kalendarz}
        </li>
        """
    tresc += "</ul><br><small>Wiadomość wygenerowana automatycznie przez Twojego bota 🤖</small>"

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
        print(
            f"📧 E-mail z okazjami pomyślnie wysłany do: {', '.join(EMAIL_RECEIVERS)}"
        )
    except Exception as e:
        print(f"❌ Błąd podczas wysyłania e-maila: {e}")


def wyslij_email_brak_okazji():
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVERS:
        return

    print("✉️ Przygotowuję e-mail o braku okazji...")
    temat = "ℹ️ Bot Skarbowy: Brak nowych okazji na dziś"

    tresc = """
    <h3>Cześć!</h3>
    <p>Dzisiejszy przegląd licytacji skarbowych został zakończony.</p>
    <p>Niestety, <b>nie znalazłem dzisiaj żadnych nowych ofert</b> pojazdów, które spełniałyby Twoje kryteria (czyli z ceną wywoławczą poniżej 50% wartości szacunkowej lub ofert sprzedaży z wolnej ręki).</p>
    <p>Jutro rano znów sprawdzę stronę i dam Ci znać, jeśli pojawi się coś interesującego.</p>
    <br><small>Wiadomość wygenerowana automatycznie przez Twojego bota 🤖</small>
    """

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(EMAIL_RECEIVERS)
    msg["Subject"] = temat
    msg.attach(MIMEText(tresc, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Wysłano informację o braku okazji do: {', '.join(EMAIL_RECEIVERS)}")
    except Exception as e:
        print(f"❌ Błąd podczas wysyłania e-maila (brak okazji): {e}")


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


def zbuduj_nazwe(przedmiot, parametry, tytul):
    p = {x.get("parameterName"): x.get("parameterValue") for x in parametry}
    czesci = [p.get("Marka"), p.get("Model")]
    nazwa = " ".join(str(c) for c in czesci if c) or przedmiot.get("name") or tytul
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


def uruchom_bota():
    print("\n" + "=" * 60)
    print("🚀 ROZPOCZYNAM DZIAŁANIE BOTA (portal eLicytacje KAS)")
    print("=" * 60)

    odwiedzone_linki = wczytaj_historie()

    print("🌐 Pobieram listę pojazdów z elicytacje.mf.gov.pl ...")
    try:
        ogloszenia = pobierz_liste_pojazdow()
    except Exception as e:
        print(f"❌ Błąd pobierania listy ogłoszeń: {e}")
        return

    aktywne = [o for o in ogloszenia if o.get("electronicStateCode") in AKTYWNE_STANY]
    print(
        f"📌 Pobrano {len(ogloszenia)} ogłoszeń, z czego {len(aktywne)} aktywnych "
        f"(oczekujące / w toku)."
    )

    znalezione_dzisiaj = []
    for index, ogl in enumerate(aktywne, start=1):
        uuid = ogl["uuid"]
        link = f"{BASE_URL}/announcements/{uuid}"

        if link in odwiedzone_linki:
            continue

        print(f"\n[{index}/{len(aktywne)}] 🔍 Nowe ogłoszenie: {ogl.get('title')} – {link}")
        try:
            szczegoly = api_get(f"/announcements/{uuid}")
            item_id = szczegoly["announcement"]["itemId"]
            item = api_get(f"/items/{item_id}")
            przedmiot = item.get("item", {})
            parametry = item.get("parameters", []) or []

            wywolawcza = ogl.get("startingPrice") or szczegoly["announcement"].get(
                "startingPrice"
            )
            # Cena wywołania na portalu jest brutto ("Zawiera VAT"), więc porównujemy z brutto
            szacunkowa = przedmiot.get("estimatedValueGross") or przedmiot.get(
                "estimatedValueNet"
            )
            grupa = szczegoly["announcement"].get("announcementTypeGroupCode") or ""
            wolna_reka = grupa.startswith("SPR") or any(
                k.startswith("SPR") for k in ogl.get("announcementTypeCodes", [])
            )
            nazwa = zbuduj_nazwe(przedmiot, parametry, ogl.get("title"))
            adres = (szczegoly.get("item") or {}).get("localizationAddress") or {}
            miasto = adres.get("city") or ogl.get("localization")
            data_licytacji = utc_na_polski_czas(ogl.get("saleBeginDateTime"))

            if wywolawcza and szacunkowa:
                procent = (float(wywolawcza) / float(szacunkowa)) * 100
            elif wolna_reka and wywolawcza:
                szacunkowa = wywolawcza
                procent = 100.0
            else:
                procent = None

            print(
                f"      🚗 {nazwa}: Wartość: {szacunkowa} zł, Wywoławcza: {wywolawcza} zł "
                f"({f'{procent:.0f}%' if procent is not None else '?'}) | "
                f"Wolna ręka: {wolna_reka} | Data: {data_licytacji} | {miasto}"
            )

            if procent is not None and (procent <= PROG_OKAZJI or wolna_reka):
                print(f"      🔥 TO OKAZJA! Liczę dystans z miasta {miasto} do Krakowa...")
                dystans = get_distance_to_krakow(miasto)
                zapisz_okazje(
                    link, szacunkowa, wywolawcza, procent, nazwa, miasto,
                    dystans, wolna_reka, data_licytacji,
                )
                znalezione_dzisiaj.append(
                    {
                        "link": link,
                        "szacunkowa": szacunkowa,
                        "wywolawcza": wywolawcza,
                        "procent": f"{procent:.0f}",
                        "nazwa": nazwa,
                        "miasto": miasto,
                        "dystans": dystans,
                        "wolna_reka": wolna_reka,
                        "data_licytacji": data_licytacji,
                    }
                )

            zapisz_do_historii(link)
            odwiedzone_linki.add(link)
            time.sleep(0.5)  # nie zasypujemy serwera zapytaniami

        except Exception as e:
            print(f"   🛑 Błąd przy przetwarzaniu ogłoszenia {link}: {e}")

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
