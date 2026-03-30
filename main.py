import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import json
import re
import os
import tempfile
import csv
import time
import math
import zipfile
import xml.etree.ElementTree as ET
import io
from datetime import datetime, timedelta
import urllib.parse
from urllib.parse import urljoin

# Biblioteki do wysyłania maili
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# BEZPIECZNE POBIERANIE KLUCZY I HASEŁ Z GITHUB SECRETS
API_KEY = os.environ.get("GEMINI_API_KEY")
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
if API_KEY:
    client = genai.Client(api_key=API_KEY)
    print("✅ Klucz API Gemini został załadowany.")
else:
    print("⚠️ Brak klucza API Gemini. Ustaw zmienną środowiskową GEMINI_API_KEY.")
    client = None

URL_GLOWNE = "https://skarbowe-licytacje.com/?q=&region=&category=pojazdy&city=&source="
PLIK_WYNIKOW = "okazje_licytacje.csv"
PLIK_HISTORII = "historia_linkow.txt"

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

def zapisz_okazje(link, szacunkowa, wywolawcza, procent, nazwa, miasto, dystans, wolna_reka, data_licytacji):
    plik_istnieje = os.path.isfile(PLIK_WYNIKOW)
    with open(PLIK_WYNIKOW, mode="a", newline="", encoding="utf-8-sig") as plik:
        writer = csv.writer(plik, delimiter=";")
        if not plik_istnieje:
            writer.writerow([
                "Data znalezienia", "Pojazd", "Typ ofery", "Miasto", "Od Krakowa [km]",
                "Link do ogłoszenia", "Wartość szacunkowa [zł]", "Cena wywoławcza [zł]",
                "Procent wartości [%]", "Data licytacji"
            ])
        teraz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        typ_oferty = "Wolna ręka" if wolna_reka else "Licytacja"
        writer.writerow([
            teraz, nazwa, typ_oferty, miasto, dystans, link,
            szacunkowa, wywolawcza, f"{procent:.2f}", data_licytacji or "Brak"
        ])
    print(f"   💾 Zapisano okazję do pliku CSV: {nazwa}")

