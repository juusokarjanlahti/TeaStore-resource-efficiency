# Testipenkki: pystytys, validointi ja havainnot

Tämä tiedosto dokumentoi mittausympäristön pystytyksen ja validoinnin 28.9.2026 niin,
että lukujen 4.3 (mittausasetelma), 4.5 (rajoitukset) ja 4.6 (tekoälyn käyttö) tekstin
voi kirjoittaa tarkkojen tietojen pohjalta. Asennusohje on [SETUP.md](SETUP.md) ja
menetelmän perustelut [MEASUREMENT_GUIDE.md](MEASUREMENT_GUIDE.md); tämä tiedosto kertoo,
mitä tehtiin, mitä löytyi ja mitä luvut tarkoittavat.

Tämä on raaka-ainetta, ei opinnäytetyön
tekstiä. `[TÄYDENNÄ]` merkitsee tietoa, joka puuttuu ja on haettava itse.

**Tärkein varaus heti alkuun:** kaikki tämän päivän luvut mitattiin läppärin ollessa
akkuvirralla. Ne kelpaavat vain osoittamaan, että mittausketju toimii. Yksikään niistä ei
ole tulos.

---

## 1. Testipenkin kuvaus

| | Mitattava järjestelmä (SUT) | Kuormageneraattori (mittaussessiot 1.10.2026 alkaen) |
|---|---|---|
| Laite | Lenovo ideapad 320-15IKB (tyyppi 81BG) | Lenovo IdeaPad 5 Pro 16ARH7 (tyyppi 82SN) |
| Suoritin | Intel Core i5-8250U, 4 ydintä / 8 säiettä, 1,6 GHz (turbo pois) | AMD Ryzen 7 6800HS Creator Edition, 8 ydintä / 16 säiettä, enintään 4,8 GHz |
| Muisti | 20 GB DDR4-2133 (4 GB + 16 GB, lisätty 16 GB:n kampa ennen mittauksia), käyttöjärjestelmälle näkyvissä 19,3 GiB, swap 3,6 GiB | 16 GB LPDDR5-6400 (4 × 4 GB, juotettu emolevyyn), josta 2 GiB on varattu integroidulle näytönohjaimelle; käyttöjärjestelmälle näkyvissä 13,3 GiB |
| Käyttöjärjestelmä | Ubuntu 24.04.5 LTS, ydin 7.0.0-34-generic | Ubuntu 24.04.5 LTS, ydin 7.0.0-34-generic |
| Ohjelmistot | Docker 29.1.3 (client ja server), Docker Compose 2.40.3, OpenJDK 11.0.32 (build), Python 3.12.3 (`rapl_server.py`, `sut_session.sh`), Git 2.43.0 | Python 3.12.3 (vain standardikirjasto), Git 2.43.0, curl 8.5.0, OpenSSH 9.6p1; mittausskriptien bash-versiot (`run_ab.sh`, `run_sweep.sh`, `check_setup.sh`) |
| Verkkosovitin | Realtek RTL8111/8168/8211/8411 PCIe Gigabit Ethernet (rev 10, `10ec:8168`), ajuri r8169; 1000 Mb/s, full duplex | Realtek RTL8153 USB Gigabit Ethernet -sovitin (`0bda:8153`), ajuri r8152; 1000 Mb/s, full duplex |
| Energiamittaus | RAPL (`/sys/class/powercap`), vyöhykkeet package-0, core, uncore, dram, psys | — |

**Kuormageneraattori vaihtui validoinnin jälkeen.** Kohtien 2 ja 3 validointi 28.9.2026
ajettiin pöytäkoneelta (AMD Ryzen 5 2600, 6 ydintä / 12 säiettä, 16 GB, Windows 11 Pro
10.0.26200, Python 3.12.8, Realtek PCIe GbE) PowerShell-skripteillä. Kaikki mittaussessiot
ajetaan yllä olevalta läppäriltä bash-skripteillä ([SETUP.md](SETUP.md), osa B). Kohdan 3
generaattorin raja, noin 1 000 pyyntöä/s yhtä Python-prosessia kohden, mitattiin
pöytäkoneella. Läppärin riittävyyden osoittaa 1. session tasoporras: 3 prosessilla se piti
myös 1 200 pyyntöä/s:n tason aikataulussa (toteutui 1 199 pyyntöä/s, lähetysviive p95
0,14 ms), eli valittu 1 000 pyyntöä/s jää selvästi sen rajan alle.

**Verkko.** Koneet on kytketty suoraan yhdellä Ethernet-kaapelilla, linkkinopeus 1 Gbit/s.
Staattiset osoitteet `10.10.10.1/24` (SUT) ja `10.10.10.2/24` (generaattori), ei
yhdyskäytävää eikä DNS:ää. Mittausliikenne ei jaa linkkiä muun liikenteen kanssa: SUT:n Wi-Fi
on sessioiden ajan pois päältä (SETUP.md, D1), ja generaattorin internetyhteys kulkee sen
Wi-Fin kautta (validoinnissa pöytäkoneen internetyhteys kulki puhelimen USB-jaon kautta).

