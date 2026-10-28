# Validointiajot 28.9.2026

`measure.py`:n raakatietueet testipenkin validoinnista. Tulkinta ja yhteenveto ovat
tiedostossa [TESTIPENKKI.md](../../TESTIPENKKI.md). **Nämä eivät ole tuloksia:** SUT oli
akkuvirralla, ja jokainen ajo on yksittäinen. Tietueet ovat tarkoituksella `data/`-hakemiston
ulkopuolella, jotta `aggregate.py` ei koskaan yhdistä niitä varsinaisiin mittauksiin.

Kaikki ajot: SUT Intel i5-8250U (Lenovo ideapad 320-15IKB), generaattori Windows 11,
kaapeli 1 Gbit/s, commit `0f64467d`, 400×310-kuvat, tuotteet 7–106, Zipf alpha 0,8.
Per-pyyntö-vasteajat (`*_latencies.csv`) on jätetty pois koon vuoksi.

| Hakemisto | Mitä | Huomio |
|---|---|---|
| `1-smoke/` | `check_setup.ps1`:n vaihe 7, 30 s per kokoonpano, suljettu malli, 4 säiettä | Ainoat ajot, joissa välimuisti on päällä (portti 8081) |
| `2-concurrency/` | Välimuisti pois, rinnakkaisuus 4–64, yksi prosessi, suljettu malli | Läpäisy tasaantuu noin 1 000 req/s:iin (generaattorin raja) |
| `3-parallel/` | Kolme generaattoriprosessia yhtä aikaa, kukin 16 säiettä | **Tiedostokohtainen J/pyyntö on virheellinen:** se jakaa koko SUT:n energian yhden prosessin pyynnöillä. Oikea arvo ≈ 6,1 W / 1 486 req/s ≈ 0,0041 J/pyyntö |
| `4-fixed-rate/` | Kiinteä taso 600 req/s, 3 prosessia × 8 säiettä, lepoteho 10 s ennen ajoa | Ajettu `measure.py`:n uudella, vielä commitoimattomalla versiolla. Tietueen `commit`-kenttä näyttää siksi `0f64467d`, vaikka skripti oli jo muutettu |
