# Mittauksista kuvioiksi ja taulukoiksi

Tämä tiedosto suunnittelee etukäteen, miten mittausdata päätyy opinnäytetyön kuvioiksi ja
taulukoiksi Wordissa. Suunnitelma kannattaa lyödä lukkoon ennen varsinaista mittaussarjaa,
koska se määrää, mitä jokaisen ajon on tallennettava: jälkikäteen puuttuvaa tietoa ei saa.

Julkisesta versiosta on jätetty pois alkuperäisen suunnitelman kohdat 4–7 (suunnitellut
kuviot ja taulukot, Word-käytännöt ja avoimet asiat). Jäljellä ovat ajojen tallentamat tiedot
sekä tilastollinen esitystapa ja toistojen määrä, joihin skriptit ja ohjeet viittaavat.
Kuten [TESTIPENKKI.md](TESTIPENKKI.md), `[TÄYDENNÄ]` merkitsee tietoa, joka on haettava itse.

---

## 1. Ketju datasta Wordiin

```
measure.py ──> data/run_*.json            yksi tietue per ajo (kohta 2)
               data/run_*_latencies.csv   jokaisen pyynnön vasteaika
                     │
aggregate.py ────────┤  tarkistukset: olosuhteet samat, ajot vuoroteltu
                     │  yhteenveto: mediaani (min–max) per kokoonpano
                     │
[raporttiskripti] ───┴──> figures/*.svg, *.png   kuviot
                          tables/*.csv           valmiiksi pyöristetyt taulukot
                                │
                             Word
```

Periaate: **yhtään lukua ei lasketa käsin.** Jokainen kuvio ja taulukko syntyy skriptillä
`data/`-hakemistosta. Kun ajo lisätään tai uusitaan, skripti ajetaan uudelleen eikä mitään
piirretä uudestaan käsin. Silloin jokainen työssä esiintyvä luku on jäljitettävissä
ajotietueeseen, ja skripti kuuluu liitteeseen 2 muiden mittausskriptien kanssa.

---

## 2. Mitä jokainen ajo tallentaa

Kaikki alla oleva tallentuu automaattisesti tiedostoon `run_*.json`.

| Tieto | Kenttä | Käyttö työssä |
|---|---|---|
| Koodiversio | `commit`, `commit_dirty`, `modified_files` | Toistettavuus (4.3); `measure.py` kieltäytyy kirjoittamasta `data/`-hakemistoon, jos seurattuja tiedostoja on muutettu |
| Mitattu build ja kytkin | `service_container.image_id`, `.catalina_opts` | Osoittaa, että kumpikin kokoonpano ajoi saman imagen ja eri kytkimen (4.3) |
| Laitteet | `host`, `sut_host` | Taulukko T1 |
| Kuormaparametrit | `config`, `load_profile` | Taulukko T2 |
| Tehoasetukset | `sut_state.*.power`: turbo, governor, energiaprofiili, verkkovirta | Olosuhteiden vakioisuus (4.3, 4.5) |
| Lämpötila | `sut_state.window_start/window_end.temperatures_c` | Kuvio K8; lämpöajelehtimisen arviointi (4.5) |
| Akku | `sut_state.*.battery` | Olosuhteiden vakioisuus (4.5) |
| Läpäisy, vasteajat | `results.throughput_rps`, `latency_ms` | T3, K4 |
| Välimuisti | `results.cache` | T3, K5; mekanismin todiste |
| Energia | `results.energy_joules`, `package_watts`, `idle_package_watts`, `joules_per_request`, `joules_per_request_above_idle` | T3, T4, K2, K3 |
| Vastauksen koko | `results.response_bytes`, `network_mbit_s` | K7; kuvamuodon vaikutuksen mekanismi |
| Aikataulussa pysyminen | `results.send_lag_ms` | Todiste, että kiinteä taso toteutui (4.3) |

`aggregate.py` vertaa näitä ajojen välillä ja varoittaa, jos jokin, jonka pitää olla sama,
eroaa: build, commit, turbo, governor, pyyntötaso, ikkunan pituus, alpha tai kuvakoko. Se
varoittaa myös akkuvirrasta, latautuvasta akusta, muutetuista tiedostoista ja puuttuvasta
build-tiedosta. **Varoitukseton yhteenveto on edellytys sille, että tulos kelpaa työhön.**

### Sessioloki (käsin)

Kaikkea ei voi lukea koneelta. Kirjaa jokaisesta mittaussessiosta yksi rivi tiedostoon
`data/SESSIOLOKI.md`:

| Päivä ja kellonaika | Huonelämpö alussa / lopussa | Lepoteho (`sut_session.sh`) | Sarja (`run_ab.sh`-parametrit) | Poikkeamat |
|---|---|---|---|---|
| esim. 2.10.2026 18.05–19.10 | 21 / 22 °C | 3,1 W | `-Rate 600 -Processes 3 -Idle 20` | pari 3 uusittu: linkki katkesi |

Poikkeamasarake on luvun 4.5 raaka-ainetta. Se on ainoa paikka, johon jää tieto asioista,
joita skriptit eivät näe.

---