**Palvelupino SUT:lla** ([docker-compose.measure.yaml](docker-compose.measure.yaml)):
tietokanta, rekisteri, persistence ja kolme kuvapalvelun instanssia samasta imagesta.
Ne eroavat vain JVM-parametreiltaan: portti 8081 = välimuisti päällä, 8082 = välimuisti
pois päältä (PNG, molempien vertailujen perustaso) ja 8083 = tuotekuvat JPEG-muodossa,
välimuisti pois päältä (`-Dimage.renderedCache.enabled`, `-Dimage.productFormat=jpeg`).
WebUI, auth ja recommender jätetään pois, koska ne kuluttaisivat energiaa, joka ei liity
vertailuun. Imaget käännetään tästä haarasta
(Tomcat 10.0.27, Temurin JDK 11 konteissa).

**Energian luku verkon yli.** `rapl_server.py` ajetaan SUT:lla root-oikeuksin ja sidotaan
vain kaapelilinkin osoitteeseen. `measure.py` lukee laskurit HTTP:llä juuri ennen ja
jälkeen mitatun ikkunan. Jos luku epäonnistuu, ajo keskeytyy. Se ei koskaan korvaa
puuttuvaa mittausta keinotekoisella luvulla (ks. kohta 4, havainto 1).

**Ohjelmistoversio.** Validointi ajettiin commitilla `0f64467d`. Jokainen ajotietue
tallentaa commitin, SUT:n tunnisteet ja kuormaprofiilin.

---

## 2. Mitä validoitiin ja miten

Validointi eteni ketjuna niin, että jokainen vaihe nojaa edelliseen, ja virhe
paikantuu siihen lenkkiin, jossa se syntyi.

1. **Käännös ja yksikkötestit (pöytäkone).** Haara käännettiin JDK 11:llä erillisessä
   Maven-kontissa. Checkstyle meni läpi, ja kuvapalvelun 34 yksikkötestiä menivät läpi,
   välimuistitestit mukaan lukien.
2. **Mittausputken kuiva-ajo.** `measure.py` ajettiin stub-palvelua vastaan (sama
   REST-sopimus kuin oikealla palvelulla) ja RAPL-palvelinta vastaan, jolle syötettiin
   keinotekoisia laskurilukemia. Tämä varmisti laskurien ylivuodon käsittelyn,
   pelkän package-vyöhykkeen käytön ja tuotetunnisteiden tarkistuksen.
3. **Koko pino paikallisesti.** Mittauspino käynnistettiin pöytäkoneella, ja Windowsin
   validointiskripti `check_setup.ps1` ajettiin sitä vastaan. Kaikki seitsemän vaihetta
   menivät läpi. Energia oli tässä keinotekoinen, muu ketju todellinen.
4. **Kahden koneen ajo kaapelin yli.** `check_setup.ps1 -Sut 10.10.10.1 -SmokeSeconds 30`
   meni läpi kaikilta osin:

| Vaihe | Tulos |
|---|---|
| RAPL SUT:lta | laskurit luettavissa ja kasvavat; lepoteho 2,48 W |
| Palvelut | molemmat valmiita, tämän haaran build tunnistettu |
| Tuoteluettelo | tuotteille 7–106 oikeat kuvat, ei "not found" -kuvaa |
| Välimuistikytkin | päällä: 20 samaa pyyntöä → 20 osumaa; pois: laskurit eivät liiku |
| Savuajo, välimuisti päällä | 400,5 req/s, 4,08 W, 0,010179 J/pyyntö, osumaprosentti 63 % |
| Savuajo, välimuisti pois | 395,4 req/s, 3,97 W, 0,010035 J/pyyntö |

Raakatietueet: [validation/2026-09-28/](validation/2026-09-28/).

---

## 3. Kuormituksen rajat: mikä pullonkaula on ja missä

Savuajossa SUT:n teho nousi lepotasosta vain noin 1,5 W. Tämä tarkoittaa, ettei kuorma
kuormittanut SUT:n suoritinta. Syy selvitettiin kolmella kokeella (välimuisti pois,
400×310-kuvat, suljettu kuormamalli).

**Rinnakkaisuuskoe, yksi generaattoriprosessi:**

| Rinnakkaisuus | req/s | Mediaani | package-0 | Generaattorin CPU |
|---|---|---|---|---|
| 4 | 442 | 4,2 ms | 4,1 W | 0,6 ydintä |
| 8 | 1 030 | 7,3 ms | 5,5 W | 1,8 ydintä |
| 16 | 1 045 | 14,9 ms | 5,6 W | 1,9 ydintä |
| 32 | 1 000 | 31,2 ms | 5,5 W | 1,9 ydintä |
| 64 | 985 | 63,7 ms | 5,5 W | 1,9 ydintä |

Läpäisy tasaantuu noin 1 000 req/s:iin ja sen jälkeen vain vasteaika kasvaa. Kyseessä on
jonoutuminen jonkin rajoittavan tekijän edessä.

