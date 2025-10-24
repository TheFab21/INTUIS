from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_HOST = "https://app.muller-intuitiv.net"

# ---- Exceptions ----------------------------------------------------------------


class IntuisApiError(Exception):
    pass


# ---- Low-level HTTP client with auth -------------------------------------------


@dataclass
class _AuthState:
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None  # epoch seconds


class IntuisHttpClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        client_id: str,
        client_secret: str,
        scope: str,
        user_prefix: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._user_prefix = user_prefix
        self._username = username
        self._password = password
        self._auth = _AuthState()

    def _auth_headers(self) -> Dict[str, str]:
        if not self._auth.access_token:
            return {}
        return {"Authorization": f"Bearer {self._auth.access_token}"}

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        form: Dict[str, Any] | None = None,
        content_type: str | None = None,
        auth: bool = True,
    ) -> Any:
        headers: Dict[str, str] = {}
        if auth:
            headers.update(self._auth_headers())
        if content_type:
            headers["Content-Type"] = content_type

        data = None
        if form is not None:
            data = aiohttp.FormData()
            for k, v in form.items():
                if v is None:
                    continue
                data.add_field(k, str(v))

        async with self._session.request(
            method, url, headers=headers, json=json, data=data
        ) as resp:
            if resp.status >= 400:
                try:
                    err_text = await resp.text()
                except Exception:
                    err_text = "<no body>"
                raise IntuisApiError(f"{method} {url} -> {resp.status} {resp.reason} | body: {err_text}")
            try:
                return await resp.json(content_type=None)
            except Exception as e:
                text = await resp.text()
                raise IntuisApiError(f"Invalid JSON from {url}: {e} | body: {text}") from e

    def _token_expiring(self) -> bool:
        if not self._auth.expires_at:
            return True
        # refresh a bit early (60s)
        return time.time() >= (self._auth.expires_at - 60)

    async def ensure_token(self) -> None:
        if self._auth.access_token and not self._token_expiring():
            return
        # try refresh
        if self._auth.refresh_token:
            try:
                await self._refresh()
                return
            except IntuisApiError as e:
                _LOGGER.warning("Token refresh failed, fallback to login: %s", e)
        # login
        await self.login()

    async def login(self) -> Dict[str, Any]:
        # Muller/Intuis OAuth2 password grant
        payload = {
            "client_id": self._client_id,
            "user_prefix": self._user_prefix,
            "client_secret": self._client_secret,
            "grant_type": "password",
            "scope": self._scope,
            "password": self._password,
            "username": self._username,
        }

        # Quelques réessais sur erreurs 5xx parfois renvoyées par /oauth2/token
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                data = await self._request_json(
                    "POST", f"{BASE_HOST}/oauth2/token", form=payload, content_type="application/x-www-form-urlencoded", auth=False
                )
                self._store_tokens(data)
                _LOGGER.debug("Logged in, token expires in %ss", data.get("expires_in"))
                return data
            except IntuisApiError as e:
                last_exc = e
                _LOGGER.error("Auth transient error, retry %d/3: %s", attempt, e)
                await asyncio.sleep(1.5 * attempt)
        raise IntuisApiError(f"Login failed: {last_exc}")

    async def _refresh(self) -> Dict[str, Any]:
        if not self._auth.refresh_token:
            raise IntuisApiError("No refresh_token")
        payload = {
            "client_id": self._client_id,
            "user_prefix": self._user_prefix,
            "client_secret": self._client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self._auth.refresh_token,
        }
        data = await self._request_json(
            "POST", f"{BASE_HOST}/oauth2/token", form=payload, content_type="application/x-www-form-urlencoded", auth=False
        )
        self._store_tokens(data)
        _LOGGER.debug("Refreshed token, expires in %ss", data.get("expires_in"))
        return data

    def _store_tokens(self, data: Dict[str, Any]) -> None:
        self._auth.access_token = data.get("access_token")
        self._auth.refresh_token = data.get("refresh_token", self._auth.refresh_token)
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)):
            self._auth.expires_at = time.time() + float(expires_in)
        else:
            self._auth.expires_at = time.time() + 3600

    # ---- Raw endpoints used by higher-level API ----


    async def get_homesdata(self) -> dict:
        """GET /api/homesdata -> dict (home à la racine).
        On tolère si le serveur renvoie du JSON sous forme de str.
        """
        await self.ensure_token()
        raw = await self._request_json("GET", f"{BASE_HOST}/api/homesdata", auth=True)

        # Si l’API renvoie une chaîne JSON, on la reparse
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                _LOGGER.error("homesdata: reçu une chaîne non-JSON (len=%s): %r", len(raw), raw[:200])
                raise IntuisApiError("Réponse /api/homesdata non JSON")

        if not isinstance(raw, (dict, list)):
            _LOGGER.error("homesdata: type inattendu: %s - extrait=%r", type(raw), str(raw)[:300])
            raise IntuisApiError("Schéma inattendu pour /api/homesdata")

        return raw


    async def post_homestatus(self, home_id: str) -> Dict[str, Any]:
        await self.ensure_token()
        form = {"home_id": home_id}
        return await self._request_json(
            "POST",
            f"{BASE_HOST}/syncapi/v1/homestatus",
            form=form,
            content_type="application/x-www-form-urlencoded",
            auth=True,
        )

    async def post_getconfigs(self) -> Dict[str, Any]:
        """Fallback discovery endpoint that includes home(s) config; no home_id needed."""
        await self.ensure_token()
        return await self._request_json(
            "POST",
            f"{BASE_HOST}/syncapi/v1/getconfigs",
            form={},
            content_type="application/x-www-form-urlencoded",
            auth=True,
        )

    async def post_gethomemeasure(
        self, home_id: str, *, scale: str, offset: int = 0, limit: int = 30, rtype: Optional[str] = None
    ) -> Dict[str, Any]:
        await self.ensure_token()
        form: Dict[str, Any] = {
            "home_id": home_id,
            "scale": scale,
            "offset": offset,
            "limit": limit,
        }
        if rtype:
            form["type"] = rtype  # ex: "energy"
        return await self._request_json(
            "POST",
            f"{BASE_HOST}/api/gethomemeasure",
            form=form,
            content_type="application/x-www-form-urlencoded",
            auth=True,
        )

    async def post_setroomthermpoint(
        self, home_id: str, room_id: str, mode: str, temp: Optional[float] = None, endtime: Optional[int] = None
    ) -> Dict[str, Any]:
        await self.ensure_token()
        # L’API /api/setroomthermpoint accepte form-urlencoded (Netatmo-like) OU JSON.
        # Ici on reste en form pour la compat.
        form: Dict[str, Any] = {"home_id": home_id, "room_id": room_id, "mode": mode}
        if isinstance(temp, (int, float)):
            form["temp"] = float(temp)
        if isinstance(endtime, (int, float)):
            form["endtime"] = int(endtime)
        return await self._request_json(
            "POST",
            f"{BASE_HOST}/api/setroomthermpoint",
            form=form,
            content_type="application/x-www-form-urlencoded",
            auth=True,
        )

    async def post_setstate_rooms(
        self, home_id: str, room_id: str, *, mode: str, temp: Optional[float] = None, endtime: Optional[int] = None
    ) -> Dict[str, Any]:
        await self.ensure_token()
        # Méthode alignée sur ton flow Node-RED pour piloter une pièce via /syncapi/v1/setstate (JSON)
        room: Dict[str, Any] = {
            "id": room_id,
            "therm_setpoint_mode": mode,  # "manual" | "home" | "away" | "hg" | "off"
        }
        if isinstance(temp, (int, float)):
            room["therm_setpoint_temperature"] = float(temp)
        if isinstance(endtime, (int, float)):
            room["therm_setpoint_end_time"] = int(endtime)

        body = {"home": {"id": home_id, "rooms": [room]}}

        return await self._request_json(
            "POST",
            f"{BASE_HOST}/syncapi/v1/setstate",
            json=body,
            auth=True,
        )

    async def post_switchhomeschedule(self, home_id: str, schedule_id: str) -> Dict[str, Any]:
        await self.ensure_token()
        form = {"home_id": home_id, "schedule_id": schedule_id}
        return await self._request_json(
            "POST",
            f"{BASE_HOST}/api/switchhomeschedule",
            form=form,
            content_type="application/x-www-form-urlencoded",
            auth=True,
        )


