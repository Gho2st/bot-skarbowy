# Bot Skarbowy

Codziennie sprawdza pojazdy na portalu [eLicytacje KAS](https://elicytacje.mf.gov.pl) (licytacje i sprzedaż z wolnej ręki prowadzone przez urzędy skarbowe) i wysyła maila z okazjami.

## Jak działa

1. Rano uruchamia go GitHub Actions (`.github/workflows/bot.yml`).
2. Pobiera z publicznego API portalu wszystkie aktywne ogłoszenia z kategorii **Pojazdy**. Nie potrzebuje do tego klucza.
3. Dla każdego nowego ogłoszenia czyta cenę, urzędową wartość szacunkową, markę, model, rok, przebieg, miasto i datę sprzedaży.
4. Porównuje aktualną cenę z wartością szacunkową. Jeśli licytacja trwa, za aktualną cenę bierze najwyższą ofertę.
5. Dla okazji liczy odległość od Krakowa (OpenStreetMap), zapisuje je do CSV i wysyła maila.

## Kryteria okazji

Cena wywołania wynika z ustawy: I licytacja to 75%, a II licytacja to 50% wartości szacunkowej. Wolna ręka zwykle wychodzi 10–40%.

| Stała w `main.py` | Domyślnie | Znaczenie |
|---|---|---|
| `PROG_LICYTACJA` | 50 | maksymalny % wartości dla licytacji (w praktyce: II licytacje) |
| `PROG_WOLNA_REKA` | 40 | maksymalny % wartości dla sprzedaży z wolnej ręki |
| `MIN_WARTOSC` | 3000 | pomija pojazdy tańsze niż ta kwota |

## Pliki

| Plik | Zawartość |
|---|---|
| `main.py` | cały bot |
| `historia_linkow.txt` | sprawdzone ogłoszenia; dzięki niej bot nie przysyła tego samego dwa razy |
| `okazje_licytacje.csv` | znalezione okazje (separator `;`, do otwarcia w Excelu) |

Po każdym uruchomieniu workflow commituje oba pliki z danymi do repo.

## Konfiguracja

W repo: **Settings → Secrets and variables → Actions** dodaj:

| Sekret | Opis |
|---|---|
| `EMAIL_SENDER` | adres Gmail, z którego idzie mail |
| `EMAIL_PASSWORD` | [hasło aplikacji](https://myaccount.google.com/apppasswords) Google, nie zwykłe hasło |
| `EMAIL_RECEIVER` | odbiorca; kilka adresów oddziel przecinkiem |

Harmonogram: `cron: "0 6 * * *"`, czyli 6:00 UTC (7:00 zimą, 8:00 latem w Polsce). Ręczne uruchomienie: **Actions → Codzienny Bot Skarbowy → Run workflow**.

## Uruchomienie lokalne

```bash
pip install -r requirements.txt
export EMAIL_SENDER=... EMAIL_PASSWORD=... EMAIL_RECEIVER=...
python main.py
```

Bez zmiennych e-mail bot działa normalnie, tylko nie wysyła maila.

## Uwagi

- Kupione przedmioty trzeba odebrać osobiście w urzędzie; wysyłki na razie nie ma.
- Do udziału potrzebne są konto na portalu (login.gov.pl) i zgoda na e-korespondencję w e-Urzędzie Skarbowym. Wadium musi wpłynąć najpóźniej 2 dni robocze przed licytacją.
- API portalu jest nieoficjalne (to samo, z którego korzysta strona), więc może się zmienić bez zapowiedzi.