**Kolme generaattoriprosessia rinnakkain:** yhteensä 1 486 req/s ja 6,1 W. Yksi
Python-prosessi on siis itsessään raja, koska sen säikeet jakavat yhden tulkin lukon
(GIL). Tämän kokeen tietueissa kunkin tiedoston J/pyyntö on virheellinen: se jakaa koko
SUT:n energian vain yhden prosessin pyynnöillä. Oikea yhdistetty arvo on noin
6,1 W / 1 486 req/s ≈ 0,0041 J/pyyntö.

**Kaistanlaskelma:** vastauksen koko tuotteille 7–106 on 65–98 KiB. Zipf-jakaumalla
(alpha 0,8) painotettu keskiarvo on 76,3 KiB, koska kuvat siirtyvät base64-merkkijonoina
JSONissa. 1 486 req/s × 76,3 KiB ≈ 930 Mbit/s, mikä on käytännössä gigabitin
hyötykuorman yläraja (noin 940 Mbit/s).

**Johtopäätös.** Tällä kuormalla ensin loppuu kuormageneraattori (noin 1 000 req/s per
prosessi) ja sitten verkko (noin 1 490 req/s). SUT:n suoritin ei ole missään vaiheessa
pullonkaula: tehon kasvu lepotasoon nähden on korkeintaan noin 3,6 W. Seuraukset
mittausasetelmalle:

- Suljetussa kuormamallissa molemmat kokoonpanot ajautuvat samaan verkon rajaan, joten
  läpäisy on sama rakenteellisista syistä, ei siksi että muutoksella ei olisi vaikutusta.
- Pyyntökohtaisesta energiasta suurin osa on lepotehoa, joka laimentaa eroja.
- Ratkaisuksi valittiin kiinteä pyyntötaso (avoin kuormamalli) kaikkien rajojen alapuolella
  ja lepotason ylittävän energian raportointi kokonaisenergian rinnalla (kohta 6).
  10 Gbit/s -verkko ei ratkaisisi ongelmaa: generaattori tulisi taas vastaan, eikä
  vertailun pätevyys edellytä SUT:n ajamista maksimiin.

**Kiinteän pyyntötason koeajo** (3 prosessia × 8 säiettä, 600 req/s): toteutui
597,8 req/s. Lähetysviive jäi 95. persentiilissä 0,66 ms:iin, eli generaattori pysyi
aikataulussa. Teho oli 5,26 W ja välittömästi ennen ajoa mitattu lepoteho 4,17 W.
Energiaa kului 0,0088 J/pyyntö, josta lepotason ylittävää vain 0,0018 J. Noin 80 %
pyyntökohtaisesta energiasta oli siis lepotehoa.

Huomaa myös, että lepoteho vaihteli samana päivänä: 2,48 W (`check_setup`) ja 4,17 W
(koeajo). Tämä on peruste mitata lepoteho jokaisen ajon yhteydessä (`--idle`) eikä
kerran istunnon alussa.

---

## 4. Pystytyksessä löydetyt virheet ja niiden merkitys pätevyydelle

Jokainen näistä olisi tuottanut uskottavan näköisiä mutta vääriä lukuja. Siksi ne
kannattaa mainita luvussa 4.3 tai 4.5: ne osoittavat, että mittausketjun oikeellisuus
tarkistettiin eikä sitä oletettu.

| # | Havainto | Mitä olisi tapahtunut | Korjaus |
|---|---|---|---|
| 1 | Energia luettiin koneelta, jolla mittausskripti ajetaan | Generaattorikoneella ei ole RAPLia, joten skripti olisi hiljaisesti vaihtanut keinotekoisiin lukuihin tai mitannut väärää konetta | `rapl_server.py` SUT:lle; ajo keskeytyy, jos luku epäonnistuu |
| 2 | Kaikki RAPL-vyöhykkeet laskettiin yhteen | core ja uncore sisältyvät package-vyöhykkeeseen, psys kattaa koko alustan. Sama energia olisi laskettu 2–3 kertaan | Pääluku vain `package-*`, muut tallennetaan erikseen |
| 3 | Tuotetunnisteet alkavat tietokannassa arvosta 7, skripti arpoi alkaen 0:sta | Tunnisteet 0–6 saavat saman "not found" -kuvan, mikä olisi nostanut osumaprosenttia keinotekoisesti | `--first-product-id 7` ja tunnistealueen tarkistus ennen ajoa |
| 4 | Oletuspino ei julkaise kuvapalvelun porttia, ja kytkin on pelkkä JVM-parametri | Ohjeen mukainen osoite olisi osunut WebUI:hin (404) | Oma Compose-tiedosto; kytkin `CATALINA_OPTS`:iin, koska `start.sh` ylikirjoittaa `JAVA_OPTS`:n |
| 5 | Docker Hubin samannimisissä imageissa ei ole välimuistia | Puuttuva paikallinen build olisi hiljaa korvautunut, jolloin olisi verrattu lähtötasoa lähtötasoon | `pull_policy: never` ja buildin tunnistus `check_setup`-vaiheessa 4 |
| 6 | `setCacheSize` muuttaa tallennuskerroksen välimuistia, ei renderöityjen kuvien välimuistia | Kokopyyhkäisy olisi mitannut väärää välimuistia | Merkitty oppaaseen keskeneräiseksi; vaatii oman rajapinnan |
| 7 | Yksi Python-prosessi rajoittaa noin 1 000 req/s:iin | Generaattorin raja olisi näyttänyt palvelun rajalta | `--processes` ja kiinteä pyyntötaso |
| 8 | Ylikuormitettu kiinteän tason ajo jatkoi myöhästyneiden pyyntöjen lähettämistä mitatun ikkunan jälkeen | 5 s:n ikkuna venyi 50 sekuntiin | Havaittiin testissä ja korjattiin ennen käyttöä |
| 9 | Kaikki tämän päivän ajot tehtiin akkuvirralla | Akkutilassa kellotaajuudet ja tehorajat eroavat verkkovirrasta ja voivat muuttua varauksen laskiessa | Varsinaiset ajot verkkovirralla ja täydellä akulla |