## 3. Tilastollinen esitystapa ja toistojen määrä

Asetelma on **parittainen**: `run_ab.sh` ajaa kokoonpanot pareittain vuorotellen
(pois–päällä, päällä–pois, …). Parin jäsenet ajetaan peräkkäin samoissa olosuhteissa,
joten parin sisäinen ero on vertailun luotettavin luku. Hitaat muutokset, kuten lämpenevä
kone, vaikuttavat parin kumpaankin jäseneen lähes yhtä paljon.

### Toistojen määrä: 10 paria

Jokainen sarja ajetaan **10 parina** (`run_ab.sh`:n oletus `--repeat 10`), eli 10 ajoa
kokoonpanoa kohden. Poikkeus on uusilla JVM-instansseilla ajettava sarja, jossa on 20 paria
(alla). Perusteet:

- **Mittaussuunnitelman tavoite** (luku 4.1) on vähintään 10 toistoa kokoonpanoa kohden.
  GSMM:n viittaama 30 toistoa (Guldner ym. 2024, luku 5.3, Kern ym.) [TARKISTA] on
  oppikirjan lähtökohta, josta poiketaan perustellusti ja avoimesti.
- **Sarjat samaan sessioon.** Yksi ajo kestää noin 4 min (tauko 60 s + lepoteho 20 s +
  esilämmitys 30 s + ikkuna 120 s), joten 10 paria vie noin 80 min. Pääsarja ja
  robustisuussarja (alpha 0,6) mahtuvat samaan sessioon, kuten mittaussuunnitelman
  tarkistuslista (kohta 11) suosittaa. 30 parilla yksi sarja veisi noin 4 h, ja sarjat
  jakautuisivat eri päiville.
- **Testi ei jää toistojen määrästä kiinni.** Wilcoxonin testin pienin kaksisuuntainen
  p-arvo on 10 parilla 2/2¹⁰ ≈ 0,002 (5 parilla 0,0625, joka ei voi alittaa rajaa 0,05).

**Hajontasääntö päätetään ennen sarjaa kohinakokeella.** Mittaussuunnitelma ohjeistaa
lisäämään toistoja, jos variaatiokerroin (CV = keskihajonta / keskiarvo) ylittää
10–15 %. Jos lisätoistoista päätettäisiin vasta A/B-sarjan jälkeen, päätös riippuisi
datasta ja heikentäisi johtopäätöstä. Siksi sääntöä sovelletaan kohinakokeeseen
([SETUP.md](SETUP.md), "A noise check"), jossa sama kokoonpano ajetaan viidesti samoilla
parametreilla kuin varsinainen sarja:

- CV(J/pyyntö) ≤ 15 % → 10 paria.
- CV > 15 % → olosuhteet korjataan (SETUP.md, Part D) ja kohinakoe uusitaan. Jos CV jää
  yhä yli 15 %:n, ajetaan 15 paria ja suuri hajonta raportoidaan rajoituksena.

`aggregate.py` tulostaa CV:n kokoonpanoittain ja varoittaa rajan ylityksestä. Sääntö
koskee kokonaisenergiaa per pyyntö. Lepotason ylittävän osuuden CV on luonnostaan
suurempi, koska se on kahden mitatun luvun erotus; se raportoidaan, mutta se ei ohjaa
toistojen määrää. Varsinaisen sarjan CV raportoidaan taulukossa T3, mutta sarjan jälkeen se ei
enää muuta toistojen määrää.

### Uusilla JVM-instansseilla ajettava sarja: 20 paria

Välimuistin pääsarja toistetaan 3. sessiossa niin, että jokainen pari ajetaan omilla, uusilla
JVM-instansseillaan ([SETUP.md](SETUP.md), D6; perustelu [TESTIPENKKI.md](TESTIPENKKI.md),
kohta 5). Parin sisäinen ero sisältää silloin välimuistin vaikutuksen lisäksi kahden
instanssin välisen eron, joka on moninkertainen kohinakokeen mittaamaan ajojen väliseen
kohinaan nähden. Kohinakokeen CV ei siksi kuvaa tämän sarjan hajontaa, eikä sääntö
"CV ≤ 15 % → 10 paria" sovellu siihen. Kohinakoe ajetaan silti, koska se tarkistaa session
olosuhteet.

Parien määrä päätettiin voima-analyysillä 6.10.2026, ennen kuin sarjasta oli yhtään ajoa
(`scripts/pair_power.py`). Lähtöarvot:

- Vaikutus 1,6 %: 1. session sarja, jossa kumpikin kokoonpano oli yksi instanssi. Lisäksi
  1,0 %, koska pyyhkäisy jättää pienemmänkin vaikutuksen mahdolliseksi.
- Parin eron keskihajonta 2,0–2,75 %: pyyhkäisyssä saman kokoonpanon kaksi instanssia erosivat
  1,5 % ja 3,4 %, ja ajojen välinen kohina on noin 0,4 % ajoa kohden. Arvio nojaa kahteen
  vertailuun ja on siksi karkea.

