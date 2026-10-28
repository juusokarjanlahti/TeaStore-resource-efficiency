# Mittausaineisto — renderöityjen kuvien välimuisti

Tämä hakemisto ei ole osa Maven-reaktoria eikä vaikuta `mvn clean install` -buildiin.
Se kokoaa opinnäytetyön liitteisiin 2 ja 3 tulevan materiaalin yhteen paikkaan tämän
forkin sisällä, erillään sovelluskoodista ja yksikkötesteistä.

## Rakenne

- `scripts/` — Liite 2: mittausskriptit ja kuormitusprofiilin määrittely
  (esim. suosituimpiin kuviin painottuva kuormageneraattori palvelutason ajoja varten).
  Manuaalinen, ei-CI:hin kuuluva JVM-sisäinen mittausajuri on erikseen täällä:
  `services/tools.descartes.teastore.image/src/test/java/tools/descartes/teastore/image/benchmark/RenderedImageCacheBenchmark.java`.
- `data/` — Liite 3: mittausten raakadata (CSV/JSON-tulosteet ajoista, lähtötaso ja
  refaktoroitu versio erikseen tunnistettavissa tiedostonimestä).

## Ajaminen

Mock-ajo (kuiva-ajo, validoi mittausputken ilman oikeaa palvelua). Kaksi terminaalia:

```bash
python scripts/stub_image_service.py --cache off --port 8090
python scripts/measure.py --label cache_off --duration 20 --warmup 5 --energy synthetic
```

Toista `--cache on` / `--label cache_on`, sitten yhteenveto:

```bash
python scripts/aggregate.py
```

Mock-ajon energialuvut ovat keinotekoisia ja merkitty tulosteeseen sekä
`"mock": true` -kenttään. Ne validoivat vain putken, eivät kelpaa tuloksiksi.

Oikea ajo (Ubuntu-läppäri SUT:na, kuormageneraattori toiselta koneelta):

```bash
python scripts/measure.py --label cache_off --target http://<sut>:8080/tools.descartes.teastore.image --duration 120 --repeat 5
```

Energia luetaan RAPL-rajapinnasta (`/sys/class/powercap`); jos lukuoikeus puuttuu,
aja `sudo` oikeuksin. Vuorottele kokoonpanot A/B/A/B — `aggregate.py` varoittaa,
jos ajot näyttävät peräkkäisiltä.

### Kahden koneen ajo (läppäri SUT, toinen Ubuntu-läppäri kuormageneraattorina)

Vaiheittainen asennus- ja validointiohje on tiedostossa [SETUP.md](SETUP.md).
Lyhyesti: koneet on kytketty suoraan kaapelilla (SUT `10.10.10.1`, kuormageneraattori
`10.10.10.2`), ja SUT:n RAPL luetaan verkon yli `rapl_server.py`:n kautta, jotta
kuormageneraattori ei kuormita mitattavan koneen suoritinta. Välimuisti päällä
palvelee portti 8081 ja pois päältä 8082. Portti 8083 palvelee tuotekuvat JPEG-muodossa
välimuisti pois päältä erillistä kuvamuotovertailua varten. Kaikki ajetaan samasta
imagesta, ja ne eroavat vain JVM-parametrien osalta. Asetelma validoidaan
kuormageneraattorilla skriptillä `scripts/check_setup.sh` ja mitataan skripteillä
`scripts/run_ab.sh` ja `scripts/run_sweep.sh`. Skripteistä on myös PowerShell-versiot
Windows-kuormageneraattoria varten (SETUP.md, B6).

`measure.py` laskee J/pyyntö-luvun vain `package-*`-vyöhykkeistä, koska `core`,
`uncore` ja `psys` ovat päällekkäisiä sen kanssa. Tuotetunnisteet alkavat
TeaStoren tietokannassa arvosta 7 (`--first-product-id`); tunnisteet 0–6 palauttaisivat
"not found" -kuvan.

### Testipenkin validointi

Testipenkin kuvaus, validoinnin tulokset, pystytyksessä löydetyt virheet, kuormituksen
rajat ja mittausasetelman päätökset perusteluineen ovat tiedostossa
[TESTIPENKKI.md](TESTIPENKKI.md). Validointiajojen raakatietueet ovat hakemistossa
`validation/`, erillään `data/`-hakemistosta. Ne eivät ole tuloksia, koska ne ajettiin
akkuvirralla.

Varsinaiset vertailut ajetaan kiinteällä pyyntötasolla (`--rate`, `--processes`) ja
lepotehon mittauksella (`--idle`). Perustelu on MEASUREMENT_GUIDE.md:n kohdassa 6.

### Tuloksista kuvioiksi

Se, mitä kukin ajo tallentaa ja miten tulokset esitetään tilastollisesti (myös toistojen
määrän perustelu), on tiedostossa
[KUVIOT_JA_TAULUKOT.md](KUVIOT_JA_TAULUKOT.md).

### Kuvanlaatu (JPEG vs PNG)

`scripts/image_quality.py` mittaa JPEG-tuotekuvien laadun PNG-lähtötasoon nähden (SSIM ja
PSNR) ja piirtää vertailukuvat kansioon `figures/`. Se tarvitsee kirjastoja, joita
mittausskriptit eivät tarvitse, joten se ajetaan kontissa repositorion juuresta, pino
käynnissä:

```bash
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$PWD/thesis/measurements:/m" -w /m/scripts python:3.12-slim sh -c \
    "pip install --user -q -r requirements-quality.txt && python image_quality.py --sut localhost"
```

## Toistettavuus

Jokaisen tallennetun tulosteen yhteyteen merkitään:
- käytetty commit-hash tässä repossa,
- suoritusympäristön versiot (ks. opinnäytetyön taulukko 1),
- kytkimen tila (`ImageProvider.RENDERED_CACHE_ENABLED_PROPERTY`),
- kuormitusprofiili ja toistojen määrä.