Lisäksi kaksi käytännön havaintoa, jotka eivät vaikuta pätevyyteen:

- Kuvapalvelun tilasivu näyttää käynnistyksessä hetken "Images Created: 0 / 0", kunnes
  persistence on rekisteröitynyt. Se ei ole virhe; ennen mittausta odotetaan, että
  `finished` palauttaa arvon true.
- Kaapelilinkki katkesi kerran läppärin puolelta, todennäköisesti lepotilan vuoksi. Lepotila
  on estettävä varsinaisen sarjan ajaksi.

---

## 5. Alustavat havainnot välimuistista ja kuvamuodosta

Nämä eivät ole tuloksia. Ne ohjasivat päätöstä ottaa toinen suunnittelumalli mukaan, ja
ne kannattaa tuntea, kun varsinaisia tuloksia tulkitaan.

**Välimuisti: näyttö on toistaiseksi ratkaisematon, ei kielteinen.**

- Paikallisesti (pöytäkoneella, sama kone) tuote 7 vastasi 2,84 ms:ssa välimuistin kanssa
  ja 2,82 ms:ssa ilman. Tuote 7 on kuitenkin suosituin tuote, joten se pysyy myös vanhassa
  tallennuskerroksen LFU-välimuistissa. Koe ei siksi olisi voinut paljastaa eroa.
- Kaapelin yli tehdyt ajot olivat generaattorin tai verkon rajoittamia (kohta 3), ja lepoteho
  hallitsi pyyntökohtaista energiaa. Eron puuttuminen niissä ei kerro välimuistista.
- Välimuistin 3 MB:n oletusbudjetti täyttyi ja poistoja oli paljon (1 957 yhdessä
  paikallisessa ajossa). Budjetti sitoo, mikä on kokopyyhkäisyn kannalta hyvä.
- Ensimmäinen koe, joka voi vastata kysymykseen, on kiinteän pyyntötason ajo verkkovirralla
  lepotason ylittävän energian kanssa.

**Kuvamuoto: vastauksen koko ohjaa pyynnön kustannusta.**

Palvelu tallentaa ja palauttaa kuvat base64-koodattuina PNG-tiedostoina
(`StoreImage.STORE_IMAGE_FORMAT = "png"`). Paikallisesti sama tuote pienessä koossa
(16,5 KiB) vastasi mediaanissa 2,56 ms:ssa ja suuressa (156,4 KiB) 3,80 ms:ssa. Noin
2,4 ms on kiinteää kustannusta ja noin 9 µs tulee jokaista KiB:ia kohti.

Palvelun omat kuvat JPEG-muotoon uudelleenkoodattuina (Javan ImageIO):

| Kuva | PNG | JPEG q 0,9 | JPEG q 0,8 | JPEG q 0,6 |
|---|---|---|---|---|
| tuote 7, 400×310 | 53 333 B | 14 177 B | 10 316 B | 7 604 B |
| tuote 8, 400×310 | 59 940 B | 21 776 B | 15 771 B | 11 389 B |
| tuote 50, 400×310 | 60 564 B | 17 138 B | 12 146 B | 8 636 B |
| tuote 106, 400×310 | 60 022 B | 19 908 B | 14 450 B | 10 405 B |
| tuote 300, 400×310 | 120 099 B | 30 610 B | 21 874 B | 15 547 B |
| tuote 7, 125×100 | 5 791 B | 2 906 B | 2 194 B | 1 703 B |
| tuote 300, 125×100 | 12 642 B | 6 342 B | 4 680 B | 3 386 B |

Laadulla 0,8 pääkuvat pienenevät 4–5-kertaisesti ja pikkukuvat 2–3-kertaisesti.

**Kuvanlaatu mitattuna (1.10.2026, `image_quality.py`).** Kaikki 100 tuotekuvaa koossa
400×310 haettiin PNG- ja JPEG-konteista sellaisina kuin asiakas ne saa, ja niitä verrattiin
SSIM-indeksillä (luminanssi, Wang ym. 2004:n parametrit) ja PSNR:llä. SSIM oli mediaanissa
0,979 (vaihteluväli 0,966–0,989) ja PSNR mediaanissa 28,5 dB (alin 23,9 dB). JPEG-tiedostot
olivat yhteensä 4,2 kertaa pienempiä. Normaalikoossa ero ei juuri erotu, mutta kolminkertaisessa
suurennoksessa teelehtien hieno rakenne on pehmeämpi ja värit haaleammat: kultaiset säikeet
harmaantuvat, ja taustan väri vuotaa reunoille. Tämä on JPEGin tavallinen virhe hienossa
värillisessä yksityiskohdassa (värikanavien alinäytteistys), ja toisen sukupolven koodaus
(kohta alla) voimistaa sitä. Aiempi arvio "silmämääräisesti ero ei näy" pätee siis vain
normaalikokoon. Vertailukuvat: `figures/image_quality_median_ssim.png` ja
`figures/image_quality_lowest_ssim.png` (tuote 71, alin SSIM).

