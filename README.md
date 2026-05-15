# 🚗 Bot Skarbowy – Automatyczny Łowca Okazji Licytacyjnych

**Inteligentny bot monitorujący aukcje Skarbu Państwa** w poszukiwaniu samochodów i pojazdów w atrakcyjnych cenach.

Bot codziennie skanuje portal `skarbowe-licytacje.com`, analizuje za pomocą **Google Gemini AI** nieustrukturyzowane dane (HTML, PDF, DOCX), filtruje tylko najlepsze okazje i wysyła powiadomienia e-mail.

---

## 🎯 O projekcie

Bot Skarbowy automatyzuje proces polowania na okazje na licytacjach komorniczych i Skarbu Państwa. Zamiast ręcznego przeglądania setek ogłoszeń dziennie – dostajesz tylko te, gdzie cena wywoławcza jest **≤ 50% wartości rynkowej** lub jest to sprzedaż z wolnej ręki.

Projekt łączy **web scraping**, **AI parsing**, **geolokalizację** oraz **automatyczne powiadomienia** – idealne rozwiązanie pokazujące praktyczne zastosowanie AI w realnym biznesie / inwestycjach.

**Aktualny status:** Działa produkcyjnie (uruchamiany automatycznie przez GitHub Actions).

---

## ✨ Główne funkcjonalności

- **Zaawansowane parsowanie AI** – Google Gemini wyciąga markę, model, cenę wywoławczą, szacowaną wartość i lokalizację z PDF-ów i stron HTML
- **Inteligentne filtrowanie** – tylko okazje ≤ 50% wartości lub "sprzedaż z wolnej ręki"
- **Kalkulator odległości** – automatyczne obliczanie dystansu do Krakowa (lub innej lokalizacji)
- **Historia i deduplikacja** – zapamiętuje sprawdzone linki (`historia_linkow.txt`), oszczędza tokeny API
- **Powiadomienia e-mail** – bogaty HTML z podsumowaniem + informacja "brak nowych okazji dzisiaj"
- **Eksport do CSV** – wszystkie znalezione okazje zapisywane do `okazje_licytacje.csv`
- **CI/CD** – automatyczne uruchamianie codziennie przez GitHub Actions

---

## 🛠 Technologie

| Warstwa               | Technologia                          |
|-----------------------|--------------------------------------|
| **Język**             | Python 3                            |
| **AI / LLM**          | Google Gemini (gemini-1.5-flash)    |
| **Scraping**          | requests + BeautifulSoup4           |
| **Dokumenty**         | Obsługa PDF i DOCX                  |
| **Geolokalizacja**    | Google Maps API / lokalna logika    |
| **Automatyzacja**     | GitHub Actions (daily workflow)     |
| **Powiadomienia**     | SMTP + HTML e-mail                  |
| **Inne**              | pandas (CSV), dotenv                |

---

## 📈 Potencjał projektu (dla rekruterów)

Ten projekt doskonale pokazuje, że potrafię:

- Tworzyć **praktyczne aplikacje AI** z realnym business value
- Pracować z **nieustrukturyzowanymi danymi** (LLM parsing PDF/HTML)
- Budować **odporne scrapery** z mechanizmami deduplikacji i oszczędzania kosztów API
- Implementować **end-to-end automatyzację** (scraping → AI → decyzja → powiadomienie)
- Używać GitHub Actions do CI/CD i codziennego uruchamiania
- Łączyć różne technologie (web, AI, e-mail, geolokalizacja)

To nie jest proste "hello world" – to działający tool, który realnie może generować oszczędności lub zyski przy inwestycjach w pojazdy z licytacji.

---

## 🚀 Jak uruchomić lokalnie

```bash
# 1. Sklonuj repozytorium
git clone https://github.com/Gho2st/bot-skarbowy.git
cd bot-skarbowy

# 2. Zainstaluj zależności
pip install -r requirements.txt

# 3. Skonfiguruj zmienne środowiskowe
cp .env.example .env
# uzupełnij:
# GEMINI_API_KEY=...
# EMAIL_SENDER=...
# EMAIL_PASSWORD=...
# EMAIL_RECEIVER=...

# 4. Uruchom bota
python main.py