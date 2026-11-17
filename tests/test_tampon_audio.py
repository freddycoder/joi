"""Le tampon des dernières secondes envoyées à AssemblyAI (oreilles.TamponAudio)."""

from types import SimpleNamespace

import pytest

from joi import oreilles
from joi.oreilles import OCTETS_PAR_MS, TamponAudio

PAQUET_MS = 50


@pytest.fixture
def horloge(monkeypatch):
    instant = [100.0]
    monkeypatch.setattr(oreilles, "time", SimpleNamespace(monotonic=lambda: instant[0]))
    return instant


def _remplir(tampon: TamponAudio, horloge: list, paquets: int, premier: int = 0) -> None:
    """Des paquets de 50 ms, arrivés toutes les 50 ms ; le paquet i est rempli de l'octet i % 256."""
    for i in range(premier, premier + paquets):
        horloge[0] = 100.0 + i * PAQUET_MS / 1000
        tampon.ajouter(bytes([i % 256]) * (PAQUET_MS * OCTETS_PAR_MS))


def test_extraire(horloge):
    tampon = TamponAudio(secondes=1)
    _remplir(tampon, horloge, 10)  # 500 ms
    audio = tampon.extraire(0, 100)
    assert audio == bytes([0]) * (50 * OCTETS_PAR_MS) + bytes([1]) * (50 * OCTETS_PAR_MS)
    # À cheval sur deux paquets.
    audio = tampon.extraire(25, 75)
    assert audio == bytes([0]) * (25 * OCTETS_PAR_MS) + bytes([1]) * (25 * OCTETS_PAR_MS)
    assert tampon.extraire(100, 100) is None
    assert tampon.extraire(-10, 50) == bytes([0]) * (50 * OCTETS_PAR_MS)


def test_extraire_apres_l_oubli_du_debut(horloge):
    tampon = TamponAudio(secondes=1)
    _remplir(tampon, horloge, 60)  # 3 s : les 2 premières sont oubliées
    assert tampon.extraire(0, 100) is None
    assert tampon.extraire(1900, 2000) is None
    audio = tampon.extraire(2900, 3000)
    assert audio == bytes([58]) * (50 * OCTETS_PAR_MS) + bytes([59]) * (50 * OCTETS_PAR_MS)
    assert tampon.extraire(2500, 2550) == bytes([50]) * (50 * OCTETS_PAR_MS)


def test_instant(horloge):
    tampon = TamponAudio(secondes=1)
    _remplir(tampon, horloge, 60)
    assert tampon.instant(2525) == pytest.approx(100.0 + 50 * 0.05)  # le paquet 50
    assert tampon.instant(100) is None  # oublié
    assert tampon.instant(10_000) is None  # pas encore arrivé