Kytkös kirjallisuuteen: Schmitt ym. (2019) mittasivat tässä samassa palvelussa,
että kuvan muuntaminen tavutaulukoksi ja base64-koodaus vastasivat 67 %:sta palvelun
tehonkulutuksesta. Pienempi kuva
pienentää juuri tätä työtä sekä siirrettävää datamäärää.

**Kytkimen toteutuksesta tehdyt havainnot (29.9.2026).** Nämä vaikuttavat siihen, miten
kuvamuotosarjan tulos tulkitaan:

- **Base64-koodausta ei tehdä pyyntöä kohden.** Tämän haaran koodissa kuvat tallennetaan
  valmiiksi base64-muodossa. Pyyntöä kohden tehdään merkkijonon kopiointi, JSON-serialisointi
  ja lähetys, ja kaikki ne skaalautuvat vastauksen koon mukaan. Schmittin ym. 67 %:n
  kytkös on siksi tarkistettava: he ovat voineet mitata eri kohtaa. [TARKISTA]
- **Tuotekuvat luodaan kokoon 300×300, mutta mittaus pyytää kokoa 400×310.** Kuva
  skaalataan ensimmäisellä pyynnöllä ja tallennetaan. JPEG-kokoonpanossa 400×310-kuva on
  siksi toisen sukupolven JPEG (300×300 JPEG → skaalaus → JPEG), mikä kannattaa mainita
  kuvanlaadun yhteydessä.
- **Skaalain tuottaa kuvia, joissa on läpinäkyvyyskanava.** PNG-lähtötason 400×310-kuvissa on
  siksi alfakanava, jota valokuva ei tarvitse. Osa JPEGin kokoedusta tulee tämän kanavan
  poistumisesta. Javan JPEG-kirjoittaja ei hyväksy läpinäkyvyyskanavallista kuvaa lainkaan:
  `ImageIO.write` palauttaa `false` eikä kirjoita tavuakaan (testattu JDK 21:llä). Kytkin
  litistää kuvan valkoiselle pohjalle ennen koodausta.
- **Vain tuotekuvat vaihtuvat JPEGiksi.** Käyttöliittymän grafiikka (logot, kuvakkeet) pysyy
  PNG:nä, koska siinä on läpinäkyvyyttä eikä se ole valokuvaa.
- **TeaStoren oma tallennuskerroksen välimuisti on päällä kaikissa kokoonpanoissa**, kuten
  TeaStoressa itsessään. Pienemmät JPEG-kuvat mahtuvat siihen moninkertaisesti, mikä vähentää
  levylukuja. Tämä on osa kuvamuodon vaikutusta TeaStoren omassa arkkitehtuurissa, eikä se
  liity tässä työssä lisättyyn renderöityjen kuvien välimuistiin, joka on JPEG-sarjassa pois
  päältä.

**JVM-instanssien välinen vaihtelu (havaittu 2. sessiossa 1.10.2026).** Kokopyyhkäisy
käynnistää välimuistikontin uudelleen jokaista budjettia varten, eli jokainen piste on oma
JVM-instanssinsa. Kaksi havaintoa:

- **Ensimmäinen ajo uudelleenkäynnistyksen jälkeen poikkeaa.** Jokaisen budjetin
  ensimmäisessä ajossa lepoteho oli 1,60–1,76 W (muut ajot noin 1,47 W) ja J/pyyntö noin
  1–1,5 % suurempi. Välimuisti pois -pisteessä, jonka konttia ei käynnistetty uudelleen,
  näin ei käynyt. JVM ei ole vielä vakiintunut kuvien luonnin jälkeen. Pyyhkäisyn
  ensimmäiset ajot jätetään analyysistä pois.
- **Saman kokoonpanon instanssit eroavat toisistaan yhtä paljon kuin välimuistin
  vaikutus.** Vain kunkin pisteen ajot 2–3:

  | Sama kokoonpano, eri instanssi | J/pyyntö |
  |---|---|
  | 3 MiB, 1. sessio (yksi instanssi, 10 ajoa) | 0,00485–0,00494 |
  | 3 MiB, pyyhkäisy (uusi instanssi) | 0,00494–0,00495 |
  | 8 MiB ja 16 MiB, osumatarkkuus molemmissa 1,0 eli sama työ | 0,00467 ja 0,00483 (ero 3,4 %) |

  Välimuisti pois -kontti oli käynnissä koko päivän ilman uudelleenkäynnistystä, ja se
  pysyi ±0,3 %:n sisällä: 1. session sarja 0,00495, kohinakokeet 0,00493–0,00496,
  pyyhkäisy 0,00493.

