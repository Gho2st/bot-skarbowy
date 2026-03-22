import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import json
import re
import os
import tempfile
import csv
import time
from datetime import datetime
from urllib.parse import urljoin

# Biblioteki do wysyłania maili
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- BEZPIECZNE POBIERANIE KLUCZY I HASEŁ Z GITHUB SECRETS ---
API_KEY = os.environ.get("GEMINI_API_KEY")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

if API_KEY:
    genai.configure(api_key=API_KEY)
else:
    print("⚠️ Brak klucza API Gemini. Ustaw zmienną środowiskową GEMINI_API_KEY.")

# Używamy modelu Flash Lite (wysokie darmowe limity)
model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")
URL_GLOWNE = "https://skarbowe-licytacje.com/?q=&region=&category=pojazdy&city=&source="
PLIK_WYNIKOW = "okazje_licytacje.csv"
PLIK_HISTORII = "historia_linkow.txt"


def wczytaj_historie():
    odwiedzone = set()
    if os.path.isfile(PLIK_HISTORII):
        with open(PLIK_HISTORII, mode="r", encoding="utf-8") as plik:
            for linia in plik:
                odwiedzone.add(linia.strip())
    return odwiedzone


def zapisz_do_historii(link):
    with open(PLIK_HISTORII, mode="a", encoding="utf-8") as plik:
        plik.write(link + "\n")


def zapisz_okazje(link, szacunkowa, wywolawcza, procent):
    plik_istnieje = os.path.isfile(PLIK_WYNIKOW)
    with open(PLIK_WYNIKOW, mode="a", newline="", encoding="utf-8-sig") as plik:
        writer = csv.writer(plik, delimiter=";")
        if not plik_istnieje:
            writer.writerow(
                [
                    "Data znalezienia",
                    "Link do ogłoszenia",
                    "Wartość szacunkowa [zł]",
                    "Cena wywoławcza [zł]",
                    "Procent wartości [%]",
                ]
            )
        teraz = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([teraz, link, szacunkowa, wywolawcza, f"{procent:.2f}"])


def zapytaj_ai_o_ceny_z_tekstu(tekst):
    if not tekst or len(tekst) < 50:
        return None, None

    prompt = f"""
    Przeanalizuj poniższy tekst obwieszczenia urzędowego.
    Znajdź dwie kwoty:
    1. Wartość szacunkowa
    2. Cena wywoławcza

    WAŻNE: Jeśli jest to "oferta sprzedaży po cenie oszacowania" lub "sprzedaż z wolnej ręki" 
    i podana jest tylko JEDNA kwota (cena sprzedaży/oszacowania), wstaw ją jako OBIEDWIE wartości.

    Zwróć wynik TYLKO jako surowy JSON, np:
    {{"szacunkowa": 110000.00, "wywolawcza": 82500.00}}
    Jeśli którejś brakuje, wstaw null. Używaj kropki dla ułamków.

    Tekst:
    {tekst[:6000]} 
    """
    try:
        response = model.generate_content(prompt)
        czysty_json = response.text.replace("```json", "").replace("```", "").strip()
        dane = json.loads(czysty_json)
        return dane.get("szacunkowa"), dane.get("wywolawcza")
    except Exception as e:
        if "429" in str(e):
            print(
                "      ⏳ Google prosi o oddech (Limit). Czekam 45 sekund przed kolejną próbą..."
            )
            time.sleep(45)
        else:
            print(f"      🤖 Błąd AI (Tekst): {e}")
        return None, None


