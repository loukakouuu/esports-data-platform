"""Client HTTP sobre : une requête à la fois, cadencée et réessayée.

Les quotas d'un fournisseur ne se respectent pas à coups de `sleep` dispersés
dans le code d'appel ; ils se respectent par construction, ici. Toute source
passe par ce client, et hérite donc de la cadence, du repli exponentiel et du
`User-Agent` identifiant sans avoir à y penser.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Codes qui justifient une nouvelle tentative. Un 403 ou un 404, non : ils
disent quelque chose de la requête, pas du moment où elle est partie."""

DEFAULT_TIMEOUT = 20.0


class HttpError(RuntimeError):
    """Échec d'appel, après épuisement des tentatives ou refus définitif."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


@dataclass(slots=True)
class RateLimiter:
    """Impose un intervalle minimal entre deux départs de requête.

    L'horloge et la mise en attente sont injectables : une cadence se teste,
    elle ne se vérifie pas à la montre.
    """

    min_interval: float
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _next_allowed: float = field(default=0.0, init=False)

    def acquire(self) -> float:
        """Attend s'il le faut, puis réserve le créneau suivant. Renvoie l'attente."""
        now = self.clock()
        waited = max(self._next_allowed - now, 0.0)
        if waited > 0:
            logger.debug("cadence : attente de %.2fs", waited)
            self.sleeper(waited)
        self._next_allowed = now + waited + self.min_interval
        return waited


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Repli exponentiel borné, avec gigue pour ne pas synchroniser les reprises."""

    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.25

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("une politique de reprise autorise au moins une tentative")

    def delay_for(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        rng: Callable[[], float] = random.random,
    ) -> float:
        """Délai avant la tentative suivante. Un `Retry-After` reçu fait autorité."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        delay = min(self.base_delay * 2.0 ** (attempt - 1), self.max_delay)
        return delay * (1 - self.jitter + 2 * self.jitter * rng())


def parse_retry_after(value: str | None) -> float | None:
    """Lit un en-tête `Retry-After` exprimé en secondes.

    La forme « date HTTP » est admise par la norme mais ni OpenDota ni Liquipedia
    ne l'emploient : on préfère l'ignorer plutôt que de mal la lire.
    """
    if value is None:
        return None
    try:
        return float(value.strip())
    except ValueError:
        logger.debug("en-tête Retry-After non numérique, ignoré : %r", value)
        return None


class HttpClient:
    """Client HTTP cadencé et réessayant, partagé par les sources."""

    def __init__(
        self,
        *,
        user_agent: str,
        min_interval: float = 0.0,
        retry: RetryPolicy | None = None,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        # Un agent générique se fait bloquer ; gzip est exigé par Liquipedia et
        # gratuit ailleurs.
        self._client.headers["User-Agent"] = user_agent
        self._client.headers["Accept-Encoding"] = "gzip"
        self._retry = retry or RetryPolicy()
        self._limiter = RateLimiter(min_interval, clock=clock, sleeper=sleeper)
        self._sleeper = sleeper

    def get(self, url: str, params: Mapping[str, Any] | None = None) -> httpx.Response:
        """Effectue un GET en respectant cadence et politique de reprise."""
        last_error = "aucune tentative"
        status: int | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            self._limiter.acquire()
            retry_after: float | None = None
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                last_error = f"erreur de transport : {exc}"
                status = None
            else:
                status = response.status_code
                if status not in RETRYABLE_STATUS:
                    if response.is_error:
                        raise HttpError(
                            f"{url} a répondu {status}", status_code=status, url=url
                        )
                    return response
                last_error = f"réponse {status}"
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt == self._retry.max_attempts:
                break
            delay = self._retry.delay_for(attempt, retry_after=retry_after)
            logger.warning(
                "%s — tentative %d/%d, nouvelle tentative dans %.1fs",
                last_error,
                attempt,
                self._retry.max_attempts,
                delay,
            )
            self._sleeper(delay)

        raise HttpError(
            f"{url} : abandon après {self._retry.max_attempts} tentatives ({last_error})",
            status_code=status,
            url=url,
        )

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET dont la réponse est attendue en JSON."""
        response = self.get(url, params)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} : réponse illisible en JSON", url=url) from exc

    def close(self) -> None:
        """Ferme la connexion sous-jacente, si ce client en est propriétaire."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