Saman kokoonpanon kaksi JVM-instanssia eroavat siis noin 1,5–3,5 %. Tämä on tunnettu
ilmiö: JIT-käännös, roskienkeruu ja muistin asettelu tekevät jokaisesta käynnistyksestä
hieman erilaisen (Georges, Buytaert & Eeckhout 2007). 1. session välimuistisarja vertasi
yhtä välimuisti päällä -instanssia yhteen välimuisti pois -instanssiin, ja sen 1,6 %:n ero
on samaa suuruusluokkaa. Sarjan 10 paria osoittavat eron olevan todellinen näiden kahden
instanssin välillä, mutta eivät erota välimuistin vaikutusta instanssien välisestä erosta.
Siksi pääsarja toistetaan niin, että jokainen pari ajetaan omilla, uusilla instansseilla
(SETUP.md, D6). Kuvamuodon ero (−27 %) on noin kymmenkertainen tähän vaihteluun nähden, eikä
sitä tarvitse toistaa. Pyyhkäisyn käyrä raportoidaan suuntaa antavana: jokainen piste on
yksi instanssi, joten 8 ja 16 MiB:n eroa ei voi lukea budjetin vaikutukseksi.

---

## 6. Päätökset ja niiden perustelut

Päätösten kirjallisuuslähteet on esitetty opinnäytetyössä.

| Päätös | Perustelu | Vaihtoehto ja miksi ei |
|---|---|---|
| Kuorma kohdistetaan suoraan kuvapalveluun | Muutos on kuvapalvelussa; eristys tekee vaikutuksen kohdistettavaksi | Koko kauppa WebUI:n kautta: muut palvelut kuluttavat energiaa, ja vaikutus laimenee ja hämärtyy |
| Zipf-suosiojakauma (alpha 0,8, robustisuus 0,6) | Breslau ym. (1999): verkkoliikenteen suosio noudattaa Zipf-jakaumaa, alpha 0,64–0,83 | TeaStoren omat profiilit arpovat tuotteet tasajakaumasta (kohta 7), mikä on lähes pahin mahdollinen tapaus välimuistille |
| Kiinteä pyyntötaso rajojen alapuolella | Molemmat kokoonpanot tekevät saman työn, ja tehoero johtuu koodista; sama periaate kuin SPECpowerin kuormatasoissa | Suljettu malli ajautuu verkon rajaan (kohta 3); 10 Gbit/s -verkko ei poistaisi generaattorin rajaa |
| Oma Python-generaattori | Vain standardikirjasto; lukee energian ja välimuistilaskurit täsmälleen mitatun ikkunan rajoilla ja tallentaa ne samaan tietueeseen; rajat mitattiin (kohta 3) | TeaStoren HTTP Load Generator, JMeter, Locust: mitoitettu WebUI-käyttäjäistuntoihin, eivät lue laskureita |
| Energian pääluku package-0; lisäksi lepotason ylittävä osuus | Vyöhykkeet ovat sisäkkäisiä (kohta 4, havainto 2); lepoteho on noin 80 % pyyntökohtaisesta energiasta ja vaihtelee | Kokonaisenergia yksin laimentaa eron; psys kattaa koko alustan eikä ole vertailukelpoinen |
| Sama binääri, kaksi konttia rinnakkain | Ei käännöseroja; vuorottelu ilman uudelleenkäynnistyksiä | Kaksi eri buildia: käännös- ja ympäristöerot sekoittuisivat vaikutukseen |
| Välimuistin pääsarja (3. sessio): molemmat kontit luodaan uudelleen ennen jokaista paria, ja yksi vakiintumisajo kontilta hylätään; 20 paria | JVM-instanssien välinen ero on samaa suuruusluokkaa kuin välimuistin vaikutus (kohta 5). Kun jokainen pari on omat instanssinsa, parien hajonta sisältää instanssien välisen vaihtelun, ja testi vertaa vaikutusta siihen (Georges ym. 2007). Vakiintumisajo, koska ensimmäinen ajo uudelleenkäynnistyksen jälkeen poikkesi pyyhkäisyssä. 20 paria voima-analyysin perusteella ennen dataa (KUVIOT_JA_TAULUKOT.md, kohta 3) | Yksi instanssi kokoonpanoa kohden (1. sessio): parit toistavat saman instanssieron, eikä sitä voi erottaa vaikutuksesta. 10 paria: 1,6 %:n säästö jäisi havaitsematta noin joka toisella kerralla. Monta instanssia ja monta ajoa kustakin (Kalibera & Jones 2013 [TARKISTA]): tarkempi, mutta ei mahdu yhteen sessioon |
| Pyyntötaso: korkein porras, jolla mikään ei kyllästy (verkko ≤ 70 % linkistä, vasteajan p95 ≤ 1,5 × alimman portaan, generaattori aikataulussa) | Kyllästynyt osa jonouttaa pyyntöjä, jolloin kokoonpanot eivät enää tee samaa työtä. Rajan alla korkeampi taso levittää lepotehon useammalle pyynnölle ja kasvattaa koodista riippuvaa osuutta. SUT:n pieni käyttöaste vastaa palvelinten tavallista tilannetta (Barroso & Hölzle 2007) [TARKISTA] | SUT:n ajaminen maksimiin: verkko ja generaattori tulevat vastaan ensin (kohta 3). Pienemmät kuvat siirtäisivät verkon rajaa, mutta muuttaisivat kuormaa |
| Välimuistin budjetti pääsarjassa 3 MiB | TeaStoren oma oletus tallennuskerroksen välimuistille, eli sitä ei ole viritetty muutoksen hyväksi. Noin 40 % tuoteluettelon kuvista mahtuu siihen, joten budjetti sitoo | Suurempi budjetti: mahtuisi koko luettelo, ja tulos olisi paras mahdollinen tapaus |
| Budjetti säädetään JVM-parametrilla, ei REST-kutsulla | Parametri tallentuu jokaiseen ajotietueeseen (`catalina_opts`), ja välimuisti on jokaisen pisteen alussa tyhjä; sama mekanismi kuin päälle/pois-kytkimessä | REST-kutsu ajon aikana: nopeampi pyyhkäisy, mutta välimuisti ja laskurit on tyhjennettävä käsin, ja väärä budjetti voi jäädä huomaamatta päälle |
| Kokopyyhkäisy: 1, 2, 3, 4, 8 ja 16 MiB sekä välimuisti pois, 3 toistoa, ennalta arvottu järjestys | Kaksinkertaistuvat askeleet sopivat logaritmiselle akselille (Breslau ym. 1999: osumaprosentti kasvaa logaritmisesti koon mukana). 3 MiB on pääsarjan piste, 16 MiB kaksinkertainen koko luettelon kuviin nähden (noin 7,5 MiB), eli kaikki mahtuu. Välimuisti pois samassa sessiossa on vertailukohta | Pyyhkäisyn jättäminen pois: tulos koskisi vain yhtä, TeaStoren valitsemaa budjettia eikä kertoisi muistin ja energian välisestä kompromissista |
| Kuvamuoto erillisenä kokeena, välimuisti pois kummassakin kokoonpanossa | Toinen, erillinen suunnittelumalli omalla kompromissillaan (kuvanlaatu). Kumpikin malli verrataan samaan lähtötasoon (PNG, välimuisti pois) | Välimuisti päällä: JPEG-kuvia mahtuisi 3 MiB:iin moninkertainen määrä, ja kuvamuodon ja osumaprosentin vaikutukset sekoittuisivat. Yhdistelmä (2×2) jää jatkotutkimukseksi |
| JPEG vain tuotekuville; käyttöliittymän grafiikka pysyy PNG:nä | Valokuville JPEG, grafiikalle ja läpinäkyvyydelle PNG (Wallace 1992 [TARKISTA]); vastaa yleistä käytäntöä | Kaikki kuvat JPEGiksi: logojen läpinäkyvyys katoaisi, eikä mittaus pyydä niitä |
| JPEG-laatu 0,8 | Kuvat pienenevät 4–5-kertaisesti; SSIM mediaanissa 0,979 (alin 0,966). Normaalikoossa ero ei juuri erotu, suurennoksessa hieno rakenne pehmenee (kohta 5) | Javan oletus 0,75 tai 0,6: pienempi koko, mutta laatuero alkaa näkyä ja on vaikeampi perustella |
| Toinen suunnittelumalli: kuvakoon optimointi (PNG → JPEG) | Kohta 5; kohdistuu Schmittin ym. mittaamaan hallitsevaan kustannukseen | Vastausten pakkaus (gzip): base64-PNG pakkautuu huonosti ja pakkaus lisää SUT:n CPU-työtä; verkon säästöä RAPL ei näe |