def przeanalizuj_pdf_z_ai(pdf_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        temp_pdf.write(pdf_bytes)
        temp_pdf_path = temp_pdf.name

    try:
        print("      📤 Przesyłam dokument PDF bezpośrednio do analizy wizualnej AI...")
        wgrany_plik = genai.upload_file(path=temp_pdf_path)

        prompt = """
        Jesteś ekspertem analizującym oficjalne dokumenty urzędowe i licytacyjne.
        Przeczytaj dokładnie ten dokument i znajdź w nim dwie konkretne kwoty:
        1. Wartość szacunkowa (lub cena oszacowania)
        2. Cena wywoławcza (lub cena wywołania)
        
        WAŻNE: Jeśli dokument to "oferta sprzedaży po cenie oszacowania" lub "sprzedaż z wolnej ręki" 
        i dokument podaje tylko jedną kwotę (za którą przedmiot zostanie sprzedany), przypisz tę kwotę do OBU pól.
        
        Zwróć wynik TYLKO jako poprawny JSON, np:
        {"szacunkowa": 110000.00, "wywolawcza": 82500.00}
        Jeśli brakuje kwot, wstaw null.
        """
        response = model.generate_content([wgrany_plik, prompt])
        genai.delete_file(wgrany_plik.name)

        czysty_json = response.text.replace("```json", "").replace("```", "").strip()
        dane = json.loads(czysty_json)
        return dane.get("szacunkowa"), dane.get("wywolawcza")
    except Exception as e:
        if "429" in str(e):
            print("      ⏳ Google prosi o oddech (Limit w PDF). Czekam 45 sekund...")
            time.sleep(45)
        else:
            print(f"      🤖 Błąd AI (PDF): {e}")
        return None, None
    finally:
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)


