import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL_SECONDS
from .api import IntuisApi, IntuisApiError

_LOGGER = logging.getLogger(__name__)


class IntuisCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    """Coordonne la récupération des données Intuis."""

    def __init__(self, hass: HomeAssistant, api: IntuisApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.api = api

    async def _async_update_data(self) -> Dict[str, Any]:
        try:
            # Auth paresseuse (ne tape /oauth2/token que si nécessaire)
            await self.api.async_authenticate()

            # Home + statut
            home_id = await self.api.get_home_id()
            status = await self.api.homestatus(home_id)

            # Mesures (énergie si dispo), sinon sans type
            measures = {}
            try:
                measures = await self.api.gethomemeasure(
                    home_id,
                    scale="day",   # "day" fonctionne pour l’historique quotidien
                    offset=0,
                    limit=30,
                    rtype="energy",
                )
            except IntuisApiError as e:
                _LOGGER.debug("gethomemeasure avec type='energy' a échoué (%s) -> retry sans type", e)
                measures = await self.api.gethomemeasure(
                    home_id, scale="day", offset=0, limit=30, rtype=None
                )

            # Compactage minimal; les entités liront dedans
            data: Dict[str, Any] = {
                "home_id": home_id,
                "status": status,      # payload homestatus complet
                "measures": measures,  # payload gethomemeasure (si présent)
            }
            return data

        except IntuisApiError as err:
            raise UpdateFailed(f"Intuis API error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