---

## 7. TeaStoren omat kuormageneraattorit

Repositorion `examples/`-hakemistossa on kolme kuormatyökalua: HTTP Load Generator
(Lua-skriptit), JMeter ja Locust. Kaikki simuloivat kaupan käyttäjiä WebUI:n kautta.
Mikään ei kutsu kuvapalvelua suoraan. Olennainen havainto koodista:

- Locust: `product_id = randint(7, 506)` eli tasajakauma 500 tuotteen yli.
- HTTP Load Generator (`teastore_browse.lua`): tuote arvotaan tasaisesti sivun linkeistä
  (`math.random`).

Tasajakauma on välimuistin kannalta lähes pahin tapaus eikä vastaa havaittua
verkkoliikennettä (Breslau ym. 1999). Nämä profiilit siis aliarvioisivat järjestelmällisesti
minkä tahansa välimuistin vaikutuksen. Tämä on konkreettinen ja tarkistettava peruste
omalle kuormaprofiilille.

HTTP Load Generator tuottaa lisäksi kuorman kiinteinä pyyntötasoina ajan funktiona
(`increasing*Intensity.csv`). Tämä tukee kiinteän pyyntötason valintaa: TeaStoren
kehittäjät mittaavat samalla periaatteella.

Havainto tulevaa työtä varten: WebUI upottaa kuvat HTML:ään data-URI:na
(`<img src="${productImage}">`). Selain ei voi välimuistittaa niitä, joten jokainen
sivunlataus hakee kaikki kuvat kuvapalvelulta uudelleen, myös otsikon logon. Kuvien
tarjoaminen omista osoitteistaan välimuistiotsakkeiden kanssa vähentäisi kuvapalvelun
kuormaa enemmän kuin kumpikaan tämän työn muutoksista. Sen mittaaminen vaatisi kuitenkin
selaimen tavoin välimuistittavan WebUI-tason kuorman.