def wyslij_email(lista_znalezionych):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print(
            "⚠️ Brak danych logowania do e-maila w zmiennych środowiskowych. Pomijam wysyłanie."
        )
        return

    temat = f"🔥 Bot znalazł {len(lista_znalezionych)} nowe okazje skarbowe!"

    tresc = "<h3>Dzisiejsze okazje poniżej 50% wartości:</h3><ul>"
    for okazja in lista_znalezionych:
        tresc += f"""
        <li style="margin-bottom: 10px;">
            <b>Oszacowano:</b> {okazja["szacunkowa"]} zł | <b>Wywoławcza:</b> {okazja["wywolawcza"]} zł<br>
            <b>Opłacalność:</b> <span style="color: green; font-weight: bold;">{okazja["procent"]}% wartości</span><br>
            🔗 <a href="{okazja["link"]}">Przejdź do ogłoszenia</a>
        </li>
        """
    tresc += "</ul><br><small>Wiadomość wygenerowana automatycznie przez Twojego bota 🤖</small>"

    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = temat
    msg.attach(MIMEText(tresc, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("📧 E-mail z powiadomieniem został pomyślnie wysłany!")
    except Exception as e:
        print(f"❌ Błąd podczas wysyłania e-maila: {e}")


def uruchom_bota():
    if not API_KEY:
        print("Zatrzymuję bota - brak klucza API.")
        return

    print("🚀 Rozpoczynam pobieranie ofert (Wersja GitHub Actions + E-mail)...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    odwiedzone_linki = wczytaj_historie()
    print(f"🧠 Bot pamięta już {len(odwiedzone_linki)} sprawdzonych wcześniej ofert.")

    try:
        odpowiedz = requests.get(URL_GLOWNE, headers=headers)
        soup = BeautifulSoup(odpowiedz.text, "html.parser")
    except Exception as e:
        print(f"❌ Błąd połączenia ze stroną główną: {e}")
        return

    linki_zrodlowe = soup.find_all(
        "a", string=re.compile("Zobacz zrodlo|Zobacz źródło", re.IGNORECASE)
    )
    liczba_ofert = len(linki_zrodlowe)
    print(f"✅ Znaleziono {liczba_ofert} ofert w agregatorze.\n{'=' * 60}")

    znalezione_dzisiaj = []

    for index, link_tag in enumerate(linki_zrodlowe, start=1):
        link = link_tag["href"]

        if link in odwiedzone_linki:
            print(f"[{index}/{liczba_ofert}] ⏭️ Link sprawdzony w przeszłości. Pomijam!")
            continue

        print(f"[{index}/{liczba_ofert}] ⏳ Wchodzę na NOWĄ stronę: {link}")

        try:
            odp_urzad = requests.get(link, headers=headers, timeout=10)

            content_type = odp_urzad.headers.get("Content-Type", "").lower()
            if "application/pdf" in content_type or link.lower().endswith(".pdf"):
                print("  📄 Wykryto bezpośredni link do PDF! Analizuję od razu...")
                szacunkowa, wywolawcza = przeanalizuj_pdf_z_ai(odp_urzad.content)
            else:
                soup_urzad = BeautifulSoup(odp_urzad.text, "html.parser")
                tekst_strony = soup_urzad.get_text(separator=" ", strip=True)

                print("  🤖 Pytam AI o ceny z tekstu HTML...")
                szacunkowa, wywolawcza = zapytaj_ai_o_ceny_z_tekstu(tekst_strony)

                if szacunkowa and wywolawcza:
                    print(
                        f"  🟢 AI znalazło ceny w HTML! (Szacunkowa: {szacunkowa}, Wywoławcza: {wywolawcza})"
                    )
                else:
                    print(
                        f"  🔍 Brak kompletu w HTML. Szukam plików PDF na podstronie..."
                    )

                    linki_pdf = []
                    for a_tag in soup_urzad.find_all("a", href=True):
                        href_maly = a_tag["href"].lower()
                        tekst_maly = a_tag.get_text().lower()
                        if (
                            ".pdf" in href_maly
                            or ".pdf" in tekst_maly
                            or "/pobierz/" in href_maly
                            or "download" in href_maly
                            or "document_library/get_file" in href_maly
                        ):
                            if a_tag not in linki_pdf:
                                linki_pdf.append(a_tag)

                    if not linki_pdf:
                        print("  🚫 Brak plików PDF na stronie.")
                    else:
                        for nr_pdf, pdf_tag in enumerate(linki_pdf, start=1):
                            pdf_url = urljoin(link, pdf_tag["href"])
                            print(
                                f"    ➡️ Pobieram plik PDF [{nr_pdf}/{len(linki_pdf)}]: {pdf_url}"
                            )

                            odp_pdf = requests.get(pdf_url, headers=headers, timeout=15)
                            szac_pdf, wyw_pdf = przeanalizuj_pdf_z_ai(odp_pdf.content)

                            if szac_pdf:
                                szacunkowa = szac_pdf
                            if wyw_pdf:
                                wywolawcza = wyw_pdf

                            if szacunkowa and wywolawcza:
                                print(
                                    f"    🟢 AI znalazło ceny analizując plik! (Szacunkowa: {szacunkowa}, Wywoławcza: {wywolawcza})"
                                )
                                break
                            else:
                                print(
                                    f"    ⚠️ AI przeanalizowało PDF, ale nie znalazło w nim kompletu cen."
                                )

            if szacunkowa and wywolawcza:
                procent = (wywolawcza / szacunkowa) * 100
                print(f"  📊 Wyliczony procent: {procent:.0f}%")

                if procent <= 50.0:
                    print(
                        f"  🔥 OKAZJA! Oszacowano: {szacunkowa} zł | Wywoławcza: {wywolawcza} zł"
                    )
                    zapisz_okazje(link, szacunkowa, wywolawcza, procent)

                    # Dodajemy do powiadomienia mailowego
                    znalezione_dzisiaj.append(
                        {
                            "link": link,
                            "szacunkowa": szacunkowa,
                            "wywolawcza": wywolawcza,
                            "procent": f"{procent:.0f}",
                        }
                    )
                else:
                    print(
                        f"  ❌ To nie okazja (powyżej 50%). Nie dodaję do powiadomień."
                    )
            else:
                print("  🤷‍♂️ Brak danych lub problem z weryfikacją.")

            zapisz_do_historii(link)
            odwiedzone_linki.add(link)

            print("  ⏱️ Czekam 5 sekund, żeby nie przeciążyć darmowego API...")
            time.sleep(5)
            print("-" * 60)

        except requests.exceptions.Timeout:
            print("  🛑 BŁĄD: Timeout (Strona nie odpowiada). Pomijam, spróbuję jutro.")
            print("-" * 60)
        except Exception as e:
            print(f"  🛑 BŁĄD: {e}")
            print("-" * 60)

    # Po przeanalizowaniu wszystkich linków sprawdzamy, czy trzeba wysłać maila
    if len(znalezione_dzisiaj) > 0:
        wyslij_email(znalezione_dzisiaj)
    else:
        print(
            "🤷‍♂️ Dzisiaj nie znaleziono żadnych nowych okazji (poniżej 50%). E-mail nie zostanie wysłany."
        )


if __name__ == "__main__":
    try:
        uruchom_bota()
    except KeyboardInterrupt:
        print(
            "\n🛑 Zatrzymano bota ręcznie (Ctrl+C). Pobrane dane i historia są bezpieczne. Do zobaczenia!"
        )