# ---- High-level API used by the integration ------------------------------------


class IntuisApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
        client_id: str,
        client_secret: str,
        scope: str,
        user_prefix: str,
    ) -> None:
        self._client = IntuisHttpClient(
            session,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            user_prefix=user_prefix,
            username=username,
            password=password,
        )
        self._home_cache: Optional[dict[str, Any]] = None
        self._username = username
        self._password = password

    # ---- Auth façade -----------------------------------------------------------

    async def async_authenticate(self) -> None:
        """Assure qu’un token est disponible (login/refresh au besoin)."""
        await self._client.ensure_token()

    async def login(self) -> Dict[str, Any]:
        """Force un login (peu utilisé si async_authenticate est appelé)."""
        return await self._client.login()

    # ---- Home discovery --------------------------------------------------------

        # ---- Home discovery (strict) ---------------------------------------------

    # ---- Home discovery (STRICT: home à la racine) ---------------------------

#-------- Extraction HOME (robuste) -----------------

    def _extract_home_from_homesdata(self, payload: Any) -> dict:
        """Accepte :
           - OBJET home à la racine (ton cas) : {"id": "...", ...}
           - sinon, tente: {"home": {...}}, {"homes": [{...}]}, [{"id": "..."}],
             ou des wrappers génériques {"data": {...}}, {"result": {...}}, {"body": {...}}.
        """
        # Pour debug : type + 1er niveau
        try:
            if isinstance(payload, dict):
                _LOGGER.debug("homesdata keys=%s", list(payload.keys())[:20])
            else:
                _LOGGER.debug("homesdata type=%s len=%s", type(payload), len(payload) if hasattr(payload, "__len__") else "?")
        except Exception:
            pass

        # Recherche itérative (BFS) afin d’identifier l’objet home le plus crédible.
        hints = {
            "rooms": 4,
            "modules": 3,
            "therm_schedules": 3,
            "schedules": 3,
            "modules_bridged": 2,
            "capabilities": 2,
            "timezone": 1,
            "city": 1,
            "name": 1,
        }

        def _score_candidate(obj: dict) -> int:
            score = 0
            for key, weight in hints.items():
                if key not in obj:
                    continue
                value = obj[key]
                if isinstance(value, (list, dict)):
                    if value:
                        score += weight
                elif value not in (None, ""):
                    score += weight
            # Favor real home dictionaries that expose child rooms explicitly.
            if "rooms" in obj and isinstance(obj["rooms"], list):
                score += 5
            return score

        queue: deque[Any] = deque([payload])
        seen: set[int] = set()
        best: dict | None = None
        best_score = -1

        while queue:
            current = queue.popleft()

            if isinstance(current, (dict, list)):
                obj_id = id(current)
                if obj_id in seen:
                    continue
                seen.add(obj_id)

            if isinstance(current, dict):
                candidate = (
                    current.get("id")
                    or current.get("_id")
                    or current.get("home_id")
                )
                if candidate is not None:
                    candidate_str = str(candidate).strip()
                    if candidate_str:
                        score = _score_candidate(current)
                        better = score > best_score
                        if not better and best is not None and score == best_score:
                            # Prefer candidates that actually look like a home
                            better = "rooms" in current and "rooms" not in best
                        if better:
                            best = current
                            best_score = score

                # Priorité aux wrappers courants : on les traite en premier
                for key in ("home", "body", "data", "result", "homes"):
                    value = current.get(key)
                    if isinstance(value, (dict, list)):
                        queue.appendleft(value)

                # Puis parcours générique pour couvrir tous les cas restants
                for value in current.values():
                    if isinstance(value, (dict, list)):
                        queue.append(value)

            elif isinstance(current, list):
                for item in current:
                    if isinstance(item, (dict, list)):
                        queue.append(item)

        if best is not None:
            return best
        def _looks_like_home(obj: dict) -> bool:
            if not any(key in obj for key in ("rooms", "modules", "capabilities", "timezone", "city")):
                return False
            candidate = obj.get("id") or obj.get("_id") or obj.get("home_id")
            if candidate is None:
                return False
            # accepte id numérique mais ignore les objets vides
            return str(candidate).strip() != ""

        def _search_home(obj: Any, depth: int = 0) -> dict | None:
            if depth > 32:  # sécurité contre la récursion cyclique
                return None
            if isinstance(obj, dict):
                if _looks_like_home(obj):
                    return obj

                # Explorer en priorité les clés pertinentes
                for key in ("home", "data", "result", "body"):
                    if key in obj:
                        found = _search_home(obj[key], depth + 1)
                        if found:
                            return found

                homes = obj.get("homes")
                if isinstance(homes, list):
                    for item in homes:
                        found = _search_home(item, depth + 1)
                        if found:
                            return found

                # Parcours générique de toutes les valeurs
                for value in obj.values():
                    found = _search_home(value, depth + 1)
                    if found:
                        return found

            elif isinstance(obj, list):
                for item in obj:
                    found = _search_home(item, depth + 1)
                    if found:
                        return found

            return None

        home = _search_home(payload)
        if home is not None:
            return home

        # Rien reconnu -> on logge l’échantillon exact pour voir ce qui arrive vraiment
        try:
            sample = json.dumps(payload, ensure_ascii=False)[:1000]
        except Exception:
            sample = str(payload)[:1000]
        _LOGGER.error("homesdata sans 'id' détectable. Échantillon: %s", sample)
        raise IntuisApiError("Schéma inattendu pour /api/homesdata (clé 'id' introuvable)")

    async def get_home_id(self) -> str:
        """Retourne l’ID du home (avec cache)."""
        if getattr(self, "_home_cache", None):
            hid = str(self._home_cache.get("id") or self._home_cache.get("_id") or self._home_cache.get("home_id") or "").strip()
            if hid:
                return hid
        home = await self.get_home()
        hid = str(home.get("id") or home.get("_id") or home.get("home_id") or "").strip()
        if not hid:
            raise IntuisApiError("home_id introuvable dans /api/homesdata")
        return hid

    async def get_home(self) -> dict:
        cached = getattr(self, "_home_cache", None)
        if isinstance(cached, dict) and str(cached.get("id", "")).strip():
            return cached

        payload = await self._client.get_homesdata()
        home = self._extract_home_from_homesdata(payload)

        if not isinstance(home, dict):  # garde-fou supplémentaire
            raise IntuisApiError("Schéma inattendu pour /api/homesdata (objet 'home' introuvable)")

        normalized = dict(home)
        hid = str(normalized.get("id") or normalized.get("_id") or normalized.get("home_id") or "").strip()
        if not hid:
            try:
                sample = json.dumps(payload, ensure_ascii=False)[:800]
            except Exception:
                sample = str(payload)[:800]
            _LOGGER.error("homesdata sans 'id' détectable. Échantillon: %s", sample)
            raise IntuisApiError("Schéma inattendu pour /api/homesdata (clé 'id' introuvable)")

        normalized["id"] = hid
        self._home_cache = normalized
        return normalized


    async def homestatus(self, home_id: Optional[str] = None) -> Dict[str, Any]:
        hid = home_id or await self.get_home_id()
        return await self._client.post_homestatus(hid)

    async def gethomemeasure(
        self,
        home_id: Optional[str] = None,
        *,
        scale: str = "day",
        offset: int = 0,
        limit: int = 30,
        rtype: Optional[str] = None,  # "energy" si supporté, sinon None
    ) -> Dict[str, Any]:
        hid = home_id or await self.get_home_id()
        return await self._client.post_gethomemeasure(hid, scale=scale, offset=offset, limit=limit, rtype=rtype)

    async def set_room_setpoint(
        self, home_id: str, room_id: str, temp: float, duration: int | None = None
    ) -> Dict[str, Any]:
        """Passe la pièce en 'manual' à temp, optionnellement pour 'duration' minutes."""
        endtime = None
        if isinstance(duration, (int, float)) and duration > 0:
            endtime = int(time.time() + float(duration) * 60.0)
        # Utilise /api/setroomthermpoint (compatible Netatmo)
        return await self._client.post_setroomthermpoint(
            home_id=home_id, room_id=room_id, mode="manual", temp=float(temp), endtime=endtime
        )

    async def set_room_mode(
        self,
        home_id: str,
        room_id: str,
        mode: str,
        *,
        temp: Optional[float] = None,
        endtime: Optional[int] = None,
        use_setstate: bool = True,
    ) -> Dict[str, Any]:
        """
        Change le mode d’une pièce.
        Modes acceptés côté Intuis (observés dans ton flow): "manual" | "home" | "away" | "hg" | "off"
        - Si temp/endtime fournis, ils sont transmis.
        - Par défaut on utilise /syncapi/v1/setstate (JSON), plus robuste.
        - Si besoin, on peut forcer la voie /api/setroomthermpoint en mettant use_setstate=False.
        """
        if use_setstate:
            return await self._client.post_setstate_rooms(
                home_id, room_id, mode=mode, temp=temp, endtime=endtime
            )
        # fallback netatmo-like
        return await self._client.post_setroomthermpoint(
            home_id=home_id, room_id=room_id, mode=mode, temp=temp, endtime=endtime
        )

    async def switch_home_schedule(self, home_id: str, schedule_id: str) -> Dict[str, Any]:
        return await self._client.post_switchhomeschedule(home_id, schedule_id)