---

## 8. Avoimet asiat ennen varsinaisia mittauksia

- [x] SUT verkkovirtaan ja akun latausraja (conservation mode) päälle; akun tila
      "Not charging" (SETUP.md, D1)
- [x] Lepotila ja kannen sulkeminen pois käytöstä sarjan ajaksi: energiapalvelin
      `systemd-inhibit`in alla ja työpöytä pysäytetty (SETUP.md, A6 ja D1)
- [x] `sut_session.sh --quiet` jokaisen session alussa (SETUP.md, D1): olosuhteet,
      taustapalvelut, prosessilista, lämpötilan tasaantuminen ja session lepoteho
- [x] Tasoporras verkkovirralla, 400–1 200 req/s; valittu 1 000 req/s (1. sessio)
- [x] Kohinakoe jokaisen session alussa (SETUP.md, D3); CV 0,4 % (1. sessio) ja 0,2 %
      (2. sessio), 10 paria
- [x] 3. sessio 6.10.2026: välimuistin pääsarja uusilla JVM-instansseilla, 20 paria (SETUP.md, D6;
      perustelu kohdassa 5, parien määrä KUVIOT_JA_TAULUKOT.md, kohta 3)
- [ ] Pyyhkäisyn analyysissä jätetään kunkin budjetin ensimmäinen ajo pois (kohta 5)
- [x] Taulukon 1 kuormageneraattoriksi mittaussessioiden läppäri, tiedot täydennetty
      (6.10.2026)
- [x] Kuvamuodon kytkin kuvapalveluun ja kolmas kontti Compose-tiedostoon (portti 8083)
- [x] Päätös kokopyyhkäisystä: tehdään, pisteet ja perustelut kohdassa 6
- [x] Renderöidyn välimuistin budjetti JVM-parametriksi (havainto 6); pyyhkäisy
      `run_sweep.ps1`, ohje SETUP.md, D5
- [x] Kuvapalvelun image käännettävä uudelleen SUT:lla (SETUP.md, A4), jotta parametri on mukana

---

## 9. Tekoälyn käyttö tässä vaiheessa (faktapohja lukuun 4.6)

Työssä käytettiin kahta Claude Code -istuntoa: toinen pöytäkoneella ja toinen läppärillä.
Molemmat kirjoittivat samaan Git-haaraan.

**Tekoäly teki:**

- Pöytäkoneen istunto: haaran käännös ja yksikkötestien ajo; mittausskriptin ensimmäisen
  version virheiden tunnistaminen (havainnot 1, 2 ja 4); koko pinon ajo ja validointi
  paikallisesti; rinnakkaisuus-, rinnakkaisprosessi- ja kaistakokeet (kohta 3);
  kuvamuotokoe (kohta 5); TeaStoren kuormatyökalujen läpikäynti (kohta 7);
  `measure.py`:n kiinteä pyyntötaso, moniprosessiajo ja lepotehon mittaus; tämä dokumentti.
- Läppärin istunto: `rapl_server.py`, mittauspinon Compose-tiedosto, `check_setup.ps1`,
  `run_ab.ps1`, SETUP.md ja havainto 3 (tuotetunnisteet).
- Istunnot tekivät osittain päällekkäistä työtä: pöytäkoneen istunto korjasi energian
  etäluvun ensin SSH:n kautta. Kun läppärin istunnon ratkaisu julkaistiin, se hylkäsi
  omansa ja siirtyi läppärin versioon.

**Käyttäjä teki itse** (täydennä ja tarkenna omin sanoin):

- Laitteiston hankinta ja kokoonpano: muistin lisäys, kaapelointi, IP-osoitteiden asetus
  Windowsiin, validointiskriptin ajo.
- Päätös validoida mittausketju ennen tarkkoja mittauksia.
- Ajatus toisesta Green Software -suunnittelumallista, jos välimuistin vaikutus jää
  pieneksi. Kuvamuodon valinta tehtiin tekoälyn ehdotuksesta mittausten jälkeen:
  [TÄYDENNÄ oma perustelu hyväksymiselle].
- Kiinteän pyyntötason ja oman kuormageneraattorin hyväksyminen tekoälyn perustelujen
  pohjalta: [TÄYDENNÄ, mitä punnitsit].

**Mitä tarkistettiin:** tekoälyn ehdotukset testattiin ennen käyttöä. Kuormamallin
ylikuormitusvirhe (havainto 8) löytyi testissä ennen kuin koodia käytettiin mittauksiin.
Tekoälyn aiempi tulkinta, että välimuisti on hyödytön, osoittautui ennenaikaiseksi, ja se
korjattiin (kohta 5). Tämä on hyvä esimerkki siitä, miksi tekoälyn johtopäätökset on
tarkistettava mittausasetelmaa vasten.