def wyciagnij_tekst_z_docx(docx_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as docx_zip:
            xml_content = docx_zip.read("word/document.xml")
            tree = ET.XML(xml_content)
            NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            tekst = []
            for paragraph in tree.iter(NAMESPACE + "p"):
                texts = [node.text for node in paragraph.iter(NAMESPACE + "t") if node.text]
                if texts:
                    tekst.append("".join(texts))
            print("   ✅ Pomyślnie wyciągnięto tekst z pliku DOCX.")
            return "\n".join(tekst)
    except Exception as e:
        print(f"   🛑 Błąd odczytu DOCX: {e}")
        return ""

def zapytaj_ai_o_ceny_z_tekstu(tekst):
    if not tekst or len(tekst) < 50:
        print("   ⚠️ Tekst jest zbyt krótki do analizy, pomijam.")
        return []

    print("   🧠 Wysyłam tekst do analizy przez Gemini AI...")
    prompt = f"""
    Przeanalizuj poniższy tekst obwieszczenia urzędowego.
    Znajdź pojazdy i dla KAŻDEGO z nich podaj 6 informacji:
    1. "szacunkowa" - Wartość szacunkowa (liczba)
    2. "wywolawcza" - Cena wywoławcza (liczba)
    3. "nazwa" - Nazwa pojazdu (marka i model)
    4. "miasto" - Miasto (miejscowość licytacji/urzędu)
    5. "wolna_reka" - Czy jest to sprzedaż z wolnej ręki? (true/false)
    6. "data_licytacji" - Data i godzina licytacji w ścisłym formacie "YYYY-MM-DD HH:MM" (np. 2024-05-12 10:00). Jeśli jest podany tylko dzień, dopisz godzinę "08:00".

    WAŻNE: Jeśli to "sprzedaż z wolnej ręki" i jest JEDNA kwota, wstaw ją jako OBIEDWIE wartości cenowe.
    Jeśli którejś informacji brakuje, wstaw null.
    
    Tekst:
    {tekst[:6000]} 
    """
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        dane = json.loads(response.text)
        print("   ✅ Otrzymano odpowiedź JSON od modelu AI.")
        if isinstance(dane, dict):
            return [dane]
        elif isinstance(dane, list):
            return dane
        return []
    except Exception as e:
        if "429" in str(e):
            print("   ⏳ Limit API Google (Tekst). Czekam 45 sekund...")
            time.sleep(45)
        else:
            print(f"   🤖 Błąd API (Tekst): {e}")
        return []

def przeanalizuj_pdf_z_ai(pdf_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        temp_pdf.write(pdf_bytes)
        temp_pdf_path = temp_pdf.name

    try:
        print("   📤 Przesyłam dokument PDF do File API Gemini...")
        wgrany_plik = client.files.upload(file=temp_pdf_path)
        print("   🧠 Wysyłam plik PDF do analizy wizualnej przez AI...")

        prompt = """
        Przeczytaj dokładnie ten dokument i znajdź pojazdy. Dla KAŻDEGO pojazdu podaj 6 informacji:
        1. "szacunkowa" - Wartość szacunkowa (liczba)
        2. "wywolawcza" - Cena wywoławcza (liczba)
        3. "nazwa" - Nazwa pojazdu (marka i model)
        4. "miasto" - Miasto (miejscowość prowadzenia licytacji)
        5. "wolna_reka" - Czy jest to sprzedaż z wolnej ręki? (true/false)
        6. "data_licytacji" - Data i godzina licytacji w ścisłym formacie "YYYY-MM-DD HH:MM" (np. 2024-05-12 10:00). Jeśli jest podana tylko data, dopisz godzinę "08:00".
        
        WAŻNE: Jeśli to "sprzedaż z wolnej ręki" i podana jest tylko jedna kwota, przypisz ją do OBU pól cenowych.
        Jeśli którejś informacji brakuje, wstaw null.
        """

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[wgrany_plik, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        print("   🗑️ Usuwam przetworzony plik z chmury Gemini...")
        client.files.delete(name=wgrany_plik.name)
        
        dane = json.loads(response.text)
        print("   ✅ Pomyślnie przetworzono odpowiedź AI z pliku PDF.")
        if isinstance(dane, dict):
            return [dane]
        elif isinstance(dane, list):
            return dane
        return []
    except Exception as e:
        if "429" in str(e):
            print("   ⏳ Limit API w PDF. Czekam 45 sekund...")
            time.sleep(45)
        else:
            print(f"   🤖 Błąd API (PDF): {e}")
        return []
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

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
    teraz = datetime.now()

    tresc = "<h3>Dzisiejsze okazje (poniżej 50% wartości LUB z wolnej ręki):</h3><ul>"
    for okazja in lista_znalezionych:
        dystans_str = f"{okazja['dystans']} km" if okazja["dystans"] is not None else "Nieznana"
        oznaczenie_wolna_reka = (
            ' <span style="background-color: #ff9800; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold;">⚡ WOLNA RĘKA</span>'
            if okazja["wolna_reka"] else ""
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
                opis = urllib.parse.quote(f"Szczegóły licytacji:\nCena wywoławcza: {okazja['wywolawcza']} zł\nLink do obwieszczenia: {okazja['link']}")
                lokalizacja = urllib.parse.quote(okazja['miasto'] or "Polska")

                url_kalendarz = f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={tytul}&dates={data_poczatek}/{data_koniec}&details={opis}&location={lokalizacja}"
                przycisk_kalendarz = f'<a href="{url_kalendarz}" style="background-color: #4285F4; color: white; padding: 4px 8px; text-decoration: none; border-radius: 4px; font-size: 12px; margin-top: 5px; display: inline-block;">📅 Dodaj do Kalendarza Google</a>'

            except Exception as e:
                print(f"   ⚠️ Błąd parsowania daty dla {okazja['nazwa']}: {data_lic_str} -> {e}")
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
        print(f"📧 E-mail z okazjami pomyślnie wysłany do: {', '.join(EMAIL_RECEIVERS)}")
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

def uruchom_bota():
    if not API_KEY:
        print("🛑 Zatrzymuję bota - brak klucza API.")
        return

    print("\n" + "="*60)
    print("🚀 ROZPOCZYNAM DZIAŁANIE BOTA (Nowe API Gemini)")
    print("="*60)
    
    headers = {"User-Agent": "Mozilla/5.0"}
    odwiedzone_linki = wczytaj_historie()

    print(f"🌐 Pobieram stronę główną: {URL_GLOWNE} ...")
    try:
        odpowiedz = requests.get(URL_GLOWNE, headers=headers, timeout=15)
        soup = BeautifulSoup(odpowiedz.text, "html.parser")
        print("✅ Pobrano stronę główną pomyślnie.")
    except Exception as e:
        print(f"❌ Błąd połączenia ze stroną główną: {e}")
        return

    linki_zrodlowe = soup.find_all(
        "a", string=re.compile("Zobacz zrodlo|Zobacz źródło", re.IGNORECASE)
    )

    liczba_ofert = len(linki_zrodlowe)
    print(f"📌 Znaleziono ogólnie {liczba_ofert} linków do licytacji na stronie.")
    znalezione_dzisiaj = []

    for index, link_tag in enumerate(linki_zrodlowe, start=1):
        link = link_tag["href"]

        if link in odwiedzone_linki:
            print(f"[{index}/{liczba_ofert}] ⏭️ Pomijam już wcześniej odwiedzony link: {link}")
            continue

        print(f"\n[{index}/{liczba_ofert}] 🔍 Sprawdzam nowe obwieszczenie: {link}")
        lista_pojazdow_z_ai = []

        try:
            print("   📥 Pobieram zawartość strony urzędu...")
            odp_urzad = requests.get(link, headers=headers, timeout=15)
            content_type = odp_urzad.headers.get("Content-Type", "").lower()

            if (
                "application/pdf" in content_type
                or link.lower().endswith(".pdf")
                or odp_urzad.content.startswith(b"%PDF")
            ):
                print("   📄 Wykryto bezpośredni link do PDF! Uruchamiam analizę...")
                wstepne_wyniki = przeanalizuj_pdf_z_ai(odp_urzad.content)
                lista_pojazdow_z_ai = [p for p in wstepne_wyniki if p.get("szacunkowa") and p.get("wywolawcza")]

            elif "wordprocessingml" in content_type or link.lower().endswith(".docx"):
                print("   📝 Wykryto bezpośredni link do DOCX! Wyciągam tekst...")
                tekst_docx = wyciagnij_tekst_z_docx(odp_urzad.content)
                wstepne_wyniki = zapytaj_ai_o_ceny_z_tekstu(tekst_docx)
                lista_pojazdow_z_ai = [p for p in wstepne_wyniki if p.get("szacunkowa") and p.get("wywolawcza")]

            else:
                soup_urzad = BeautifulSoup(odp_urzad.text, "html.parser")
                tekst_strony = soup_urzad.get_text(separator=" ", strip=True)
                print("   🌐 Pobrano czysty HTML strony. Szukam danych bez załączników...")
                wstepne_wyniki = zapytaj_ai_o_ceny_z_tekstu(tekst_strony)

                lista_pojazdow_z_ai = [p for p in wstepne_wyniki if p.get("szacunkowa") and p.get("wywolawcza")]

                if not lista_pojazdow_z_ai:
                    print("   🔎 Brak kompletnych kwot w treści HTML. Szukam plików do pobrania (załączników)...")
                    linki_pliki = []

                    for a_tag in soup_urzad.find_all("a", href=True):
                        href_maly = a_tag["href"].lower()
                        tekst_maly = a_tag.get_text().lower()

                        if (
                            ".pdf" in href_maly or ".pdf" in tekst_maly
                            or ".docx" in href_maly or ".docx" in tekst_maly
                            or "/pobierz/" in href_maly or "download" in href_maly
                            or "/c/document_library/" in href_maly or "get_file" in href_maly
                            or "uuid=" in href_maly or "załącznik" in tekst_maly
                            or "zawiadomienie" in tekst_maly or "obwieszczenie" in tekst_maly
                        ):
                            if a_tag not in linki_pliki:
                                linki_pliki.append(a_tag)

                    if linki_pliki:
                        print(f"   📎 Znaleziono {len(linki_pliki)} potencjalnych załączników na stronie.")
                        for nr_pliku, a_tag in enumerate(linki_pliki, start=1):
                            plik_url = urljoin(link, a_tag["href"])
                            print(f"      ➡️ Pobieram załącznik [{nr_pliku}/{len(linki_pliki)}]: {plik_url}")

                            odp_plik = requests.get(plik_url, headers=headers, timeout=15)
                            typ_pliku = odp_plik.headers.get("Content-Type", "").lower()

                            if "wordprocessingml" in typ_pliku or plik_url.lower().endswith(".docx"):
                                tekst_docx = wyciagnij_tekst_z_docx(odp_plik.content)
                                wynik_ai = zapytaj_ai_o_ceny_z_tekstu(tekst_docx)

                            elif "pdf" in typ_pliku or "octet-stream" in typ_pliku or odp_plik.content.startswith(b"%PDF"):
                                wynik_ai = przeanalizuj_pdf_z_ai(odp_plik.content)

                            else:
                                print(f"      ⚠️ Pomijam plik (to nie PDF ani DOCX): {typ_pliku}")
                                continue

                            if wynik_ai:
                                znalezione_w_pliku = [p for p in wynik_ai if p.get("szacunkowa") and p.get("wywolawcza")]
                                if znalezione_w_pliku:
                                    lista_pojazdow_z_ai.extend(znalezione_w_pliku)
                                    print(f"      🟢 BINGO! AI znalazło {len(znalezione_w_pliku)} pojazd(ów) z cenami w tym pliku!")
                                    break 
                    else:
                         print("   🤷 Brak jakichkolwiek obiecujących załączników do pobrania.")

            print(f"   📊 Podsumowanie AI dla linku: {len(lista_pojazdow_z_ai)} kompletnych pojazdów.")
            for pojazd in lista_pojazdow_z_ai:
                szacunkowa = pojazd.get("szacunkowa")
                wywolawcza = pojazd.get("wywolawcza")
                nazwa = pojazd.get("nazwa")
                miasto = pojazd.get("miasto")
                wolna_reka = pojazd.get("wolna_reka", False)
                data_licytacji = pojazd.get("data_licytacji")

                if szacunkowa and wywolawcza:
                    procent = (wywolawcza / szacunkowa) * 100
                    dystans = get_distance_to_krakow(miasto)
                    print(f"      🚗 {nazwa}: Wartość: {szacunkowa} zł, Wywoławcza: {wywolawcza} zł ({procent:.0f}%) | Wolna ręka: {wolna_reka} | Data: {data_licytacji}")

                    if procent <= 50.0 or wolna_reka:
                        print(f"      🔥 TO OKAZJA! Dodaję do dzisiejszej listy wysyłkowej!")
                        zapisz_okazje(link, szacunkowa, wywolawcza, procent, nazwa, miasto, dystans, wolna_reka, data_licytacji)

                        znalezione_dzisiaj.append({
                            "link": link,
                            "szacunkowa": szacunkowa,
                            "wywolawcza": wywolawcza,
                            "procent": f"{procent:.0f}",
                            "nazwa": nazwa,
                            "miasto": miasto,
                            "dystans": dystans,
                            "wolna_reka": wolna_reka,
                            "data_licytacji": data_licytacji
                        })
                else:
                    print(f"      🤷 Znaleziono obiekt {nazwa}, ale AI zgubiło którąś kwotę (null).")

            print(f"   ✅ Oznaczam link jako odwiedzony.")
            zapisz_do_historii(link)
            odwiedzone_linki.add(link)

            print("   ⏱️ Zabezpieczenie przed limitami: Czekam 5 sekund...")
            time.sleep(5)

        except Exception as e:
            print(f"   🛑 BŁĄD KRYTYCZNY przy przetwarzaniu tego konkretnego ogłoszenia: {e}")

    print("\n" + "="*60)
    print("🏁 ETAP POBIERANIA ZAKOŃCZONY")
    if len(znalezione_dzisiaj) > 0:
        print(f"🎉 Znalazłem dzisiaj {len(znalezione_dzisiaj)} okazji! Wysyłam raport...")
        wyslij_email(znalezione_dzisiaj)
    else:
        print("🤷‍♂️ Dzisiaj nie znaleziono żadnych nowych okazji. Wysyłam e-mail z pustym raportem...")
        wyslij_email_brak_okazji()

if __name__ == "__main__":
    uruchom_bota()