| Parin eron keskihajonta | Vaikutus | 10 paria | 15 paria | 20 paria |
|---|---|---|---|---|
| 2,0 % | 1,6 % | 60 % | 79 % | 92 % |
| 2,75 % | 1,6 % | 37 % | 54 % | 67 % |
| 2,0 % | 1,0 % | 29 % | 42 % | 54 % |
| 2,75 % | 1,0 % | 17 % | 24 % | 32 % |

Luvut ovat testin voima: todennäköisyys, että tarkka Wilcoxonin testi saavuttaa p < 0,05, kun
vaikutus on todellinen. 10 parilla 1,6 %:n säästö jäisi havaitsematta noin joka toisella
kerralla, ja erojen keskiarvon 95 %:n luottamusvälin puolileveys olisi 1,4–2,0 % eli
vaikutuksen suuruinen. 20 parilla puolileveys on 0,9–1,3 %.

Päätös: **20 paria yhdessä sessiossa** (noin 5 h 25 min ajoja). Päätöksen pitävät datasta
riippumattomana nämä säännöt:

- Parien määrä on kiinteä. Pareja ei lisätä tuloksen nähtyään, vaikka p jäisi yli 0,05:n.
  Silloin raportoidaan luottamusväli ja todetaan, ettei vaikutusta voitu erottaa instanssien
  välisestä vaihtelusta.
- Pari uusitaan vain syystä, joka ei riipu energialuvuista: sarja keskeytyy, tai
  `measure.py` varoittaa, ettei ajo pysynyt pyydetyssä tasossa (SETUP.md, D4). Puuttuva pari
  ajetaan samalla siemenellä ja järjestyksellä uusilla instansseilla (`--skip-pairs`,
  SETUP.md, D6), ja uusinta kirjataan sessiolokiin.
- 1. session sarja raportoidaan rinnalla yhden instanssiparin vertailuna, ei tämän sarjan
  osana.

### Esitystapa

- **Pääluku:** parittainen ero `päällä − pois` jokaisesta parista ja sen suhteellinen
  muutos (%). Raportoidaan erojen mediaani ja vaihteluväli (min–max) sekä *n*.
- **Merkitsevyys: Wilcoxonin merkittyjen sijalukujen testi**, tarkka ja kaksisuuntainen,
  parittaisista eroista (J/pyyntö lepotason yli ja kokonais-J/pyyntö). Testi on valittu
  etukäteen, koska se ei oleta erojen normaalijakaumaa. Mittaussuunnitelma ehdottaa ensin
  normaalisuustestiä (Shapiro–Wilk) ja sen perusteella t-testiä tai Wilcoxonia, mutta
  10 havainnolla normaalisuustestillä on vähän voimaa, ja testin valinta datan perusteella
  on itsessään datasta riippuva päätös. Parittainen t-testi ja erojen keskiarvon 95 %:n
  luottamusväli raportoidaan rinnalla herkkyystarkasteluna (vrt. Georges ym. 2007).
- **Kokoonpanokohtaiset luvut:** mediaani (min–max) ja CV. Mediaani kestää yksittäisen
  poikkeavan ajon, jota keskiarvo ei kestä. CV ilmaisee hajonnan suhteellisena, joten sitä
  voi verrata hajontasääntöön.
- **Jokainen toisto näkyviin kuvioissa.** Kymmenellä pisteellä pylväs ja virhejana tai
  laatikkokuvio piilottavat enemmän kuin näyttävät.

### Poikkeamat mittaussuunnitelmasta

Mittaussuunnitelmaa noudatetaan muilta osin. Nämä poikkeamat kerrotaan luvussa 4.3 tai 4.5
perusteluineen:

| Mittaussuunnitelma | Tässä työssä | Peruste |
|---|---|---|
| Mitattu ikkuna 10 min | 120 s | Monta lyhyttä, vuoroteltua paria hallitsee lämpöajelehtimista paremmin kuin harvat pitkät ajot. 600 req/s:n tasolla 120 s on noin 72 000 pyyntöä. [TARKISTA: päivitä pyyntömäärä, kun taso on valittu (SETUP.md, D2); 1 000 req/s:lla noin 120 000] |
| Lepoteho 5 min kerran ennen sarjaa | 20 s ennen jokaista ajoa | Lepoteho vaihteli samana päivänä 2,48–4,17 W (TESTIPENKKI.md, kohta 3) |
| Esilämmitys 1–2 min | 30 s | [TARKISTA: riittääkö 30 s JIT-käännökselle ja välimuistin täyttymiselle; koeajo ennen sarjaa] |
| Tauko 2–3 min | 60 s | Kuvio K8 näyttää, palautuuko lämpötila ajojen välillä. [TARKISTA K8:sta ensimmäisen sarjan jälkeen] |
| Normaalisuustesti, sitten t-testi tai Wilcoxon | Wilcoxon etukäteen | Yllä, kohta "Esitystapa" |
| Keskiarvo ja keskihajonta; pylväs ja virhejana sekä laatikkokuvio | Mediaani (min–max) ja CV; kaikki toistot pisteinä | Yllä |
| Lisätoistoja, jos CV > 10–15 % | Sääntö sovelletaan kohinakokeeseen ennen sarjaa | Toistojen määrä ei saa riippua vertailun tuloksesta |
