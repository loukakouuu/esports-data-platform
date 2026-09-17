"""Le client HTTP porte à lui seul le respect des quotas : il se teste en détail."""

from __future__ import annotations

import httpx
import pytest

from ingestion.core.http import (
    HttpClient,
    HttpError,
    RateLimiter,
    RetryPolicy,
    parse_retry_after,
)


class FakeClock:
    """Horloge et mise en attente déterministes : le temps y avance sur commande."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_la_premiere_requete_ne_patiente_pas() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleeper=clock.sleep)

    assert limiter.acquire() == 0.0
    assert clock.slept == []


def test_la_cadence_est_tenue_entre_deux_requetes() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleeper=clock.sleep)

    limiter.acquire()
    waited = limiter.acquire()

    assert waited == pytest.approx(2.0)
    assert clock.slept == [pytest.approx(2.0)]


def test_un_appelant_lent_ne_paie_pas_de_cadence() -> None:
    """Le temps passé à traiter une page compte dans l'intervalle."""
    clock = FakeClock()
    limiter = RateLimiter(2.0, clock=clock.time, sleeper=clock.sleep)

    limiter.acquire()
    clock.now += 5.0

    assert limiter.acquire() == 0.0


def test_le_repli_double_et_reste_borne() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=10.0, jitter=0.0)
    delays = [policy.delay_for(attempt) for attempt in range(1, 6)]

    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0]


def test_la_gigue_reste_dans_sa_fourchette() -> None:
    policy = RetryPolicy(base_delay=4.0, jitter=0.25)

    assert policy.delay_for(1, rng=lambda: 0.0) == pytest.approx(3.0)
    assert policy.delay_for(1, rng=lambda: 1.0) == pytest.approx(5.0)


def test_un_retry_after_recu_fait_autorite() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=30.0)

    assert policy.delay_for(1, retry_after=12.0) == 12.0
    assert policy.delay_for(1, retry_after=900.0) == 30.0


def test_retry_after_illisible_est_ignore() -> None:
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after(None) is None


def test_une_politique_sans_tentative_est_refusee() -> None:
    with pytest.raises(ValueError, match="au moins une tentative"):
        RetryPolicy(max_attempts=0)


def test_le_user_agent_part_avec_la_requete() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(
        user_agent="esports-data-platform/0.1 (contact)",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.get_json("https://exemple.test/api")

    assert seen[0].headers["User-Agent"] == "esports-data-platform/0.1 (contact)"
    assert "gzip" in seen[0].headers["Accept-Encoding"]


def test_un_429_est_reessaye_en_respectant_retry_after() -> None:
    attempts: list[int] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(
        user_agent="tests",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=slept.append,
    )

    assert client.get_json("https://exemple.test/api") == {"ok": True}
    assert len(attempts) == 2
    assert slept == [7.0]


def test_une_panne_serveur_persistante_finit_par_abandonner() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = HttpClient(
        user_agent="tests",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=0.0),
        sleeper=lambda _: None,
    )

    with pytest.raises(HttpError, match="abandon après 3 tentatives") as raised:
        client.get("https://exemple.test/api")
    assert raised.value.status_code == 503


def test_un_404_ne_se_reessaie_pas() -> None:
    """Un refus définitif ne devient pas vrai parce qu'on insiste."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404)

    client = HttpClient(
        user_agent="tests",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _: None,
    )

    with pytest.raises(HttpError, match="404"):
        client.get("https://exemple.test/introuvable")
    assert len(attempts) == 1


def test_une_coupure_reseau_est_reessayee() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("connexion refusée", request=request)
        return httpx.Response(200, json=[])

    client = HttpClient(
        user_agent="tests",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(base_delay=0.0, jitter=0.0),
        sleeper=lambda _: None,
    )

    assert client.get_json("https://exemple.test/api") == []
    assert len(attempts) == 3


def test_une_reponse_qui_n_est_pas_du_json_est_signalee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    client = HttpClient(
        user_agent="tests",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(HttpError, match="illisible en JSON"):
        client.get_json("https://exemple.test/api")
